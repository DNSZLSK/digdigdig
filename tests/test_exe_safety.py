"""Garde-fou .exe : le chemin forensic (artefacts inclus) ne doit spawner AUCUN subprocess.

Le CLI `flac` n'est PAS embarque dans le .exe (policy no-ffmpeg). Certaines fonctions de
flac_detective shell-out vers lui ; on ne doit appeler QUE les fonctions PURES. Ce test
monkeypatche subprocess pour exploser au moindre appel, puis analyse un FLAC en forensic.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality


def test_forensic_path_spawns_no_subprocess(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("subprocess interdit dans le chemin forensic (pas de `flac` dans l'exe)")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _boom, raising=False)

    # FLAC plein-spectre ecrit via libsndfile (pas de CLI flac) -> passe par le chemin artefacts.
    rng = np.random.default_rng(0)
    x = rng.standard_normal((44100 * 3, 2)) * 0.1
    p = tmp_path / "x.flac"
    sf.write(str(p), x, 44100, subtype="PCM_16")

    qr = quality.analyze_file(p, detector="forensic")
    assert qr.verdict in (quality.LOSSLESS, quality.HQ, quality.DOUTEUX, quality.MAUVAIS)
    assert qr.confidence != "fake"   # le modele 'fake' a ete remplace par 'suspect'
