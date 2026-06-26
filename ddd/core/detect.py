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

import numpy as np

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


# --- Tier 1/2 : seuils d'artefacts codec -------------------------------------------------
ALIASING_STRONG = 0.5        # detect_hf_aliasing : correlation > 0.5 = signal fort
PREECHO_STRONG_PCT = 10.0    # detect_preecho_artifacts : > 10% de transitoires affectees
STEREO_STRONG = 0.7          # stereo_collapse : > 0.7 = largeur stereo HF effondree (joint-stereo)
ARTIFACT_CONSENSUS = 2       # nb de signaux forts requis pour FLAGGER suspect (un seul ne suffit pas)


def forensic_classify(
    cutoff: float,
    cutoff_std: float,
    hf_energy_ratio: float,
    container_bitrate: int,
    sample_rate: int,
    ext: str,
    fclass: str,
    artifacts=None,
) -> Tuple[str, str, str, int, List[str]]:
    """Verdict forensique -> (verdict, confidence, reason, est, signals).

    Ne durcit que les conteneurs lossless (le probleme fake-FLAC) ; pour le reste, renvoie
    le verdict legacy inchange.

    Tier 0 (sans `artifacts`) : promote-only, ne descend JAMAIS sous le verdict legacy.
    Tier 1 (avec `artifacts`) : une empreinte codec FORTE (>= ARTIFACT_CONSENSUS signaux parmi
    HF-aliasing / comb MP3 / pre-echo) marque le fichier SUSPECT (confidence='suspect') meme a
    cutoff plein -- c'est le cas 320/resample, la zone grise NON separable de facon certaine :
    on garde le verdict du cutoff (honnete) et on FLAGGE pour revue (refuse en puriste,
    cf is_accepted), on ne condamne pas. Un seul signal ne suffit pas (anti-faux-positif).
    """
    est = estimate_mp3_bitrate(cutoff)

    # Baseline legacy = le plancher (Tier 0 ne descend jamais en-dessous).
    if fclass != "lossless_container":
        v, c, r = q._band(cutoff, est)
        return v, c, r, est, []
    base_v, base_c, base_r = q._classify_lossless(cutoff, container_bitrate, ext)

    # Regles durcies (fonctions PURES de flac_detective).
    (_s1, r1), est1 = apply_rule_1_mp3_bitrate(
        cutoff, container_bitrate, cutoff_std, sample_rate, hf_energy_ratio)
    _s8, r8 = apply_rule_8_nyquist_exception(cutoff, sample_rate, est1, None)
    signals = list(r1) + list(r8)

    # Tier 1 : artefacts codec. Une empreinte forte (>= consensus) sur un fichier qui passerait
    # sinon -> SUSPECT, PAS une condamnation. Plein spectre + artefacts = zone grise 320/resample
    # (non separable de facon certaine) : on garde le verdict du cutoff (honnete) et la confiance
    # porte le doute -> flagge pour revue (refuse en puriste), pas "faux".
    if artifacts:
        aliasing_hit = artifacts.get("aliasing", 0.0) > ALIASING_STRONG
        comb_hit = bool(artifacts.get("mp3_comb"))
        preecho_hit = artifacts.get("preecho", 0.0) > PREECHO_STRONG_PCT
        stereo_hit = artifacts.get("stereo", 0.0) > STEREO_STRONG
        # Un signal codec FIABLE (aliasing/comb) est REQUIS comme ancre : pre-echo et stereo se
        # declenchent a tort sur l'electro (transitoires denses, aigus retrecis) -> seuls ils ne
        # condamnent jamais, ils ne font que corroborer (valide en shadow sur biblio reelle).
        anchor = aliasing_hit or comb_hit
        total = sum((aliasing_hit, comb_hit, preecho_hit, stereo_hit))
        if anchor and total >= ARTIFACT_CONSENSUS and base_v in (q.LOSSLESS, q.HQ, q.DOUTEUX):
            strong = []
            if aliasing_hit:
                strong.append(f"HF aliasing {artifacts['aliasing']:.2f}")
            if comb_hit:
                strong.append("comb de bruit MP3")
            if preecho_hit:
                strong.append(f"pre-echo {artifacts['preecho']:.0f}%")
            if stereo_hit:
                strong.append(f"collapse stereo {artifacts['stereo']:.2f}")
            reason = (f"artefacts codec ({', '.join(strong)}) malgre cutoff {cutoff:.0f} Hz "
                      f"-> source lossy possible, a verifier")
            return base_v, "suspect", reason, est, signals + strong

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


