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
