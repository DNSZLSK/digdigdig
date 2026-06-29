<p align="center">
  <img src="docs/logo.png" alt="DDD - DigDigDig" width="400">
</p>

<h1 align="center">DigDigDig</h1>

<p align="center">
  <em>The crate digger that digs three times.</em><br>
  Dig your sources -> Dig Soulseek -> Dig the file's spectrum.
</p>

<p align="center">
  <a href="https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-windows.zip">
    <img src="https://img.shields.io/badge/DOWNLOAD--ME-Windows%20.exe-1DB954?style=for-the-badge&logo=windows&logoColor=white" alt="Download DDD for Windows">
  </a>
  <a href="https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-macos-AppleSilicon.zip">
    <img src="https://img.shields.io/badge/DOWNLOAD--ME-macOS%20Apple%20Silicon-1DB954?style=for-the-badge&logo=apple&logoColor=white" alt="Download DDD for macOS">
  </a>
  <br>
  <a href="https://github.com/DNSZLSK/digdigdig/releases">
    <img src="https://img.shields.io/github/downloads/DNSZLSK/digdigdig/total?style=for-the-badge&label=downloads&color=1DB954" alt="Total downloads">
  </a>
  <br>
  <sub>Double-click, no install. <a href="https://dnszlsk.github.io/digdigdig/">Landing page</a></sub>
</p>

---

**DDD cleans up your music library and bumps it to club-playable quality, on its own.**

You point it at a folder (or your Discogs / Bandcamp favorites), and DDD:
- spots the **fake lossless**: MP3s re-encoded as .flac/.wav/.aiff that *look* lossless but aren't,
- goes and finds a **real version** on Soulseek (FLAC, WAV or AIFF, with an automatic MP3 320 fallback if nothing lossless turns up),
- checks the **spectrum** of the downloaded file to make sure it holds up (not an upscale), that it's the **right track** and not a snippet,
- files it into **a single** clean library, and sends the fakes to the trash.

No need to be a developer: download the `.exe`, double-click, it's a window.

## What it looks like

**Library - scan a folder and upgrade** (Lossless, HQ, Iffy or Bad? live per-track status):

<p align="center"><img src="docs/screenshot-library.png" alt="Library tab: quality scan + upgrade" width="900"></p>

**Sort by genre - filed from the spectrum** (local audio-ML; even untagged / badly named tracks):

<p align="center"><img src="docs/screenshot-sort-by.png" alt="Sort by genre tab: audio-ML filing" width="900"></p>

**Get favorites - Discogs / Bandcamp** straight to lossless:

<p align="center"><img src="docs/screenshot-get-favorite.png" alt="Get favorites tab" width="900"></p>

**YouTube set - paste a set / playlist URL**, DDD scrapes the tracklist:

<p align="center"><img src="docs/screenshot-youtube.png" alt="YouTube set tab" width="900"></p>

## What it does

- **Quality scan**: every file is ranked by its **spectral cutoff** (the frequency where the sound stops) into one of four bands, plus duplicates:
  - **Lossless** (green): full spectrum, real lossless.
  - **HQ** (blue): >= 18 kHz, playable on a big system (includes MP3 320).
  - **Iffy** (yellow): 16-18 kHz, borderline.
  - **Bad** (red): < 16 kHz, mush.
