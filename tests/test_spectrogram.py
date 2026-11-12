"""Spectrogramme : PNG valide, et un fichier coupe a 16 kHz est sombre au-dessus."""

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest
import soundfile as sf

from ddd.core import spectrogram

SR = 44100


def _noise(path, seconds=6.0, cutoff=None):
    rng = np.random.default_rng(0)
    n = int(SR * seconds)
    spec = np.fft.rfft(rng.standard_normal(n))
    if cutoff:
        spec[np.fft.rfftfreq(n, 1 / SR) > cutoff] = 0
    x = np.fft.irfft(spec, n=n)
    sf.write(str(path), (0.4 * x / np.abs(x).max()).astype(np.float32), SR, format="FLAC")
    return path


def _decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    pos, idat = 8, b""
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += png[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    rows = [raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h)]
    return np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(h, w, 3)


def test_png_is_valid_with_requested_size(tmp_path):
    sp = spectrogram.render(_noise(tmp_path / "a.flac"), width=200, height=80)
    img = _decode_png(sp.png)
    assert img.shape == (80, 200, 3)
    assert sp.nyquist == SR / 2 and sp.duration_s == pytest.approx(6.0, abs=0.05)


def test_band_above_cutoff_is_dark(tmp_path):
    sp = spectrogram.render(_noise(tmp_path / "cut.flac", cutoff=16000), width=200, height=100,
                            marks_hz=())
    img = _decode_png(sp.png).astype(int).sum(axis=2)
    y16 = int(sp.y_of(16000))
    above = img[2:y16 - 4].mean()          # 17-22 kHz : rien
    below = img[y16 + 4:-2].mean()         # 0-15 kHz : bruit plein
    assert below > above * 3


def test_y_of_maps_top_and_bottom(tmp_path):
    sp = spectrogram.render(_noise(tmp_path / "b.flac"), width=50, height=101)
    assert sp.y_of(sp.nyquist) == 0
    assert sp.y_of(0) == 100
    assert sp.y_of(sp.nyquist / 2) == pytest.approx(50)


def test_too_short_file_raises(tmp_path):
    p = tmp_path / "short.flac"
    sf.write(str(p), np.zeros(1000, dtype=np.float32), SR, format="FLAC")
    with pytest.raises(ValueError):
        spectrogram.render(p)
