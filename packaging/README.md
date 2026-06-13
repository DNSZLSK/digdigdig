# Packaging DDD - le .exe pour tout le monde

But : un logiciel que n'importe qui lance sans installer Python, venv, sldl, etc.
La fenetre native (Flet) s'ouvre, on choisit un dossier, on scanne, on upgrade.

## Ce qui est embarque

- Le coeur Python (`ddd/`) + ses deps (numpy, scipy, soundfile, mutagen, requests,
  cloudscraper, bs4).
- `bin/sldl/` (le binaire Soulseek) et `config/sldl.conf` (les profils).
- Le client desktop Flet (Flutter) + `libsndfile` (lecture audio).
- **Pas de ffmpeg** : l'audio passe par soundfile/libsndfile, embarque.

Les donnees ecrites au runtime (staging, logs, outputs) vont dans le dossier de
config de l'OS (`%APPDATA%\ddd` sur Windows), pas a cote du .exe : l'app marche
meme installee dans un emplacement en lecture seule. Voir `ddd/paths.py`.

## Windows

```powershell
# une fois : installer l'outillage de build dans le venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui,build]"

# build
.\packaging\build.ps1            # ou: .\packaging\build.ps1 -Clean
```

Sortie : `dist\DDD\DDD.exe`. Distribuer le dossier `dist\DDD\` entier (le zipper).
Double-clic sur `DDD.exe` = la fenetre s'ouvre.

Premier lancement : aller dans Reglages, renseigner le login Soulseek (et le token
Discogs si on veut scraper). C'est stocke dans `%APPDATA%\ddd\config.json`.

## Mac / Linux

Le coeur est pur Python et portable. Deux voies :

1. **PyInstaller** sur la machine cible (meme spec) : `pyinstaller packaging/ddd.spec`.
   Fournir un binaire `sldl` natif (Mac/Linux) dans `bin/sldl/` avant le build.
2. **`flet build macos` / `flet build linux`** (toolchain Flutter requise) pour un
   bundle natif.

sldl est un binaire .NET self-contained : telecharger la release correspondant a
l'OS depuis le projet slsk-batchdl et la placer dans `bin/sldl/`.

## Notes

- Flet est epingle `>=0.24,<0.30` : la ligne 0.85+ ("Flet 1.0", alpha) casse l'API.
- Taille du bundle : ~150-250 MB (client Flutter + scipy + sldl). Normal pour une
  app desktop Python+Flutter.
- La CLI reste disponible sans empaquetage : `python -m ddd scan|upgrade|rename|buy|scrape|acquire|gui`.
