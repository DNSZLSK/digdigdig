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


def test_strip_trailing_asterisk():
    """Les * en fin de titre (marqueur source unreleased/ID) sont vires (polluent la recherche)."""
    from ddd.core.scrapers.djset import _strip_catalog, _rows_from_pairs
    assert _strip_catalog("Letna Disko*") == "Letna Disko"
    assert _strip_catalog("Bongs & Bongos**") == "Bongs & Bongos"
    assert _strip_catalog("Backstage Access (Original Mix)*") == "Backstage Access (Original Mix)"
    rows = _rows_from_pairs([("Artist", "Backstage Access*")], "djset:test", "u")
    assert rows[0]["Title"] == "Backstage Access"


def test_dedup_double_remix_paren():
    """"(Admo Remix) (Admo Remix)" -> un seul, et fusionne avec la version simple a la dedup."""
    from ddd.core.scrapers.djset import _strip_catalog, _rows_from_pairs
    assert _strip_catalog("Funky Blaster (Admo Remix) (Admo Remix)") == "Funky Blaster (Admo Remix)"
    pairs = [
        ("Sunaas", "Funky Blaster (Admo Remix)"),
        ("Sunaas", "Funky Blaster (Admo Remix) (Admo Remix)"),
    ]
    rows = _rows_from_pairs(pairs, "djset:test", "u")
    assert len(rows) == 1                                        # les 2 fusionnent
    assert rows[0]["Title"] == "Funky Blaster (Admo Remix)"      # un seul (Admo Remix)


def test_playlist_id_detects_real_playlist():
    from ddd.core.scrapers.djset import _playlist_id
    assert _playlist_id("https://www.youtube.com/watch?v=abc&list=PLxxx&index=22") == "PLxxx"
    assert _playlist_id("https://www.youtube.com/playlist?list=OL12345") == "OL12345"
    assert _playlist_id("https://www.youtube.com/watch?v=abc&list=RDmix") is None   # mix/radio auto
    assert _playlist_id("https://www.youtube.com/watch?v=abc") is None              # pas de playlist


def test_clean_video_title_strips_noise_keeps_version():
    from ddd.core.scrapers.djset import _clean_video_title
    assert _clean_video_title("Daft Punk - Around the World (Official Video)") == "Daft Punk - Around the World"
    assert _clean_video_title("Foo - Bar [Official Audio] (Free DL)") == "Foo - Bar"
    assert _clean_video_title("Foo - Bar | Some Label Records") == "Foo - Bar"      # vire le tail apres |
    assert _clean_video_title("X - Y (Original Mix)") == "X - Y (Original Mix)"     # garde la version
    assert _clean_video_title("X - Y (Mtherapy Remix)") == "X - Y (Mtherapy Remix)"


def test_pairs_from_entries_parses_and_skips():
    from ddd.core.scrapers.djset import _pairs_from_entries
    entries = [
        {"title": "A - B (Official Video)"},   # -> ("A","B")
        {"title": "pas de separateur"},        # skip (pas de ' - ')
        {"title": None},                       # skip
        {"title": "X - Y (Original Mix)"},     # garde la version
        "pas un dict",                         # skip robustement
    ]
    assert _pairs_from_entries(entries) == [("A", "B"), ("X", "Y (Original Mix)")]


def test_strip_catalog_strips_trailing_year():
    from ddd.core.scrapers.djset import _strip_catalog, _rows_from_pairs
    assert _strip_catalog("CR Break (1996)") == "CR Break"
    assert _strip_catalog("Inhaled Deeply (Original Mix) (2012)") == "Inhaled Deeply (Original Mix)"
    rows = _rows_from_pairs([("A", "B (1996)")], "djset:test", "u")
    assert rows[0]["Title"] == "B"


def test_playlist_video_titles_both_formats():
    from ddd.core.scrapers.djset import _playlist_video_titles
    data = {
        "x": [{"playlistVideoRenderer": {"title": {"runs": [{"text": "Old - Format"}]}}}],
        "y": {"lockupViewModel": {"metadata": {"lockupMetadataViewModel": {"title": {"content": "New - Format"}}}}},
    }
    assert _playlist_video_titles(data) == ["Old - Format", "New - Format"]


def test_continuation_token():
    from ddd.core.scrapers.djset import _continuation_token
    assert _continuation_token({"a": {"continuationCommand": {"token": "TOK123"}}}) == "TOK123"
    assert _continuation_token({"a": {"b": 1}}) is None


