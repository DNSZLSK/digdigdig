"""Robustesse WMA/ASF : lire les tags ne doit pas planter, et un seul fichier
bancal ne doit jamais tuer tout le scan (on le loggue et on l'ignore).

Regression : mutagen rend des ASFUnicodeAttribute (pas des str) pour les tags
ASF/WMA -> un .strip() direct levait AttributeError et abortait le scan complet.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mutagen
from mutagen.asf import ASFUnicodeAttribute

from ddd.core import naming, scan


class _FakeTags:
    """Imite mf.tags : .get(key) rend une liste d'attributs ASF (comme le vrai)."""
    def __init__(self, data):
        self._d = data

    def get(self, key):
        return self._d.get(key)


class _FakeASF:
    def __init__(self, tags):
        self.tags = tags


def test_read_tags_coerces_asf_attribute(monkeypatch):
    """read_tags ne plante pas sur un ASFUnicodeAttribute et rend une str strippee."""
    tags = _FakeTags({
        "artist": [ASFUnicodeAttribute("  Aphex Twin  ")],
        "title": [ASFUnicodeAttribute("Windowlicker")],
    })
    monkeypatch.setattr(mutagen, "File", lambda *a, **k: _FakeASF(tags))

    out = naming.read_tags("whatever.wma")   # ne doit PAS lever
    assert out["artist"] == "Aphex Twin"
    assert out["title"] == "Windowlicker"
    assert out["album"] == "" and out["genre"] == ""


def test_scan_library_skips_crashing_file(tmp_path, monkeypatch):
    """Un fichier dont l'audit plante est ignore ; le reste du scan aboutit."""
    (tmp_path / "Artist - Good.mp3").write_bytes(b"")
    (tmp_path / "Bad.mp3").write_bytes(b"")

    real_audit = scan.audit_mod.audit_file

    def fake_audit(f):
        if Path(f).name == "Bad.mp3":
            raise AttributeError("'ASFUnicodeAttribute' object has no attribute 'strip'")
        return real_audit(f)

    monkeypatch.setattr(scan.audit_mod, "audit_file", fake_audit)

    records = scan.scan_library(tmp_path)   # ne doit PAS lever
    names = {r.quality.filename for r in records}
    assert "Artist - Good.mp3" in names
    assert "Bad.mp3" not in names


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
