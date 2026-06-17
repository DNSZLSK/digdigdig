"""Tests du lookup de genre : HTTP monkeypatche (zero reseau), cache sur tmp_path."""

from __future__ import annotations

import pytest

import ddd.core.genre as genre
from ddd.core.genre import GenreResult


# --- jeux de resultats Discogs canned ----------------------------------------

def _hit_results():
    # Mr Fingers - Mystery Of Love : "Acid House" revient 2x (dominant), "Deep House" 1x.
    # + une comp megamix (demote) et un mauvais artiste (filtre).
    return {"results": [
        {"title": "Mr Fingers - Mystery Of Love", "style": ["Acid House", "Deep House"], "genre": ["Electronic"]},
        {"title": "Mr Fingers - Amnesia",         "style": ["Acid House"],               "genre": ["Electronic"]},
        {"title": "Various - Megamix 3000",        "style": ["Trance"],                   "genre": ["Electronic"]},
        {"title": "Some Other Guy - Mystery Of Love", "style": ["Pop"],                   "genre": ["Rock"]},
    ]}


def test_query_title_strips_version_and_junk():
    from ddd.core.genre import _query_title
    assert _query_title("Get Down (Original Mix)") == "Get Down"
    assert _query_title("Tonight (Original Mix) heydj.pro") == "Tonight"
    assert _query_title("Guiro (1)") == "Guiro"
    assert _query_title("Pua (feat. Penya)") == "Pua"
    assert _query_title("Juicy Tracksuit Original Mix") == "Juicy Tracksuit"
    assert _query_title("Vogue (Younger Than Me Edit) #51 - Free download") == "Vogue"
    assert _query_title("Whater You Want ((Innershades Remix))") == "Whater You Want"
    assert _query_title("Up There") == "Up There"          # nom propre inchange


def test_discogs_aggregates_styles_by_frequency(monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: _hit_results())
    r = genre.lookup_genre("Mr Fingers", "Mystery of Love", token="x")
    assert r.source == "discogs"
    assert r.styles[0] == "Acid House"            # frequence : 2 > 1
    assert "Deep House" in r.styles
    assert "Trance" not in r.styles               # comp megamix demotee
    assert "Pop" not in r.styles                  # mauvais artiste filtre


def test_discogs_genre_fallback_when_styles_empty(monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: {
        "results": [{"title": "Unknown - Obscure", "style": [], "genre": ["House"]}]})
    r = genre.lookup_genre("Unknown", "Obscure", token="x")
    assert r.found and r.source == "discogs"
    assert r.styles == [] and r.genres == ["House"]


def test_discogs_miss_returns_unfound(monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: {"results": []})
    # pas de MB pour ce test (source discogs seule)
    r = genre.lookup_genre("Nobody", "Nothing", sources=("discogs",), token="x")
    assert not r.found and r.source == ""


def test_cache_roundtrip_no_second_network_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: _hit_results())
    r1 = genre.lookup_genre("Mr Fingers", "Mystery of Love", token="x", cache_dir=tmp_path)
    assert r1.styles[0] == "Acid House"

    def boom(url, token):
        raise AssertionError("network must NOT be hit on a cache hit")

    monkeypatch.setattr(genre, "http_get", boom)
    r2 = genre.lookup_genre("Mr Fingers", "Mystery of Love", token="x", cache_dir=tmp_path)
    assert r2.styles[0] == "Acid House" and r2.source == "discogs"


def test_negative_result_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: {"results": []})
    r1 = genre.lookup_genre("Nobody", "Nothing", sources=("discogs",), token="x", cache_dir=tmp_path)
    assert not r1.found

    def boom(url, token):
        raise AssertionError("a cached MISS must not re-hit the network")

    monkeypatch.setattr(genre, "http_get", boom)
    r2 = genre.lookup_genre("Nobody", "Nothing", sources=("discogs",), token="x", cache_dir=tmp_path)
    assert not r2.found


def test_musicbrainz_fallback_when_discogs_empty(monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: {"results": []})
    monkeypatch.setattr(genre, "_musicbrainz_lookup",
                        lambda a, t: GenreResult(styles=["Techno"], genres=["Techno"],
                                                 source="musicbrainz", query=f"{a} - {t}"))
    r = genre.lookup_genre("Jeff Mills", "The Bells", token="x")
    assert r.source == "musicbrainz" and r.styles == ["Techno"]


def test_sources_order_discogs_wins_mb_not_called(monkeypatch):
    monkeypatch.setattr(genre, "http_get", lambda url, token: _hit_results())

    def must_not_run(a, t):
        raise AssertionError("MusicBrainz must not be called when Discogs hits")

    monkeypatch.setattr(genre, "_musicbrainz_lookup", must_not_run)
    r = genre.lookup_genre("Mr Fingers", "Mystery of Love", token="x")
    assert r.source == "discogs"


def test_no_token_skips_discogs(monkeypatch):
    # token vide + pas d'env/config -> discogs saute, MB repond
    monkeypatch.setattr(genre.config, "get", lambda *a, **k: "")
    monkeypatch.setattr(genre, "http_get",
                        lambda url, token: (_ for _ in ()).throw(AssertionError("discogs skipped")))
    monkeypatch.setattr(genre, "_musicbrainz_lookup",
                        lambda a, t: GenreResult(styles=["House"], genres=["House"], source="musicbrainz"))
    r = genre.lookup_genre("X", "Y", token="")
    assert r.source == "musicbrainz"
