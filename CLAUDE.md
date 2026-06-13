# DDD - DigDigDig

> **Le crate digger qui creuse trois fois.**
> Dig tes sources → Dig Soulseek → Dig le spectre du fichier.

Outil CLI pour construire/maintenir une bibliothèque musicale DJ en **vrai lossless**, depuis des listes de favoris multi-sources (Discogs, Bandcamp, ...) avec vérification spectrale anti-fake-FLAC à la sortie.

> **Note sur le scope** : la cible de sortie (le "DEPLOY") peut être n'importe quoi - clé USB DJ, dossier local, NAS, library Rekordbox/Serato, etc. C'est juste une copie configurable, pas le cœur du projet.

Logo : `docs/logo.png`

---

## Architecture (3 phases = 3 D)

```
┌──── DIG ────┐     ┌── DOWNLOAD ──┐     ┌──── DETECT ────┐     ┌── deploy ──┐
│ scrapers    │ ──▶ │ sldl + retry │ ──▶ │ flac-detective │ ──▶ │ copy to    │
│ (Discogs,   │     │ (Soulseek)   │     │ + audit/clean  │     │ target dir │
│  Bandcamp)  │     │ strict match │     │ FFT spectral   │     │ (USB/NAS/  │
│             │     │              │     │                │     │  local/...)│
└─────────────┘     └──────────────┘     └────────────────┘     └────────────┘
   lib/scrapers/      bin/sldl/            .venv/ + lib/         lib/route-files.ps1

   ^^ les 3 D, le ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^ la sortie,
      cœur du projet                                          configurable
```

**Pourquoi ce projet ?** Parce que je suis feignant et que j'aime le son de qualité. Combinaison redoutable. Maintenir une bibliothèque DJ en vrai lossless à la main c'est des heures perdues : vérifier la source de chaque track, retracker quand un fichier est foireux, jongler entre les plateformes, refaire la même recherche pour la 4e fois parce qu'on a oublié qu'on l'avait déjà. Ce projet fait tout ça en batch pour qu'on ait juste à digger et écouter.

---

## Stack technique

