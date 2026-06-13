"""Audit nommage + tags d'une bibliotheque existante (mode index-free).

Sans `_index.csv` (ce qu'on a demande), la verite d'un fichier deja sur disque,
c'est son nom ET ses tags embarques. On verifie leur coherence :
  - le nom est-il parseable en "Artiste - Titre" ?
  - les tags ID3/Vorbis collent-ils au nom (couverture artist/title) ?
  - la version (remix/edit/...) du nom et des tags concordent-elles ?

Statuts : OK | NAME_ONLY (pas de tags a comparer) | TAG_MISMATCH | VERSION_MISMATCH
| UNPARSEABLE_NAME. C'est complementaire de quality.py (qui juge l'audio, pas le nom).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

from . import tokenize as tok
from .naming import parse_filename, read_tags

logger = logging.getLogger(__name__)

# Statuts d'audit nommage
OK = "OK"
NAME_ONLY = "NAME_ONLY"            # pas de tags -> on ne peut que constater le nom
TAG_MISMATCH = "TAG_MISMATCH"      # tags presents mais ne collent pas au nom
VERSION_MISMATCH = "VERSION_MISMATCH"
UNPARSEABLE = "UNPARSEABLE_NAME"

# Couverture tag minimale pour considerer qu'un tag "colle" au nom
TAG_OK_THRESHOLD = 0.5


@dataclass
class NameAudit:
    path: str
    filename: str
    name_artist: str
    name_title: str
    tag_artist: str
    tag_title: str
    artist_coverage: float     # recall tokens(nom artist) vs tokens(tag artist+title)
    title_coverage: float
    name_version: str
    tag_version: str
    status: str
    reason: str

    def as_dict(self) -> Dict:
        return asdict(self)


def audit_file(path) -> NameAudit:
    p = Path(path)
    parsed = parse_filename(p)
    tags = read_tags(p)
    tag_artist = tags.get("artist", "")
    tag_title = tags.get("title", "")

    name_ver = tok.version_key(parsed.title)
    tag_ver = tok.version_key(tag_title)

    base = NameAudit(
        path=str(p), filename=p.name,
        name_artist=parsed.artist, name_title=parsed.title,
        tag_artist=tag_artist, tag_title=tag_title,
        artist_coverage=-1.0, title_coverage=-1.0,
        name_version=name_ver, tag_version=tag_ver,
        status="", reason="",
    )

    if not parsed.parseable:
        base.status = UNPARSEABLE
        base.reason = "nom sans 'Artiste - Titre' exploitable"
        return base

    # Pas de tags -> on ne peut pas corroborer, juste constater le nom
    if not tag_artist and not tag_title:
        base.status = NAME_ONLY
        base.reason = "aucun tag artist/title a comparer"
        return base

    # Recall : tokens du nom presents dans les tags
    name_artist_tokens = tok.get_tokens(parsed.artist, min_len=2)
    name_title_tokens = tok.get_tokens(tok.remove_feat_tail(parsed.title))
    tag_tokens = set(tok.get_tokens(tag_artist, min_len=2)) | set(tok.get_tokens(tag_title))

    a_cov = tok.token_coverage(name_artist_tokens, tag_tokens)
    t_cov = tok.token_coverage(name_title_tokens, tag_tokens)
    base.artist_coverage = a_cov
    base.title_coverage = t_cov

    if name_ver != tag_ver:
        base.status = VERSION_MISMATCH
        base.reason = f"version nom='{name_ver or 'original'}' != tag='{tag_ver or 'original'}'"
        return base

    # Un tag "colle" si couverture >= seuil (ou non mesurable faute de tokens)
    a_ok = a_cov < 0 or a_cov >= TAG_OK_THRESHOLD
    t_ok = t_cov < 0 or t_cov >= TAG_OK_THRESHOLD
    if a_ok and t_ok:
        base.status = OK
        base.reason = "nom et tags coherents"
    else:
        base.status = TAG_MISMATCH
        bad = []
        if not a_ok:
            bad.append(f"artist {a_cov:.0%}")
        if not t_ok:
            bad.append(f"title {t_cov:.0%}")
        base.reason = "tags ne collent pas au nom (" + ", ".join(bad) + ")"
    return base


NAME_AUDIT_FIELDS = [
    "status", "filename", "name_artist", "name_title", "tag_artist", "tag_title",
    "artist_coverage", "title_coverage", "name_version", "tag_version", "reason", "path",
]
