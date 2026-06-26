"""Detecteur de qualite : agregation des fenetres spectrales (anti faux positif).

Le verdict prenait le MIN du cutoff sur 3 fenetres -> une intro calme / un breakdown
filtre (pauvre en aigus) tirait un vrai FLAC lossless sous la barre. On agrege desormais
par le MAX (la fenetre la plus revelatrice). Ces tests verrouillent ce choix.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality


def test_aggregate_takes_max_cutoff_not_min():
    # 3 fenetres : intro calme (6 kHz), drop plein spectre (22 kHz), breakdown (8 kHz).
    # min() rejetait le fichier (6 kHz) ; max() le reconnait lossless (22 kHz).
    cutoffs = [6000.0, 22000.0, 8000.0]
    hfs = [0.01, 0.40, 0.02]
    cutoff, std, hf = quality._aggregate_windows(cutoffs, hfs)
    assert cutoff == 22000.0, "la fenetre la plus revelatrice (plein spectre) doit gagner"
    assert hf == 0.40, "le hf doit suivre la fenetre retenue (le drop), pas une autre"
    assert std > 0


def test_aggregate_max_does_not_rescue_a_fake():
    # un faux (transcode) a un mur DUR : aucune fenetre ne depasse ~16 kHz, donc meme le max
    # reste au mur -> toujours flague. Le passage au max ne "sauve" pas un faux lossless.
    cutoffs = [16000.0, 15800.0, 16000.0]
    cutoff, _, _ = quality._aggregate_windows(cutoffs, [0.01, 0.01, 0.01])
    assert cutoff == 16000.0


def test_aggregate_single_window():
    cutoff, std, hf = quality._aggregate_windows([16000.0], [0.05])
    assert cutoff == 16000.0 and std == 0.0 and hf == 0.05
