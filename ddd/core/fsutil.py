"""Utilitaires fichier partages (deplacement anti-collision).

Extrait de `upgrade._deposit` pour etre reutilise par `organize` (le tri) sans
coupler les deux modules. `shutil.move` gere le cross-drive (copie+suppression si
le systeme de fichiers cible differe).
"""

from __future__ import annotations

import shutil
from pathlib import Path


def safe_move(src, dest_dir, *, dry_run: bool = False) -> Path:
    """Deplace `src` dans `dest_dir`, en suffixant ' (n)' si le nom est deja pris.

    Retourne le chemin de destination final (avec suffixe eventuel). `dry_run=True`
    calcule et retourne cette destination SANS rien creer ni deplacer (preview pur).
    Si `src` est deja a sa place (dest == src), ne touche a rien et retourne `src`.
    """
    src = Path(src)
    dest_dir = Path(dest_dir)
    dest = dest_dir / src.name
    i = 1
    while dest.exists() and dest.resolve() != src.resolve():
        dest = dest_dir / f"{src.stem} ({i}){src.suffix}"
        i += 1
    if not dry_run and dest.resolve() != src.resolve():
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    return dest