- **Quality / target modes** (in Settings) - the bar DDD keeps to, and what it goes hunting for:
  - **DJ Club** (>= 18 kHz) - *default*: keeps anything club-playable, MP3 320 included.
  - **Audiophile** (>= 20 kHz): rejects MP3s below 320.
  - **Purist** (pure lossless): real full-spectrum lossless only; if it's not on Soulseek -> buy links, no MP3 fallback.
  - **MP3 320** (vintage / mobile): hunts MP3 320 straight, skips FLAC - for old gear that won't read FLAC, or syncing over mobile data. Bumps your sub-320s up to 320; leaves the lossless you already have untouched.
  - **WAV/AIFF only** / **FLAC only**: target a single lossless container (old CDJs/samplers that won't read FLAC, or a FLAC-homogeneous library). DDD never transcodes what you already own - the mode just picks the format of what it fetches.
- **Upgrade**: replaces your below-the-bar files with something better, found on Soulseek. In **DJ Club / Audiophile** it looks for FLAC, WAV and AIFF (lots of DJs share in WAV/AIFF), with an **automatic MP3 320 fallback** for tracks that can't be found in lossless. MP3s below 320 kbps are **banned across the board**, whatever the mode.
- **Get favorites**: scrapes your Discogs wantlist / Bandcamp wishlist and downloads it.
- **YouTube set / playlist**: paste a set URL (YouTube / 1001Tracklists) or a **YouTube playlist** (each video = a track) -> DDD extracts the tracklist into a want-list (CSV).
- **Single library**: everything that passes lands in `~/Music/DDD` (changeable in Settings), de-duplicated. Rejects go to the **trash** (recoverable), never hard-deleted.
- **Sort by genre** (*Sort by genre* button / `ddd sort`): files your loose tracks into your own **vibe folders** through a cascade - the file's **ID3 genre tag**, then **Discogs** (+ MusicBrainz), and when both come up empty a **local audio model** (Discogs-EffNet, 400 Discogs styles) that reads the genre **from the spectrum** - so even an untagged, badly-named edit (`Track_01.flac`) lands in the right folder instead of `_INBOX`. The default set is house/techno-oriented (ACID, DEEPWATER, HOUSERZ, PROG, TECHNO, TRANCE, GARAGE, DISCO-FUNK, BREAKS-ELECTRO) and is **fully editable** in Settings. **Dry-run by default** - you preview, then Apply. Only loose files are touched, never your curated subfolders. (The audio model runs **on-device, no cloud**; by the MTG/UPF, CC BY-NC.)
- **Not found -> buy links**: whatever Soulseek can't find comes out as a clickable page (DDD logo + theme) with **Discogs** (vinyl marketplace, perfect for old pressings) and **Bandcamp** links to buy it.

**The safety net: the spectrum is law.** Every download is re-audited by spectrum (FFT); **the declared format and bitrate are only used for the Soulseek search, never for the keep-or-reject decision.** The spectrum doesn't lie, tags do - that's what tells a real 320 / lossless apart from an upscale (an MP3 128 re-encoded as .flac or .wav, which Soulseek's filters can't see). A file is only kept if it passes three checks: **spectral** (above the preset's bar, not an upscale), **duration** (not a snippet / preview) and **title + artist identity** (the right track, not a wrong match). Otherwise -> trash.

## Getting started (user)

> **You need a free Soulseek account** to download - create one in 30 seconds at [slsknet.org](https://www.slsknet.org/). DDD logs into Soulseek to fetch the upgrades; without it, DDD still scans and rates your files, but can't download anything. (sldl, the Soulseek client, is bundled in the `.exe` - nothing else to install.)

1. **Download** [Windows `.exe`](https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-windows.zip) or [macOS `.app`](https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-macos-AppleSilicon.zip) (Apple Silicon), unzip, double-click. On macOS, first launch: **right-click -> Open** (the app is unsigned).
2. Open **Settings** (gear, top right) and fill in:
   - your **Soulseek** login (required to download),
   - your **Discogs token** + username, and/or your **Bandcamp** username (to pull your favorites),
   - the **library folder** (default `~/Music/DDD`).
3. **Library** tab: pick a folder -> *Scan* -> check the files -> *Upgrade selection*.
   **Get favorites** tab: pick Discogs/Bandcamp -> *Fetch & download*.

> The 3 D's: **DIG** your sources -> **DOWNLOAD** from Soulseek -> **DETECT** by spectrum. The output
> is your verified lossless library, which you can then share / point anywhere you want.

## Usage & responsibility

DDD is a tool to manage **your** library (quality analysis, organization, fetching via Soulseek).
It **hosts, distributes and provides no content**: it's a client that automates the search, like a
browser or a torrent client.

Soulseek is a peer-to-peer network. Downloading copyrighted music without authorization from the
rights holders may be **illegal** in your country. **You are solely responsible for your use** and
for complying with copyright law. Use DDD for what you have the right to: your own music, your
productions, your promos / white-labels, public domain / CC, or re-downloading in lossless what you
**already own**.

## Stack

Portable **Python** core (Windows / Mac / Linux) + native **Flet** window. Downloading via
**sldl** ([fiso64/slsk-batchdl](https://github.com/fiso64/slsk-batchdl), bundled). Spectral detection
via numpy/scipy/soundfile; genre-from-audio via a **Discogs-EffNet** ONNX model run with **onnxruntime** (no TensorFlow). Scrapers for Discogs (API), Bandcamp (cloudscraper), YouTube sets and
playlists (yt-dlp). Everything is bundled into the `.exe` (no Python, no ffmpeg needed).

---

<details>
<summary><b>For developers</b> (CLI, exe build, legacy PowerShell pipeline)</summary>

### CLI

```powershell
# install the core + the GUI
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"

# Scan a folder: Lossless / HQ / Iffy / Bad? well named? duplicates?
.\.venv\Scripts\python.exe -m ddd scan "C:\path\to\Music"

# Upgrade: drops real lossless into the library, fake source -> trash
.\.venv\Scripts\python.exe -m ddd upgrade "C:\path\to\Music"

# Import an existing folder into the library (lossless kept, the rest trashed)
.\.venv\Scripts\python.exe -m ddd import "C:\path\to\Music"

# Rename a folder back to "Artist - Title" (from name + tags; dry-run, --apply to write)
.\.venv\Scripts\python.exe -m ddd rename "C:\path\to\Music"

# Sort loose tracks into vibe folders by genre (Discogs/MusicBrainz; dry-run, --apply to move)
.\.venv\Scripts\python.exe -m ddd sort "C:\path\to\Music" --library "C:\path\to\Music"

# Pull your favorites -> library
.\.venv\Scripts\python.exe -m ddd scrape bandcamp <user>
.\.venv\Scripts\python.exe -m ddd acquire outputs\bandcamp_<user>.csv

# DJ set or YouTube playlist (each video = a track) -> extract the tracklist to a want-list CSV
.\.venv\Scripts\python.exe -m ddd scrape djset "https://www.youtube.com/playlist?list=..."

# Not found -> Discogs + Bandcamp buy-links page (folder, upgrade report, or want-list)
.\.venv\Scripts\python.exe -m ddd buy "C:\path\to\Music"

# Settings (library folder, Discogs token, Soulseek login) -> %APPDATA%\ddd
.\.venv\Scripts\python.exe -m ddd config set download_dir "D:\My Library"
.\.venv\Scripts\python.exe -m ddd config set discogs_token <token>

# The native window
.\.venv\Scripts\python.exe -m ddd gui
```

Query resolution: name `Artist - Title` -> else ID3/Vorbis tags -> else title-only.
Compilations (`Various Artists`), vinyl side prefixes (`A1`, `B2`...) and artists duplicated
in the title are normalized before the search.

### Building the `.exe`

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui,build]"
.\packaging\build.ps1
```

Output: `dist\DDD\DDD.exe`. sldl, the profiles, the GUI client and the audio decoding
(libsndfile) are bundled. Details + Mac/Linux build: `packaging/README.md`.

### Docker (NAS / headless Linux)

The whole pipeline as a CLI image (no GUI): scan, upgrade, acquire, scrape, rename, sort, buy.
sldl is bundled, so Soulseek downloads work too - creds via environment variables.

```sh
docker build -t ddd .

# scan a mounted library (spectral audit)
docker run --rm -v /mnt/music:/music ddd scan /music -o /music/ddd-scan.csv

# upgrade via Soulseek (creds by env, output into the mounted library)
docker run --rm \
  -e DDD_SOULSEEK_USER=you -e DDD_SOULSEEK_PASS=secret \
  -v /mnt/music:/music \
  ddd upgrade /music --download-dir /music --apply
```

x86_64 only. Details: `docker/README.md`.

### Legacy PowerShell pipeline (still available)

The project started as a PowerShell pipeline (`pipeline.ps1` + `lib/`), which still works:

```powershell
$env:DISCOGS_TOKEN = "your_token"
.\.venv\Scripts\python.exe lib\scrapers\discogs.py <user> -o inputs\sldl_input.csv
.\pipeline.ps1 -SkipConvert -AutoClean       # DOWNLOAD + audit + DETECT
.\pipeline.ps1 -SkipConvert -SkipDownload -SkipVerify -DoDeploy -UsbRoot "D:\My Library"
```

</details>
