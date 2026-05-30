<p align="center">
  <img src="docs/logo.png" alt="DDD - DigDigDig" width="400">
</p>

<h1 align="center">DDD - DigDigDig</h1>

<p align="center">
  <em>Le crate digger qui creuse trois fois.</em><br>
  Dig tes sources → Dig Soulseek → Dig le spectre du fichier.
</p>

---

**Pourquoi DDD ?** Parce que je suis feignant et que j'aime le bon son. Combinaison redoutable.

Maintenir une bibliothèque DJ en **vrai lossless** à la main, c'est des heures perdues :
vérifier la source de chaque track, retracker les fichiers foireux, jongler entre les
plateformes, refaire la même recherche pour la 4e fois parce qu'on a oublié qu'on l'avait
déjà. DDD fait tout ça à ma place : il part de mes favoris (Discogs, Bandcamp, …), va digger
sur Soulseek en **matchant le titre complet** (pas d'à-peu-près), passe chaque fichier au
**scanner spectral anti-fake-FLAC**, et me laisse juste digger et écouter.

> La sortie (le « DEPLOY ») est **configurable** : dossier local, NAS, library
> Rekordbox/Serato… c'est juste une copie vers la cible de ton choix, pas le cœur du projet.

## Les 3 D

```
┌──── DIG ────┐     ┌── DOWNLOAD ──┐     ┌──── DETECT ────┐     ┌── deploy ──┐
│ scrapers    │ ──▶ │ sldl + retry │ ──▶ │ flac-detective │ ──▶ │ copie vers │
│ (Discogs,   │     │ (Soulseek)   │     │ + audit titre- │     │ une cible  │
│  Bandcamp)  │     │ match strict │     │ complet + clean│     │ configurable│
└─────────────┘     └──────────────┘     └────────────────┘     └────────────┘
   favoris /          vrai lossless,        spectre FFT +           dossier local,
   wishlists          titre exact           anti-mismatch           NAS, Rekordbox…
```

1. **DIG** - scrape tes favoris (`lib/scrapers/discogs.py`, `bandcamp.py`) → un CSV de tracks.
2. **DOWNLOAD** - `sldl` télécharge en lossless depuis Soulseek (profil strict), avec retry sur les misses.
3. **DETECT** - double contrôle :
   - **audit titre-complet** : le fichier doit matcher *exactement* ce qui a été demandé -
     rappel + **précision** (pas de mots en trop) + **version** (`Original` ≠ `(X Remix)` ≠
     `Extended`, selon ta demande) + durée ±10 % + tags. Les mauvais → quarantaine.
   - **flac-detective** : analyse spectrale FFT pour démasquer les faux FLAC (MP3 transcodés).
4. **deploy** *(opt-in)* - copie **uniquement** les fichiers validés vers la cible de ton choix.

## Stack

- **PowerShell** - pipeline orchestrateur + lib utilitaire (natif Windows)
- **Python 3.12** (venv local `.venv/`) - scrapers + FLAC_Detective
- **sldl** (`bin/sldl/`) - binary .NET self-contained, batch Soulseek ([fiso64/slsk-batchdl](https://github.com/fiso64/slsk-batchdl))
- **ffmpeg / ffprobe** - décodage + durée/tags pour l'audit (`winget install Gyan.FFmpeg`)
- **cloudscraper** - bypass FingerprintJS sur Bandcamp
- **slskd** *(optionnel)* - daemon Soulseek headless, dashboard sur le port 5030

## Layout

```
ddd/
├── pipeline.ps1                # entrypoint
├── lib/
│   ├── convert-csv.ps1         # CSV (FR) -> CSV sldl
│   ├── audit-staging.ps1       # match titre COMPLET (rappel + précision + version)
│   ├── clean-staging.ps1       # quarantaine SUSPECT -> _rejected/ + rename
│   ├── retry-fakes.ps1         # query variants pour les misses
│   ├── route-files.ps1         # deploy (Status=OK only) vers la cible
│   └── scrapers/
│       ├── discogs.py          # wantlist + collection (API officielle)
│       └── bandcamp.py         # wishlist (cloudscraper + fancollection API)
├── config/sldl.conf            # profils lossless / lossless-strict / mp3-fallback
├── bin/sldl/                   # sldl.exe
├── docs/logo.png               # le triple D
├── inputs/  outputs/  staging/  logs/   # données run-time (gitignored)
└── .venv/                      # venv Python (gitignored)
```

## Usage

```powershell
# 0. Une fois : deps
#    - sldl dans bin/sldl/ ; winget install Gyan.FFmpeg
#    - python -m venv .venv ; .venv\Scripts\pip install requests beautifulsoup4 cloudscraper flac-detective

# 1. DIG : scrape une source -> inputs\sldl_input.csv
$env:DISCOGS_TOKEN = "ton_token"
.\.venv\Scripts\python.exe lib\scrapers\discogs.py <user> -o inputs\sldl_input.csv
#   ou
.\.venv\Scripts\python.exe lib\scrapers\bandcamp.py <user> -o inputs\sldl_input.csv

# 2. DOWNLOAD + DETECT (audit + clean) en un appel
.\pipeline.ps1 -SkipConvert -AutoClean

# 3. DEPLOY (opt-in) vers la cible de ton choix (n'importe quel dossier)
.\pipeline.ps1 -SkipConvert -SkipDownload -SkipVerify -DoDeploy -UsbRoot "D:\Ma Library"
```

**Switches utiles** : `-Limit N` (smoke test), `-OnlyTier N`, `-DoRetry` (retry des misses),
`-AutoClean` (audit + clean auto), `-DeleteOld` (avec `-DoDeploy`, supprime les vieux fichiers remplacés).

## Roadmap

- [x] Pipeline complet : scrape → sldl → audit titre-complet → flac-detective → deploy configurable
- [x] Scrapers Discogs (wantlist/collection) + Bandcamp (wishlist)
- [x] Audit durci : précision + version + durée, quarantaine + garde au deploy `Status=OK`
- [ ] Scraper SoundCloud likes (yt-dlp)
- [ ] Fallback multi-provider quand Soulseek miss (Bandcamp pay, Beatport, Tidal, Qobuz)
- [ ] DB SQLite (historique d'acquisition, stats)
- [ ] Dashboard web
- [ ] Mode service Windows (poll des wishlists, rebuild incrémental)
- [ ] Auto-tagging MusicBrainz/Discogs + génération de playlists Rekordbox/Serato
