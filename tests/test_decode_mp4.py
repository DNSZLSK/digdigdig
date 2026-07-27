"""Formats que libsndfile ne sait pas ouvrir : MP4/M4A (AAC + ALAC), WMA.

Avant le repli PyAV, un dossier de .mp4 n'etait meme pas parcouru (extension absente de
AUDIO_EXTS) -> il paraissait VIDE, et un .m4a/.wma finissait en ERROR "info illisible".
Les fixtures sont encodees a la volee par PyAV : pas de binaire audio dans le repo, et le
test prouve la chaine complete (encodage reel -> scan -> verdict).
"""

from pathlib import Path

import numpy as np
import pytest

from ddd.core import decode
from ddd.core.quality import ERROR, LOSSY_EXTS, SKIPPED, analyze_file
from ddd.core.scan import AUDIO_EXTS, iter_audio_files

av = pytest.importorskip("av", reason="PyAV requis pour les formats non-libsndfile")

SR = 44100
DUR = 12.0


def _signal(sr=SR, dur=DUR):
    """Sinus + bruit large bande : donne un vrai contenu HF a mesurer."""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    rng = np.random.default_rng(0)
    left = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.05 * rng.standard_normal(t.size)
    right = 0.3 * np.sin(2 * np.pi * 660 * t) + 0.05 * rng.standard_normal(t.size)
    return np.stack([left, right]).astype(np.float32)


def _encode(path: Path, codec: str, fmt=None, **stream_opts) -> Path:
    with av.open(str(path), "w", format=fmt) as container:
        stream = container.add_stream(codec, rate=SR, **stream_opts)
        stream.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(_signal(), format="fltp", layout="stereo")
        frame.sample_rate = SR
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


def _encode_wma(path: Path) -> Path:
    """wmav2 veut du s16 entrelace ET un bit_rate explicite, sinon avcodec_open2 refuse."""
    s16 = (_signal() * 32767).astype(np.int16).T.reshape(1, -1)
    with av.open(str(path), "w", format="asf") as container:
        stream = container.add_stream("wmav2", rate=SR)
        stream.layout = "stereo"
        stream.bit_rate = 128000
        frame = av.AudioFrame.from_ndarray(s16, format="s16", layout="stereo")
        frame.sample_rate = SR
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


# --- le trou d'origine : l'extension n'etait meme pas parcourue -------------------

def test_mp4_est_parcouru_par_le_scan(tmp_path):
    """Un dossier de .mp4 ne doit plus avoir l'air vide."""
    _encode(tmp_path / "track.mp4", "aac", fmt="mp4")
    assert ".mp4" in AUDIO_EXTS
    assert [p.name for p in iter_audio_files(tmp_path)] == ["track.mp4"]


@pytest.mark.parametrize("ext", [".mp4", ".m4a", ".m4b", ".wma", ".ape", ".tta", ".wv"])
def test_extensions_declarees(ext):
    assert ext in AUDIO_EXTS


# --- decodage : infos + fenetre au sample rate natif ------------------------------

@pytest.mark.parametrize("name,codec,fmt", [
    ("aac.m4a", "aac", None),
    ("alac.m4a", "alac", None),
    ("aac.mp4", "aac", "mp4"),
])
def test_open_info_repli_pyav(tmp_path, name, codec, fmt):
    path = _encode(tmp_path / name, codec, fmt=fmt)
    info = decode.open_info(path)
    assert info.backend == "av"          # libsndfile a bien refuse, le repli a pris
    assert info.codec == codec
    assert info.samplerate == SR
    assert info.channels == 2
    assert info.duration == pytest.approx(DUR, abs=0.1)
    assert info.stream_bitrate > 0


