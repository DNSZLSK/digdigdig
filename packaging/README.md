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

## Mac (automatise via GitHub Actions)

Le workflow `.github/workflows/build-macos.yml` build le `.app` sur un runner
macOS Apple Silicon : il recupere le binaire `sldl` osx v2.6.0 (le meme qu'en
Windows), lance PyInstaller (`packaging/ddd.spec` -> `DDD.app`), zippe et publie.
- **Manuel** : onglet Actions -> "build-macos" -> Run workflow ; le zip sort en
  artifact `DDD-macos-AppleSilicon`.
- **Auto** : a chaque release publiee (`gh release create vX ...`),
  `DDD-macos-AppleSilicon.zip` est attache a la release, a cote du zip Windows.

Intel (x64) non couvert pour l'instant (runners GitHub Intel en fin de vie + parc
Mac quasi 100% Apple Silicon). Ajoutable via une matrice si un testeur Intel le demande.

**Non signe** (pas de compte Apple Developer) : au 1er lancement, l'utilisateur fait
**clic-droit -> Ouvrir** (ou `xattr -dr com.apple.quarantine DDD.app`) pour passer
Gatekeeper. Normal pour de l'open-source non notarise.

Icone : le `.app` prend `ddd/assets/ddd.icns` s'il existe, sinon icone generique.
Pour brander, commiter un vrai `ddd.icns` carre.

## Mac / Linux (manuel, depuis la machine cible)

Le coeur est pur Python et portable. Deux voies :

1. **PyInstaller** sur la machine cible (meme spec) : `pyinstaller packaging/ddd.spec`.
   Fournir un binaire `sldl` natif (Mac/Linux) dans `bin/sldl/` avant le build.
2. **`flet build macos` / `flet build linux`** (toolchain Flutter requise) pour un
   bundle natif.

sldl est un binaire .NET self-contained : telecharger la release correspondant a
l'OS depuis le projet slsk-batchdl et la placer dans `bin/sldl/`.

## Notes

- Flet est epingle `>=0.24,<0.28.3` (flet + flet-desktop + flet-cli) : la 0.28.3
  casse le FilePicker natif macOS (bug Flet #5334), et la 0.85+ ("Flet 1.0", alpha)
  casse l'API. Voir le commentaire dans `pyproject.toml`.
- Taille du bundle : ~150-250 MB (client Flutter + scipy + sldl). Normal pour une
  app desktop Python+Flutter.
- La CLI reste disponible sans empaquetage : `python -m ddd scan|upgrade|rename|buy|scrape|acquire|gui`.
