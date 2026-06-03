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


def test_skips_social_links():
    text = (
        "Tracklist\n"
        "►Follow Gene On Earth - https://www.instagram.com/gene_on_earth/\n"
        "►Buy - geneonearth.bandcamp.com\n"
        "Gene On Earth - Lowcomotion\n"
    )
    pairs = parse_tracklist_text(text)
    assert pairs == [("Gene On Earth", "Lowcomotion")]   # seule la vraie track, pas les liens


def test_split_keeps_remix_skips_id_and_noise():
    assert _split_artist_title("Foo - Bar (Someone Remix)") == ("Foo", "Bar (Someone Remix)")
    assert _split_artist_title("ID - ID") is None
    assert _split_artist_title("no separator here") is None
    assert _split_artist_title(" - Track") is None               # artiste vide


def test_strip_catalog_and_dedup():
    from ddd.core.scrapers.djset import _rows_from_pairs
    pairs = [
        ("Longhair", "A Moment Of Peace"),
        ("Longhair", "A Moment Of Peace [MMD025]"),
        ("Wolfsheim", "The Sparrows And The Nightingales [MS 11071-02]"),
        ("Marcello Giordani", "Something (Original Mix)"),
    ]
    rows = _rows_from_pairs(pairs, "djset:test", "u")
    titles = [r["Title"] for r in rows]
    assert titles.count("A Moment Of Peace") == 1                # dedup malgre le [MMD025]
    assert "The Sparrows And The Nightingales" in titles         # [MS 11071-02] vire
    assert "Something (Original Mix)" in titles                  # (Original Mix) garde
    assert len(rows) == 3


def test_scrape_djset_dedup_and_format(monkeypatch):
    # URL quelconque -> branche yt-dlp ; on la mock pour rester offline
    monkeypatch.setattr(d, "_scrape_youtube", lambda url, prog: [("A", "B"), ("A", "B"), ("C", "D")])
    rows = scrape_djset("http://example.com/x", progress=None)
    assert len(rows) == 2                                        # dedup lower(a)-lower(t)
    assert rows[0]["Artist"] == "A" and rows[0]["Title"] == "B"
    assert set(rows[0]) == {"Artist", "Title", "Album", "Length", "Year", "Source", "SourceUrl"}


def test_human_timestamps_stripped():
    """Timestamps "humains" (45min, 1h05, 90sec, 5m30s) en tete -> stripped, pas pris
    pour l'artiste (sinon "45min - X - Y" donnait artist="min")."""
    text = (
        "45min - Frank De Wulf - Compression\n"
        "1h05 - Artist X - Title Y\n"
        "90sec - Foo - Bar\n"
        "5m30s - Baz - Qux\n"
    )
    pairs = parse_tracklist_text(text)
    assert ("Frank De Wulf", "Compression") in pairs
    assert ("Artist X", "Title Y") in pairs
    assert ("Foo", "Bar") in pairs
    assert ("Baz", "Qux") in pairs
    assert not any(a.lower() in ("min", "45min", "sec", "h", "m", "s") for a, _ in pairs)


def test_human_timestamp_guard_keeps_real_artists():
    """Garde-fou : un nombre+lettre qui n'est PAS une duree reste intact -> on ne mange
    pas l'artiste (808 State, 4 Hero, 50 Cent)."""
    text = (
        "808 State - Pacific\n"
        "4 Hero - Mr Kirks Nightmare\n"
        "50 Cent - In Da Club\n"
    )
    pairs = parse_tracklist_text(text)
    assert ("808 State", "Pacific") in pairs
    assert ("4 Hero", "Mr Kirks Nightmare") in pairs
    assert ("50 Cent", "In Da Club") in pairs
