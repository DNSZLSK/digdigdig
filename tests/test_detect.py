"""Detecteur forensique Tier 0 : durcissement anti-faux-positif, JAMAIS pire que le legacy."""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import detect, quality

# Rang d'acceptation croissant : sert a verifier qu'on ne retrograde jamais.
_RANK = {quality.MAUVAIS: 0, quality.DOUTEUX: 1, quality.HQ: 2, quality.LOSSLESS: 3}


def _fc(cutoff, std=0.0, hf=0.0, container=800, sr=44100, ext=".flac",
        fclass="lossless_container"):
    return detect.forensic_classify(cutoff, std, hf, container, sr, ext, fclass)


def test_bit_depth_mapping():
    assert detect.subtype_to_bit_depth("PCM_16") == 16
    assert detect.subtype_to_bit_depth("PCM_24") == 24
    assert detect.subtype_to_bit_depth("FLOAT") == 32
    assert detect.subtype_to_bit_depth("MPEG_LAYER_III") == 0
    assert detect.subtype_to_bit_depth("") == 0


def test_promotes_genuine_near_nyquist_lossless():
    # cutoff 21.2 kHz (>= 95% Nyquist) + bitrate eleve : le legacy dit HQ (est=320 car
    # 21.2k < 21.5k), le forensique le reconnait LOSSLESS. Un vrai 320 plafonne ~20.5k.
    v, conf, _r, _est, _sig = _fc(21200, std=50, hf=0.00002, container=800)
    assert v == quality.LOSSLESS and conf == "high"


def test_real_320_transcode_stays_hq_not_promoted():
    # vrai 320 -> FLAC : cutoff ~20.4k (< 95% Nyquist), container dans la plage 320, variance 0.
    v, _conf, _r, _est, _sig = _fc(20400, std=0.0, hf=0.0, container=750)
    assert v == quality.HQ and v != quality.LOSSLESS


def test_low_bitrate_fake_flac_stays_demoted():
    # 128 kbps deguise en FLAC : cutoff 15k + container dans la plage 128 -> reste flague.
    v, _conf, _r, _est, _sig = _fc(15000, std=0.0, hf=0.0, container=480)
    assert _RANK[v] <= _RANK[quality.DOUTEUX]


def test_full_spectrum_stays_lossless():
    v, _conf, _r, _est, _sig = _fc(22050, std=100, hf=0.1, container=900)
    assert v == quality.LOSSLESS


def test_gray_zone_marks_confidence_not_verdict():
    # HQ ou Rule 1 ne confirme pas de MP3 (container hors plage) -> uncertain, verdict inchange.
    v, conf, _r, _est, _sig = _fc(19000, std=0.0, hf=0.0, container=1000)
    assert v == quality.HQ and conf == "uncertain"


def test_never_worse_than_legacy():
    # INVARIANT Tier 0 : le forensique n'est JAMAIS sous le verdict legacy, sur une grille.
    for c in [13000, 15000, 16500, 18500, 19500, 20000, 20400, 21000, 21200, 22050]:
        for br in [150, 480, 700, 800, 1000]:
            for st in [0.0, 50.0, 200.0]:
                lv, _lc, _lr = quality._classify_lossless(c, br, ".flac")
                fv, _fc2, _fr, _fe, _fs = _fc(c, std=st, container=br)
                assert _RANK[fv] >= _RANK[lv], f"regression cutoff={c} br={br} std={st}: {lv}->{fv}"


# --- Tier 1 : artefacts codec ------------------------------------------------------------

def test_artifact_signals_clean_on_white_noise():
    # GARDE anti-faux-positif : du bruit blanc plein-spectre ne doit declencher aucun signal.
    rng = np.random.default_rng(0)
    mono = rng.standard_normal(44100 * 3)
    sig = detect.artifact_signals(mono, 44100)
    assert sig["aliasing"] < detect.ALIASING_STRONG
    assert not sig["mp3_comb"]
    assert sig["preecho"] <= detect.PREECHO_STRONG_PCT


