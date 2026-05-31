"""Resolution des chemins : ressources embarquees (lecture seule) vs donnees (ecriture).

Marche en dev (depuis le repo) ET empaquete par PyInstaller :
  - ressources read-only (bin/sldl, config/) -> sys._MEIPASS une fois gele,
  - donnees inscriptibles (staging, logs, outputs) -> dossier utilisateur OS
    (car le .exe peut etre installe dans un emplacement non-inscriptible).

En dev, tout pointe vers la racine du repo : comportement identique a avant.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_base() -> Path:
    """Base des ressources embarquees en lecture seule (bin/sldl, config/)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent  # racine du repo (dev)


def data_base() -> Path:
    """Base des donnees inscriptibles (staging, logs, outputs)."""
    if not is_frozen():
        return Path(__file__).resolve().parent.parent  # repo en dev
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ddd"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ddd"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ddd"


def sldl_exe() -> Path:
    name = "sldl.exe" if platform.system() == "Windows" else "sldl"
    return resource_base() / "bin" / "sldl" / name


def sldl_config() -> Path:
    return resource_base() / "config" / "sldl.conf"


def app_icon() -> Path:
    """Icone DDD (.ico) pour la fenetre + l'exe (embarquee, frozen-aware)."""
    return resource_base() / "ddd" / "assets" / "ddd.ico"


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_download_dir() -> Path:
    """Bibliotheque lossless par defaut : le dossier Musique de l'utilisateur courant.

    Resout par utilisateur et par OS (C:\\Users\\X\\Music\\DDD sur Windows,
    ~/Music/DDD sur Mac/Linux). Jamais de chemin en dur. Modifiable dans Reglages.
    """
    return Path.home() / "Music" / "DDD"


def download_dir(cfg=None) -> Path:
    """Dossier bibliotheque effectif (config 'download_dir' sinon defaut), cree."""
    chosen = (cfg or {}).get("download_dir") if cfg else None
    return _ensure(Path(chosen) if chosen else default_download_dir())


def cache_dl_dir() -> Path:
    """Cache transitoire ou sldl telecharge avant validation (jamais montre)."""
    return _ensure(data_base() / ".cache-dl")


def staging_dir() -> Path:
    return _ensure(data_base() / "staging")


def logs_dir() -> Path:
    return _ensure(data_base() / "logs")


def outputs_dir() -> Path:
    return _ensure(data_base() / "outputs")