- **PowerShell** : pipeline orchestrateur + lib utilitaire (Windows-natif)
- **Python 3.12** (venv local `.venv/`) : scrapers + FLAC_Detective
- **sldl** (`bin/sldl/`) : binary .NET self-contained, batch Soulseek download
- **ffmpeg** : décodage FLAC pour analyse spectrale (winget install Gyan.FFmpeg)
- **cloudscraper** : bypass FingerprintJS sur Bandcamp
- **slskd** (`C:\slskd\`) : daemon Soulseek headless, gardé en standby comme dashboard (port 5030)

---

## Layout du projet

```
ddd/
├── README.md
├── CLAUDE.md                   # ce fichier
├── .gitignore
├── pyproject.toml              # package `ddd` (entry points ddd / ddd-gui ; extras gui,build)
│
├── ddd/                        # >>> LE COEUR ACTUEL : package Python portable <<<
│   ├── __main__.py             # `python -m ddd ...`
│   ├── cli.py                  # CLI : scan | upgrade | rename | buy | scrape | acquire | import | config | gui
│   ├── gui.py                  # fenetre native Flet (0.28.x)
│   ├── paths.py                # chemins frozen-aware (dev vs .exe PyInstaller)
│   └── core/
│       ├── quality.py          # detecteur lossless universel (WAV/FLAC/AIFF/MP3, cutoff spectral)
│       ├── scan.py             # scan index-free d'un dossier -> rapport (qualite+nom+doublons)
│       ├── tokenize.py         # tokens/recall/precision/version (port de audit-staging.ps1)
│       ├── audit.py            # audit nom<->tags ID3 (mutagen), sans _index.csv
│       ├── naming.py           # parse "Artiste - Titre" depuis un nom de fichier
│       ├── soulseek.py         # wrapper sldl (creds, run, lecture _index.csv)
│       ├── upgrade.py          # boucle upgrade + RE-AUDIT anti-upscale ; acquire_rows()
│       ├── config.py           # creds/reglages user -> %APPDATA%\ddd\config.json
│       └── scrapers/           # discogs.py + bandcamp.py (importables)
│
├── packaging/                  # build du .exe
│   ├── ddd.spec                # PyInstaller (bundle sldl + config + Flet + libsndfile)
│   ├── entry.py  build.ps1  README.md
│
├── tests/                      # test_quality/scan_merge/upgrade_logic/gui_build
│
├── pipeline.ps1                # ANCIEN orchestrateur PowerShell (toujours dispo)
├── lib/                        # ANCIENS scripts PS (convert/audit/clean/retry/route + scrapers/)
├── config/sldl.conf            # profils lossless / lossless-strict / mp3-fallback
├── bin/sldl/                   # sldl.exe (gitignored)
├── docs/                       # logo.png + index.html (page GitHub Pages)
├── inputs/ outputs/ staging/ logs/   # runtime (gitignored)
├── dist/ build/                # artefacts PyInstaller (gitignored)
└── .venv/                      # Python venv (gitignored)
```

---

## Pipeline complet

```powershell
# 0. Une fois : install deps (déjà fait si le projet existe)
#    - sldl binary dans bin/sldl/
#    - winget install Gyan.FFmpeg
#    - python -m venv .venv ; .venv\Scripts\pip install requests beautifulsoup4 cloudscraper flac-detective

# 1. DIG : scrape une source
$env:DISCOGS_TOKEN = "ton_token"
.\.venv\Scripts\python.exe lib\scrapers\discogs.py dnszlsk -o inputs\sldl_input.csv

# OU
.\.venv\Scripts\python.exe lib\scrapers\bandcamp.py gamolka -o inputs\sldl_input.csv

# 2-4. DOWNLOAD + audit + DETECT en un seul appel
.\pipeline.ps1 -SkipConvert -AutoClean

# 5. DEPLOY (opt-in, modifie la clé USB)
.\pipeline.ps1 -SkipConvert -SkipDownload -SkipVerify -DoDeploy
# Add -DeleteOld pour aussi supprimer les vieux WAV remplacés
```

**Switches utiles** :
- `-Limit N` : pilote sur N tracks (smoke test)
- `-OnlyTier 1` : juste le tier prioritaire de l'audit
- `-DoRetry` : Phase E - retry queries variantes pour les misses
- `-AutoClean` : audit + clean après sldl (auto-rename + auto-delete junk)

---

## État actuel (2026-06-13) : productisé en logiciel `ddd`

Le projet n'est plus seulement un pipeline PowerShell : c'est maintenant un **vrai
logiciel** (coeur Python portable + fenêtre native + `.exe` une-touche). L'ancien
pipeline PowerShell reste présent et fonctionnel ; le nouveau coeur `ddd/` est ce
sur quoi on construit désormais.

### Décisions de productisation (validées par l'user)
- **Coeur d'abord**, puis packaging. **Multiplateforme** (Windows/Mac/Linux) → coeur 100% Python.
- **GUI native** (pas dashboard web). Toolkit : **Flet**, épinglé `>=0.24,<0.30`
  (la 0.85+ "Flet 1.0" alpha casse l'API : boutons en `text=` kwarg, pas d'`ExpansionTile`).
- Cible de sortie toujours **configurable** (ne jamais la décrire comme "la clé USB").

### Ce qui marche (coeur `ddd/`)
- ✅ **`ddd scan <dossier>`** : détecteur lossless universel (WAV/FLAC/AIFF/MP3) par
  cutoff spectral (réutilise la math de flac-detective) + audit nom/tags ID3 + doublons.
  Index-free : marche sur n'importe quel dossier, pas besoin du pipeline. Verdicts
  AUTHENTIC / SUSPICIOUS / FAKE_LOSSLESS / LOSSY.
- ✅ **`ddd upgrade <dossier>`** : cherche un vrai lossless sur Soulseek pour les
  fichiers flaggés, **re-audite chaque download** et n'accepte que l'AUTHENTIC
  (les filtres sldl ne détectent PAS les upscales - le re-audit, si). Dry-run par
  défaut ; `--apply` remplace, `--delete-old` supprime l'original. **Prouvé en réel**
  sur GAMOLKA\Soa Spirit : 3 vrais FLAC posés, 2 upscales (320k déguisés en .flac) rejetés.
- ✅ **`ddd rename <dossier>`** : remet les fichiers en `Artiste - Titre` via un résolveur de
  nom commun (nom propre → tag-titre → tags → déslug ; gère les tags piégés type compilateur,
  ex. `artist="Tibor Tury"` + vrai couple dans le tag titre). Dry-run par défaut ; `--apply`
  écrit, `--dedup` vire les copies byte-identiques. Le même résolveur alimente `upgrade`/`buy`
  (`search_title` nettoie aussi les `[label, année]` côté requête).
- ✅ **`ddd scrape discogs|bandcamp|djset`** + **`ddd acquire <csv>`** (télécharge une want-list
  en vrai lossless). **djset** : URL d'un set (YouTube/1001TL, tracklist) ou d'une **playlist
  YouTube** (chaque vidéo = un track).
- ✅ **`ddd buy <dossier|rapport|wantlist>`** : pour les introuvables Soulseek, génère une page
  HTML cliquable (logo + thème DDD) avec liens **Discogs** + **Bandcamp**. Auto-émise en fin
  d'`upgrade`/`acquire` ; helper unique `stores.write_unfindable()` câblé CLI + workers GUI.
- ✅ **`ddd config show|set`** : creds/réglages user dans `%APPDATA%\ddd\config.json`.
- ✅ **`ddd gui`** : fenêtre native Flet (dossier, scan, tableau filtrable, upgrade, réglages).
- ✅ **`.exe` autonome** : `packaging/build.ps1` → `dist/DDD/DDD.exe` (255 Mo, embarque
  sldl + profils + client Flet + libsndfile ; **pas de ffmpeg requis**). Lancé et vérifié.
- ✅ Tests : `tests/test_quality.py`, `test_scan_merge.py`, `test_upgrade_logic.py`, `test_gui_build.py`.

### Distribution
- Repo : **github.com/DNSZLSK/digdigdig** (branche `master`).
- **Release v0.1.0** avec asset `DDD-windows.zip` ; lien stable
  `https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-windows.zip`.
- **GitHub Pages** depuis `docs/` : `https://dnszlsk.github.io/digdigdig/` (page `docs/index.html`).
- README : badge "TÉLÉCHARGE-MOI" en tête.

### Reste à faire / idées
- **Revoir l'UX/UI du `.exe`** (demandé explicitement par l'user, pour plus tard).
- Builds Mac/Linux (même `packaging/ddd.spec` sur la plateforme cible + binaire sldl natif).
- `deploy.py` (port de `route-files.ps1`) pas encore fait - `upgrade.py` remplace en place.
- Anciennes idées hors-scope : SoundCloud, multi-provider fallback, DB SQLite, auto-tagging.

