"""Detecteur forensique multi-signaux (en construction, opt-in - Tier 0).

Couche ADDITIVE au-dessus du detecteur cutoff de `quality.py`. Tier 0 cable les regles
DURCIES que `flac_detective` embarque deja (apply_rule_1/8) sur les scalaires que
`quality.py` calcule deja (cutoff, cutoff_std, hf_energy_ratio, container_bitrate,
sample_rate). Objectif Tier 0 : REDUIRE les faux positifs (un vrai lossless quasi plein
spectre n'est plus retrograde a cause d'un faux match de signature MP3) SANS jamais
retrograder un fichier sous son verdict legacy.

GARANTIE (testee, cf tests/test_detect.py) : `forensic_classify` ne rend jamais un verdict
plus bas que le legacy. Au mieux il PROMEUT (vers LOSSLESS) un fichier quasi plein spectre
mal flague, ou marque la zone grise via `confidence="uncertain"` (sans toucher verdict ni
cutoff -> `is_accepted` inchange).

PIEGE EXE : n'utiliser QUE des fonctions PURES de flac_detective. Les fonctions a base de
chemin (analyze_silence_ratio, apply_rule_10/11 path-based, cassette...) shell-out vers le
CLI `flac` qui n'est PAS embarque dans le .exe -> ne jamais les appeler ici.
"""

from __future__ import annotations

from typing import List, Tuple

from flac_detective.analysis.new_scoring.bitrate import estimate_mp3_bitrate
from flac_detective.analysis.new_scoring.rules.spectral import (
    apply_rule_1_mp3_bitrate,
    apply_rule_8_nyquist_exception,
)

from . import quality as q

# soundfile subtype -> profondeur de bits (detection de faux 24-bit, branche au cablage).
_SUBTYPE_BITS = {
    "PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32,
    "FLOAT": 32, "DOUBLE": 64, "ALAC_16": 16, "ALAC_24": 24, "ALAC_32": 32,
}


def subtype_to_bit_depth(subtype: str) -> int:
    """Profondeur de bits depuis le subtype soundfile (0 = inconnu / lossy)."""
    return _SUBTYPE_BITS.get((subtype or "").upper(), 0)


def forensic_classify(
    cutoff: float,
    cutoff_std: float,
    hf_energy_ratio: float,
    container_bitrate: int,
    sample_rate: int,
    ext: str,
    fclass: str,
) -> Tuple[str, str, str, int, List[str]]:
    """Verdict forensique Tier 0 -> (verdict, confidence, reason, est, signals).

    Ne durcit que les conteneurs lossless (le probleme fake-FLAC) ; pour le reste, renvoie
    le verdict legacy inchange. Ne descend JAMAIS sous le verdict legacy.
    """
    est = estimate_mp3_bitrate(cutoff)

    # Baseline legacy = le plancher : on ne descend jamais en-dessous en Tier 0.
    if fclass != "lossless_container":
        v, c, r = q._band(cutoff, est)
        return v, c, r, est, []
    base_v, base_c, base_r = q._classify_lossless(cutoff, container_bitrate, ext)

    # Regles durcies (fonctions PURES de flac_detective).
    (_s1, r1), est1 = apply_rule_1_mp3_bitrate(
        cutoff, container_bitrate, cutoff_std, sample_rate, hf_energy_ratio)
    _s8, r8 = apply_rule_8_nyquist_exception(cutoff, sample_rate, est1, None)
    signals = list(r1) + list(r8)

    nyquist = sample_rate / 2.0
    near_nyquist = cutoff >= 0.95 * nyquist          # zone bonus Rule 8 = anti-aliasing authentique
    flac_red_flag = ext == ".flac" and 0 < container_bitrate < q.FLAC_BITRATE_RED_FLAG

    # PROMOTION vers LOSSLESS : preuve POSITIVE d'authenticite (cutoff >= 95% Nyquist) +
    # aucune signature MP3 confirmee (est1 None) + pas de red flag conteneur. Un vrai 320
    # plafonne ~20.5 kHz (< 95% Nyquist), donc il ne remonte PAS ici -> promotion sure.
    if base_v != q.LOSSLESS and est1 is None and near_nyquist and not flac_red_flag:
        reason = f"cutoff {cutoff:.0f} Hz >= 95% Nyquist, aucune signature MP3 (Rule 1/8) -> authentique"
        return q.LOSSLESS, "high", reason, est, signals

    # ZONE GRISE : le legacy retrograde (HQ/DOUTEUX) mais Rule 1 ne confirme PAS de MP3.
    # On ne change NI le verdict NI le cutoff (is_accepted inchange) : juste la confiance.
    if base_v in (q.HQ, q.DOUTEUX) and est1 is None:
        return base_v, "uncertain", base_r + " (pas de signature MP3 confirmee -> incertain)", est, signals

    # Sinon : legacy inchange (MP3 confirme, ou deja LOSSLESS, ou MAUVAIS red flag).
    return base_v, base_c, base_r, est, signals


def diff_folder(folder, exclude_names=(), limit: int = 0, progress=None):
    """Mode SHADOW : compare legacy vs forensic sur un dossier, NE MODIFIE RIEN.

    Re-analyse chaque fichier avec les DEUX moteurs (2x le cout spectral : c'est un
    diagnostic, pas le chemin chaud) et renvoie (n_total, diffs), ou diffs est la liste
    (path, verdict_legacy, verdict_forensic, confidence_forensic, reason_forensic) des
    SEULS fichiers dont le verdict OU la confiance changerait sous 'forensic'."""
    from .scan import iter_audio_files
    diffs = []
    n = 0
    for p in iter_audio_files(folder, exclude_names):
        if limit and n >= limit:
            break
        n += 1
        leg = q.analyze_file(p, detector="legacy")
        fo = q.analyze_file(p, detector="forensic")
        if progress:
            progress(n, p)
        if leg.verdict != fo.verdict or leg.confidence != fo.confidence:
            diffs.append((str(p), leg.verdict, fo.verdict, fo.confidence, fo.reason))
    return n, diffs
