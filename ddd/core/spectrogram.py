"""Spectrogramme d'un fichier -> PNG (pour le voir de ses propres yeux avant de juger).

Sans dependance en plus : numpy pour la STFT, zlib + struct pour ecrire le PNG
(matplotlib est volontairement exclu du .exe, Pillow n'est pas une dependance).

Le fichier est lu par blocs (jamais entier en memoire : un 7 min stereo float64 = 300 Mo).
Axe des frequences LINEAIRE : c'est le haut du spectre qui trahit un MP3 reencode
(plateau net a 16 / 19 / 20 kHz), une echelle log l'ecraserait.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from . import decode

NFFT = 4096
BLOCK_S = 20.0
FLOOR_DB = -100.0

# Degrade fond sombre -> bleu nuit -> sarcelle -> lime (accent de l'app) -> blanc chaud.
_STOPS = [
    (0.00, (17, 19, 22)),
    (0.30, (34, 38, 82)),
    (0.55, (38, 120, 150)),
    (0.78, (198, 221, 98)),
    (1.00, (250, 250, 228)),
]
_LINE = (139, 144, 153)        # reperes 16/18/20 kHz
_CUT = (236, 97, 86)           # cutoff mesure


@dataclass
class Spectrogram:
    png: bytes
    width: int
    height: int
    nyquist: float
    duration_s: float

    def y_of(self, hz: float) -> float:
        """Ordonnee (px, 0 = haut) d'une frequence, pour placer les libelles cote GUI."""
        return (1.0 - min(max(hz / self.nyquist, 0.0), 1.0)) * (self.height - 1)


def _lut() -> np.ndarray:
    xs = np.linspace(0.0, 1.0, 256)
    pos = [s[0] for s in _STOPS]
    return np.stack([np.interp(xs, pos, [s[1][c] for s in _STOPS]) for c in range(3)],
                    axis=1).astype(np.uint8)


_LUT = _lut()


def _png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _columns(path: Path, info, width: int) -> np.ndarray:
    """Magnitudes (dB) de `width` trames regulierement espacees -> (width, NFFT//2+1)."""
    total = int(info.frames)
    sr = int(info.samplerate)
    if total < NFFT:
        raise ValueError("file too short for a spectrogram")
    starts = np.linspace(0, total - NFFT, width).astype(np.int64)
    win = np.hanning(NFFT)
    out = np.full((width, NFFT // 2 + 1), FLOOR_DB, dtype=np.float32)
    block = max(int(BLOCK_S * sr), NFFT * 2)
    i = 0
    pos = 0
    while i < width and pos < total:
        frames = min(block + NFFT, total - pos)
        data = decode.read_window(path, info, pos, frames)
        if data is not None and len(data):
            mono = data.mean(axis=1) if data.ndim > 1 else data
            while i < width and starts[i] + NFFT <= pos + len(mono):
                off = int(starts[i] - pos)
                if off >= 0:
                    seg = mono[off:off + NFFT]
                    if len(seg) == NFFT:
                        mag = np.abs(np.fft.rfft(seg * win))
                        out[i] = 20.0 * np.log10(mag + 1e-10)
                i += 1
        pos += block
    return out


def render(path, width: int = 900, height: int = 300,
           cutoff_hz: Optional[float] = None, marks_hz: List[float] = (16000, 18000, 20000)
           ) -> Spectrogram:
    """Spectrogramme de tout le morceau (temps en x, frequence lineaire en y)."""
    path = Path(path)
    info = decode.open_info(path)
    sr = int(info.samplerate)
    nyq = sr / 2.0
    cols = _columns(path, info, width)                      # (width, bins)

    ref = np.percentile(cols, 99.9)
    norm = np.clip((cols - ref - FLOOR_DB) / -FLOOR_DB, 0.0, 1.0)   # ref-100 dB .. ref
    bins = norm.shape[1]
    edges = np.linspace(0, bins, height + 1).astype(int)   # regroupe les bins par ligne
    rows = np.stack([norm[:, a:max(b, a + 1)].max(axis=1) for a, b in zip(edges[:-1], edges[1:])],
                    axis=0)[::-1]                            # (height, width), aigus en haut
    rgb = _LUT[(rows * 255).astype(np.uint8)].copy()

    spec = Spectrogram(b"", width, height, nyq, float(info.duration))
    for hz in marks_hz:
        if hz < nyq:
            y = int(round(spec.y_of(hz)))
            rgb[y, ::6] = _LINE                              # pointilles discrets
            rgb[y, 1::6] = _LINE
    if cutoff_hz and 0 < cutoff_hz < nyq:
        y = int(round(spec.y_of(cutoff_hz)))
        rgb[max(0, y - 1):y + 1, :] = _CUT
    spec.png = _png(rgb)
    return spec
