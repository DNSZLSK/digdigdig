"""Renommage propre d'un dossier en 'Artiste - Titre.ext' (mode index-free).

Pour chaque fichier, `resolve_name()` derive le meilleur (artist, title) depuis le
nom ET les tags, on propose 'Artiste - Titre.ext' (assaini, anti-collision). On ne
renomme QUE les resolutions sures (`confident`) ; les conflits nom<->tags sont laisses
tels quels et listes. Optionnellement (`dedup`), les copies byte-identiques sont
envoyees a la corbeille (reversible) en gardant un exemplaire.

Dry-run par defaut : rien n'est touche tant que `apply=False`.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

from . import trash
from .naming import resolve_name
from .scan import iter_audio_files

logger = logging.getLogger(__name__)

# Caracteres interdits dans un nom de fichier Windows -> '_'
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')

# Actions d'une operation de renommage
REN = "REN"        # renomme (ou le serait en dry-run)
OK = "OK"          # nom deja propre, rien a faire
SKIP = "SKIP"      # resolution peu fiable / echec -> laisse tel quel
DUP = "DUP"        # copie redondante (envoyee a la corbeille si apply+dedup)


@dataclass
class RenameOp:
    action: str
    src: str
    dst: str = ""
    reason: str = ""
    source: str = ""        # provenance resolve_name (name/title-tag/tags/deslug)
    applied: bool = False


@dataclass
class DupGroup:
    keep: str
    redundant: List[str]
    size_bytes: int


@dataclass
class RenameReport:
    ops: List[RenameOp] = field(default_factory=list)
    dups: List[DupGroup] = field(default_factory=list)
    applied: bool = False
    log_path: str = ""

    def of(self, action: str) -> List[RenameOp]:
        return [o for o in self.ops if o.action == action]

    @property
    def wasted_bytes(self) -> int:
        return sum(g.size_bytes * len(g.redundant) for g in self.dups)


def _sanitize(name: str) -> str:
    """Remplace les caracteres interdits et retire point/espace de fin (Windows)."""
    return _ILLEGAL.sub("_", name).strip().rstrip(". ")


def _file_hash(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _dup_groups(files: Sequence[Path]) -> List[List[Path]]:
    """Groupes de fichiers byte-identiques : meme taille PUIS meme hash (hash que si collision de taille)."""
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for f in files:
        try:
            by_size[f.stat().st_size].append(f)
        except OSError:
            pass
    groups: List[List[Path]] = []
    for fs in by_size.values():
        if len(fs) < 2:
            continue
        by_hash: Dict[str, List[Path]] = defaultdict(list)
        for f in fs:
            try:
                by_hash[_file_hash(f)].append(f)
            except OSError:
                pass
        groups.extend(g for g in by_hash.values() if len(g) > 1)
    return groups


def _choose_keep(group: Sequence[Path]) -> Path:
    """Garde l'exemplaire au nom le plus propre (pas 'Copie'/'copy', le plus court, stable)."""
    def score(p: Path):
        n = p.name.lower()
        return (n.count("copie") + n.count("copy"), len(p.name), p.name)
    return min(group, key=score)


def _unique_dest(dst: Path, src: Path, reserved: set) -> Path:
    """Resout les collisions par suffixe ' (n)'. dst egal a src (deja bon nom) -> garde dst."""
    def taken(p: Path) -> bool:
        if str(p).lower() in reserved:
            return True
        return p.exists() and p.resolve() != src.resolve()

    if not taken(dst):
        return dst
    i = 1
    while True:
        cand = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
        if not taken(cand):
            return cand
        i += 1


def rename_folder(root, *, apply: bool = False, dedup: bool = False,
                  exclude: Sequence[str] = (), outputs_dir=None) -> RenameReport:
    """Construit (et applique si `apply`) le plan de renommage d'un dossier."""
    root = Path(root)
    files = list(iter_audio_files(root, exclude))
    report = RenameReport(applied=apply)

    # 1. Doublons byte-identiques : on retire les redondants du pool de renommage,
    #    et on les envoie a la corbeille si apply+dedup.
    redundant: set = set()
    for group in _dup_groups(files):
        keep = _choose_keep(group)
        reds = [f for f in group if f != keep]
        try:
            size = keep.stat().st_size
        except OSError:
            size = 0
        report.dups.append(DupGroup(str(keep), [str(r) for r in reds], size))
        for r in reds:
            redundant.add(r)
            op = RenameOp(DUP, str(r), reason=f"copy of {keep.name}")
            if apply and dedup:
                try:
                    op.applied = bool(trash.send_to_trash(r))
                except Exception as e:  # noqa: BLE001
                    logger.warning("corbeille echouee %s: %r", r, e)
            report.ops.append(op)

    # 2. Renommage des survivants (resolutions sures uniquement).
    reserved: set = set()
    for f in sorted(files):
        if f in redundant:
            continue
        r = resolve_name(f)
        if not (r.confident and r.artist and r.title):
            report.ops.append(RenameOp(SKIP, str(f), source=r.source,
                                       reason=f"resolution peu fiable ({r.source})"))
            continue
        proposed = _sanitize(f"{r.artist} - {r.title}{f.suffix}")
        dst = f.with_name(proposed)
        if dst.name == f.name:
            report.ops.append(RenameOp(OK, str(f), str(f), "deja propre", r.source))
            continue
        final = _unique_dest(dst, f, reserved)
        reserved.add(str(final).lower())
        op = RenameOp(REN, str(f), str(final), source=r.source)
        if apply:
            try:
                f.rename(final)
                op.applied = True
            except OSError as e:
                op.action, op.reason = SKIP, f"rename failed: {e}"
        report.ops.append(op)

    # 3. Journal d'annulation (sur apply uniquement).
    if apply and outputs_dir is not None:
        report.log_path = _write_log(report, Path(outputs_dir) / f"rename_{root.name}.csv")
    return report


def _write_log(report: RenameReport, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "from", "to", "applied", "reason"])
        for o in report.ops:
            w.writerow([o.action, o.src, o.dst, int(o.applied), o.reason])
    return str(path)
