"""Corbeille reversible : on ne supprime JAMAIS en dur.

DDD jette des fichiers (faux lossless remplaces, candidats rejetes, restes de
migration). Tout part a la corbeille de l'OS via send2trash -> recuperable en un
clic. Si send2trash manque (env minimal), fallback : on DEPLACE vers un dossier
`_corbeille/` sous les donnees DDD (toujours reversible, jamais de unlink sec).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from send2trash import send2trash as _send2trash
except Exception:  # noqa: BLE001  (dep absente -> fallback dossier)
    _send2trash = None


def send_to_trash(path) -> bool:
    """Envoie `path` a la corbeille OS (ou au dossier _corbeille en fallback).

    Retourne True si quelque chose a ete fait. Ne leve pas : un echec de mise a la
    corbeille ne doit jamais casser un run (on log et on continue).
    """
    p = Path(path)
    if not p.exists():
        return False
    if _send2trash is not None:
        try:
            _send2trash(str(p))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("send2trash a echoue sur %s (%r) -> fallback _corbeille", p, e)

    # Fallback : deplacer vers <data>/_corbeille/<nom> (jamais d'unlink sec)
    try:
        from .. import paths
        bin_dir = paths.data_base() / "_corbeille"
        bin_dir.mkdir(parents=True, exist_ok=True)
        dest = bin_dir / p.name
        i = 1
        while dest.exists():           # evite d'ecraser dans la corbeille de secours
            dest = bin_dir / f"{p.stem} ({i}){p.suffix}"
            i += 1
        shutil.move(str(p), str(dest))
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("impossible de mettre a la corbeille %s : %r", p, e)
        return False
