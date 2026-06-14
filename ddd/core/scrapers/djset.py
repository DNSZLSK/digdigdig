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
import urllib.request
from urllib.parse import parse_qs, urlparse
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
# Timestamp "humain" en tete : "45min", "1h05", "90 sec", "1h05min30s", "5m30s".
# Sans ca, "45min - Frank De Wulf - Compression" donne artist="min". Garde-fou (?![a-z]) :
# l'unite ne doit PAS etre suivie d'une lettre -> ne mange pas "808 State", "4 Hero", "1 hour".
_TS_HUMAN = re.compile(
    r"^\s*[\[(]?\s*"
    r"\d{1,3}\s*(?:h|min|sec|m|s)(?![a-z])"               # 1er <nombre><unite> obligatoire
    r"(?:\s*\d{1,3}\s*(?:h|min|sec|m|s)(?![a-z]))*"       # <nombre><unite> suivants (1h05min30s)
    r"(?:\s*\d{1,2})?"                                    # nombre nu final colle (1h05, 5min30)
    r"\s*[\])]?\s*[-.)]?\s*",
    re.IGNORECASE,
)
# Numero de piste en tete : "1." / "01)" / "12 -"
_NUM = re.compile(r"^\s*\d{1,3}\s*[.)\]:-]\s+")
_SEP = " - "
# Une ligne avec une URL (lien social/promo "►Follow X - https://...") n'est jamais une track
_URL = re.compile(r"https?://|www\.|\.(?:com|net|org|io|tv|fm|co|me|ly|gl)\b", re.IGNORECASE)
_TRIM = " -\t·•–—:"   # bullets/dashes/colons en bords


def _clean_line(line: str) -> str:
    """Enleve le timestamp et/ou le numero de piste en tete (2 passes : tout ordre)."""
    s = line.strip()
    for _ in range(2):
        s = _NUM.sub("", s)
        s = _TS.sub("", s)
        s = _TS_HUMAN.sub("", s)
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
        if _URL.search(raw):        # ligne avec une URL (lien social/promo) -> jamais une track
            continue
        pair = _split_artist_title(_clean_line(raw))
        if pair:
            out.append(pair)
    return out


_BRACKETS = re.compile(r"\[[^\]]*\]")          # [numero de catalogue] / [label]
_TRAIL_STAR = re.compile(r"\s*\*+\s*$")        # * en fin de titre (marqueur source unreleased/ID)
_DUP_PAREN = re.compile(r"(\([^)]*\))(?:\s*\1)+", re.IGNORECASE)  # "(X) (X)" -> "(X)"
_ANY_YEAR = re.compile(r"\s*\((?:19|20)\d{2}\)")        # "(1996)" debut/milieu/fin -> pollue la recherche
_TRAIL_LABEL = re.compile(r"\s*\(([^()]+)\)\s*$")       # un (groupe) en fin = candidat label
# Si le (groupe) final contient un de ces mots, c'est une vraie version -> on GARDE.
_VERSION_WORDS = {
    "mix", "remix", "edit", "edited", "edits", "reedit", "dub", "rework", "refix", "rerub", "rub", "flip", "vip",
    "version", "vocal", "instrumental", "acapella", "bootleg", "mashup", "extended",
    "radio", "original", "club", "live", "demo", "acoustic", "reprise", "remaster",
    "remastered", "remake", "interlude", "intro", "outro", "skit", "part", "pt",
    "feat", "ft", "featuring", "pres", "presents", "beats", "beat", "take", "session",
    "edition", "cut", "rmx", "rmx.", "remixes",
}


def _strip_trailing_label(s: str) -> str:
    """Vire un label en fin entre parentheses ('Last (BASENOTIC)' -> 'Last'), mais GARDE
    une vraie version ('(Original Mix)', '(Sax Mix)'). Prudent : on ne strip qu'un (mot)
    SEUL, alphabetique, qui n'est pas un mot de version ni un chiffre romain (= nom propre
    de label type Basenotic/Wiggle, pas '(Part II)' ni '(Remix)')."""
    m = _TRAIL_LABEL.search(s)
    if not m:
        return s
    inner = m.group(1).strip()
    if (" " not in inner and inner.lower() not in _VERSION_WORDS
            and re.fullmatch(r"[A-Za-z][A-Za-z'&.\-]*", inner)
            and not re.fullmatch(r"(?i)[ivxlcdm]+", inner)):
        head = s[:m.start()].rstrip()
        if head:                            # ne jamais tout vider
            return head
    return s


