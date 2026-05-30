"""Scraper wishlist Bandcamp -> liste de pistes.

Refactor de lib/scrapers/bandcamp.py : logique exposee comme `scrape_bandcamp()`.
Pas d'auth (wishlists publiques). cloudscraper contourne FingerprintJS.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import cloudscraper
except ImportError:  # pragma: no cover
    cloudscraper = None

from bs4 import BeautifulSoup

BASE = "https://bandcamp.com"
ProgressCb = Optional[Callable[[str], None]]


def _make_scraper():
    if cloudscraper is None:
        raise RuntimeError("cloudscraper requis : pip install cloudscraper")
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True})


def _wishlist_blob(scraper, username: str):
    r = scraper.get(f"{BASE}/{username}/wishlist", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    blob = soup.find("div", id="pagedata")
    return json.loads(blob["data-blob"]) if blob else None


def _secs(x):
    if not x:
        return ""
    try:
        return int(round(float(x)))
    except (ValueError, TypeError):
        return ""


def _album_tracklist(scraper, album_url: str, cache_dir: Path):
    cache_file = cache_dir / f"{hashlib.md5(album_url.encode()).hexdigest()}.json"
    if cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    r = scraper.get(album_url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tralbum = None
    for script in soup.find_all("script"):
        if "data-tralbum" in str(script.attrs):
            tralbum = script.get("data-tralbum")
            break
    data = json.loads(tralbum) if tralbum else {}
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def scrape_bandcamp(
    username: str,
    expand_albums: bool = True,
    cache_dir: str = "inputs/.bandcamp-cache",
    progress: ProgressCb = None,
) -> List[Dict]:
    """Scrape la wishlist publique -> liste de rows (albums deplies en pistes)."""
    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    scraper = _make_scraper()
    if progress:
        progress(f"Bandcamp: wishlist de {username}...")
    blob = _wishlist_blob(scraper, username)
    if not blob:
        raise RuntimeError("impossible de charger la wishlist (blob absent)")

    fan_id = blob.get("fan_data", {}).get("fan_id")
    wishlist = blob.get("item_cache", {}).get("wishlist", {})
    cdata = blob.get("wishlist_data", {})
    sequence = cdata.get("sequence", [])
    last_token = cdata.get("last_token")

    rows: List[Dict] = []
    seen = set()

    def add_item(item):
        band = item.get("band_name", "")
        title = item.get("item_title", "")
        item_url = item.get("item_url", "")
        if not title:
            return
        key = (band.lower(), title.lower())
        if key in seen:
            return
        seen.add(key)
        if item.get("item_type") == "album" and expand_albums and item_url:
            try:
                data = _album_tracklist(scraper, item_url, cdir)
                for t in data.get("trackinfo", []):
                    t_title = t.get("title", "")
                    if not t_title:
                        continue
                    rows.append({
                        "Artist": band, "Title": t_title, "Album": title,
                        "Length": _secs(t.get("duration")), "Year": "",
                        "Source": "bandcamp:wishlist", "SourceUrl": item_url,
                    })
                return
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(f"  skip album {title}: {e}")
        rows.append({
            "Artist": band, "Title": title, "Album": "", "Length": "", "Year": "",
            "Source": "bandcamp:wishlist", "SourceUrl": item_url,
        })

    for item_id in sequence:
        item = wishlist.get(str(item_id))
        if item:
            add_item(item)

    if fan_id and last_token:
        api_url = f"{BASE}/api/fancollection/1/wishlist_items"
        while True:
            payload = {"fan_id": fan_id, "older_than_token": last_token, "count": 50}
            try:
                r = scraper.post(api_url, json=payload, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(f"  pagination stop: {e}")
                break
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                add_item(item)
            if progress:
                progress(f"  {len(rows)} pistes...")
            last_token = data.get("last_token")
            if not data.get("more_available"):
                break
            time.sleep(0.5)

    if progress:
        progress(f"Bandcamp: {len(rows)} pistes")
    return rows


def main() -> int:
    from . import ROW_FIELDS
    ap = argparse.ArgumentParser(description="Scrape Bandcamp wishlist -> CSV")
    ap.add_argument("username")
    ap.add_argument("-o", "--output", default="bandcamp_wishlist.csv")
    ap.add_argument("--no-expand-albums", action="store_true")
    ap.add_argument("--cache-dir", default="inputs/.bandcamp-cache")
    args = ap.parse_args()
    rows = scrape_bandcamp(args.username, not args.no_expand_albums, args.cache_dir,
                           progress=lambda m: print(m, file=sys.stderr))
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
