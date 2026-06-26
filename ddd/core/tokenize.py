"""Tokenization + scoring de nommage. Port de lib/audit-staging.ps1.

Briques reutilisables pour mesurer si un fichier est bien nomme et si ses tags
collent a son nom : suppression des diacritiques, tokens significatifs, recall
(couverture), precision (mots en trop), signature de version.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Sequence

STOP_WORDS = {
    "the", "and", "feat", "with", "mix", "remix", "edit", "club", "original",
    "extended", "vocal", "version", "live", "premiere", "featuring", "main",
    "dub", "long", "short", "radio", "pres", "presents", "rmx", "ver", "vol",
}

# Mots de pur bruit dans un nom de fichier (format/source)
NOISE_WORDS = {
    "flac", "wav", "aiff", "mp3", "web", "vinyl", "cd", "ep", "lp", "kbps", "hz",
    "remaster", "remastered", "promo", "scene", "www", "com", "net", "org",
}

# Qualificateurs de version distinctifs (doivent matcher entre demande et fichier)
DISTINCTIVE_VER = [
    "remix", "rework", "rwk", "edit", "reedit", "redit", "refix", "flip",
    "extended", "radio", "club", "dub", "vocal", "instrumental", "inst",
    "acapella", "acappella", "acoustic", "live", "bootleg", "boot",
    "mashup", "vip", "demo",
]

VER_CANON = {
    "rmx": "remix", "reedit": "edit", "redit": "edit", "rwk": "rework",
    "inst": "instrumental", "acappella": "acapella", "boot": "bootleg",
}

MIX_MARKER_RE = re.compile(
    r"\b(megamix|mega mix|continuous|dj ?mix|mixtape|non ?stop|sampler|"
    r"compilation|full album|b2b|back to back|live set|essential mix|versus|podcast)\b",
    re.IGNORECASE,
)

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")
_FEAT_TAIL = re.compile(r"(?i)\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b.*$")
# Separateurs d'artistes (collab) : on ne garde que le 1er pour l'identite
_ARTIST_SPLIT = re.compile(
    r"(?i)\s*(?:,|&|/|\+|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b|\bx\b|\bwith\b|\bpres\.?\b)\s*")
# Parentheses/crochets (souvent un qualificatif de version : (Original Mix), [Label001])
_PAREN = re.compile(r"[\(\[\{].*?[\)\]\}]")


def remove_diacritics(s: str) -> str:
    if not s:
        return ""
    norm = unicodedata.normalize("NFD", s)
    out = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", out)


def get_tokens(s: str, min_len: int = 3) -> List[str]:
    """Tokens significatifs : sans accents, minuscules, sans ponctuation, sans stopwords."""
    if not s:
        return []
    s = remove_diacritics(s).lower()
    s = _NON_ALNUM.sub(" ", s)
    return [t for t in _WS.split(s) if len(t) >= min_len and t not in STOP_WORDS]


def token_coverage(requested: Sequence[str], found: Sequence[str]) -> float:
    """Recall : fraction des tokens demandes presents dans `found`. -1 si rien demande."""
    if not requested:
        return -1.0
    found_set = set(found)
    matched = sum(1 for r in requested if r in found_set)
    return round(matched / len(requested), 3)


def remove_feat_tail(s: str) -> str:
    if not s:
        return ""
    return _FEAT_TAIL.sub("", s).strip()


def primary_artist(artist: str) -> str:
    """Premier artiste avant tout separateur de collab (feat / & / , / x / vs / +)."""
    if not artist:
        return ""
    return _ARTIST_SPLIT.split(artist, maxsplit=1)[0].strip()


def core_artist_tokens(artist: str) -> List[str]:
    """Tokens identite de l'artiste : l'artiste principal seul (sans les feats)."""
    return get_tokens(primary_artist(artist))


def core_title_tokens(title: str) -> List[str]:
    """Tokens identite du titre : titre de base sans feat ni parenthese de version
    ((Original Mix), [Label001], (X Remix)...). Permet de reconnaitre un meme titre
    malgre les variantes de nommage entre la requete et le fichier partage."""
    return get_tokens(_PAREN.sub(" ", remove_feat_tail(title or "")))


def loose_tokens(s: str) -> List[str]:
    """Tokens permissifs : garde les chiffres et les mots < 3 lettres (vire juste les
    stopwords). Repli pour les titres tres courts ('2 ME' -> ['2', 'me']) ou get_tokens
    renvoie vide et laisserait l'identite non jugeable (fail-open)."""
    if not s:
        return []
    s = remove_diacritics(s).lower()
    s = _NON_ALNUM.sub(" ", s)
    return [t for t in _WS.split(s) if t and t not in STOP_WORDS]


def loose_title_tokens(title: str) -> List[str]:
    """core_title_tokens en mode loose (meme base sans feat/version, tokenizer permissif)."""
    return loose_tokens(_PAREN.sub(" ", remove_feat_tail(title or "")))


def is_noise_token(tok: str) -> bool:
    if re.fullmatch(r"\d+", tok):
        return True
    if len(tok) <= 2:
        return True
    return tok in NOISE_WORDS


def extra_words(file_tokens: Sequence[str], allowed_tokens: Sequence[str]) -> List[str]:
    """Mots du fichier non demandes et non-bruit (mesure de precision)."""
    allowed = set(allowed_tokens)
    return [t for t in file_tokens if t not in allowed and not is_noise_token(t)]


def version_key(title: str) -> str:
    """Signature de version normalisee. '' = original/main/album."""
    if not title:
        return ""
    t = remove_diacritics(title).lower()
    found: List[str] = []
    for v in DISTINCTIVE_VER:
        if re.search(rf"\b{re.escape(v)}\b", t):
            canon = VER_CANON.get(v, v)
            if canon not in found:
                found.append(canon)
    return "+".join(sorted(found))
