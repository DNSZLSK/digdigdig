"""Scan d'une bibliotheque existante (mode index-free).

Parcourt un dossier arbitraire, analyse la qualite de chaque fichier audio et
ecrit un rapport unifie (CSV + JSON). Pas besoin de `_index.csv` : on juge chaque
fichier sur son propre spectre (et, plus tard, son nom + ses tags).
"""

from __future__ import annotations

import csv
import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

from . import audit as audit_mod
from .audit import NameAudit
from .quality import CSV_FIELDS, LOSSLESS_EXTS, LOSSY_EXTS, QualityResult, analyze_file

logger = logging.getLogger(__name__)

AUDIO_EXTS = LOSSLESS_EXTS | LOSSY_EXTS

ProgressCb = Callable[[int, int, Path], None]


def iter_audio_files(root, exclude_names: Sequence[str] = ()) -> Iterator[Path]:
    """Itere les fichiers audio sous `root`, en elaguant les dossiers exclus."""
    root = Path(root)
    exclude_lower = {e.lower() for e in exclude_names}
    for dirpath, dirnames, filenames in os.walk(root):
        # Elague les dossiers exclus (ex: PROD = prods perso) in-place
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude_lower]
        for fn in sorted(filenames):
            if Path(fn).suffix.lower() in AUDIO_EXTS:
                yield Path(dirpath) / fn


def scan_folder(
    root,
    exclude_names: Sequence[str] = (),
    progress: Optional[ProgressCb] = None,
) -> List[QualityResult]:
    """Analyse tous les fichiers audio sous `root`."""
    files = list(iter_audio_files(root, exclude_names))
    total = len(files)
    results: List[QualityResult] = []
    for i, f in enumerate(files, 1):
        try:
            results.append(analyze_file(f))
        except Exception as e:  # noqa: BLE001
            # Un seul fichier bancal (tag exotique, I/O reseau en rade...) ne doit JAMAIS
            # tuer tout le scan : on le loggue et on l'ignore.
            logger.warning("scan: fichier ignore %s: %r", f, e)
        if progress:
            progress(i, total, f)
    return results


def write_csv(results, path, fields: Sequence[str] = CSV_FIELDS) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        for r in results:
            row = r.as_dict()
            writer.writerow({k: row.get(k, "") for k in fields})
    return path


def write_json(results, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([r.as_dict() for r in results], fh, ensure_ascii=False, indent=2)
    return path


def summarize(results: Sequence[QualityResult]) -> Counter:
    return Counter(r.verdict for r in results)


# ---- Scan complet : qualite + nommage/tags + doublons -----------------------

@dataclass
class ScanRecord:
    """Une ligne du scan complet : qualite audio + audit nommage + doublon."""
    quality: QualityResult
    naming: NameAudit
    size_bytes: int
    dup_count: int          # nb de fichiers partageant exactement cette taille (1 = unique)

    @property
    def is_duplicate(self) -> bool:
        return self.dup_count > 1

    def as_dict(self) -> Dict:
        q = self.quality.as_dict()
        n = self.naming.as_dict()
        return {
            "quality_verdict": q["verdict"],
            "name_status": n["status"],
            "dup_count": self.dup_count,
            "format_class": q["format_class"],
            "ext": q["ext"],
            "filename": q["filename"],
            "cutoff_hz": q["cutoff_hz"],
            "est_source_bitrate": q["est_source_bitrate"],
            "container_bitrate": q["container_bitrate"],
            "sample_rate": q["sample_rate"],
            "duration_s": q["duration_s"],
            "name_artist": n["name_artist"],
            "name_title": n["name_title"],
            "tag_artist": n["tag_artist"],
            "tag_title": n["tag_title"],
            "size_bytes": self.size_bytes,
            "quality_reason": q["reason"],
            "name_reason": n["reason"],
            "path": q["path"],
        }


SCAN_RECORD_FIELDS = [
    "quality_verdict", "name_status", "dup_count", "format_class", "ext", "filename",
    "cutoff_hz", "est_source_bitrate", "container_bitrate", "sample_rate", "duration_s",
    "name_artist", "name_title", "tag_artist", "tag_title", "size_bytes",
    "quality_reason", "name_reason", "path",
]


def scan_library(
    root,
    exclude_names: Sequence[str] = (),
    progress: Optional[ProgressCb] = None,
) -> List[ScanRecord]:
    """Scan complet : pour chaque fichier, qualite + nommage/tags, puis doublons (taille)."""
    files = list(iter_audio_files(root, exclude_names))
    total = len(files)
    raw = []
    size_counts: Dict[int, int] = defaultdict(int)
    for i, f in enumerate(files, 1):
        try:
            q = analyze_file(f)
            n = audit_mod.audit_file(f)
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            raw.append((q, n, size))
            if size > 0:
                size_counts[size] += 1
        except Exception as e:  # noqa: BLE001
            # Un seul fichier bancal (tag exotique, I/O reseau en rade...) ne doit JAMAIS
            # tuer tout le scan : on le loggue et on l'ignore.
            logger.warning("scan: fichier ignore %s: %r", f, e)
        if progress:
            progress(i, total, f)

    records: List[ScanRecord] = []
    for q, n, size in raw:
        dup = size_counts.get(size, 1) if size > 0 else 1
        records.append(ScanRecord(q, n, size, dup))
    return records


def duplicate_groups(records: Sequence[ScanRecord]) -> List[List[ScanRecord]]:
    """Regroupe les doublons (meme taille en octets) ; groupes de 2+ uniquement."""
    by_size: Dict[int, List[ScanRecord]] = defaultdict(list)
    for r in records:
        if r.size_bytes > 0:
            by_size[r.size_bytes].append(r)
    groups = [g for g in by_size.values() if len(g) > 1]
    groups.sort(key=lambda g: g[0].size_bytes, reverse=True)
    return groups
