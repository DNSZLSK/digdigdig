"""Colonne FORMAT : le debit affiche est cale sur le CBR standard (tags/pochette inclus
dans la mesure faisaient afficher 'mp3 321'), sans masquer un vrai VBR."""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from ddd import gui_theme as theme
from ddd.core import quality


@pytest.mark.parametrize("measured,shown", [
    (321, 320), (322, 320), (318, 320), (329, 320),   # CBR 320 + tags/pochette
    (193, 192), (129, 128), (257, 256),
    (245, 245), (290, 290), (175, 175),               # VBR : tel quel
    (0, 0),
])
def test_display_kbps(measured, shown):
    assert theme.display_kbps(measured) == shown


def test_format_label_mp3():
    q = SimpleNamespace(verdict=quality.HQ, ext=".mp3", format_class="lossy", container_bitrate=322)
    assert theme.format_label(q) == "mp3 320"
