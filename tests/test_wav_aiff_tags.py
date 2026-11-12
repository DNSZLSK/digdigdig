"""WAV/AIFF tagues en ID3 (Beatport, Rekordbox) : read_tags doit rendre artiste/titre/genre.

Regression : mutagen.File(easy=True) n'a pas de mode easy pour WAV/AIFF et rend de l'ID3
brut (TPE1/TIT2/TCON) -> tout sortait vide et Sort classait ces fichiers au modele audio.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest
import soundfile as sf
from mutagen.aiff import AIFF
from mutagen.id3 import TALB, TCON, TIT2, TPE1
from mutagen.wave import WAVE

from ddd.core import naming


@pytest.mark.parametrize("ext,fmt,cls", [("wav", "WAV", WAVE), ("aiff", "AIFF", AIFF)])
def test_read_tags_id3_in_wav_aiff(tmp_path, ext, fmt, cls):
    p = tmp_path / f"x.{ext}"
    sf.write(str(p), np.zeros((4410, 2), dtype=np.float32), 44100, format=fmt, subtype="PCM_16")
    f = cls(str(p))
    f.add_tags()
    f.tags.add(TPE1(encoding=3, text="Kasper Lund"))
    f.tags.add(TIT2(encoding=3, text="Night Bus"))
    f.tags.add(TALB(encoding=3, text="Ostende EP"))
    f.tags.add(TCON(encoding=3, text="Techno"))
    f.save()

    assert naming.read_tags(p) == {"artist": "Kasper Lund", "title": "Night Bus",
                                   "album": "Ostende EP", "genre": "Techno"}


def test_read_tags_untagged_wav_is_empty(tmp_path):
    p = tmp_path / "bare.wav"
    sf.write(str(p), np.zeros((4410, 2), dtype=np.float32), 44100, format="WAV", subtype="PCM_16")
    assert naming.read_tags(p) == {}