def test_read_window_taille_et_sample_rate_natif(tmp_path):
    """La fenetre doit tomber au bon endroit, a la bonne taille, sans resample.

    Un resample deplacerait le cutoff spectral, c-a-d la mesure qui fonde le verdict.
    """
    path = _encode(tmp_path / "aac.m4a", "aac")
    info = decode.open_info(path)
    start, frames = 5 * SR, 3 * SR
    win = decode.read_window(path, info, start, frames)
    assert win is not None
    assert win.shape == (frames, 2)               # (samples, canaux), comme soundfile
    assert win.dtype == np.float64
    assert np.abs(win).max() > 0.05               # du signal, pas du silence


def test_read_window_seek_reel(tmp_path):
    """Le seek doit vraiment avancer : deux fenetres eloignees different."""
    path = _encode(tmp_path / "aac.m4a", "aac")
    info = decode.open_info(path)
    a = decode.read_window(path, info, 0, SR)
    b = decode.read_window(path, info, 8 * SR, SR)
    assert a is not None and b is not None
    assert not np.allclose(a, b)


# --- classement : le codec prime sur l'extension ----------------------------------

def test_alac_dans_m4a_est_un_conteneur_lossless(tmp_path):
    """.m4a est dans LOSSY_EXTS, mais de l'ALAC dedans est du lossless."""
    path = _encode(tmp_path / "alac.m4a", "alac")
    assert ".m4a" in LOSSY_EXTS                   # l'extension seule dirait "lossy"
    res = analyze_file(path)
    assert res.format_class == "lossless_container"
    assert res.verdict not in (ERROR, SKIPPED)


def test_aac_dans_mp4_reste_lossy(tmp_path):
    path = _encode(tmp_path / "aac.mp4", "aac", fmt="mp4")
    res = analyze_file(path)
    assert res.format_class == "lossy"
    assert res.verdict not in (ERROR, SKIPPED)
    assert res.sample_rate == SR


def test_wma_analysable(tmp_path):
    """Le .wma ne faisait plus crasher le scan (c280175) mais restait illisible."""
    path = _encode_wma(tmp_path / "track.wma")
    res = analyze_file(path)
    assert res.verdict not in (ERROR, SKIPPED)
    assert res.sample_rate == SR
    assert res.duration_s > 0


# --- le piege du .mp4 video : le debit ne doit pas compter la video ---------------

def test_bitrate_mp4_video_vient_de_la_piste_audio(tmp_path):
    """taille*8/duree sur un .mp4 video mesure surtout la VIDEO -> debit fantaisiste."""
    path = tmp_path / "clip.mp4"
    rng = np.random.default_rng(1)
    with av.open(str(path), "w") as container:
        vs = container.add_stream("mpeg4", rate=25)
        vs.width, vs.height, vs.pix_fmt = 320, 240, "yuv420p"
        audio = container.add_stream("aac", rate=SR)
        audio.layout = "stereo"
        for i in range(75):                       # 3 s de bruit video (gros, incompressible)
            img = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
            vf = av.VideoFrame.from_ndarray(img, format="rgb24")
            vf.pts = i
            for packet in vs.encode(vf):
                container.mux(packet)
        af = av.AudioFrame.from_ndarray(_signal(dur=3.0), format="fltp", layout="stereo")
        af.sample_rate = SR
        for packet in audio.encode(af):
            container.mux(packet)
        for packet in vs.encode(None):
            container.mux(packet)
        for packet in audio.encode(None):
            container.mux(packet)

    res = analyze_file(path)
    assert res.verdict not in (ERROR, SKIPPED)    # la piste audio est bien trouvee
    size_based = int(path.stat().st_size * 8 / res.duration_s / 1000)
    assert res.container_bitrate < size_based / 2  # on a pris l'audio, pas le fichier


# --- degradation propre si PyAV manque --------------------------------------------

def test_sans_pyav_erreur_lisible(tmp_path, monkeypatch):
    """Install source sans `av` : verdict ERROR clair, jamais un crash."""
    path = _encode(tmp_path / "aac.m4a", "aac")
    monkeypatch.setattr(decode, "_av", lambda: None)
    res = analyze_file(path)
    assert res.verdict == ERROR
    assert "info illisible" in res.reason
