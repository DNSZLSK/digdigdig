"""Utilitaires fichier partages (deplacement anti-collision).

Extrait de `upgrade._deposit` pour etre reutilise par `organize` (le tri) sans
coupler les deux modules. `shutil.move` gere le cross-drive (copie+suppression si
le systeme de fichiers cible differe).
"""

from __future__ import annotations

import shutil
import os
from pathlib import Path

from .content import file_hash


def verified_deposit(src, dest_dir) -> Path:
    """Copy into an exclusively reserved name and verify bytes before removing staging.

    On failure only our incomplete copy is removed. The source remains available.
    A collision keeps both files; existing library files are never overwritten.
    """
    src, dest_dir = Path(src), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.resolve() == src.resolve():
        raise ValueError("download staging must be separate from the library")
    index = 0
    while True:
        try:
            output = dest.open("xb")
            break
        except FileExistsError:
            index += 1
            dest = dest_dir / f"{src.stem} ({index}){src.suffix}"
    try:
        with output, src.open("rb") as source:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        if src.stat().st_size != dest.stat().st_size or file_hash(src) != file_hash(dest):
            raise OSError("download copy verification failed")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    src.unlink()
    return dest


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
