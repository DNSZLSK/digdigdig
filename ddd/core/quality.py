"""Detecteur de qualite lossless universel.

Classe n'importe quel fichier audio en vrai lossless vs lossy/upscale via
l'analyse du cutoff spectral. On reutilise la math eprouvee de flac-detective
(detect_cutoff, calculate_high_frequency_energy, estimate_mp3_bitrate) mais avec
un lecteur leger par fenetres (3 x 30 s) qui marche pareil sur WAV/FLAC/AIFF/MP3,
sans la copie-vers-temp ni le cache oriente-FLAC de flac-detective.

Principe : un vrai lossless garde de l'energie jusqu'a ~Nyquist (~20-22 kHz en
44.1 kHz) ; un MP3 reencode en lossless a un mur (cutoff) plus bas
(~16 kHz pour du 128, ~20 kHz pour du 320).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
from scipy.fft import rfft, rfftfreq

from flac_detective.analysis.spectrum import (
    calculate_high_frequency_energy,
    detect_cutoff,
)
from flac_detective.analysis.new_scoring.bitrate import estimate_mp3_bitrate

logger = logging.getLogger(__name__)

# Conteneurs qui pretendent etre lossless (a verifier au spectre)
LOSSLESS_EXTS = {".flac", ".wav", ".wave", ".aif", ".aiff", ".aifc"}
# Formats ouvertement lossy (candidats upgrade par definition)
LOSSY_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma"}

# Verdicts : 4 bandes orientees "jouable en club", classees par le cutoff spectral
# mesure. La math reste celle de flac-detective (detect_cutoff/estimate_mp3_bitrate) ;
# on ne change QUE la facon de nommer/grouper le resultat.
LOSSLESS = "LOSSLESS"            # plein spectre (estimate_mp3_bitrate == 0) - vrai lossless
HQ = "HQ"                        # cutoff >= 18 kHz - jouable club (inclut le MP3 320)
DOUTEUX = "DOUTEUX"              # 16-18 kHz - audible sur bon systeme, a revoir
MAUVAIS = "MAUVAIS"              # < 16 kHz - bouillie / MP3 bas debit
SKIPPED = "SKIPPED"             # pas un fichier audio qu'on analyse
ERROR = "ERROR"                 # echec de lecture/analyse

SAMPLE_WINDOW_S = 30.0
LONG_FILE_S = 90.0
# Un FLAC dont le bitrate conteneur est sous ce seuil vient quasi surement d'un MP3.
# (Ne s'applique pas au WAV : le WAV non compresse est toujours ~1411 kbps.)
FLAC_BITRATE_RED_FLAG = 160

# Frontieres de bande (Hz) sur le cutoff mesure
HQ_CUTOFF_HZ = 18000            # >= 18 kHz : jouable sur un gros systeme
DOUTEUX_CUTOFF_HZ = 16000       # 16-18 kHz : limite
AUDIOPHILE_CUTOFF_HZ = 20000    # seuil du preset "audiophile"
# Un fichier ouvertement lossy (MP3/AAC/...) sous ce bitrate conteneur est banni
# partout, quel que soit le preset (un MP3 < 320 ne rentre jamais dans la bibliotheque).
MIN_LOSSY_BITRATE = 310

# Presets de qualite : cutoff minimum (Hz) pour ACCEPTER un fichier.
# "puriste" = None -> on exige le plein spectre (verdict LOSSLESS, = l'ancien AUTHENTIC).
QUALITY_PRESETS = {"dj_club": HQ_CUTOFF_HZ, "audiophile": AUDIOPHILE_CUTOFF_HZ, "puriste": None}
DEFAULT_PRESET = "dj_club"


@dataclass
class QualityResult:
    path: str
    filename: str
    ext: str
    format_class: str          # lossless_container | lossy | unknown
    sample_rate: int
    channels: int
    duration_s: float
    cutoff_hz: float
    cutoff_std_hz: float
    hf_energy_ratio: float
    est_source_bitrate: int    # 0 = plein spectre (pas de signature MP3)
    container_bitrate: int     # taille*8/duree (kbps)
    verdict: str
    confidence: str
    reason: str

    def as_dict(self) -> Dict:
        return asdict(self)


def _format_class(ext: str) -> str:
    if ext in LOSSY_EXTS:
        return "lossy"
    if ext in LOSSLESS_EXTS:
        return "lossless_container"
    return "unknown"


def _window_cutoff(data: np.ndarray, sr: int) -> Tuple[Optional[float], float]:
    """FFT d'une fenetre mono -> (cutoff_hz, hf_energy_ratio)."""
    mono = data.mean(axis=1) if data.ndim > 1 else data
    if mono.size < 256:
        return None, 0.0
    windowed = mono * np.hanning(len(mono))
    spec = rfft(windowed)
    freq = rfftfreq(len(windowed), 1.0 / sr)
    mag = np.abs(spec)
    mag_db = 20.0 * np.log10(mag + 1e-10)
    cutoff = detect_cutoff(freq, mag_db, sr)
    hf = calculate_high_frequency_energy(freq, mag)
    return float(cutoff), float(hf)