def _titlecase_if_lower(s: str) -> str:
    """Majuscule en tete de mot SEULEMENT si le champ est tout en minuscules
    ('tiko - the mood' -> 'Tiko - The Mood'). Si la source a deja casse, on respecte."""
    if s and any(c.isalpha() for c in s) and s == s.lower():
        return " ".join(w[:1].upper() + w[1:] if w else w for w in s.split(" "))
    return s


def _strip_catalog(s: str) -> str:
    """Nettoie un champ pour la recherche Soulseek + la dedup :
    - vire les [...] (numero de catalogue / label) ;
    - vire les * en fin de titre (marqueur source = unreleased / ID, pollue la recherche) ;
    - dedoublonne les parentheses consecutives identiques ("(X Remix) (X Remix)" -> "(X Remix)",
      sinon la dedup ne fusionne pas avec la version simple).
    On GARDE un seul (Original Mix)/(Remix) (vraie info de version)."""
    s = _BRACKETS.sub("", s)
    s = _DUP_PAREN.sub(r"\1", s)
    s = _TRAIL_STAR.sub("", s)
    s = _ANY_YEAR.sub("", s)                # "(1996)" debut/milieu/fin (titres YouTube/sets) -> hors recherche
    s = _strip_trailing_label(s)            # "Last (BASENOTIC)" -> "Last" (garde "(Original Mix)")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return _titlecase_if_lower(s)           # "tiko - the mood" -> "Tiko - The Mood"


def _rows_from_pairs(pairs: List[Pair], source: str, url: str) -> List[Dict]:
    rows: List[Dict] = []
    seen = set()
    for artist, title in pairs:
        artist = _strip_catalog(artist)
        title = _strip_catalog(title)         # vire les [catalogue] -> recherche propre +
        if not artist or not title:           # dedup correcte (clean == avec-catalogue)
            continue
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

def _find_all(obj, key: str, out: list) -> None:
    """Collecte recursivement toutes les valeurs de `key` dans un JSON imbrique."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            _find_all(v, key, out)
    elif isinstance(obj, list):
        for it in obj:
            _find_all(it, key, out)


def _youtube_music_section(video_url: str) -> List[Pair]:
    """Section 'Musique' (Content ID de YouTube) -> [(artist, title)].

    Pas dans l'info dict yt-dlp : on lit `ytInitialData` de la page et on extrait les
    cartes `videoAttributeViewModel` (title = titre, subtitle = artiste). C'est la base
    de fingerprint de YouTube : souvent des tracks que ni la description ni set79 n'ont.
    """
    req = urllib.request.Request(video_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    idx = html.find("ytInitialData = ")
    if idx < 0:
        idx = html.find("ytInitialData=")
    if idx < 0:
        return []
    start = html.find("{", idx)
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start:])
    except (ValueError, TypeError):
        return []
    cards: list = []
    _find_all(data, "videoAttributeViewModel", cards)
    pairs: List[Pair] = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        artist = (c.get("subtitle") or "").strip()
        title = (c.get("title") or "").strip()
        if artist and title and not _is_id(artist) and not _is_id(title):
            pairs.append((artist, title))
    return pairs


def _scrape_youtube(url: str, progress: ProgressCb) -> List[Pair]:
    """3 couches YouTube combinees : description + section Musique (Content ID) + commentaires."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("yt-dlp missing: pip install yt-dlp") from e

    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30,
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": ["80", "all", "0"],
                                       "comment_sort": ["top"]}},
    }
    if progress:
        progress("YouTube: description + Music + comments...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    desc = parse_tracklist_text(info.get("description") or "")
    # commentaire qui ressemble le plus a une tracklist ; il faut >= 4 lignes parsees pour
    # que ca compte (sinon un commentaire bavard avec un " - " passe pour une track = bruit).
    best: List[Pair] = []
    for c in (info.get("comments") or []):
        cp = parse_tracklist_text(c.get("text") or "")
        if c.get("is_pinned") and len(cp) >= 4:
            best = cp
            break
        if len(cp) > len(best):
            best = cp
    if len(best) < 4:
        best = []
    # section Musique (Content ID) : la grosse source manquante, via la page
    try:
        music = _youtube_music_section(url)
    except Exception as e:  # noqa: BLE001
        music = []
        logger.debug("section Musique echec: %r", e)
    if progress:
        progress(f"  -> {len(music)} Music + {len(desc)} description + {len(best)} comment")
    return music + desc + best   # le dedup se fait dans _rows_from_pairs


# ---- YouTube playlist (chaque video = un track, titre = "Artiste - Titre") ---

# Bruit de titre YouTube a virer AVANT le split. On GARDE (Original Mix)/(X Remix).
_YT_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"official\s*(?:music\s*)?video|official\s*audio|official\s*visualizer|official"
    r"|lyric\s*video|lyrics?|visuali[sz]er|audio|video|hd|4k|hq|m/?v"
    r"|premiere|out\s*now|free\s*(?:download|dl)|teaser|preview"
    r")\s*[\)\]]",
    re.IGNORECASE,
)


