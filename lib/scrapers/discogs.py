"""
Discogs wantlist/collection scraper.

Usage:
    python discogs.py <username> [--token TOKEN] [--include-collection] [-o OUTPUT]

Token : create one at https://www.discogs.com/settings/developers
        (free, no app registration needed for personal use)
        Pass via --token or set $env:DISCOGS_TOKEN

Output : CSV with columns Artist;Title;Album;Year;Source;SourceUrl
         Compatible with sldl --input-type csv (uses Artist + Title cols).

Notes :
  - Wantlist is paginated (100 per page).
  - For each release, we fetch the tracklist (1 extra API call per release).
  - Rate limit : 60 req/min authenticated. We sleep 1.1s between calls.
  - Resumable : a cache of fetched releases is kept in inputs/.discogs-cache/
    so re-runs only fetch new releases.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

DISCOGS_BASE = "https://api.discogs.com"
USER_AGENT = "searchseek/0.1 +local"


def discogs_get(url: str, headers: dict, params: dict | None = None) -> dict[str, Any]:
    """GET with retry on rate-limit (429)."""
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            print(f"  rate-limited, sleeping {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Too many rate-limits for {url}")


def fetch_paginated(url: str, headers: dict, key: str) -> list[dict]:
    """Fetch all pages of a paginated endpoint, returning items under `key`."""
    items: list[dict] = []
    params: dict | None = {"per_page": 100, "page": 1}
    while url:
        data = discogs_get(url, headers, params)
        items.extend(data.get(key, []))
        next_url = data.get("pagination", {}).get("urls", {}).get("next")
        url = next_url
        params = None  # next URL already has params
        time.sleep(1.1)
    return items


def fetch_release(release_id: int, headers: dict, cache_dir: Path) -> dict:
    """Fetch a single release with tracklist, with local cache."""
    cache_file = cache_dir / f"{release_id}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    data = discogs_get(f"{DISCOGS_BASE}/releases/{release_id}", headers)
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(1.1)
    return data


def join_artists(artists: list[dict]) -> str:
    """Discogs artists have name + join (e.g. 'feat.', '&'). Reconstruct cleanly."""
    parts = []
    for a in artists or []:
        name = a.get("name", "")
        # Strip numeric disambiguation like "Various (2)"
        if name.endswith(")") and " (" in name:
            name = name.rsplit(" (", 1)[0]
        parts.append(name)
        join = a.get("join", "").strip()
        if join and join != ",":
            parts.append(join)
    return " ".join(p.strip() for p in parts if p).strip()


def expand_tracklist(release: dict, source_url: str) -> list[dict]:
    """Walk the tracklist, returning canonical rows. Skip headings/index tracks."""
    rows = []
    album = release.get("title", "")
    year = str(release.get("year", "")) if release.get("year") else ""
    release_artist = join_artists(release.get("artists", []))
    for tr in release.get("tracklist", []):
        # Skip non-track entries (headings, index tracks)
        if tr.get("type_") and tr.get("type_") not in ("track",):
            continue
        title = tr.get("title", "").strip()
        if not title:
            continue
        # Track may override artists (compilations)
        track_artist = join_artists(tr.get("artists", []))
        artist = track_artist if track_artist else release_artist
        if not artist:
            continue
        rows.append({
            "Artist": artist,
            "Title": title,
            "Album": album,
            "Year": year,
            "Source": "discogs:wantlist",
            "SourceUrl": source_url,
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="Scrape Discogs wantlist into CSV")
    ap.add_argument("username", help="Your Discogs username")
    ap.add_argument("--token", default=os.environ.get("DISCOGS_TOKEN"),
                    help="Discogs personal access token (or set $env:DISCOGS_TOKEN)")
    ap.add_argument("--include-collection", action="store_true",
                    help="Also fetch your collection (folder 0 = all)")
    ap.add_argument("-o", "--output", default="outputs/discogs_wantlist.csv",
                    help="Output CSV path")
    ap.add_argument("--cache-dir", default="inputs/.discogs-cache",
                    help="Where to cache release JSON")
    ap.add_argument("--max-releases", type=int, default=0,
                    help="Limit number of releases (for testing, 0=all)")
    args = ap.parse_args()

    if not args.token:
        ap.error("Discogs token required (--token or $env:DISCOGS_TOKEN)")

    headers = {
        "Authorization": f"Discogs token={args.token}",
        "User-Agent": USER_AGENT,
    }

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Fetch wantlist
    print(f"Fetching wantlist of {args.username}...")
    wants_url = f"{DISCOGS_BASE}/users/{args.username}/wants"
    wants = fetch_paginated(wants_url, headers, "wants")
    print(f"  {len(wants)} releases in wantlist")

    if args.include_collection:
        print(f"Fetching collection of {args.username}...")
        coll_url = f"{DISCOGS_BASE}/users/{args.username}/collection/folders/0/releases"
        coll = fetch_paginated(coll_url, headers, "releases")
        print(f"  {len(coll)} releases in collection")
        wants.extend(coll)

    if args.max_releases > 0:
        wants = wants[: args.max_releases]
        print(f"Limited to {len(wants)} releases")

    # 2. For each, fetch tracklist + expand
    all_rows = []
    for i, w in enumerate(wants, 1):
        rid = w.get("id")
        if not rid:
            continue
        source_url = f"https://www.discogs.com/release/{rid}"
        try:
            release = fetch_release(rid, headers, cache_dir)
        except Exception as e:
            print(f"  [{i}/{len(wants)}] FAIL release {rid} : {e}", file=sys.stderr)
            continue
        rows = expand_tracklist(release, source_url)
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(wants):
            print(f"  [{i}/{len(wants)}] {release.get('title','?')} : +{len(rows)} tracks (total {len(all_rows)})")

    # 3. Dedupe (artist+title key, case-insensitive)
    seen: dict[str, dict] = {}
    for row in all_rows:
        key = (row["Artist"].lower().strip() + " - " + row["Title"].lower().strip())
        if key not in seen:
            seen[key] = row
    deduped = list(seen.values())
    if len(deduped) < len(all_rows):
        print(f"Deduplicated : {len(all_rows)} -> {len(deduped)}")

    # 4. Write CSV
    if not deduped:
        print("No tracks to write", file=sys.stderr)
        sys.exit(1)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(deduped[0].keys()))
        writer.writeheader()
        writer.writerows(deduped)
    print(f"Wrote {len(deduped)} unique tracks to {out_path}")


if __name__ == "__main__":
    main()
