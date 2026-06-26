"""Detecteur forensique Tier 0 : durcissement anti-faux-positif, JAMAIS pire que le legacy."""

import sys
from pathlib import Path

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
