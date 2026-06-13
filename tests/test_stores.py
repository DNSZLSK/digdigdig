"""Tests des liens d'achat (offline, pas de reseau)."""

from __future__ import annotations

from urllib.parse import quote_plus

from ddd.core import stores


def test_search_url_discogs_encodes_and_release_type():
    url = stores.search_url(stores.DISCOGS, "Soul Station", "Fool For Love (Mad Moses Mix)")
    assert url.startswith("https://www.discogs.com/search/?q=")
    assert "type=release" in url
    assert quote_plus("Soul Station Fool For Love (Mad Moses Mix)") in url


def test_search_url_bandcamp():
    url = stores.search_url(stores.BANDCAMP, "The Deep", "Dom Dom Jump")
    assert url.startswith("https://bandcamp.com/search?q=")
    assert quote_plus("The Deep Dom Dom Jump") in url


def test_query_strips_label_year():
    # search_title doit virer [SOCO Audio, 2001] de la requete
    url = stores.search_url(stores.DISCOGS, "Eddie Richards", "Xtrk [SOCO Audio, 2001]")
    assert "SOCO" not in url and "2001" not in url
    assert quote_plus("Eddie Richards Xtrk") in url


def test_special_chars_encoded():
    # le '&' de l'artiste doit etre encode (%26), pas casser la query string
    url = stores.search_url(stores.DISCOGS, "Leo Young & Mr. Beef", "African Rhapsody")
    assert " " not in url
    assert url.count("&") == 1   # seul &type=release ; le & de l'artiste est encode


def test_links_for_keys():
    assert set(stores.links_for("X", "Y")) == {stores.DISCOGS, stores.BANDCAMP}


def test_unknown_store_raises():
    import pytest
    with pytest.raises(ValueError):
        stores.search_url("beatport", "X", "Y")


def test_write_buy_page_dedup_and_content(tmp_path):
    tracks = [
        ("The Deep", "Dom Dom Jump (Sax Mix)"),
        ("THE DEEP", "dom dom jump (sax mix)"),   # doublon (casse) -> dedup
        ("", ""),                                  # vide -> ignore
    ]
    html, csvp = stores.write_buy_page(tracks, tmp_path / "buy.html", tmp_path / "buy.csv", "Test")
    body = html.read_text(encoding="utf-8")
    assert "Dom Dom Jump (Sax Mix)" in body
    assert "discogs.com/search" in body and "bandcamp.com/search" in body
    assert body.count('class="card"') == 1          # dedup -> une seule carte
    lines = csvp.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "artist,title,discogs,bandcamp"
    assert len(lines) == 2                          # header + 1 track


def test_html_escapes_special_chars(tmp_path):
    html, _ = stores.write_buy_page([("AC/DC & Co", "Rock <b> Roll")],
                                    tmp_path / "h.html", tmp_path / "c.csv")
    body = html.read_text(encoding="utf-8")
    assert "<b>" not in body.split("<style>")[1]    # le titre du track est echappe
    assert "Rock &lt;b&gt; Roll" in body


def test_write_unfindable_filters_not_found(tmp_path):
    from types import SimpleNamespace as NS
    from ddd.core.upgrade import ACT_NOT_FOUND, ACT_ACQUIRED
    outcomes = [
        NS(action=ACT_NOT_FOUND, artist="The Deep", title="Dom Dom Jump"),
        NS(action=ACT_ACQUIRED, artist="X", title="Found"),    # trouve -> ignore
        NS(action=ACT_NOT_FOUND, artist="Y", title=""),         # sans titre -> ignore
    ]
    html = stores.write_unfindable(outcomes, tmp_path, "test")
    assert html is not None and html.exists()
    body = html.read_text(encoding="utf-8")
    assert "Dom Dom Jump" in body and "Found" not in body
    assert (tmp_path / "unfindable_test.csv").exists()


def test_write_unfindable_none_when_all_found(tmp_path):
    from types import SimpleNamespace as NS
    from ddd.core.upgrade import ACT_ACQUIRED
    out = stores.write_unfindable([NS(action=ACT_ACQUIRED, artist="X", title="Y")], tmp_path, "t")
    assert out is None
