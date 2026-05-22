# DDD — DigDigDig

> **Le crate digger qui creuse trois fois.**
> Dig tes sources → Dig Soulseek → Dig le spectre du fichier.

Outil CLI pour construire/maintenir une bibliothèque musicale DJ en **vrai lossless**, depuis des listes de favoris multi-sources (Discogs, Bandcamp, ...) avec vérification spectrale anti-fake-FLAC à la sortie.

> **Note sur le scope** : la cible de sortie (le "DEPLOY") peut être n'importe quoi — clé USB DJ, dossier local, NAS, library Rekordbox/Serato, etc. Le cas de test initial était une clé USB (`D:\2023 Playlist Ultime`) parce que c'est ce qui a déclenché le projet (315 faux WAVs détectés dessus), mais la phase Deploy est juste une copie configurable, pas le cœur du projet.

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

**Pourquoi ce projet ?** Diagnostic initial : sur 329 WAVs d'une clé USB DJ, **315 étaient des MP3 transcodés en faux WAV** (détecté via FFT spectral cliff < 16 kHz). Le but est de tout reconstruire en lossless authentique.

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
├── pipeline.ps1                # entrypoint
├── docs/
│   └── logo.png                # le triple D
├── lib/
│   ├── convert-csv.ps1         # audit CSV (FR) -> sldl CSV
│   ├── audit-staging.ps1       # score similarity DL'd vs requested
│   ├── clean-staging.ps1       # delete junk + rename misnamed
│   ├── retry-fakes.ps1         # query variants pour misses
│   ├── route-files.ps1         # deploy verified FLAC -> USB
│   └── scrapers/
│       ├── discogs.py          # wantlist + collection (API officielle)
│       └── bandcamp.py         # wishlist (cloudscraper + fancollection API)
├── config/
│   └── sldl.conf               # profiles lossless / lossless-strict / mp3-fallback
├── bin/
│   └── sldl/                   # sldl.exe (binary self-contained, .NET inclus)
├── inputs/                     # CSV inputs + caches scraper (gitignored)
├── outputs/                    # rapports CSV/JSON (gitignored)
├── staging/                    # DL temporaires avant validation (gitignored)
├── logs/                       # logs sldl + pipeline (gitignored)
├── tools/                      # scripts utilitaires (audit FFT historique)
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
- `-DoRetry` : Phase E — retry queries variantes pour les misses
- `-AutoClean` : audit + clean après sldl (auto-rename + auto-delete junk)

---

## État au moment du rename (2026-05-23)

### Ce qui marche
- ✅ **Audit FFT** d'origine : a diagnostiqué 315 faux WAVs sur la clé `D:\2023 Playlist Ultime`. Liste tier-isée dans `D:\2023 Playlist Ultime\to_replace.csv`.
- ✅ **sldl run 1** (fuzzy) : 101/329 trouvés, beaucoup de mismatches → audit a viré 14 mauvais DLs
- ✅ **sldl run 2** (strict mode `strict-title` + `strict-artist`) : **57% terminé** (27 OK / 131 fails) **— interrompu pour le rename, à relancer**
- ✅ **Discogs scraper** : 136 tracks scrapées du wantlist `dnszlsk`
- ✅ **Bandcamp scraper** : **1228 tracks** scrapées du wishlist `gamolka` (279 items, beaucoup d'albums dépliés)
- ✅ Pipeline complet (`pipeline.ps1`) avec convert + sldl + audit + clean + verify + retry + deploy
- ✅ Routing via `_index.csv` de sldl (zero unmapped sur le test précédent)

### Ce qui reste à faire (par ordre de priorité)
1. **Finir le rename** : `searchseek/` → `ddd/` (bloqué par VSCode lock, à faire après close)
2. **Relancer sldl run 2** (strict mode) sur les 248 tracks pas encore traités
3. **Lancer Discogs en run 3** sur `outputs/discogs_wantlist.csv` (136 tracks)
4. **Lancer Bandcamp en run 4** sur `outputs/bandcamp_wishlist.csv` (1228 tracks)
5. **Merger / dédupliquer** entre les sources (Discogs ∩ Bandcamp ∩ to_replace)
6. **flac-detective verify** sur tout le staging final
7. **Phase F deploy** vers la clé USB en utilisant la structure de dossiers d'origine pour `to_replace`, et un `inbox/` pour Discogs+Bandcamp (pas de mapping vers les dossiers existants)
8. **Optionnel** : scraper SoundCloud (mais user a dit "caca", à voir)

### Données qui existent déjà
- `staging/` : 87 FLACs/WAVs auditées et clean (Run 1 + début Run 2)
- `inputs/sldl_input.csv` : 275 rows (audit `to_replace.csv` filtré sans-artiste)
- `inputs/sldl_input_map.json` : routing key → dossier USB
- `outputs/discogs_wantlist.csv` : 136 tracks
- `outputs/bandcamp_wishlist.csv` : 1228 tracks
- `outputs/staging_audit.csv` : dernier audit similarity
- `logs/sldl_index_run1_pre-strict.csv` : archive du run 1 (pour routing)
- `staging/sldl_input/_index.csv` : index live du run 2 (interrompu)

### Décisions actées
- **Nom** : `DDD` (court) / `DigDigDig` (long). Triple D = trois étapes de digging.
- **Strict-mode sldl par défaut** (sinon fuzzy match de merde — testé, prouvé)
- **Filtrer les CSV rows sans artiste** avant sldl (sinon random match assuré)
- **Dédupliquer cross-source** sur clé `lower(artist) - lower(title)`
- **Pas de SoundCloud** pour le moment
- **Auto-catégorisation par genre = non** ; tout va dans `inbox/`, l'user classe manuellement

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
