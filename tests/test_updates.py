"""Logique de comparaison de version pour la notif de mise a jour (sans reseau)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import updates


def test_parse_version_strips_v_and_suffix():
    assert updates.parse_version("v0.2.10") == (0, 2, 10)
    assert updates.parse_version("0.2.9") == (0, 2, 9)
    assert updates.parse_version("V1.0.0-beta") == (1, 0, 0)
    assert updates.parse_version("") == ()


def test_is_newer_numeric_not_lexical():
    # le piege classique : '0.2.10' < '0.2.9' en lexical, mais 0.2.10 EST plus recent
    assert updates.is_newer("v0.2.10", "0.2.9") is True
    assert updates.is_newer("0.2.9", "0.2.9") is False
    assert updates.is_newer("0.2.8", "0.2.9") is False
    assert updates.is_newer("v0.3.0", "0.2.9") is True
    assert updates.is_newer("garbage", "0.2.9") is False  # tag illisible -> jamais "plus recent"
