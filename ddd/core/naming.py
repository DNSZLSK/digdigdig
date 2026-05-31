"""Parsing du nom de fichier -> (artist, title) pour construire une want-list.

Port de la logique de convert-csv.ps1 (Clean : strip des codes label) et de
Split-Name de audit-staging.ps1 (split sur le PREMIER ' - '). Sert au mode
index-free : la "verite demandee" d'un fichier existant, c'est son propre nom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
