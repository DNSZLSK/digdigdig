"""Scraper wantlist (+ collection) Discogs -> liste de pistes.

Refactor de lib/scrapers/discogs.py : la logique est exposee comme `scrape_discogs()`
pour etre appelee par la CLI et la GUI ; `main()` garde l'usage standalone.
Auth : token Discogs (https://www.discogs.com/settings/developers), passe en argument,
via $DISCOGS_TOKEN, ou via la config ddd.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

API = "https://api.discogs.com"
UA = "ddd-digdigdig/0.1 +https://github.com/DNSZLSK/digdigdig"

ProgressCb = Optional[Callable[[str], None]]


def _http_get(url: str, token: str, retries: int = 5):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Discogs token={token}")
    req.add_header("User-Agent", UA)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", 2)))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Discogs: echec apres {retries} essais: {url}")


def _dur_to_secs(dur: str):
    if not dur or ":" not in dur:
        return ""
    parts = dur.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return ""
    return ""


def _join_artists(artists) -> str:
    out = ""
    for a in artists:
        name = re.sub(r"\s*\(\d+\)$", "", a.get("name", "").rstrip())
        out += name
        join = a.get("join", "")
        if join == ",":
            out += ", "
        elif join:
            out += f" {join} "
    return out.strip().rstrip(",").strip()


def _paginated(url: str, token: str, key: str):
    while url:
        data = _http_get(url, token)
        for item in data.get(key, []):
            yield item
        url = data.get("pagination", {}).get("urls", {}).get("next")


def _release(release_id: int, token: str, cache_dir: Path):
    cache_file = cache_dir / f"{release_id}.json"
    if cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    data = _http_get(f"{API}/releases/{release_id}", token)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def scrape_discogs(
    username: str,
    token: str = "",
    include_collection: bool = False,
    cache_dir: str = "inputs/.discogs-cache",
    progress: ProgressCb = None,
) -> List[Dict]:
    """Scrape la wantlist (et option. la collection) -> liste de rows."""
    if not token:
        token = os.environ.get("DISCOGS_TOKEN", "")
    if not token:
        try:
            from .. import config
            token = config.get("discogs_token", "") or ""
        except Exception:  # noqa: BLE001
            token = ""
    if not token:
        raise ValueError("token Discogs requis (argument, $DISCOGS_TOKEN, ou config ddd)")

    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    seen = set()
    sources = [("wantlist", f"{API}/users/{username}/wants")]
    if include_collection:
        sources.append(("collection", f"{API}/users/{username}/collection/folders/0/releases"))

    for source_name, start_url in sources:
        if progress:
            progress(f"Discogs: {source_name} de {username}...")
        key = "wants" if source_name == "wantlist" else "releases"
        for item in _paginated(start_url, token, key):
            basic = item.get("basic_information", {})
            rid = basic.get("id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            try:
                release = _release(rid, token, cdir)
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(f"  skip release {rid}: {e}")
                continue
            album = release.get("title", "")
            year = release.get("year", "")
            rel_artists = _join_artists(release.get("artists", []))
            for track in release.get("tracklist", []):
                if track.get("type_") != "track":
                    continue
                title = track.get("title", "").strip()
                if not title:
                    continue
                t_artists = track.get("artists")
                artist = _join_artists(t_artists) if t_artists else rel_artists
                key2 = (artist.lower(), title.lower())
                if key2 in seen:
                    continue
                seen.add(key2)
                rows.append({
                    "Artist": artist, "Title": title, "Album": album,
                    "Length": _dur_to_secs(track.get("duration", "")), "Year": year,
                    "Source": f"discogs:{source_name}",
                    "SourceUrl": f"https://www.discogs.com/release/{rid}",
                })
    if progress:
        progress(f"Discogs: {len(rows)} pistes")
    return rows


def main() -> int:
    from . import ROW_FIELDS
    ap = argparse.ArgumentParser(description="Scrape Discogs wantlist/collection -> CSV")
    ap.add_argument("username")
    ap.add_argument("-o", "--output", default="discogs_wantlist.csv")
    ap.add_argument("--token", default="")
    ap.add_argument("--include-collection", action="store_true")
    ap.add_argument("--cache-dir", default="inputs/.discogs-cache")
    args = ap.parse_args()
    try:
        rows = scrape_discogs(args.username, args.token, args.include_collection,
                              args.cache_dir, progress=lambda m: print(m, file=sys.stderr))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} tracks to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