### Décisions actées (pipeline PowerShell d'origine, toujours valides)
- **Nom** : `DDD` (court) / `DigDigDig` (long). Triple D = trois étapes de digging.
- **Strict-mode sldl par défaut** (sinon fuzzy match de merde - testé, prouvé)
- **Filtrer les CSV rows sans artiste** avant sldl (sinon random match assuré)
- **Dédupliquer cross-source** sur clé `lower(artist) - lower(title)`
- **Pas de SoundCloud** pour le moment
- **Auto-catégorisation par genre = non** ; tout va dans `inbox/`, l'user classe manuellement
- **Match du titre COMPLET** (durci 2026-05-30) : l'audit ne mesure plus seulement le
  rappel mais aussi la **précision** (mots du fichier qu'on n'a PAS demandés) et la
  **version** (`Original` ≠ `(X Remix)` ≠ `Extended` ≠ `Radio` - symétrique selon ce
  qu'on a demandé). Garde-fous anti-faux-rejet : `(Original Mix)`≡original, `feat.`
  ignoré, bruit (format/année/label/catalogue) ignoré.
  - **Sévérité** : `SUSPECT` (= mauvais enregistrement : mauvaise version, durée
    aberrante, tag mismatch, rappel trop bas, **marqueur compilation/megamix**, ou
    **≥3 mots en trop**) → **quarantaine** `staging/_rejected/` (réversible).
    `PARTIAL` (= rappel complet mais 1-2 mots en trop = souvent bon audio mal nommé
    type `Album - 02 Track`, ou titre court) → **gardé** en staging pour revue, jamais déployé.
  - **Garde au deploy** : `route-files.ps1` ne copie QUE les `Status=OK` (via
    `staging_audit.csv`). PARTIAL/SUSPECT n'atteignent jamais la cible.
  - **Durée à la source** : les scrapers émettent une colonne `Length` (s) - Discogs
    convertit `m:ss`, Bandcamp prend `trackinfo.duration`. sldl auto-détecte `Length`
    → filtre `length-tol` (15 s) au download + écrit la durée demandée dans
    `_index.csv`, que l'audit lit pour le check ±10 %. (Vider `inputs/.bandcamp-cache/`
    une fois pour backfiller les durées des albums déjà en cache.)
  - Knobs audit : `-MaxExtraWords` (1), `-SuspectExtraWords` (3), `-NoVersionCheck`,
    `-NoPrecisionCheck`, `-NoShortTitleGuard`, `-DurationTolerancePct` (10), `-MaxDurationOutlier` (720).

