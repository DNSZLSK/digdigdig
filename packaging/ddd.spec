# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec pour DDD - DigDigDig.

Produit un .exe fenetre (la GUI Flet) qui embarque :
  - le coeur Python (package ddd),
  - le binaire sldl (bin/sldl/) + les profils sldl (config/),
  - le client desktop Flet + libsndfile (via les hooks PyInstaller de flet/soundfile).

Pas besoin de ffmpeg : le coeur lit l'audio via soundfile (libsndfile embarque).
Les donnees inscriptibles (staging/logs/outputs) vont dans le dossier de config OS
au runtime (voir ddd/paths.py), donc le .exe marche meme installe en lecture seule.

Build :  pyinstaller packaging/ddd.spec --noconfirm
Sortie : dist/DDD/DDD.exe   (+ dossier de support a cote)
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

SPECPATH_ = Path(SPECPATH)            # packaging/
ROOT = SPECPATH_.parent               # racine du repo

ICON = ROOT / "ddd" / "assets" / "ddd.ico"

# Ressources embarquees (lecture seule) : sldl + profils + icone (pour la fenetre Flet)
datas = [
    (str(ROOT / "bin" / "sldl"), "bin/sldl"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "ddd" / "assets"), "ddd/assets"),
]
binaries = []
hiddenimports = ["ddd", "ddd.gui", "ddd.cli"]

# Tout ramener pour ces paquets :
#  - flet/flet_desktop : client desktop ; soundfile : libsndfile (decodage audio)
#  - yt_dlp : IMPORT PARESSEUX (dans des fonctions de djset.py) que l'analyse statique de
#    PyInstaller rate -> sans collect_all, le scrape YouTube (set + repli playlist) est mort
#    dans le .exe ("yt-dlp missing"). cloudscraper (1001/set79/Bandcamp) embarque aussi du JS.
#  - flac_detective : sous-modules charges dynamiquement par quality.py.
for pkg in ("flet", "flet_desktop", "soundfile", "yt_dlp", "cloudscraper", "flac_detective",
            "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DDD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                    # appli fenetre (pas de console)
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DDD",
)
