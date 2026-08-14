"""Tests du cap `newest` (les N derniers ajouts) sur les 3 scrapers, reseau mocke.

Contrat verifie :
- Discogs : la source est triee `added desc` ET on ne fetch le detail que de N releases
  (le cap coupe la pagination AVANT `_release` = le gain de temps).
- Bandcamp : si la 1re page (sequence) suffit, la boucle d'API paginee (`scraper.post`)
  n'est jamais atteinte.
- djset : `_youtube_playlist_titles` stoppe la pagination InnerTube des N titres et tronque.
"""

from __future__ import annotations

import ddd.core.scrapers.bandcamp as bc
import ddd.core.scrapers.discogs as dg
import ddd.core.scrapers.djset as dj


# ------------------------------- Discogs -----------------------------------

def _fake_release(rid, token, cache_dir):
    return {"title": f"Album{rid}", "year": 2020, "artists": [{"name": "A"}],
            "tracklist": [{"type_": "track", "title": f"Track{rid}", "duration": "3:00"}]}


def test_discogs_newest_caps_and_sorts(monkeypatch, tmp_path):
    calls = {"urls": [], "releases": 0}

    def fake_http_get(url, token, retries=5):
        calls["urls"].append(url)
        return {"wants": [{"basic_information": {"id": i}} for i in range(1, 6)],
                "pagination": {"urls": {}}}

    def counting_release(rid, token, cache_dir):
        calls["releases"] += 1
        return _fake_release(rid, token, cache_dir)

    monkeypatch.setattr(dg, "http_get", fake_http_get)
    monkeypatch.setattr(dg, "_release", counting_release)

    rows = dg.scrape_discogs("user", token="x", cache_dir=str(tmp_path), newest=2)

    assert calls["releases"] == 2                       # 2 releases fetchees sur 5, pas plus
    assert len(rows) == 2
    assert "sort=added&sort_order=desc" in calls["urls"][0]   # tri recent-d'abord present


def test_discogs_newest_zero_no_sort_all_fetched(monkeypatch, tmp_path):
    calls = {"urls": [], "releases": 0}

    def fake_http_get(url, token, retries=5):
        calls["urls"].append(url)
        return {"wants": [{"basic_information": {"id": i}} for i in range(1, 6)],
                "pagination": {"urls": {}}}

    def counting_release(rid, token, cache_dir):
        calls["releases"] += 1
        return _fake_release(rid, token, cache_dir)

    monkeypatch.setattr(dg, "http_get", fake_http_get)
    monkeypatch.setattr(dg, "_release", counting_release)

    rows = dg.scrape_discogs("user", token="x", cache_dir=str(tmp_path), newest=0)

    assert calls["releases"] == 5                       # tout scrape (comportement d'origine)
    assert len(rows) == 5
    assert "sort=added" not in calls["urls"][0]          # pas de tri force sans cap


# ------------------------------- Bandcamp ----------------------------------

def test_bandcamp_newest_skips_api_pagination(monkeypatch, tmp_path):
    posts = {"count": 0}

    class FakeScraper:
        def get(self, *a, **k):
            raise AssertionError("no direct get expected")

        def post(self, *a, **k):
            posts["count"] += 1
            raise AssertionError("API pagination should be skipped when 1st page suffices")

    seq = [1, 2, 3, 4, 5]
    blob = {
        "fan_data": {"fan_id": 42},
        "item_cache": {"wishlist": {
            str(i): {"band_name": f"Band{i}", "item_title": f"Title{i}",
                     "item_type": "track", "item_url": f"http://x/{i}"} for i in seq}},
        "wishlist_data": {"sequence": seq, "last_token": "TOK"},
    }
    monkeypatch.setattr(bc, "_make_scraper", lambda: FakeScraper())
    monkeypatch.setattr(bc, "_wishlist_blob", lambda scraper, u: blob)

    rows = bc.scrape_bandcamp("user", cache_dir=str(tmp_path), newest=3)

    assert posts["count"] == 0                          # jamais entre dans la boucle paginee
    assert len(rows) == 3                               # les 3 premieres (recent-d'abord)


# -------------------------------- djset ------------------------------------

# HTML de 1re page minimal accepte par _youtube_playlist_titles (cle + ytInitialData + un {})
_FAKE_HTML = '"INNERTUBE_API_KEY":"K" "INNERTUBE_CLIENT_VERSION":"1.0" ytInitialData = {"p":0}'


def test_youtube_playlist_titles_stops_early(monkeypatch):
    pages = {"continuations": 0}

    def fake_yt_get(url, data=None):
        if data is None:                               # 1re page (HTML playlist)
            return _FAKE_HTML
        pages["continuations"] += 1
        return '{"cont":1}'                            # reponse continuation (JSON)

    title_pages = iter([["a - t1", "b - t2"], ["c - t3", "d - t4"], ["e - t5"]])

    def fake_titles(node):
        try:
            return next(title_pages)
        except StopIteration:
            return []

    monkeypatch.setattr(dj, "_yt_get", fake_yt_get)
    monkeypatch.setattr(dj, "_playlist_video_titles", fake_titles)
    monkeypatch.setattr(dj, "_continuation_token", lambda node: "TOKEN")  # pagine "a l'infini"

    out = dj._youtube_playlist_titles("PL", None, newest=3)

    assert out == ["a - t1", "b - t2", "c - t3"]        # stoppe + tronque a 3
    assert pages["continuations"] == 1                  # une seule page suppl. (2+2 >= 3 -> stop)


def test_youtube_playlist_titles_no_cap_paginates_all(monkeypatch):
    def fake_yt_get(url, data=None):
        return _FAKE_HTML if data is None else '{"cont":1}'

    title_pages = iter([["a - t1"], ["b - t2"], ["c - t3"]])

    def fake_titles(node):
        try:
            return next(title_pages)
        except StopIteration:
            return []

    tokens = iter(["T1", "T2", None])                  # 3e page = fin

    monkeypatch.setattr(dj, "_yt_get", fake_yt_get)
    monkeypatch.setattr(dj, "_playlist_video_titles", fake_titles)
    monkeypatch.setattr(dj, "_continuation_token", lambda node: next(tokens, None))

    out = dj._youtube_playlist_titles("PL", None, newest=0)

    assert out == ["a - t1", "b - t2", "c - t3"]        # tout, sans cap