def test_strip_lead_year():
    from ddd.core.scrapers.djset import _split_artist_title, _rows_from_pairs
    rows = _rows_from_pairs([_split_artist_title("(1997) Dimitri From Paris - Just About Right")], "t", "u")
    assert rows[0]["Artist"] == "Dimitri From Paris" and rows[0]["Title"] == "Just About Right"


def test_strip_trailing_label_keeps_version():
    from ddd.core.scrapers.djset import _strip_catalog
    assert _strip_catalog("Last (BASENOTIC)") == "Last"
    assert _strip_catalog("Dom Dom Jump - Sax Mix (Basenotic)") == "Dom Dom Jump - Sax Mix"
    assert _strip_catalog("Inhaled Deeply (Original Mix)") == "Inhaled Deeply (Original Mix)"   # version gardee
    assert _strip_catalog("French Lesson (Science Friction Remix)") == "French Lesson (Science Friction Remix)"
    assert _strip_catalog("Track (Part II)") == "Track (Part II)"   # 2 mots -> garde


def test_titlecase_only_when_all_lower():
    from ddd.core.scrapers.djset import _strip_catalog
    assert _strip_catalog("tiko") == "Tiko"
    assert _strip_catalog("the mood is right") == "The Mood Is Right"
    assert _strip_catalog("DJ Koze") == "DJ Koze"          # deja casse -> respecte
    assert _strip_catalog("Don't Stop") == "Don't Stop"


def test_strip_year_anywhere_keeps_version():
    from ddd.core.scrapers.djset import _strip_catalog
    # annee au MILIEU (suivie d'un marqueur de version) -> annee partie, version gardee
    assert _strip_catalog("Buzz Time (1996) (Edited)") == "Buzz Time (Edited)"
    assert _strip_catalog("Think Positive (Kid Smart Remix) (2001)") == "Think Positive (Kid Smart Remix)"
    assert _strip_catalog("They're Among Us (1996)") == "They're Among Us"
    assert _strip_catalog("Captain Future (2003) (Q-NRT Club Mix)") == "Captain Future (Q-NRT Club Mix)"


_UC = "UCabcdefghijklmnopqrstuv"   # UC + 22 = 24 chars (id de chaine valide)


def test_channel_uploads_id_direct_and_negatives():
    from ddd.core.scrapers.djset import _channel_uploads_id
    # /channel/UC... : id direct dans l'URL -> uploads playlist UU... (pas de reseau)
    assert _channel_uploads_id(f"https://www.youtube.com/channel/{_UC}") == "UU" + _UC[2:]
    assert _channel_uploads_id(f"https://www.youtube.com/channel/{_UC}/videos") == "UU" + _UC[2:]
    # pas une chaine -> None, sans aucun fetch
    assert _channel_uploads_id("https://www.youtube.com/watch?v=abc") is None
    assert _channel_uploads_id("https://www.youtube.com/playlist?list=PLxxx") is None
    assert _channel_uploads_id("https://youtu.be/abc123") is None


def test_channel_uploads_id_handle_resolves_via_page(monkeypatch):
    # @handle / c/ / user/ : on lit la page et on extrait le channelId UC... du JSON
    html = f'var x = {{"responseContext":{{}},"channelId":"{_UC}","title":"Some Digger"}};'
    monkeypatch.setattr(d, "_yt_get", lambda url, data=None: html)
    assert d._channel_uploads_id("https://www.youtube.com/@SomeDigger/videos") == "UU" + _UC[2:]
    assert d._channel_uploads_id("https://www.youtube.com/c/SomeDigger") == "UU" + _UC[2:]


def test_channel_uploads_id_unresolvable_returns_none(monkeypatch):
    # page de chaine sans aucun UC... -> None (et le dispatcher retombera sur la branche video)
    monkeypatch.setattr(d, "_yt_get", lambda url, data=None: "<html>no id here</html>")
    assert d._channel_uploads_id("https://www.youtube.com/@Ghost") is None


def test_scrape_djset_routes_channel(monkeypatch):
    # URL de chaine -> branche djset:youtube-channel, titres via le paginateur (mocke offline)
    monkeypatch.setattr(d, "_channel_uploads_id", lambda url: "UU" + _UC[2:])
    monkeypatch.setattr(d, "_youtube_playlist_titles",
                        lambda upid, prog, **kw: ["A - B", "C - D (Original Mix)", "A - B"])
    rows = scrape_djset("https://www.youtube.com/@SomeDigger/videos", progress=None)
    assert [(r["Artist"], r["Title"]) for r in rows] == [("A", "B"), ("C", "D (Original Mix)")]
    assert rows[0]["Source"] == "djset:youtube-channel"   # routage + dedup lower(a)-lower(t)
