"""Cache persistant des analyses spectrales (scan incremental).

L'analyse d'un fichier (~0,8 s, CPU) ne change pas tant que le fichier ne change pas :
on memorise le QualityResult par chemin, invalide des que la taille ou la date de
modification bouge, ou que le moteur de detection / la version de DDD change.
Un rescan apres upgrade ne re-analyse donc que les fichiers nouveaux ou modifies.

Le cache ne doit JAMAIS casser un scan : toute erreur SQLite -> on continue sans lui.
Desactivable par l'env DDD_SCAN_CACHE=0 (ou chemin explicite de la base).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Optional

from .. import __version__
from .quality import ERROR, SKIPPED, QualityResult

logger = logging.getLogger(__name__)

# A incrementer si l'analyse change sans changement de version (rare : la version suffit).
ANALYSIS_REV = 1
_COMMIT_EVERY = 200
_QR_FIELDS = {f.name for f in fields(QualityResult)}


def default_path() -> Optional[Path]:
    env = os.environ.get("DDD_SCAN_CACHE", "").strip()
    if env == "0":
        return None
    if env:
        return Path(env)
    from .. import paths
    return paths.data_base() / ".scan-cache" / "scan.sqlite3"


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class ScanCache:
    def __init__(self, db_path: Path, detector: str) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), timeout=10)
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS results ("
                " path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, key TEXT, data TEXT)")
        except Exception:
            self._db.close()      # sinon Windows garde le fichier verrouille (pas de renommage)
            raise
        self._key = f"{__version__}|{ANALYSIS_REV}|{detector}"
        self._pending = 0
        self.hits = 0

    def get(self, path: Path, st: os.stat_result) -> Optional[QualityResult]:
        row = self._db.execute(
            "SELECT size, mtime_ns, key, data FROM results WHERE path = ?", (_norm(path),)).fetchone()
        if not row or row[0] != st.st_size or row[1] != st.st_mtime_ns or row[2] != self._key:
            return None
        data = json.loads(row[3])
        if set(data) != _QR_FIELDS:
            return None
        data["path"], data["filename"] = str(path), Path(path).name
        self.hits += 1
        return QualityResult(**data)

    def put(self, path: Path, st: os.stat_result, q: QualityResult) -> None:
        if q.verdict in (ERROR, SKIPPED):   # erreur possiblement passagere (I/O reseau)
            return
        self._db.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?)",
            (_norm(path), st.st_size, st.st_mtime_ns, self._key, json.dumps(q.as_dict())))
        self._pending += 1
        if self._pending >= _COMMIT_EVERY:
            self._db.commit()
            self._pending = 0

    def close(self) -> None:
        try:
            self._db.commit()
        finally:
            self._db.close()


def open_cache(detector: str) -> Optional[ScanCache]:
    """Ouvre le cache par defaut ; None si desactive ou inutilisable (jamais d'exception)."""
    p = None
    try:
        p = default_path()
        return ScanCache(p, detector) if p else None
    except sqlite3.DatabaseError as e:
        # Fichier corrompu : on le met de cote et on repart d'un cache vide (sinon le
        # cache resterait desactive pour toujours, en silence).
        logger.warning("scan cache corrompu, reconstruit: %r", e)
        try:
            p.replace(p.with_suffix(".corrupt"))
            return ScanCache(p, detector)
        except Exception as e2:  # noqa: BLE001
            logger.warning("scan cache indisponible, scan complet: %r", e2)
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("scan cache indisponible, scan complet: %r", e)
        return None
