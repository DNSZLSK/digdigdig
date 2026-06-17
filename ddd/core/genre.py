"""Lookup de genre/style par 'Artiste - Titre' (Discogs puis MusicBrainz, avec cache).

Couche reseau pure : prend un (artiste, titre), renvoie les styles/genres poses par
des humains sur les bases. Discogs d'abord (Database Search : chaque resultat porte
deja `style[]`/`genre[]` -> 1 requete par track) ; MusicBrainz en repli (2 sauts,
~1 req/s, tags communautaires). Tout est cache sur disque (1 JSON par cle normalisee,
les MISS inclus) -> les re-runs sur un gros dossier ne retapent pas le reseau.

Le mapping style -> dossier de vibe vit dans `organize.py` (pur, sans reseau).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import quote, urlencode

import requests

from . import config
from .naming import match_key
from .scrapers.discogs import API as DISCOGS_API, http_get
from .tokenize import MIX_MARKER_RE, core_artist_tokens, get_tokens

logger = logging.getLogger(__name__)

DISCOGS = "discogs"
MUSICBRAINZ = "musicbrainz"
DEFAULT_SOURCES = (DISCOGS, MUSICBRAINZ)

PER_PAGE = 25        # resultats Discogs par requete
TOP_N = 8            # nb de resultats agreges pour deduire le style dominant
CACHE_VERSION = 2    # bump quand la requete/parsing change -> invalide l'ancien cache (re-lookup)

MB_API = "https://musicbrainz.org/ws/2"
# MusicBrainz EXIGE un User-Agent explicite avec un contact.
MB_UA = "ddd-digdigdig/0.2 (+https://github.com/DNSZLSK/digdigdig)"
MB_PAUSE_S = 1.0     # respect du rate-limit ~1 req/s entre les 2 sauts


@dataclass
class GenreResult:
    styles: List[str] = field(default_factory=list)   # fins, ex. ["Acid House", "Deep House"]
    genres: List[str] = field(default_factory=list)   # larges, ex. ["Electronic", "House"]
    source: str = ""        # "discogs" | "musicbrainz" | "" (miss)
    query: str = ""         # "artiste - titre" effectivement cherche

    @property
    def found(self) -> bool:
        return bool(self.styles or self.genres)


# ---- Cache disque (1 fichier par cle, miss inclus) --------------------------

def _cache_file(cache_dir, key: str) -> Path:
    # La cle brute ("artiste - titre") contient des caracteres interdits sous Windows
    # (* ? : " < > | /) -> on hashe pour le nom de fichier.
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{h}.json"


def _load_cache(cache_dir, key: str) -> Optional[GenreResult]:
    p = _cache_file(cache_dir, key)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if d.get("v") != CACHE_VERSION:        # ancienne version de requete -> re-lookup
        return None
    return GenreResult(styles=d.get("styles", []), genres=d.get("genres", []),
                       source=d.get("source", ""), query=d.get("query", ""))


def _store_cache(cache_dir, key: str, result: GenreResult) -> None:
    p = _cache_file(cache_dir, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "v": CACHE_VERSION,
            "styles": result.styles, "genres": result.genres,
            "source": result.source, "query": result.query,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        logger.warning("genre cache write failed %s: %r", p, e)


# ---- Discogs Database Search ------------------------------------------------

def _agg(results: Sequence[dict], field_name: str) -> List[str]:
    """Agrege un champ-liste (`style`/`genre`) sur des resultats, par frequence desc."""
    counts: Counter = Counter()
    for r in results:
        for v in (r.get(field_name) or []):
            counts[v] += 1
    return [v for v, _ in counts.most_common()]


# Nettoyage du titre pour la requete : on cherche le titre de BASE (sans version).
_DOMAIN_RE = re.compile(r"\b[\w-]+\.(?:com|net|org|pro|io|co|me|info)\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]")
_HASH_RE = re.compile(r"#.*$")
_TRAIL_MIX_RE = re.compile(r"(?i)\s*-?\s*(?:original|extended)\s+mix\s*$")
_WS_RE = re.compile(r"\s+")


def _query_title(title: str) -> str:
    """Titre de base pour la recherche Discogs : retire (Original Mix)/(X Remix)/(feat ...),
    junk domaine (heydj.pro), '#...', suffixe ' (1)', ' - Original Mix'. Pour le GENRE la
    version importe peu, et couper ces suffixes recupere beaucoup plus de releases (teste)."""
    t = _HASH_RE.sub(" ", _DOMAIN_RE.sub(" ", title or ""))
    prev = None
    while prev != t:                       # retire les groupes (...)/[...] meme imbriques
        prev = t
        t = _PAREN_RE.sub(" ", t)
    t = _TRAIL_MIX_RE.sub("", t)
    return _WS_RE.sub(" ", t).strip(" -_")


def _discogs_search(artist: str, title: str, token: str) -> Optional[GenreResult]:
    """1 requete Database Search -> styles agreges par frequence (filtre artiste + comps).

    Requete `artist=` + `q=<titre de base>` : le champ `track=` de Discogs est incomplet
    (beaucoup de releases ratent), `q` contraint par l'artiste en retrouve bien plus (teste
    sur l'_INBOX reel : Gene On Earth, D'Julz... passent de 0 a des hits propres).
    """
    q = _query_title(title)
    if not q:
        return None
    params = {"type": "release", "artist": artist, "q": q, "per_page": PER_PAGE}
    data = http_get(f"{DISCOGS_API}/database/search?{urlencode(params)}", token)
    results = data.get("results") or []
    if not results:
        return None

    # 1. Garde les resultats dont le titre ("Artiste - Release") contient l'artiste demande.
    a_tokens = set(core_artist_tokens(artist))

    def artist_ok(r: dict) -> bool:
        return (not a_tokens) or bool(a_tokens & set(get_tokens(r.get("title", ""))))

    pool = [r for r in results if artist_ok(r)] or results

    # 2. Demote les compilations / megamix (sauf s'il n'y a que ca).
    def is_comp(r: dict) -> bool:
        t = r.get("title", "") or ""
        return ("various" in t.lower()) or bool(MIX_MARKER_RE.search(t))

    pool = [r for r in pool if not is_comp(r)] or pool

    # 3. Style dominant par frequence sur le top-N ; sinon repli sur le genre (large).
    top = pool[:TOP_N]
    styles = _agg(top, "style")
    genres = _agg(top, "genre")
    if styles or genres:
        return GenreResult(styles=styles, genres=genres, source=DISCOGS,
                           query=f"{artist} - {title}")
    return None


# ---- MusicBrainz (repli) ----------------------------------------------------

def _mb_get(url: str, retries: int = 4):
    """GET MusicBrainz avec User-Agent obligatoire ; respect du 503 (rate-limit)."""
    headers = {"User-Agent": MB_UA, "Accept": "application/json"}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 503:                  # rate-limit -> on attend
            time.sleep(1 + attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"MusicBrainz: failed after {retries} tries: {url}")


def _mb_phrase(s: str) -> str:
    """Phrase Lucene sure : on neutralise les guillemets qui casseraient la requete."""
    return (s or "").replace('"', " ").strip()


def _musicbrainz_lookup(artist: str, title: str) -> Optional[GenreResult]:
    """Recherche le recording puis lit ses genres communautaires (repli inc=tags)."""
    q = f'recording:"{_mb_phrase(title)}"'
    if artist:
        q = f'artist:"{_mb_phrase(artist)}" AND ' + q
    data = _mb_get(f"{MB_API}/recording?query={quote(q)}&fmt=json&limit=5")
    recs = data.get("recordings") or []
    mbid = recs[0].get("id") if recs else None
    if not mbid:
        return None
    time.sleep(MB_PAUSE_S)
    g = _mb_get(f"{MB_API}/recording/{mbid}?inc=genres&fmt=json")
    genres = [x.get("name") for x in (g.get("genres") or []) if x.get("name")]
    if not genres:
        time.sleep(MB_PAUSE_S)
        g2 = _mb_get(f"{MB_API}/recording/{mbid}?inc=tags&fmt=json")
        genres = [t.get("name") for t in (g2.get("tags") or [])
                  if t.get("name") and (t.get("count") or 0) >= 1]
    if not genres:
        return None
    # MusicBrainz n'a pas de "style" facon Discogs -> on traite ses genres comme les deux,
    # pour que le mapping puisse matcher.
    return GenreResult(styles=list(genres), genres=list(genres), source=MUSICBRAINZ,
                       query=f"{artist} - {title}")


# ---- Orchestrateur ----------------------------------------------------------

def lookup_genre(
    artist: str,
    title: str,
    *,
    sources: Sequence[str] = DEFAULT_SOURCES,
    token: str = "",
    cache_dir=None,
) -> GenreResult:
    """Cherche styles/genres pour (artiste, titre) : cache -> sources dans l'ordre.

    Premiere source avec un resultat non vide gagne. Resultat (miss inclus) mis en cache.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        return GenreResult()
    key = match_key(artist, title)

    if cache_dir is not None:
        cached = _load_cache(cache_dir, key)
        if cached is not None:
            return cached

    if not token:
        token = os.environ.get("DISCOGS_TOKEN", "") or (config.get("discogs_token", "") or "")

    result = GenreResult(query=f"{artist} - {title}")
    errored = False
    for src in sources:
        try:
            if src == DISCOGS:
                if not token:        # pas de token -> Database Search renverrait 401, on saute
                    continue
                r = _discogs_search(artist, title, token)
            elif src == MUSICBRAINZ:
                r = _musicbrainz_lookup(artist, title)
            else:
                r = None
        except Exception as e:  # noqa: BLE001  (un lookup foireux ne doit jamais casser le tri)
            logger.warning("genre lookup via %s failed for '%s - %s': %r", src, artist, title, e)
            errored = True
            r = None
        if r and r.found:
            result = r
            break

    # Cache le resultat, MAIS jamais un miss du a une erreur reseau/429 : sinon un echec
    # transitoire empoisonne le cache en negatif et la piste ne sera plus jamais retrouvee.
    if cache_dir is not None and (result.found or not errored):
        _store_cache(cache_dir, key, result)
    return result
