"""
Bandcamp wishlist scraper.

Usage:
    python bandcamp.py <username> [-o OUTPUT] [--no-expand-albums]

The wishlist URL is https://bandcamp.com/<username>/wishlist (publicly accessible
unless the user has made it private). No auth needed.

How it works :
  1. GET the wishlist page, parse the `data-blob` JSON embedded in <div id="pagedata">.
     That gives us :
       - the first ~20-50 items
       - fan_id (needed for pagination)
       - last_token (cursor for the next batch)
  2. If more_available, POST to https://bandcamp.com/api/fancollector/1/wishlist_items
     with {"fan_id": ..., "older_than_token": ..., "count": 100} repeatedly until
     more_available == false.
  3. For each wishlist item :
       - If tralbum_type == 't' (track) : emit one row directly
       - If tralbum_type == 'a' (album) : fetch the album page and parse
         TralbumData JS to get the tracklist, emit one row per track
  4. Dedupe by (artist + " - " + title) lowercased.

Output : same CSV format as the Discogs scraper, so the pipeline can ingest
either source identically (Artist;Title;Album;Year;Source;SourceUrl).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

WISHLIST_URL = "https://bandcamp.com/{username}/wishlist"
WISHLIST_API = "https://bandcamp.com/api/fancollector/1/wishlist_items"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 searchseek/0.1"
)

# Regex to find TralbumData in album pages (set as a JS variable)
TRALBUM_RE = re.compile(r"var\s+TralbumData\s*=\s*(\{.*?\});", re.DOTALL)


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def fetch_initial_wishlist(session: requests.Session, username: str) -> dict[str, Any]:
    """GET wishlist page, parse data-blob to extract initial items + fan_id + token."""
    url = WISHLIST_URL.format(username=username)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Bandcamp puts the page state in <div id="pagedata" data-blob="JSON_HERE">
    pagedata = soup.find("div", id="pagedata")
    if not pagedata or not pagedata.get("data-blob"):
        raise RuntimeError(f"Could not find data-blob on {url} (page structure changed?)")
    blob = json.loads(pagedata["data-blob"])
    return blob


def extract_initial_items(blob: dict) -> tuple[list[dict], int | None, str | None, bool]:
    """Walk the blob to find (items, fan_id, last_token, more_available)."""
    # The shape differs slightly across Bandcamp page versions.
    # Try common paths.
    fan_id = None
    if "fan_data" in blob and "fan_id" in blob["fan_data"]:
        fan_id = blob["fan_data"]["fan_id"]
    elif "current_fan" in blob and "fan_id" in blob["current_fan"]:
        fan_id = blob["current_fan"]["fan_id"]

    wd = blob.get("wishlist_data") or blob.get("item_cache", {}).get("wishlist") or {}
    items = wd.get("items") or list((wd.get("sequence") or []) or [])
    last_token = wd.get("last_token") or wd.get("older_than_token")
    more = bool(wd.get("more_available", False))

    # Some versions put items in item_cache.wishlist as dict keyed by id
    if not items and isinstance(blob.get("item_cache", {}).get("wishlist"), dict):
        items = list(blob["item_cache"]["wishlist"].values())

    return items, fan_id, last_token, more


def fetch_more_wishlist(session: requests.Session, fan_id: int, token: str) -> dict:
    payload = {"fan_id": fan_id, "older_than_token": token, "count": 100}
    r = session.post(WISHLIST_API, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_album_tracklist(session: requests.Session, album_url: str, cache_dir: Path) -> list[dict]:
    """GET an album page, extract the tracklist via TralbumData regex."""
    cache_file = cache_dir / (re.sub(r'[^\w]', '_', album_url) + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    r = session.get(album_url, timeout=30)
    if r.status_code != 200:
        return []
    m = TRALBUM_RE.search(r.text)
    if not m:
        # Modern Bandcamp pages embed in data-tralbum attribute or window.TralbumData
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.find(attrs={"data-tralbum": True})
        if node:
            data = json.loads(node["data-tralbum"])
        else:
            return []
    else:
        # The regex grabs JS-style JSON which is not strict JSON; eval-friendly cleanup
        raw = m.group(1)
        # Remove trailing commas before } or ]
        raw = re.sub(r',(\s*[}\]])', r'\1', raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Last resort: try a permissive JS evaluator
            try:
                import ast
                data = ast.literal_eval(raw)
            except Exception:
                return []

    tracks = []
    artist = data.get("artist") or data.get("current", {}).get("artist") or ""
    album = data.get("current", {}).get("title", "")
    year_str = data.get("album_release_date") or data.get("current", {}).get("publish_date") or ""
    year_match = re.search(r"\b(19|20)\d{2}\b", str(year_str))
    year = year_match.group(0) if year_match else ""

    for tr in data.get("trackinfo", []) or []:
        title = tr.get("title", "").strip()
        if not title:
            continue
        tracks.append({
            "Artist": artist,
            "Title": title,
            "Album": album,
            "Year": year,
            "SourceUrl": album_url,
        })

    cache_file.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.5)
    return tracks


def item_to_track_row(item: dict) -> dict | None:
    """Build a row from a wishlist item that already represents a single track."""
    title = item.get("item_title") or item.get("title")
    artist = item.get("band_name") or item.get("artist")
    if not title or not artist:
        return None
    return {
        "Artist": artist,
        "Title": title,
        "Album": item.get("album_title") or "",
        "Year": "",
        "SourceUrl": item.get("item_url") or item.get("tralbum_url") or "",
    }


def main():
    ap = argparse.ArgumentParser(description="Scrape Bandcamp wishlist into CSV")
    ap.add_argument("username", help="Bandcamp username (the one in URL https://bandcamp.com/<USER>)")
    ap.add_argument("-o", "--output", default="outputs/bandcamp_wishlist.csv")
    ap.add_argument("--cache-dir", default="inputs/.bandcamp-cache")
    ap.add_argument("--no-expand-albums", action="store_true",
                    help="Keep albums as single rows instead of expanding into tracks")
    ap.add_argument("--max-items", type=int, default=0, help="Limit for testing")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = get_session()

    # 1. Fetch initial page + parse data-blob
    print(f"Fetching wishlist page for {args.username}...")
    blob = fetch_initial_wishlist(session, args.username)
    items, fan_id, token, more = extract_initial_items(blob)
    print(f"  initial : {len(items)} items, fan_id={fan_id}, more_available={more}")

    # 2. Paginate via internal API
    while more and fan_id and token and (args.max_items == 0 or len(items) < args.max_items):
        time.sleep(0.8)
        data = fetch_more_wishlist(session, fan_id, token)
        new_items = data.get("items") or []
        items.extend(new_items)
        token = data.get("last_token") or data.get("older_than_token")
        more = bool(data.get("more_available", False))
        print(f"  +{len(new_items)} items (total {len(items)})")

    if args.max_items > 0:
        items = items[: args.max_items]
        print(f"Limited to {len(items)} items")

    # 3. Expand albums into tracks (or keep as-is)
    rows: list[dict] = []
    for i, item in enumerate(items, 1):
        is_track = item.get("tralbum_type") == "t" or item.get("item_type") == "track"
        if is_track or args.no_expand_albums:
            r = item_to_track_row(item)
            if r:
                r["Source"] = "bandcamp:wishlist"
                rows.append(r)
        else:
            url = item.get("item_url") or item.get("tralbum_url")
            if not url:
                continue
            tracks = fetch_album_tracklist(session, url, cache_dir)
            for t in tracks:
                t["Source"] = "bandcamp:wishlist"
                rows.append(t)
            if i % 5 == 0 or i == len(items):
                print(f"  [{i}/{len(items)}] {item.get('band_name','?')} - {item.get('item_title','?')} : total tracks {len(rows)}")

    # 4. Dedupe
    seen: dict[str, dict] = {}
    for r in rows:
        key = (r["Artist"].lower().strip() + " - " + r["Title"].lower().strip())
        if key not in seen:
            seen[key] = r
    deduped = list(seen.values())
    if len(deduped) < len(rows):
        print(f"Deduplicated : {len(rows)} -> {len(deduped)}")

    if not deduped:
        print("No tracks to write", file=sys.stderr)
        sys.exit(1)

    fields = ["Artist", "Title", "Album", "Year", "Source", "SourceUrl"]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in deduped:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"Wrote {len(deduped)} unique tracks to {out_path}")


if __name__ == "__main__":
    main()
