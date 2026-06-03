"""Scraper de tracklist de set DJ : une URL -> liste de pistes (Artist/Title/...).

But : tu files une URL de set (YouTube / 1001Tracklists), on ramasse le MAXIMUM de
tracks depuis les sources possibles, et on renvoie une want-list au format standard
(ROW_FIELDS) -> `ddd acquire` telecharge le reste comme d'habitude (Soulseek + re-audit).

Sources :
- YouTube : la description + les commentaires (yt-dlp), ou trainent souvent les
  tracklists "0:00 Artiste - Titre" (le commentaire epingle, surtout).
- 1001Tracklists : base communautaire, la plus fournie pour l'electronique
  (Cloudflare -> cloudscraper, comme bandcamp.py). Faisabilite a verifier en live.
Skip les "ID - ID" (tracks inconnus). Dedup sur lower(artist) - lower(title).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable, Dict, List, Optional, Tuple

try:
    import cloudscraper
except ImportError:  # pragma: no cover
    cloudscraper = None

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
ProgressCb = Optional[Callable[[str], None]]
Pair = Tuple[str, str]

# Timestamp en tete : "0:00", "[01:23]", "1:02:33" (option crochets/parentheses + sep)
_TS = re.compile(r"^\s*[\[(]?\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*[\])]?\s*[-.)]?\s*")
# Numero de piste en tete : "1." / "01)" / "12 -"
_NUM = re.compile(r"^\s*\d{1,3}\s*[.)\]:-]\s+")
_SEP = " - "
_TRIM = " -\t·•–—:"   # bullets/dashes/colons en bords


def _clean_line(line: str) -> str:
    """Enleve le timestamp et/ou le numero de piste en tete (2 passes : tout ordre)."""
    s = line.strip()
    for _ in range(2):
        s = _NUM.sub("", s)
        s = _TS.sub("", s)
    return s.strip(_TRIM)


def _is_id(s: str) -> bool:
    return s.lower().strip() in ("id", "id - id", "???", "unknown")


def _split_artist_title(s: str) -> Optional[Pair]:
    """'Artiste - Titre' -> (artist, title). None si pas parsable ou ID inconnu.

    On GARDE les "(Original Mix)" / "(X Remix)" dans le titre (utiles pour la recherche).
    """
    if _SEP not in s:
        return None
    artist, title = (p.strip() for p in s.split(_SEP, 1))
    if not artist or not title or _is_id(artist) or _is_id(title):
        return None
    if len(artist) > 120 or len(title) > 160:
        return None
    return artist, title


def parse_tracklist_text(text: str) -> List[Pair]:
    """Extrait les paires (artist, title) d'un bloc texte (description / commentaire)."""
    out: List[Pair] = []
    for raw in (text or "").splitlines():
        pair = _split_artist_title(_clean_line(raw))
        if pair:
            out.append(pair)
    return out


def _rows_from_pairs(pairs: List[Pair], source: str, url: str) -> List[Dict]:
    rows: List[Dict] = []
    seen = set()
    for artist, title in pairs:
        key = f"{artist.lower()} - {title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "Artist": artist, "Title": title, "Album": "", "Length": "",
            "Year": "", "Source": source, "SourceUrl": url,
        })
    return rows


# ---- YouTube (description + commentaires via yt-dlp) -------------------------

def _scrape_youtube(url: str, progress: ProgressCb) -> List[Pair]:
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("yt-dlp manquant : pip install yt-dlp") from e

    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30,
        "getcomments": True,
        # cap : ~80 commentaires top, pas de reponses (la tracklist est dans l'epingle/top)
        "extractor_args": {"youtube": {"max_comments": ["80", "all", "0"],
                                       "comment_sort": ["top"]}},
    }
    if progress:
        progress("YouTube : description + commentaires (yt-dlp)...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    pairs = parse_tracklist_text(info.get("description") or "")
    # commentaires : on garde celui qui ressemble le plus a une tracklist (le + de paires),
    # en privilegiant l'epingle.
    best: List[Pair] = []
    for c in (info.get("comments") or []):
        cp = parse_tracklist_text(c.get("text") or "")
        if c.get("is_pinned") and len(cp) >= 3:
            best = cp
            break
        if len(cp) > len(best):
            best = cp
    if len(best) > len(pairs):
        pairs = best
    return pairs


# ---- 1001Tracklists (cloudscraper, comme bandcamp.py) -----------------------

def _make_scraper():
    if cloudscraper is None:
        raise RuntimeError("cloudscraper requis : pip install cloudscraper")
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True})


