"""Auto-test de bout en bout, pense pour le .exe GELE (`DDD.exe --selftest`).

Les tests pytest prouvent le code source ; ils ne prouvent PAS le bundle : un import
paresseux oublie par PyInstaller, une DLL manquante, un chemin frozen faux ne cassent
que dans le .exe livre. Ce module rejoue les chaines reelles DANS le bundle :
decodage (WAV/FLAC/MP4-AAC/WMA) -> verdict, empreinte fpcalc, binaire sldl, modele de
genre ONNX, construction de la GUI, et (sauf --offline) les vrais scrapers reseau.

Ecrit un rapport lisible (logs/selftest.txt) et rend 0 si tout est OK, 1 sinon.
Aucune ecriture hors d'un dossier temporaire + du rapport ; aucun login Soulseek.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

from .. import __version__, paths

Check = Tuple[str, Callable[[Path], str]]   # (nom, fn(tmpdir) -> detail ; leve si echec)

SR = 44100
DUR = 12.0

# URLs publiques stables pour le reseau (petites : quelques entrees seulement)
SC_LIKES = "https://soundcloud.com/ninja-tune/likes"
YT_CHANNEL = "https://www.youtube.com/@spinninrecords"   # uploads en 'Artiste - Titre'
YT_PRIVATE = "https://www.youtube.com/playlist?list=LL"


class SelfTestError(AssertionError):
    pass


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise SelfTestError(msg)


# ---- fixtures audio, generees a la volee (pas de binaire dans le bundle) ------

def _signal() -> np.ndarray:
    t = np.linspace(0, DUR, int(SR * DUR), endpoint=False)
    rng = np.random.default_rng(0)
    left = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.05 * rng.standard_normal(t.size)
    right = 0.3 * np.sin(2 * np.pi * 660 * t) + 0.05 * rng.standard_normal(t.size)
    return np.stack([left, right]).astype(np.float32)


def _write_sf(path: Path) -> Path:
    import soundfile as sf
    sf.write(str(path), _signal().T, SR)
    return path


def _encode_av(path: Path, codec: str, fmt: Optional[str] = None, s16: bool = False) -> Path:
    import av
    sig = _signal()
    with av.open(str(path), "w", format=fmt) as container:
        stream = container.add_stream(codec, rate=SR)
        stream.layout = "stereo"
        if s16:
            stream.bit_rate = 128000
            frame = av.AudioFrame.from_ndarray((sig * 32767).astype(np.int16).T.reshape(1, -1),
                                               format="s16", layout="stereo")
        else:
            frame = av.AudioFrame.from_ndarray(sig, format="fltp", layout="stereo")
        frame.sample_rate = SR
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


def _analyze(path: Path, want_class: str) -> str:
    from .quality import ERROR, analyze_file
    q = analyze_file(path)
    _expect(q.verdict != ERROR, f"{path.name}: ERROR ({q.reason})")
    _expect(q.format_class == want_class,
            f"{path.name}: format_class={q.format_class}, attendu {want_class}")
    _expect(abs(q.duration_s - DUR) < 0.5, f"{path.name}: duree {q.duration_s:.1f}s")
    return f"{q.verdict}, cutoff {q.cutoff_hz:.0f} Hz"


# ---- checks hors reseau -------------------------------------------------------

def _c_paths(tmp: Path) -> str:
    out = []
    for name, fn in (("logs", paths.logs_dir), ("staging", paths.staging_dir),
                     ("outputs", paths.outputs_dir)):
        d = fn()
        probe = d / ".ddd-selftest-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(f"{name}={d}")
    return "; ".join(out)


def _c_wav(tmp: Path) -> str:
    return _analyze(_write_sf(tmp / "t.wav"), "lossless_container")


def _c_flac(tmp: Path) -> str:
    return _analyze(_write_sf(tmp / "t.flac"), "lossless_container")


def _c_mp4_aac(tmp: Path) -> str:
    return _analyze(_encode_av(tmp / "t.mp4", "aac"), "lossy")


def _c_m4a_alac(tmp: Path) -> str:
    return _analyze(_encode_av(tmp / "t.m4a", "alac", fmt="ipod"), "lossless_container")


def _c_wma(tmp: Path) -> str:
    return _analyze(_encode_av(tmp / "t.wma", "wmav2", fmt="asf", s16=True), "lossy")


def _c_scan_mp4_folder(tmp: Path) -> str:
    from .scan import iter_audio_files
    d = tmp / "mp4dir"
    d.mkdir()
    _encode_av(d / "clip.mp4", "aac")
    found = [p.name for p in iter_audio_files(d)]
    _expect(found == ["clip.mp4"], f"dossier .mp4 parcouru = {found}")
    return "dossier .mp4 bien parcouru"


def _c_fpcalc(tmp: Path) -> str:
    from .identify import fingerprint_file
    exe = paths.fpcalc_exe()
    _expect(exe.exists(), f"fpcalc absent: {exe}")
    dur, fp = fingerprint_file(_write_sf(tmp / "fp.wav"))
    _expect(abs(dur - DUR) <= 1 and len(fp) > 20, f"empreinte inutilisable (dur={dur})")
    return f"{exe.name}, empreinte {len(fp)} car."


def _c_sldl(tmp: Path) -> str:
    exe = paths.sldl_exe()
    _expect(exe.exists(), f"sldl absent: {exe}")
    _expect(paths.sldl_config().exists(), f"profils sldl absents: {paths.sldl_config()}")
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    proc = subprocess.run([str(exe), "--help"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, **kw)
    _expect("Usage" in (proc.stdout + proc.stderr), f"sldl --help muet (rc={proc.returncode})")
    return f"{exe} demarre (pas de login Soulseek)"


def _c_genre_model(tmp: Path) -> str:
    from . import audioml
    _expect(audioml.available(), f"modele ONNX indisponible ({paths.effnet_model()})")
    res = audioml.classify(_write_sf(tmp / "g.wav"))
    _expect(bool(res), "classify() n'a rien rendu")
    return f"onnxruntime OK, top style = {res[0][0]}"


def _c_gui_build(tmp: Path) -> str:
    import flet  # noqa: F401
    from .. import gui
    _expect(callable(getattr(gui, "main", None)), "ddd.gui.main absent")
    return f"flet {getattr(flet, '__version__', '?')} + ddd.gui importes"


# ---- checks reseau (vraies sources, petites quantites) ------------------------

def _c_soundcloud(tmp: Path) -> str:
    from .scrapers.djset import scrape_djset
    rows = scrape_djset(SC_LIKES, newest=5)
    _expect(len(rows) >= 1, "0 track")
    with_len = sum(1 for r in rows if r["Length"])
    _expect(with_len >= 1, "aucune duree (colonne Length vide)")
    return f"{len(rows)} tracks, {with_len} avec duree, ex. {rows[0]['Artist']} - {rows[0]['Title']}"


def _c_youtube_channel(tmp: Path) -> str:
    from .scrapers.djset import scrape_djset
    msgs: List[str] = []
    rows = scrape_djset(YT_CHANNEL, progress=msgs.append, newest=5)
    _expect(any("channel" in m.lower() for m in msgs), f"pas route en chaine: {msgs[:2]}")
    _expect(len(rows) >= 1, "0 track")
    return f"{len(rows)} tracks, ex. {rows[0]['Artist']} - {rows[0]['Title']}"


def _c_youtube_private(tmp: Path) -> str:
    from .scrapers.djset import scrape_djset
    msgs: List[str] = []
    rows = scrape_djset(YT_PRIVATE, progress=msgs.append)
    _expect(rows == [] and any("private" in m for m in msgs), f"message absent: {msgs}")
    return "message 'liste privee' affiche"


def _c_bandcamp(tmp: Path) -> str:
    from . import config
    from .scrapers.bandcamp import scrape_bandcamp
    user = config.get("bandcamp_username") or ""
    if not user:   # jamais de pseudo code en dur : le repo est public
        return "SKIP (pas de pseudo Bandcamp configure)"
    rows = scrape_bandcamp(user, cache_dir=str(tmp / "bc-cache"), newest=2)
    _expect(len(rows) >= 1, "0 track")
    return f"{len(rows)} tracks, ex. {rows[0]['Artist']} - {rows[0]['Title']}"


def _c_discogs(tmp: Path) -> str:
    import os
    from . import config
    from .scrapers.discogs import scrape_discogs
    token = os.environ.get("DISCOGS_TOKEN") or config.get("discogs_token") or ""
    user = config.get("discogs_username") or ""
    if not token or not user:   # jamais de pseudo code en dur : le repo est public
        return "SKIP (pas de pseudo/token Discogs configure)"
    rows = scrape_discogs(user, token=token, cache_dir=str(tmp / "dc-cache"), newest=2)
    _expect(len(rows) >= 1, "0 track")
    return f"{len(rows)} tracks, ex. {rows[0]['Artist']} - {rows[0]['Title']}"


OFFLINE: List[Check] = [
    ("dossiers inscriptibles", _c_paths),
    ("decode WAV", _c_wav),
    ("decode FLAC", _c_flac),
    ("decode MP4 (AAC)", _c_mp4_aac),
    ("decode M4A (ALAC)", _c_m4a_alac),
    ("decode WMA", _c_wma),
    ("scan d'un dossier .mp4", _c_scan_mp4_folder),
    ("empreinte fpcalc", _c_fpcalc),
    ("binaire sldl", _c_sldl),
    ("modele de genre ONNX", _c_genre_model),
    ("GUI importable", _c_gui_build),
]
NETWORK: List[Check] = [
    ("scrape SoundCloud likes", _c_soundcloud),
    ("scrape YouTube chaine", _c_youtube_channel),
    ("YouTube likes -> message prive", _c_youtube_private),
    ("scrape Bandcamp wishlist", _c_bandcamp),
    ("scrape Discogs wantlist", _c_discogs),
]


def run(offline: bool = False, report: Optional[Path] = None) -> int:
    checks = OFFLINE + ([] if offline else NETWORK)
    lines = [f"DDD selftest - version {__version__} - "
             f"{'EXE GELE' if paths.is_frozen() else 'source'} - {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"python {sys.version.split()[0]} - {sys.executable}", ""]
    n_fail = 0
    with tempfile.TemporaryDirectory(prefix="ddd-selftest-") as td:
        for name, fn in checks:
            t0 = time.time()
            try:
                detail = fn(Path(td))
                status = "SKIP" if detail.startswith("SKIP") else "OK  "
            except Exception as e:  # noqa: BLE001 - on veut TOUT voir, pas s'arreter
                n_fail += 1
                status = "FAIL"
                detail = f"{type(e).__name__}: {e}"
                tb = traceback.format_exc().strip().splitlines()
                detail += "\n        " + "\n        ".join(tb[-6:])
            lines.append(f"[{status}] {name:<32} {time.time() - t0:5.1f}s  {detail}")
    lines += ["", f"{len(checks) - n_fail}/{len(checks)} OK" + ("" if not n_fail else f" - {n_fail} ECHEC(S)")]
    text = "\n".join(lines) + "\n"
    report = report or (paths.logs_dir() / "selftest.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    try:
        print(text)
        print(f"rapport: {report}")
    except Exception:  # noqa: BLE001 - .exe sans console : stdout peut etre absent
        pass
    return 1 if n_fail else 0


def main(argv: List[str]) -> int:
    report = None
    if "--report" in argv:
        i = argv.index("--report")
        if i + 1 < len(argv):
            report = Path(argv[i + 1])
    return run(offline="--offline" in argv, report=report)
