"""Config + creds par utilisateur, dans le dossier de config de l'OS.

Permet a "tout le monde" de poser SES identifiants (token Discogs, login Soulseek,
cible de deploiement) une fois, persistes hors du repo. JSON simple, pas de secret
chiffre (scope perso) - documente comme tel.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional

APP = "ddd"
_FILENAME = "config.json"

# Cles connues (documentees) - le dict reste ouvert pour extension
KNOWN_KEYS = (
    "discogs_token",
    "discogs_username",   # pseudo Discogs (prerempli dans l'onglet Recuperer favoris)
    "bandcamp_username",  # pseudo Bandcamp (idem)
    "soulseek_user",
    "soulseek_pass",
    "default_target",     # cible de deploiement par defaut
    "default_excludes",   # liste de sous-dossiers a ignorer au scan
    "download_dir",       # bibliotheque lossless verifiee (upgrade + acquire deposent ici)
    "last_inbox",         # (legacy) ancien dossier de destination acquire
)


def config_dir() -> Path:
    """Dossier de config selon l'OS (creable)."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP


def config_path() -> Path:
    return config_dir() / _FILENAME


def load() -> Dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(cfg: Dict[str, Any]) -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = config_path()
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def get(key: str, default: Optional[Any] = None) -> Any:
    return load().get(key, default)


def set_value(key: str, value: Any) -> Path:
    cfg = load()
    cfg[key] = value
    return save(cfg)


def set_many(values: Dict[str, Any]) -> Path:
    cfg = load()
    cfg.update(values)
    return save(cfg)
