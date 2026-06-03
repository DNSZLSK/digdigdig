"""Tests du parsing de tracklist (offline, sans reseau ni yt-dlp)."""

from __future__ import annotations

import ddd.core.scrapers.djset as d
from ddd.core.scrapers.djset import parse_tracklist_text, _split_artist_title, scrape_djset


def test_parse_basic():
    text = """
    Tracklist:
    0:00 Artist One - Track One (Original Mix)
    1. 03:45 Artist Two - Track Two
    [07:12] Artist Three - Track Three [Label123]
    ID - ID
    08:00 id - id
    random line without separator
    Artist Four - Track Four
    """
    pairs = parse_tracklist_text(text)
    assert ("Artist One", "Track One (Original Mix)") in pairs   # garde (Original Mix)
    assert ("Artist Two", "Track Two") in pairs                  # vire "1." et "03:45"
    assert ("Artist Three", "Track Three [Label123]") in pairs   # garde le label (recherche)
    assert ("Artist Four", "Track Four") in pairs
    assert not any(a.lower() == "id" for a, _ in pairs)          # les ID sont skip
    assert len(pairs) == 4


def test_split_keeps_remix_skips_id_and_noise():
    assert _split_artist_title("Foo - Bar (Someone Remix)") == ("Foo", "Bar (Someone Remix)")
    assert _split_artist_title("ID - ID") is None
    assert _split_artist_title("no separator here") is None
    assert _split_artist_title(" - Track") is None               # artiste vide


def test_scrape_djset_dedup_and_format(monkeypatch):
    # URL quelconque -> branche yt-dlp ; on la mock pour rester offline
    monkeypatch.setattr(d, "_scrape_youtube", lambda url, prog: [("A", "B"), ("A", "B"), ("C", "D")])
    rows = scrape_djset("http://example.com/x", progress=None)
    assert len(rows) == 2                                        # dedup lower(a)-lower(t)
    assert rows[0]["Artist"] == "A" and rows[0]["Title"] == "B"
    assert set(rows[0]) == {"Artist", "Title", "Album", "Length", "Year", "Source", "SourceUrl"}