def _scrape_1001(url: str, progress: ProgressCb) -> List[Pair]:
    if progress:
        progress("1001Tracklists : page (cloudscraper)...")
    scraper = _make_scraper()
    r = scraper.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    pairs: List[Pair] = []
    # 1001TL expose chaque piste en microdata : <meta itemprop="name" content="Artist - Title">
    for node in soup.select('meta[itemprop="name"]'):
        p = _split_artist_title((node.get("content") or "").strip())
        if p:
            pairs.append(p)
    # repli : selecteurs de texte de piste (la structure HTML bouge souvent)
    if not pairs:
        for sel in (".tlToggle", ".trackFormat", ".tlpTog", "span.trackValue"):
            for node in soup.select(sel):
                p = _split_artist_title(node.get_text(" ", strip=True))
                if p:
                    pairs.append(p)
            if pairs:
                break
    return pairs


# ---- set79 (JSON-LD schema.org MusicPlaylist, ouvert) -----------------------

_SET79_NAME = re.compile(r'"@type":\s*"MusicRecording",\s*"name":\s*"((?:[^"\\]|\\.)*)"')


def _scrape_set79(url: str, progress: ProgressCb) -> List[Pair]:
    if progress:
        progress("set79 : page (JSON-LD)...")
    scraper = _make_scraper()
    r = scraper.get(url, timeout=30)
    r.raise_for_status()
    # Le JSON-LD de set79 est souvent malforme (les slots de tracks non identifies sont
    # laisses vides -> json.loads casse). On extrait les noms de MusicRecording par regex
    # sur le texte brut (robuste), puis on decode les echappements JSON (\uXXXX, \").
    pairs: List[Pair] = []
    for m in _SET79_NAME.finditer(r.text):
        raw = m.group(1)
        try:
            name = json.loads(f'"{raw}"')
        except (ValueError, TypeError):
            name = raw
        p = _split_artist_title(name.strip())   # set79 = "Titre - Artiste", ordre sans
        if p:                                    # importance (DDD tokenise les 2 cotes)
            pairs.append(p)
    return pairs


# ---- Entry point ------------------------------------------------------------

def scrape_djset(url: str, progress: ProgressCb = None) -> List[Dict]:
    """URL de set -> want-list (ROW_FIELDS). Ramasse le max depuis les sources dispo."""
    url = (url or "").strip()
    low = url.lower()
    pairs: List[Pair] = []
    source = "djset"

    # Mode "coller" : un fichier texte de tracklist (ex : copie depuis 1001Tracklists,
    # lisible dans le navigateur meme si bloque au scrape). On parse le texte directement.
    if not low.startswith("http"):
        from pathlib import Path
        fp = Path(url)
        if fp.is_file():
            if progress:
                progress(f"Tracklist collee : {fp.name}")
            rows = _rows_from_pairs(
                parse_tracklist_text(fp.read_text(encoding="utf-8", errors="replace")),
                "djset:paste", url)
            if progress:
                progress(f"{len(rows)} piste(s) trouvee(s).")
            return rows

    if "youtube.com" in low or "youtu.be" in low:
        source = "djset:youtube"
        try:
            pairs = _scrape_youtube(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"YouTube echec : {e}")
    elif "1001tracklists.com" in low:
        source = "djset:1001"
        try:
            pairs = _scrape_1001(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"1001Tracklists echec (Cloudflare ?) : {e}")
    elif "set79.com" in low:
        source = "djset:set79"
        try:
            pairs = _scrape_set79(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"set79 echec : {e}")
    else:
        # URL inconnue : yt-dlp gere aussi SoundCloud/Mixcloud/etc., on tente.
        try:
            pairs = _scrape_youtube(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"extraction echec : {e}")

    rows = _rows_from_pairs(pairs, source, url)
    if progress:
        progress(f"{len(rows)} piste(s) trouvee(s).")
    return rows