def stereo_collapse(window, sample_rate) -> float:
    """Collapse joint-stereo : largeur stereo normale en medium mais EFFONDREE en HF.

    L'intensity stereo (MP3/AAC bas/moyen debit) code les aigus en quasi-mono -> l'energie
    Side (L-R) s'effondre en HF alors qu'elle est presente plus bas. Renvoie 0..1 (1 =
    collapse net). On s'ABSTIENT (0) sur un mix quasi-mono (Side faible partout = pas un
    signal de transcode), ce qui evite le faux positif classique de cette mesure."""
    w = np.asarray(window)
    if w.ndim < 2 or w.shape[1] < 2:
        return 0.0
    n = w.shape[0]
    if n < 4096:
        return 0.0
    L = w[:, 0].astype(np.float64)
    R = w[:, 1].astype(np.float64)
    win = np.hanning(n)
    freq = np.fft.rfftfreq(n, 1.0 / sample_rate)
    p_mid = np.abs(np.fft.rfft((L + R) * 0.5 * win)) ** 2
    p_side = np.abs(np.fft.rfft((L - R) * 0.5 * win)) ** 2

    def side_over_mid(lo, hi):
        m = (freq >= lo) & (freq < hi)
        em = float(p_mid[m].sum())
        return float(p_side[m].sum()) / (em + 1e-12)

    mid_ratio = side_over_mid(1000.0, 5000.0)
    if mid_ratio < 0.05:               # mix quasi-mono -> abstention (signal non fiable)
        return 0.0
    hf_ratio = side_over_mid(10000.0, 16000.0)
    return max(0.0, 1.0 - hf_ratio / mid_ratio)


def artifact_signals(window, sample_rate) -> dict:
    """Signaux d'artefacts codec (fonctions PURES) sur la fenetre deja en memoire :
    {aliasing: 0..1, mp3_comb: bool, preecho: %, stereo: 0..1}. Accepte mono OU stereo
    (les 3 premiers detecteurs downmixent ; stereo_collapse a besoin du L/R). Tout echoue
    en douceur.

    Import tardif : artifacts.py tire transitivement audio_loader (subprocess), mais on
    n'appelle QUE les detecteurs purs (np.ndarray) -> aucun subprocess `flac` ne tourne."""
    out = {"aliasing": 0.0, "mp3_comb": False, "preecho": 0.0, "stereo": 0.0}
    if window is None:
        return out
    from flac_detective.analysis.new_scoring.artifacts import (
        detect_hf_aliasing, detect_mp3_noise_pattern, detect_preecho_artifacts)
    # 1) ANCRES codec fiables (specifiques au codec, peu de faux positifs).
    try:
        out["aliasing"] = float(detect_hf_aliasing(window, sample_rate))
    except Exception:  # noqa: BLE001
        pass
    try:
        out["mp3_comb"] = bool(detect_mp3_noise_pattern(window, sample_rate))
    except Exception:  # noqa: BLE001
        pass
    # 2) CORROBORATEURS (pre-echo LENT + stereo genre-prone) : SEULEMENT si une ancre a firme.
    #    -> perf (on saute le couteux sur la plupart des fichiers) ET pas de faux positif
    #    pre-echo+stereo seuls.
    if out["aliasing"] > ALIASING_STRONG or out["mp3_comb"]:
        try:
            out["preecho"] = float(detect_preecho_artifacts(window, sample_rate)[0])
        except Exception:  # noqa: BLE001
            pass
        try:
            out["stereo"] = float(stereo_collapse(window, sample_rate))
        except Exception:  # noqa: BLE001
            pass
    return out


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
