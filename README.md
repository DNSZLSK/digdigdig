# searchseek

Pipeline lossless DJ-grade : remplace les "faux WAVs" (MP3 transcodés) d'une bibliothèque musicale par de vrais fichiers lossless via Soulseek + vérification spectrale automatique.

## Vue d'ensemble

```
audit FFT      →     to_replace.csv     →     sldl batch DL     →     FLAC_Detective verify
(spectral)         (315 tracks Tier 1-3)     (lossless profile)         (cliff detection)
                                                                            ↓
                                                          authentic / fake / unfindable
                                                                            ↓
                                                          deploy sur clé USB (par dossier)
```

Le projet n'invente rien : il orchestre [sldl](https://github.com/fiso64/slsk-batchdl) (search + DL) et [FLAC_Detective](https://github.com/GuillainM/FLAC_Detective) (anti-fake-FLAC) avec une couche de routing par dossier d'origine.

## Stack

- **PowerShell** (pipeline et lib) — natif Windows, choix par défaut
- **sldl** — binary .NET, `bin/sldl/sldl.exe`
- **FLAC_Detective** — Python via venv local `.venv/`
- **ffmpeg** — requis par FLAC_Detective pour décoder FLAC vers PCM

## Layout

```
searchseek/
├── pipeline.ps1          # entrypoint
├── lib/                  # modules réutilisables (convert, route, retry)
├── config/               # configs sldl, mappings
├── inputs/               # CSV d'entrée
├── outputs/              # rapports (authentic.csv, fake.csv, ...)
├── staging/              # DL temporaires (gitignored)
├── logs/                 # logs run-time (gitignored)
├── bin/                  # binaries externes (sldl)
└── tools/                # scripts utilitaires (audit FFT, etc.)
```

## Usage (work in progress)

```powershell
# Première fois : install deps et activate venv
.\bootstrap.ps1

# Run pipeline complet (input = to_replace.csv généré par l'étape audit)
.\pipeline.ps1 -Input "D:\2023 Playlist Ultime\to_replace.csv" -Tier 1
```

## Roadmap

- [x] MVP : pipeline sldl + FLAC_Detective + routing pour rebuild une lib existante
- [ ] Scrapers multi-source (likes SoundCloud, wantlist Discogs, playlists Spotify)
- [ ] Fallback multi-provider (Bandcamp, Deezer, Tidal, Qobuz, YouTube)
- [ ] Dashboard web
- [ ] DB SQLite pour historique
- [ ] Mode service Windows (rebuild incrémental auto)