def _playlist_id(url: str) -> Optional[str]:
    """ID de playlist depuis ?list=... (sauf mix/radio auto 'RD...'). None sinon."""
    try:
        lid = (parse_qs(urlparse(url).query).get("list") or [""])[0].strip()
    except ValueError:
        return None
    if not lid or lid.upper().startswith("RD"):   # RD... = mix/radio auto-genere
        return None
    return lid


def _clean_video_title(title: str) -> str:
    """Titre de video YouTube -> propre pour le split : vire le bruit (Official Video,
    Lyric Video, | Channel, Free DL...). Garde les (Original Mix)/(X Remix)."""
    s = _YT_NOISE.sub("", title or "")
    head = s.split("|", 1)[0]            # segment apres '|' = label/chaine, vire si le debut
    if _SEP in head:                     # garde encore 'Artiste - Titre'
        s = head
    return re.sub(r"\s{2,}", " ", s).strip()


def _pairs_from_entries(entries) -> List[Pair]:
    """Entrees flat yt-dlp (playlist) -> [(artist, title)]. Pur, testable sans reseau."""
    pairs: List[Pair] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip()
        if not title:
            continue
        p = _split_artist_title(_clean_video_title(_clean_line(title)))
        if p:
            pairs.append(p)
    return pairs


# yt-dlp ne pagine plus le nouveau format de page playlist YouTube (il s'arrete a la
# 1re page ~100). On lit ytInitialData (1re page) puis on suit les tokens de continuation
# via l'API interne (InnerTube), comme _youtube_music_section. yt-dlp reste un repli.
_INNERTUBE_KEY = re.compile(r'"INNERTUBE_API_KEY":"([^"]+)"')
_INNERTUBE_VER = re.compile(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _yt_get(url: str, data=None) -> str:
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def _playlist_video_titles(node) -> List[str]:
    """Titres de video d'une page playlist : ancien `playlistVideoRenderer` + nouveau `lockupViewModel`."""
    titles: List[str] = []
    pv: list = []
    _find_all(node, "playlistVideoRenderer", pv)
    for r in pv:
        if not isinstance(r, dict):
            continue
        t = (((r.get("title") or {}).get("runs") or [{}])[0].get("text")
             or (r.get("title") or {}).get("simpleText"))
        if t:
            titles.append(t)
    lv: list = []
    _find_all(node, "lockupViewModel", lv)
    for r in lv:
        meta: list = []
        _find_all(r, "lockupMetadataViewModel", meta)
        for m in meta:
            t = (m.get("title") or {}).get("content") if isinstance(m, dict) else None
            if t:
                titles.append(t)
    return titles


def _continuation_token(node) -> Optional[str]:
    out: list = []
    _find_all(node, "continuationCommand", out)
    for c in out:
        if isinstance(c, dict) and c.get("token"):
            return c["token"]
    return None


def _youtube_playlist_titles(list_id: str, progress: ProgressCb, max_pages: int = 80) -> List[str]:
    """TOUS les titres de video d'une playlist via l'API interne YouTube (pagination complete)."""
    html = _yt_get(f"https://www.youtube.com/playlist?list={list_id}")
    mkey, idx = _INNERTUBE_KEY.search(html), html.find("ytInitialData")
    if not mkey or idx < 0:
        return []
    mver = _INNERTUBE_VER.search(html)
    key, ver = mkey.group(1), (mver.group(1) if mver else "2.20240101.00.00")
    data, _ = json.JSONDecoder().raw_decode(html[html.find("{", idx):])

    titles = _playlist_video_titles(data)
    token, pages = _continuation_token(data), 0
    while token and pages < max_pages:
        body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": ver}},
                           "continuation": token}).encode()
        try:
            resp = json.loads(_yt_get(f"https://www.youtube.com/youtubei/v1/browse?key={key}", body))
        except Exception as e:  # noqa: BLE001
            logger.debug("continuation playlist p%d echec: %r", pages, e)
            break
        new = _playlist_video_titles(resp)
        titles += new
        token, pages = _continuation_token(resp), pages + 1
        if progress and pages % 3 == 0:
            progress(f"  playlist: {len(titles)} videos...")
        if not new:
            break
    return titles