def test_forensic_flags_suspect_on_strong_artifacts():
    # >=2 signaux forts -> SUSPECT (pas une condamnation), le verdict du cutoff est GARDE.
    arts = {"aliasing": 0.7, "mp3_comb": True, "preecho": 0.0}
    v, conf, _r, _e, _s = detect.forensic_classify(
        22050, 100, 0.1, 900, 44100, ".flac", "lossless_container", artifacts=arts)
    assert v == quality.LOSSLESS and conf == "suspect"


def test_forensic_single_artifact_not_suspect():
    # un seul signal ne flagge pas : plein-spectre reste LOSSLESS, confiance non suspecte.
    arts = {"aliasing": 0.7, "mp3_comb": False, "preecho": 0.0}
    v, conf, _r, _e, _s = detect.forensic_classify(
        22050, 100, 0.1, 900, 44100, ".flac", "lossless_container", artifacts=arts)
    assert v == quality.LOSSLESS and conf != "suspect"


def test_forensic_requires_codec_anchor():
    # pre-echo + collapse stereo SANS aliasing/comb (le faux positif electro) -> PAS suspect :
    # un signal codec fiable est requis comme ancre.
    arts = {"aliasing": 0.1, "mp3_comb": False, "preecho": 80.0, "stereo": 0.95}
    v, conf, _r, _e, _s = detect.forensic_classify(
        22050, 100, 0.1, 900, 44100, ".flac", "lossless_container", artifacts=arts)
    assert v == quality.LOSSLESS and conf != "suspect"


def test_is_accepted_suspect_flagged_on_all_presets():
    # plein spectre mais SUSPECT (artefacts ancres) : le spectre prime sur le cutoff -> ce n'est
    # PAS "deja bon", meme en dj_club ; il reste candidat a l'upgrade (le re-audit fait garde-fou).
    qr = quality.QualityResult(
        path="x", filename="x.flac", ext=".flac", format_class="lossless_container",
        sample_rate=44100, channels=2, duration_s=200.0, cutoff_hz=22050.0,
        cutoff_std_hz=100.0, hf_energy_ratio=0.1, est_source_bitrate=0,
        container_bitrate=900, verdict=quality.LOSSLESS, confidence="suspect", reason="test")
    assert not quality.is_accepted(qr, "dj_club")
    assert not quality.is_accepted(qr, "puriste")
    # 'uncertain' (zone grise) reste clement -> accepte en dj_club (pas de churn faux-positif electro).
    qr_unc = quality.QualityResult(
        path="y", filename="y.flac", ext=".flac", format_class="lossless_container",
        sample_rate=44100, channels=2, duration_s=200.0, cutoff_hz=22050.0,
        cutoff_std_hz=100.0, hf_energy_ratio=0.1, est_source_bitrate=0,
        container_bitrate=900, verdict=quality.LOSSLESS, confidence="uncertain", reason="test")
    assert quality.is_accepted(qr_unc, "dj_club")


def test_stereo_collapse_genuine_vs_joint():
    rng = np.random.default_rng(2)
    sr, n = 44100, 44100 * 3
    # vrai stereo : L/R independants partout -> Side ~ Mid -> pas de collapse.
    genuine = np.stack([rng.standard_normal(n), rng.standard_normal(n)], axis=1)
    assert detect.stereo_collapse(genuine, sr) < detect.STEREO_STRONG
    # joint-stereo : independants en bas, mais HF de R == HF de L (mono au-dessus de 8 kHz)
    # -> Side HF effondree -> collapse net.
    L = rng.standard_normal(n)
    XR = np.fft.rfft(rng.standard_normal(n))
    XL = np.fft.rfft(L)
    hf = np.fft.rfftfreq(n, 1.0 / sr) >= 8000
    XR[hf] = XL[hf]
    joint = np.stack([L, np.fft.irfft(XR, n=n)], axis=1)
    assert detect.stereo_collapse(joint, sr) > detect.STEREO_STRONG


def test_stereo_collapse_mono_abstains():
    # un fichier mono (ou quasi) ne doit PAS declencher le signal (Side faible partout).
    rng = np.random.default_rng(3)
    mono_sig = rng.standard_normal(44100 * 3)
    stereo_mono = np.stack([mono_sig, mono_sig], axis=1)   # L == R -> Side = 0 partout
    assert detect.stereo_collapse(stereo_mono, 44100) == 0.0