### Risques connus
- Soulseek refus si ratio compte bas → slskd peut auto-partager le `staging/` (déjà configuré dans slskd.yml)
- Tracks vraiment rares → `unfindable.txt` final, fallback Beatport/Bandcamp paid
- Bandcamp scraping → dépend de cloudscraper, peut breaker si Bandcamp change

---

## Commandes utiles pour reprendre la conversation

```powershell
# Voir l'état du staging
cd C:\Users\kposz\Documents\CDA2025-2026\ddd
Get-ChildItem staging -File | Measure-Object | Select-Object Count
Import-Csv staging\sldl_input\_index.csv | Group-Object state

# Voir où on en est par dossier USB
Import-Csv outputs\staging_audit.csv | Group-Object Status

# Relancer le run 2 sldl (avec skip-existing, repart où on en était)
.\pipeline.ps1 -SkipConvert -AutoClean

# Lancer Discogs run 3
Copy-Item outputs\discogs_wantlist.csv inputs\sldl_input.csv -Force
.\pipeline.ps1 -SkipConvert -AutoClean

# Lancer Bandcamp run 4
Copy-Item outputs\bandcamp_wishlist.csv inputs\sldl_input.csv -Force
.\pipeline.ps1 -SkipConvert -AutoClean

# Re-audit FFT sur la clé après tout déploiement (script historique)
& tools\classify_fakes.ps1  # (à copier depuis %TEMP% si pas dans tools/)
```

---

## Credentials utilisés

Ces creds sont dans la conversation et utilisables localement. À régénérer si compromis :

- **Soulseek** : DNSZLSK (dans `%LOCALAPPDATA%\slskd\slskd.yml`)
- **Discogs** : token dans `$env:DISCOGS_TOKEN` (générer sur discogs.com/settings/developers)
- **Bandcamp** : pas d'auth, scrape public via cloudscraper (username `gamolka`)
- **slskd web UI** : login `DNSZLSK` / pwd same (port 5030)
- **slskd API key** : dans `C:\slskd\.apikey`
- **Creds de l'app `ddd`** : login Soulseek + token Discogs saisis dans la GUI (Réglages)
  ou `ddd config set`, stockés dans `%APPDATA%\ddd\config.json`. `soulseek.py` lit dans
  l'ordre : env `DDD_SOULSEEK_USER/PASS` → config ddd → `slskd.yml`.

### Identité git (important)
Les commits doivent être **DNSZLSK** (`155122610+DNSZLSK@users.noreply.github.com`), pas
`kposz` (nom du compte Windows). Ce repo avait un override **local** dans `.git/config`
qui forçait `kposz` ; il a été retiré (le repo hérite maintenant du global, correct).
Pas de mention "Co-Authored-By" ni IA dans les commits/PR.

---

## Roadmap (vision "beaucoup plus loin")

Hors-scope MVP, à coder si la base marche :
- [ ] Scraper SoundCloud likes (`lib/scrapers/soundcloud.py` via yt-dlp)
- [ ] Multi-provider fallback (Bandcamp pay, Beatport, Tidal, Qobuz) quand Soulseek miss
- [ ] DB SQLite pour historique des acquisitions et stats
- [ ] Web dashboard
- [ ] Mode service Windows pour rebuild incrémental (poll les wishlists toutes les 24h)
- [ ] Auto-tagging des fichiers DL'd avec métadonnées MusicBrainz/Discogs
- [ ] Génération de playlists Rekordbox / Serato à partir de l'inventaire
