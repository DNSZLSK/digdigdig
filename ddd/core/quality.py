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

# Verdicts
AUTHENTIC = "AUTHENTIC"          # vrai lossless plein spectre
SUSPICIOUS = "SUSPICIOUS"        # cutoff ~320 kbps : upscale probable ou rolloff naturel -> revue
FAKE = "FAKE_LOSSLESS"           # conteneur lossless mais source clairement lossy
LOSSY = "LOSSY"                  # format lossy assume -> candidat upgrade
SKIPPED = "SKIPPED"              # pas un fichier audio qu'on analyse
ERROR = "ERROR"                  # echec de lecture/analyse

SAMPLE_WINDOW_S = 30.0
LONG_FILE_S = 90.0
# Un FLAC dont le bitrate conteneur est sous ce seuil vient quasi surement d'un MP3.
# (Ne s'applique pas au WAV : le WAV non compresse est toujours ~1411 kbps.)
FLAC_BITRATE_RED_FLAG = 160


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
    """Analyse 3 fenetres (debut/milieu/fin) -> (cutoff_min, cutoff_std, hf_min)."""
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
    cutoff_min = min(cutoffs)
    cutoff_std = float(np.std(cutoffs)) if len(cutoffs) > 1 else 0.0
    return cutoff_min, cutoff_std, min(hfs)


def _classify_lossless(cutoff: float, container_bitrate: int, ext: str) -> Tuple[str, str, str]:
    est = estimate_mp3_bitrate(cutoff)
    if ext == ".flac" and 0 < container_bitrate < FLAC_BITRATE_RED_FLAG:
        return (FAKE, "high",
                f"bitrate conteneur FLAC {container_bitrate} kbps < {FLAC_BITRATE_RED_FLAG} (source lossy)")
    if est == 0:
        return AUTHENTIC, "high", f"spectre plein, cutoff {cutoff:.0f} Hz"
    if est >= 320:
        return (SUSPICIOUS, "medium",
                f"cutoff {cutoff:.0f} Hz ~ source 320 kbps (upscale probable ou rolloff naturel)")
    return FAKE, "high", f"cutoff {cutoff:.0f} Hz ~ source lossy {est} kbps"


def _error(p: Path, ext: str, fclass: str, msg: str) -> QualityResult:
    return QualityResult(str(p), p.name, ext, fclass, 0, 0, 0.0, 0.0, 0.0, 0.0, 0,
                         0, ERROR, "n/a", msg)


def analyze_file(path) -> QualityResult:
    """Analyse un fichier -> QualityResult."""
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

    if fclass == "lossy":
        verdict, conf = LOSSY, "high"
        reason = f"format lossy {ext[1:]} (spectre ~ {est or 320}+ kbps) - candidat upgrade"
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


# Champs CSV stables (ordre)
CSV_FIELDS = [
    "verdict", "confidence", "format_class", "ext", "filename",
    "cutoff_hz", "est_source_bitrate", "container_bitrate", "sample_rate",
    "channels", "duration_s", "cutoff_std_hz", "hf_energy_ratio", "reason", "path",
]
