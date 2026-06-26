"""Cablage du detecteur 'forensic' dans analyze_file + mode shadow (diff_folder).

Audio synthetique (bruit blanc, mur FFT net) -> on verifie via le VRAI chemin analyze_file
que le forensic route bien et n'est jamais pire que le legacy, et que le shadow ne modifie
rien."""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import detect, quality

_RANK = {quality.MAUVAIS: 0, quality.DOUTEUX: 1, quality.HQ: 2, quality.LOSSLESS: 3}


def _write_noise(path, sr=44100, dur=4.0, cutoff=None, seed=0):
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    x = rng.standard_normal((n, 2)) * 0.1
    if cutoff is not None:                        # mur FFT net (cutoff deterministe)
        X = np.fft.rfft(x, axis=0)
        freq = np.fft.rfftfreq(n, 1.0 / sr)
        X[freq > cutoff, :] = 0.0
        x = np.fft.irfft(X, n=n, axis=0)
    sf.write(str(path), x, sr, subtype="PCM_16")


def test_routing_never_worse_through_analyze_file(tmp_path):
    pf = tmp_path / "full.wav"
    _write_noise(pf)
    pl = tmp_path / "low.wav"
    _write_noise(pl, cutoff=16000)
    for p in (pf, pl):
        leg = quality.analyze_file(p, detector="legacy")
        fo = quality.analyze_file(p, detector="forensic")
        assert _RANK[fo.verdict] >= _RANK[leg.verdict], f"{p.name}: {leg.verdict} -> {fo.verdict}"


def test_default_detector_is_legacy(tmp_path):
    # sans config, le defaut doit etre legacy (zero changement de comportement).
    assert quality._current_detector() == "legacy"
    p = tmp_path / "full.wav"
    _write_noise(p)
    assert quality.analyze_file(p).verdict == quality.analyze_file(p, detector="legacy").verdict


def test_shadow_diff_folder_changes_nothing(tmp_path):
    _write_noise(tmp_path / "full.wav")
    _write_noise(tmp_path / "low.wav", cutoff=16000)
    n, diffs = detect.diff_folder(tmp_path)
    assert n == 2
    assert (tmp_path / "full.wav").exists() and (tmp_path / "low.wav").exists()
    for _path, lv, fv, _conf, _reason in diffs:
        assert lv in _RANK and fv in _RANK
        assert _RANK[fv] >= _RANK[lv]            # un diff n'est jamais une retrogradation
