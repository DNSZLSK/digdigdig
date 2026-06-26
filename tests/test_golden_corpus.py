"""Corpus golden de NON-REGRESSION : des cas synthetiques DETERMINISTES dont le verdict
ne doit jamais regresser, quel que soit le tier du detecteur.

C'est le FILET DE SECURITE avant le Tier 1 (qui introduit des demotions) : si une regle
d'artefact se met a demoter A TORT un vrai plein-spectre, un de ces asserts casse au lieu
de passer en silence. Audio synthetique (numpy + soundfile), pas d'audio copyrighte,
reproductible (seed fixe). Les deux moteurs (legacy + forensic) sont verifies.
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality

_RANK = {quality.MAUVAIS: 0, quality.DOUTEUX: 1, quality.HQ: 2, quality.LOSSLESS: 3}
_DETECTORS = ("legacy", "forensic")


def _noise(path, sr=44100, dur=4.0, cutoff=None, seed=1):
    """Bruit blanc stereo ; si cutoff, mur FFT net (cutoff deterministe)."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    x = rng.standard_normal((n, 2)) * 0.1
    if cutoff is not None:
        X = np.fft.rfft(x, axis=0)
        freq = np.fft.rfftfreq(n, 1.0 / sr)
        X[freq > cutoff, :] = 0.0
        x = np.fft.irfft(X, n=n, axis=0)
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path


def test_genuine_fullspectrum_never_demoted(tmp_path):
    # ANCRE anti-faux-positif : un vrai plein-spectre (44.1k ET 48k) ne doit jamais tomber
    # en MAUVAIS/DOUTEUX ni etre refuse en dj_club. C'est ce que le Tier 1 ne doit pas casser.
    for sr in (44100, 48000):
        p = _noise(tmp_path / f"full_{sr}.wav", sr=sr)
        for det in _DETECTORS:
            qr = quality.analyze_file(p, detector=det)
            assert qr.verdict not in (quality.MAUVAIS, quality.DOUTEUX), \
                f"sr={sr} {det}: plein spectre demote a tort -> {qr.verdict}"
            assert quality.is_accepted(qr, "dj_club"), f"sr={sr} {det}: plein spectre refuse a tort"


def test_transcode_128_stays_flagged(tmp_path):
    # ANCRE de detection : un 128 (mur ~15 kHz) doit rester refuse en dj_club, tous tiers.
    p = _noise(tmp_path / "t128.wav", cutoff=15000)
    for det in _DETECTORS:
        qr = quality.analyze_file(p, detector=det)
        assert not quality.is_accepted(qr, "dj_club"), f"{det}: transcode 128 accepte a tort"


def test_transcode_320_is_hq_not_lossless(tmp_path):
    # Le modele club : un 320 (mur ~20 kHz) est HQ (jouable), accepte dj_club mais PAS LOSSLESS
    # (donc refuse en puriste). Ne doit pas non plus etre promu LOSSLESS par erreur.
    p = _noise(tmp_path / "t320.wav", cutoff=20000)
    for det in _DETECTORS:
        qr = quality.analyze_file(p, detector=det)
        assert qr.verdict == quality.HQ, f"{det}: 320 -> {qr.verdict} (attendu HQ)"
        assert quality.is_accepted(qr, "dj_club")
        assert not quality.is_accepted(qr, "puriste")


def test_forensic_promotes_near_nyquist(tmp_path):
    # Le gain Tier 0 : un vrai lossless a ~21.2 kHz (>=95% Nyquist) est LOSSLESS en forensic,
    # alors que legacy le garde en HQ. Doit rester vrai a travers les tiers suivants.
    p = _noise(tmp_path / "near.wav", cutoff=21200)
    assert quality.analyze_file(p, "legacy").verdict == quality.HQ
    assert quality.analyze_file(p, "forensic").verdict == quality.LOSSLESS


def test_forensic_never_below_legacy_on_corpus(tmp_path):
    # Invariant global du corpus : le forensic n'est jamais sous le legacy.
    specs = [{}, {"sr": 48000}, {"cutoff": 21200}, {"cutoff": 20000},
             {"cutoff": 18000}, {"cutoff": 15000}, {"cutoff": 13000}]
    for i, kw in enumerate(specs):
        p = _noise(tmp_path / f"c{i}.wav", **kw)
        leg = quality.analyze_file(p, "legacy")
        fo = quality.analyze_file(p, "forensic")
        assert _RANK[fo.verdict] >= _RANK[leg.verdict], f"{kw}: {leg.verdict} -> {fo.verdict}"