def _spectral(path: Path, info: "sf._SoundFileInfo") -> Optional[Tuple[float, float, float]]:
    """Analyse 3 fenetres (debut/milieu/fin) -> (cutoff, cutoff_std, hf) de la fenetre la
    plus revelatrice (cf _aggregate_windows)."""
    sr = info.samplerate
    total = info.frames
    dur = float(info.duration)
    if dur <= 0:
        return None
    num = 3 if dur > LONG_FILE_S else 1
    win_s = min(SAMPLE_WINDOW_S, dur / num)
    win_frames = max(1, int(win_s * sr))

    cutoffs: List[float] = []
    hfs: List[float] = []
    for i in range(num):
        center = (dur / (num + 1)) * (i + 1)
        start = max(0, int((center - win_s / 2) * sr))
        if start + win_frames > total:
            start = max(0, total - win_frames)
        try:
            data, _ = sf.read(str(path), start=start, frames=win_frames,
                              dtype="float64", always_2d=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("lecture fenetre %d echouee pour %s: %r", i, path.name, e)
            continue
        if len(data) == 0:
            continue
        c, hf = _window_cutoff(data, sr)
        if c is not None:
            cutoffs.append(c)
            hfs.append(hf)

    if not cutoffs:
        return None
    return _aggregate_windows(cutoffs, hfs)


def _aggregate_windows(cutoffs: List[float], hfs: List[float]) -> Tuple[float, float, float]:
    """Agrege les fenetres en (cutoff, cutoff_std, hf) de la fenetre LA PLUS REVELATRICE.

    On prend le MAX du cutoff, pas le min : une seule fenetre plein spectre prouve que le
    fichier porte vraiment les aigus -> vrai lossless. Un transcode ne peut PAS fabriquer une
    fenetre a cutoff haut (les aigus sont jetes a l'encodage), donc le max ne laisse pas passer
    de faux ; le min, lui, rejetait a tort les morceaux dynamiques (un breakdown filtre ou une
    intro calme = fenetre pauvre en HF qui tirait tout le fichier sous la barre). Le hf suit la
    meme fenetre que le cutoff retenu (coherence pour un futur detecteur de resample)."""
    best = max(range(len(cutoffs)), key=cutoffs.__getitem__)
    cutoff_std = float(np.std(cutoffs)) if len(cutoffs) > 1 else 0.0
    return cutoffs[best], cutoff_std, hfs[best]


def _band(cutoff: float, est: int) -> Tuple[str, str, str]:
    """Bande de qualite a partir du cutoff mesure (verdict, confidence, reason).

    LOSSLESS = plein spectre (pas de signature MP3). Sinon on classe par la
    position du mur : >=18 kHz jouable (HQ), 16-18 limite (DOUTEUX), <16 bouillie.
    """
    if est == 0:
        return LOSSLESS, "high", f"spectre plein, cutoff {cutoff:.0f} Hz"
    if cutoff >= HQ_CUTOFF_HZ:
        return HQ, "medium", f"cutoff {cutoff:.0f} Hz (~{est} kbps) - jouable club"
    if cutoff >= DOUTEUX_CUTOFF_HZ:
        return DOUTEUX, "medium", f"cutoff {cutoff:.0f} Hz (~{est} kbps) - limite, a revoir"
    return MAUVAIS, "high", f"cutoff {cutoff:.0f} Hz (~{est} kbps) - source lossy bas debit"


def _classify_lossless(cutoff: float, container_bitrate: int, ext: str) -> Tuple[str, str, str]:
    est = estimate_mp3_bitrate(cutoff)
    if ext == ".flac" and 0 < container_bitrate < FLAC_BITRATE_RED_FLAG:
        return (MAUVAIS, "high",
                f"bitrate conteneur FLAC {container_bitrate} kbps < {FLAC_BITRATE_RED_FLAG} (source lossy)")
    return _band(cutoff, est)


def _error(p: Path, ext: str, fclass: str, msg: str) -> QualityResult:
    return QualityResult(str(p), p.name, ext, fclass, 0, 0, 0.0, 0.0, 0.0, 0.0, 0,
                         0, ERROR, "n/a", msg)


def analyze_file(path, detector=None) -> QualityResult:
    """Analyse un fichier -> QualityResult.

    `detector` : 'legacy' (defaut) = bandes par cutoff ; 'forensic' = couche durcie
    additive (cf detect.py). None -> lu en config ('detector'). Le defaut ne change RIEN."""
    p = Path(path)
    ext = p.suffix.lower()
    fclass = _format_class(ext)
    if fclass == "unknown":
        return QualityResult(str(p), p.name, ext, fclass, 0, 0, 0.0, 0.0, 0.0, 0.0, 0,
                             0, SKIPPED, "n/a", "format non audio / non gere")

    try:
        info = sf.info(str(p))
    except Exception as e:  # noqa: BLE001
        return _error(p, ext, fclass, f"info illisible: {e}")

    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    container_bitrate = int(size * 8 / info.duration / 1000) if info.duration else 0

    sp = _spectral(p, info)
    if sp is None:
        return _error(p, ext, fclass, "analyse spectrale impossible (lecture vide)")
    cutoff, std, hf = sp
    est = estimate_mp3_bitrate(cutoff)
    if detector is None:
        detector = _current_detector()

    if fclass == "lossy":
        # MP3/AAC/... : banni d'office sous 320 kbps (container_bitrate ~ debit reel),
        # sinon bande par le cutoff comme un conteneur lossless.
        if 0 < container_bitrate < MIN_LOSSY_BITRATE:
            verdict, conf = MAUVAIS, "high"
            reason = f"{ext[1:]} {container_bitrate} kbps < 320 - banni"
        else:
            verdict, conf, reason = _band(cutoff, est)
    elif detector == "forensic":
        from . import detect as _detect      # import tardif : evite le cycle quality <-> detect
        verdict, conf, reason, est, _sig = _detect.forensic_classify(
            cutoff, std, hf, container_bitrate, info.samplerate, ext, fclass)
    else:
        verdict, conf, reason = _classify_lossless(cutoff, container_bitrate, ext)

    return QualityResult(
        path=str(p),
        filename=p.name,
        ext=ext,
        format_class=fclass,
        sample_rate=info.samplerate,
        channels=info.channels,
        duration_s=round(float(info.duration), 1),
        cutoff_hz=round(cutoff, 1),
        cutoff_std_hz=round(std, 1),
        hf_energy_ratio=round(hf, 6),
        est_source_bitrate=est,
        container_bitrate=container_bitrate,
        verdict=verdict,
        confidence=conf,
        reason=reason,
    )


def preset_from_config(default: str = DEFAULT_PRESET) -> str:
    """Preset de qualite courant (config 'quality_preset'), valide, defaut dj_club."""
    try:
        from . import config as _config
        val = _config.get("quality_preset")
    except Exception:  # noqa: BLE001
        val = None
    return val if val in QUALITY_PRESETS else default


def _current_detector() -> str:
    """Moteur de detection courant (config 'detector'), valide, defaut 'legacy'."""
    try:
        from . import config as _config
        val = _config.get("detector")
    except Exception:  # noqa: BLE001
        val = None
    return val if val in ("legacy", "forensic") else "legacy"


def is_accepted(qr: "QualityResult", preset: str = DEFAULT_PRESET) -> bool:
    """Le fichier passe-t-il la porte du preset ? (a garder vs a upgrader)

    - puriste : seulement le plein spectre (verdict LOSSLESS).
    - dj_club / audiophile : cutoff >= seuil du preset.
    Dans tous les cas un MP3 (conteneur lossy) sous 320 kbps est refuse.
    """
    if qr.verdict in (SKIPPED, ERROR):
        return False
    if qr.ext in LOSSY_EXTS and 0 < qr.container_bitrate < MIN_LOSSY_BITRATE:
        return False
    if preset not in QUALITY_PRESETS:
        preset = DEFAULT_PRESET
    floor = QUALITY_PRESETS[preset]
    if floor is None:                      # puriste
        return qr.verdict == LOSSLESS
    return qr.verdict == LOSSLESS or qr.cutoff_hz >= floor


# Champs CSV stables (ordre)
CSV_FIELDS = [
    "verdict", "confidence", "format_class", "ext", "filename",
    "cutoff_hz", "est_source_bitrate", "container_bitrate", "sample_rate",
    "channels", "duration_s", "cutoff_std_hz", "hf_energy_ratio", "reason", "path",
]
