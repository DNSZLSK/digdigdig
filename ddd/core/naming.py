"""Parsing du nom de fichier -> (artist, title) pour construire une want-list.

Port de la logique de convert-csv.ps1 (Clean : strip des codes label) et de
Split-Name de audit-staging.ps1 (split sur le PREMIER ' - '). Sert au mode
index-free : la "verite demandee" d'un fichier existant, c'est son propre nom.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from . import tokenize as tok

logger = logging.getLogger(__name__)

try:
    import mutagen
    _MUTAGEN = True
except ImportError:  # pragma: no cover
    _MUTAGEN = False

# Codes label/catalogue en crochets : [MELCURE010], [001], [KTLP001V1]
_LABEL_CODE = re.compile(r"\[[A-Za-z0-9]+\]")
# Numero de piste en tete : "02 - ", "A1. ", "11 - "
_TRACK_PREFIX = re.compile(r"^\s*[A-Da-d]?\d{1,2}[\.\)\-]\s+")
_WS = re.compile(r"\s+")


def clean(s: str) -> str:
    """Nettoie un fragment artist/title : retire codes label, espaces parasites."""
    if not s:
        return ""
    s = _LABEL_CODE.sub("", s)
    s = _WS.sub(" ", s).strip()
    # tirets/underscores parasites en bord (ex: "Mr Pauli _- Hydra")
    return s.strip(" -_")


@dataclass
class ParsedName:
    artist: str
    title: str
    parseable: bool      # True si on a pu isoler un artist ET un title
    raw_stem: str


def parse_filename(path) -> ParsedName:
    """Extrait (artist, title) d'un nom de fichier "Artiste - Titre.ext".

    Split sur le PREMIER ' - ' (les ' - ' suivants restent dans le titre, ex:
    "Fries & Bridges - Uprock - Ghetto Mix"). Sans separateur, pas d'artiste ->
    non parseable (trop risque pour une recherche stricte sur Soulseek).
    """
    stem = Path(path).stem
    stem_clean = _TRACK_PREFIX.sub("", stem)

    # separateur " - " (avec espaces) ; tolere variantes "_- " vues dans GAMOLKA
    sep = re.search(r"\s[-_]\s|\s-\s", stem_clean)
    if not sep:
        return ParsedName("", clean(stem_clean), False, stem)

    artist = clean(stem_clean[: sep.start()])
    title = clean(stem_clean[sep.end():])
    parseable = bool(artist and title)
    return ParsedName(artist, title, parseable, stem)


def match_key(artist: str, title: str) -> str:
    """Cle de correspondance stable entre la want-list et le retour sldl."""
    return f"{artist} - {title}".lower().strip()


# Artistes "compilation" : le vrai artiste est dans le titre, pas dans ce champ.
_VA_ARTISTS = {
    "various artists", "various artist", "various", "va", "v/a", "v.a.", "v a",
    "compilation", "compilations", "diverse", "verschiedene",
}
# Prefixe de face vinyle en tete de titre : A1, A2, B1, C12, D1... (lettre A-H + 1-2 chiffres)
_SIDE_PREFIX = re.compile(r"^\s*[A-H][0-9]{1,2}[\s.\)\-_]+", re.IGNORECASE)
_SEP = re.compile(r"\s[-_]\s|\s-\s")


def _light(s: str) -> str:
    """Strip leger (espaces + tirets/underscores de bord), sans toucher au contenu."""
    return _WS.sub(" ", s or "").strip().strip(" -_")


def normalize_artist_title(artist: str, title: str):
    """Normalise (artist, title) AVANT envoi a sldl pour eviter les requetes introuvables.

    1. Vire un prefixe de face vinyle en tete de titre ('A1 ildec - Voice' -> 'ildec - Voice').
    2. Artiste 'Various Artists'/'VA'/'Compilation' : le vrai artiste est dans le titre ->
       split sur le 1er ' - ' ('Various Artists' + 'Zumo - Iamthecomputer' -> 'Zumo' / 'Iamthecomputer').
    3. Artiste duplique en tete du titre -> dedupliquer ('ildec' + 'ildec - Voice' -> 'Voice').

    Idempotent : appliquer deux fois donne le meme resultat.
    """
    a = _light(artist)
    t = _SIDE_PREFIX.sub("", _light(title)).strip()

    if a.lower() in _VA_ARTISTS:
        m = _SEP.search(t)
        if m:
            a = _light(t[: m.start()])
            t = _SIDE_PREFIX.sub("", _light(t[m.end():])).strip()

    if a:                                   # artiste re-colle en tete du titre -> on l'enleve
        m = _SEP.search(t)
        if m and _light(t[: m.start()]).lower() == a.lower():
            t = _light(t[m.end():])

    return _light(a), _light(t)


# ---- Lecture des tags embarques (ID3/Vorbis/...) -----------------------------

def read_tags(path) -> Dict[str, str]:
    """Lit artist/title/album/genre via mutagen (uniforme tous formats). {} si rien.

    Vit ici (et plus dans audit.py) pour que le resolveur de nom puisse l'utiliser
    sans import circulaire (audit importe naming, jamais l'inverse).
    """
    if not _MUTAGEN:
        return {}
    try:
        mf = mutagen.File(str(path), easy=True)
    except Exception as e:  # noqa: BLE001
        logger.debug("mutagen fail %s: %r", path, e)
        return {}
    if mf is None or not getattr(mf, "tags", None):
        return {}

    def first(key: str) -> str:
        val = mf.tags.get(key) if mf.tags else None
        if not val:
            return ""
        return (val[0] if isinstance(val, (list, tuple)) else str(val)).strip()

    return {"artist": first("artist"), "title": first("title"),
            "album": first("album"), "genre": first("genre")}


# ---- Resolveur de nom : meilleur (artist, title) pour recherche + rename -----

# Marques invisibles (ZWSP/ZWNJ/ZWJ/LRM/RLM/BOM) qui parasitent les tags exportes
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u200e\u200f\ufeff]")
# Tirets unicode (hyphen .. horizontal bar, signe moins) -> hyphen ASCII
_UNI_DASH = re.compile("[\u2010-\u2015\u2212]")
# Caractere de remplacement (mojibake) isole entre espaces = un tiret separateur casse
_MOJIBAKE_DASH = re.compile("\\s\ufffd+\\s")
# Annee en fin de titre : "(1997)" / "(2005)"
_TRAIL_YEAR = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
# Prefixe "audiomack download" colle en tete de slug (am-dl-...)
_JUNK_SLUG_TOKENS = {"am", "dl"}
# Parentheses consecutives identiques : "(X Remix) (X Remix)" -> "(X Remix)"
_DUP_PAREN = re.compile(r"(\([^)]*\))(?:\s*\1)+", re.IGNORECASE)
# Crochets = label / numero de catalogue / annee : "[Circus Company, 2009]"
_BRACKETS = re.compile(r"\[[^\]]*\]")
# '*' de fin de titre (marqueur source unreleased/ID, pollue la recherche)
_TRAIL_STAR = re.compile(r"\s*\*+\s*$")


def _norm_dashes(s: str) -> str:
    """Normalise les tirets/marques unicode pour que le split ' - ' fonctionne."""
    if not s:
        return ""
    s = _ZERO_WIDTH.sub("", s)
    s = _UNI_DASH.sub("-", s)
    s = _MOJIBAKE_DASH.sub(" - ", s)
    return _WS.sub(" ", s).strip()


def _looks_slug(s: str) -> bool:
    """Vrai si le fragment ressemble a un slug ('am-dl-the-bar-dub') et pas a un vrai
    artiste/titre. Seuil a 3 tirets : laisse passer 'Jazz-N-Groove', 'Nae-Fix'."""
    s = (s or "").strip()
    return bool(s) and " " not in s and s.count("-") >= 3


def _strip_year(t: str) -> str:
    """Retire une annee finale entre parentheses, garde les autres parentheses (mix)."""
    return _TRAIL_YEAR.sub("", t or "").strip()


def _clean_title(t: str) -> str:
    """Titre depuis un tag : strip annee finale + dedoublonne les parentheses consecutives."""
    return _DUP_PAREN.sub(r"\1", _strip_year(t)).strip()


def search_title(t: str) -> str:
    """Titre nettoye pour la requete Soulseek : retire [label/catalogue], '*' de fin,
    annee finale, parentheses doublees. GARDE les (Original Mix)/(X Remix) (vraie version).

    A appliquer cote recherche (WantItem) PAS au rename : on garde '[Label, 2009]' dans
    le nom de fichier (metadonnee utile) mais on ne le balance pas a sldl (sinon 0 match).
    """
    t = _BRACKETS.sub("", t or "")
    t = _TRAIL_STAR.sub("", t)
    t = _strip_year(t)
    t = _DUP_PAREN.sub(r"\1", t)
    return _WS.sub(" ", t).strip()


# Mots "promo" qui polluent un nom de fichier sans etre une version musicale. On les vire
# a l'AFFICHAGE (prefixe colle a l'artiste, ou entre parentheses/crochets dans le titre).
# On NE touche PAS aux vraies versions : (Original Mix) / (X Remix) / (Extended) / (Dub)...
_PROMO_WORD = r"premi[eè]re|free\s*(?:download|dl)|out\s*now|teaser|preview|snippet|exclusive"
# Prefixe "Premiere_ ", "Premiere - ", "FREE DL: ", "Out Now | " colle en tete d'artiste.
_PROMO_PREFIX = re.compile(r"^\s*(?:" + _PROMO_WORD + r")\s*[_\-:|]+\s*", re.IGNORECASE)
# Mot promo entre parentheses/crochets dans le titre : "(Premiere)", "[Free DL]".
_PROMO_PAREN = re.compile(r"\s*[\(\[]\s*(?:" + _PROMO_WORD + r")\s*[\)\]]", re.IGNORECASE)
# Suffixe promo en fin de titre : "... #7 - Free download", "... - Out Now", "#4 - free dl".
_PROMO_SUFFIX = re.compile(
    r"\s*(?:#\d+\s*)?(?:[-|]\s*)?(?:" + _PROMO_WORD + r")\s*$", re.IGNORECASE)
# Marqueur "#N" isole en fin de titre (numero de piste compilation).
_TRAIL_HASHNUM = re.compile(r"\s*#\d+\s*$")
# Artiste qui n'est QUE le mot promo (forme "Premiere - Artist - Title") -> vrai couple
# dans le titre, a re-splitter.
_PROMO_BARE = {"premiere", "première", "free download", "free dl", "out now"}


def display_artist_title(artist: str, title: str):
    """(artist, title) nettoyes pour l'AFFICHAGE de la table GUI.

    - titre : retire [label/catalogue], annee finale, '*', et les mots promo entre
      parentheses ('(Premiere)') via search_title + _PROMO_PAREN ;
    - artiste : retire les [crochets] et un prefixe promo colle ('Premiere_ X' -> 'X') ;
    - cas 'Premiere - Artist - Title' : l'artiste = juste le mot promo -> on recupere le
      vrai couple en re-splittant le titre sur le 1er ' - '.

    Complementaire de search_title/normalize_artist_title (cote requete) : ici c'est
    purement cosmetique, on ne renvoie jamais une chaine vide si on peut l'eviter.
    """
    title = _PROMO_PAREN.sub("", search_title(title))
    title = _TRAIL_HASHNUM.sub("", _PROMO_SUFFIX.sub("", title))
    artist = _PROMO_PREFIX.sub("", _BRACKETS.sub("", artist or "")).strip(" -_")
    if (not artist) or artist.lower() in _PROMO_BARE:
        m = _SEP.search(title)
        if m:
            cand = _light(title[: m.start()])
            if cand and cand.lower() not in _PROMO_BARE:
                artist, title = cand, _light(title[m.end():])
    return _light(artist), _light(_WS.sub(" ", title))


def _deslug(stem: str) -> str:
    """Slug 'a-b-c' -> 'A B C' (titre-seul, artiste inconnu). Strip annee + junk 'am dl'."""
    s = _norm_dashes(stem)
    s = re.sub(r"\bcopie\b", "", s, flags=re.IGNORECASE)        # variantes "- Copie"
    parts = [p for p in re.split(r"[-_\s]+", s) if p]
    parts = [p for p in parts if not re.fullmatch(r"(?:19|20)\d{2}", p)]   # annees
    while parts and parts[0].lower() in _JUNK_SLUG_TOKENS:
        parts.pop(0)
    return " ".join(w.capitalize() for w in parts).strip()


@dataclass
class ResolvedName:
    artist: str
    title: str
    source: str        # name | title-tag | tags | name-title | deslug
    confident: bool    # True = sur de soi -> ok pour renommer le fichier sur disque


def resolve_name(path, tags: Optional[Dict[str, str]] = None) -> ResolvedName:
    """Meilleur (artist, title) pour un fichier, par cascade de sources fiables.

    1. NOM 'Artiste - Titre' propre (pas un slug).
    2. TAG titre contenant ' - ' : le vrai couple (cas mixtape ou artist tag = compilateur,
       ex: artist='Tibor Tury', title='John Kano - Havana Funk (1997)').
    3. TAGS artist+title propres (artist non-VA, titre recouvrant le nom de fichier).
    4. Deslugification du nom -> titre-seul (artiste inconnu), non fiable.

    `confident=False` -> a chercher mais jamais a renommer automatiquement.
    """
    stem = Path(path).stem
    parsed = parse_filename(path)

    # 1. NOM propre
    if parsed.parseable and not _looks_slug(parsed.artist) and not _looks_slug(parsed.title):
        a, t = normalize_artist_title(parsed.artist, parsed.title)
        if a and t:
            return ResolvedName(a, t, "name", True)

    if tags is None:
        tags = read_tags(path)
    t_artist = _norm_dashes(tags.get("artist", ""))
    t_title = _norm_dashes(tags.get("title", ""))

    # 2. TAG titre = 'Artiste - Titre' (cas mixtape : artist tag = compilateur)
    m = _SEP.search(t_title) if t_title else None
    if m:
        a, t = normalize_artist_title(_light(t_title[: m.start()]),
                                      _clean_title(_light(t_title[m.end():])))
        if a and t:
            return ResolvedName(a, t, "title-tag", True)

    # 3. TAGS propres -- fiable seulement si le NOM corrobore artiste ET titre. Sinon un
    #    tag auto faux (slug 'dj-assasins-...' tagge 'The Players Association', ou
    #    'the-origin-of-dance...' tagge 'Mandrill') passerait a tort -> on le marque
    #    non-fiable : cherchable, mais jamais renomme automatiquement.
    if t_artist and t_title and t_artist.lower() not in _VA_ARTISTS:
        a, t = normalize_artist_title(t_artist, _clean_title(t_title))
        if a and t:
            slug_tokens = tok.get_tokens(stem)
            title_cov = tok.token_coverage(tok.get_tokens(tok.remove_feat_tail(t)), slug_tokens)
            artist_cov = tok.token_coverage(tok.core_artist_tokens(a), slug_tokens)
            confident = (title_cov < 0 or title_cov >= 0.5) and artist_cov != 0
            return ResolvedName(a, t, "tags", confident)

    # 4. dernier recours : titre-seul depuis le nom (deslug si slug)
    if parsed.title and not _looks_slug(parsed.title):
        return ResolvedName("", _strip_year(parsed.title), "name-title", False)
    return ResolvedName("", _deslug(stem), "deslug", False)
