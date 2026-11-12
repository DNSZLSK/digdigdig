"""Reglages communs aux tests."""

import pytest


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch):
    """Les tests ne lisent ni n'ecrivent le cache de scan reel (dossier de donnees de
    l'app, le repo en dev). Les tests du cache le reactivent sur un chemin temporaire."""
    monkeypatch.setenv("DDD_SCAN_CACHE", "0")