def _scrape_youtube_playlist(list_id: str, progress: ProgressCb) -> List[Pair]:
    """Playlist YouTube : chaque video = un track. InnerTube (pagine TOUT), repli yt-dlp."""
    if progress:
        progress("YouTube playlist: extracting videos...")
    try:
        titles = _youtube_playlist_titles(list_id, progress)
    except Exception as e:  # noqa: BLE001
        titles = []
        logger.debug("playlist InnerTube echec: %r", e)
    if not titles:                          # repli : yt-dlp (ne pagine plus -> souvent 1re page)
        pairs = _scrape_youtube_playlist_ytdlp(list_id, progress)
        if not pairs and progress:          # rien des 2 cotes -> dire POURQUOI, pas juste "0 found"
            progress("  -> 0 video: the playlist may be private, unlisted (the link is "
                     "required), empty, or the URL is wrong.")
        return pairs
    pairs = _pairs_from_entries([{"title": t} for t in titles])
    if progress:
        progress(f"  -> {len(titles)} videos, {len(pairs)} parsed as Artist - Title")
    return pairs


def _scrape_youtube_playlist_ytdlp(list_id: str, progress: ProgressCb) -> List[Pair]:
    """Repli yt-dlp flat (ne pagine plus le nouveau format -> souvent la 1re page seulement)."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        return []
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30,
            "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)
    return _pairs_from_entries(info.get("entries") or [])


# ---- 1001Tracklists (cloudscraper, comme bandcamp.py) -----------------------

def _make_scraper():
    if cloudscraper is None:
        raise RuntimeError("cloudscraper required: pip install cloudscraper")
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True})


def _scrape_1001(url: str, progress: ProgressCb) -> List[Pair]:
    if progress:
        progress("1001Tracklists: page (cloudscraper)...")
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
        progress("set79: page (JSON-LD)...")
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
                progress(f"Pasted tracklist: {fp.name}")
            rows = _rows_from_pairs(
                parse_tracklist_text(fp.read_text(encoding="utf-8", errors="replace")),
                "djset:paste", url)
            if progress:
                progress(f"{len(rows)} track(s) found.")
            return rows

    if "youtube.com" in low or "youtu.be" in low:
        list_id = _playlist_id(url)
        if list_id:                              # playlist : chaque video = un track
            source = "djset:youtube-playlist"
            try:
                pairs = _scrape_youtube_playlist(list_id, progress)
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(f"YouTube playlist failed: {e}")
        else:                                    # set : 1 video, tracklist desc/commentaires/ContentID
            source = "djset:youtube"
            try:
                pairs = _scrape_youtube(url, progress)
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(f"YouTube failed: {e}")
    elif "1001tracklists.com" in low:
        source = "djset:1001"
        try:
            pairs = _scrape_1001(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"1001Tracklists failed (Cloudflare?): {e}")
    elif "set79.com" in low:
        source = "djset:set79"
        try:
            pairs = _scrape_set79(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"set79 failed: {e}")
    else:
        # URL inconnue : yt-dlp gere aussi SoundCloud/Mixcloud/etc., on tente.
        try:
            pairs = _scrape_youtube(url, progress)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"extraction failed: {e}")

    rows = _rows_from_pairs(pairs, source, url)
    if progress:
        progress(f"{len(rows)} track(s) found.")
    return rows
