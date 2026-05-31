<p align="center">
  <img src="docs/logo.png" alt="DDD - DigDigDig" width="400">
</p>

<h1 align="center">DigDigDig</h1>

<p align="center">
  <em>Le crate digger qui creuse trois fois.</em><br>
  Dig tes sources -> Dig Soulseek -> Dig le spectre du fichier.
</p>

<p align="center">
  <a href="https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-windows.zip">
    <img src="https://img.shields.io/badge/T%C3%89L%C3%89CHARGE--MOI-Windows%20.exe-1DB954?style=for-the-badge&logo=windows&logoColor=white" alt="Telecharge DDD pour Windows">
  </a>
  <br>
  <sub>Double-clic, aucune installation. <a href="https://dnszlsk.github.io/digdigdig/">Page de presentation</a></sub>
</p>

---

**DDD nettoie ta bibliotheque musicale et la passe en vrai lossless, tout seul.**

Tu pointes un dossier (ou tes favoris Discogs / Bandcamp), et DDD :
- repere les **faux FLAC** : des MP3 reencodes en .flac/.wav qui *paraissent* lossless mais ne le sont pas,
- va chercher la **vraie version lossless** sur Soulseek,
- verifie au **spectre** que le fichier telecharge est authentique (pas un upscale), que c'est le **bon morceau** et pas un extrait,
- le range dans **une seule bibliotheque** propre, et jette les faux a la corbeille.

Pas besoin d'etre developpeur : telecharge l'`.exe`, double-clic, c'est une fenetre.

## A quoi ca ressemble

**Scanner un dossier et upgrader** (vrai lossless ou faux ? statut live par piste) :

<p align="center"><img src="docs/screenshot-bibliotheque.png" alt="Onglet Bibliotheque : scan qualite + upgrade" width="900"></p>

**Recuperer tes favoris Discogs / Bandcamp** directement en lossless :

<p align="center"><img src="docs/screenshot-favoris.png" alt="Onglet Recuperer favoris" width="900"></p>

## Ce que ca fait

- **Scan qualite** : pour chaque fichier, vrai lossless / faux lossless / suspect 320k / lossy, + doublons.
- **Upgrade** : remplace tes faux/lossy par de vrais lossless trouves sur Soulseek.
- **Recuperer favoris** : scrape ta wantlist Discogs / wishlist Bandcamp et la telecharge en lossless.
- **Une seule bibliotheque** : tout ce qui est valide atterrit dans `~/Music/DDD` (modifiable dans les Reglages), dedoublonne. Les rejets partent a la **corbeille** (recuperables), jamais supprimes en dur.

**Le filet de securite** : un fichier telecharge n'est garde que s'il passe trois controles - **spectral** (vrai lossless, pas un upscale MP3 deguise en FLAC, ce que les filtres Soulseek ne voient pas), **duree** (pas un extrait / preview), et **identite titre + artiste** (le bon morceau, pas un faux match). Sinon -> corbeille.

## Demarrer (utilisateur)

1. [**Telecharge l'exe**](https://github.com/DNSZLSK/digdigdig/releases/latest/download/DDD-windows.zip), dezippe, double-clic sur `DDD.exe`.
2. Ouvre les **Reglages** (engrenage en haut a droite) et renseigne :
   - ton login **Soulseek** (requis pour telecharger),
   - ton **token Discogs** + username, et/ou ton username **Bandcamp** (pour recuperer tes favoris),
   - le **dossier bibliotheque** (par defaut `~/Music/DDD`).
3. Onglet **Bibliotheque** : choisis un dossier -> *Scanner* -> coche les fichiers -> *Upgrader la selection*.
   Onglet **Recuperer favoris** : choisis Discogs/Bandcamp -> *Recuperer & telecharger*.

> Les 3 D : **DIG** tes sources -> **DOWNLOAD** sur Soulseek -> **DETECT** au spectre. La sortie,
> c'est ta bibliotheque lossless verifiee, que tu peux ensuite partager / pointer ou tu veux.

## Stack

Coeur **Python** portable (Windows / Mac / Linux) + fenetre native **Flet**. Telechargement via
**sldl** ([fiso64/slsk-batchdl](https://github.com/fiso64/slsk-batchdl), embarque). Detection spectrale
via numpy/scipy/soundfile. Scrapers Discogs (API) et Bandcamp (cloudscraper). Tout est embarque dans
l'`.exe` (pas besoin de Python ni de ffmpeg).

---

<details>
<summary><b>Pour les developpeurs</b> (CLI, build de l'exe, ancien pipeline PowerShell)</summary>

### CLI

```powershell
# installer le coeur + la GUI
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"

# Scanner un dossier : vrai lossless ou faux ? bien nomme ? doublons ?
.\.venv\Scripts\python.exe -m ddd scan "C:\chemin\vers\Musique"

# Upgrader : depose les vrais lossless dans la bibliotheque, faux source -> corbeille
.\.venv\Scripts\python.exe -m ddd upgrade "C:\chemin\vers\Musique"

# Importer un dossier existant dans la bibliotheque (AUTHENTIC garde, reste corbeille)
.\.venv\Scripts\python.exe -m ddd import "C:\chemin\vers\Musique"

# Recuperer ses favoris -> bibliotheque
.\.venv\Scripts\python.exe -m ddd scrape bandcamp <user>
.\.venv\Scripts\python.exe -m ddd acquire outputs\bandcamp_<user>.csv

# Reglages (dossier bibliotheque, token Discogs, login Soulseek) -> %APPDATA%\ddd
.\.venv\Scripts\python.exe -m ddd config set download_dir "D:\Ma Bibliotheque"
.\.venv\Scripts\python.exe -m ddd config set discogs_token <token>

# La fenetre native
.\.venv\Scripts\python.exe -m ddd gui
```

Resolution des requetes : nom `Artiste - Titre` -> sinon tags ID3/Vorbis -> sinon titre-seul.
Les compilations (`Various Artists`), prefixes de face vinyle (`A1`, `B2`...) et artistes
dupliques dans le titre sont normalises avant la recherche.

### Build du `.exe`

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui,build]"
.\packaging\build.ps1
```

Sortie : `dist\DDD\DDD.exe`. sldl, les profils, le client graphique et le decodage audio
(libsndfile) sont embarques. Details + build Mac/Linux : `packaging/README.md`.

### Ancien pipeline PowerShell (toujours dispo)

Le projet a demarre comme un pipeline PowerShell (`pipeline.ps1` + `lib/`), qui reste fonctionnel :

```powershell
$env:DISCOGS_TOKEN = "ton_token"
.\.venv\Scripts\python.exe lib\scrapers\discogs.py <user> -o inputs\sldl_input.csv
.\pipeline.ps1 -SkipConvert -AutoClean       # DOWNLOAD + audit + DETECT
.\pipeline.ps1 -SkipConvert -SkipDownload -SkipVerify -DoDeploy -UsbRoot "D:\Ma Library"
```

</details>
