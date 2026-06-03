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

**DDD nettoie ta bibliotheque musicale et la passe en qualite jouable en club, tout seul.**

Tu pointes un dossier (ou tes favoris Discogs / Bandcamp), et DDD :
- repere les **faux lossless** : des MP3 reencodes en .flac/.wav/.aiff qui *paraissent* lossless mais ne le sont pas,
- va chercher une **vraie version** sur Soulseek (FLAC, WAV ou AIFF, avec repli MP3 320 automatique si rien en lossless),
- verifie au **spectre** que le fichier telecharge tient la route (pas un upscale), que c'est le **bon morceau** et pas un extrait,
- le range dans **une seule bibliotheque** propre, et jette les faux a la corbeille.

Pas besoin d'etre developpeur : telecharge l'`.exe`, double-clic, c'est une fenetre.

## A quoi ca ressemble

**Scanner un dossier et upgrader** (Lossless, HQ, Douteux ou Mauvais ? statut live par piste) :

<p align="center"><img src="docs/screenshot-bibliotheque.png" alt="Onglet Bibliotheque : scan qualite + upgrade" width="900"></p>

**Recuperer tes favoris Discogs / Bandcamp** directement en lossless :

<p align="center"><img src="docs/screenshot-favoris.png" alt="Onglet Recuperer favoris" width="900"></p>

## Ce que ca fait

- **Scan qualite** : chaque fichier est classe par son **cutoff spectral** (la frequence ou le son s'arrete) dans une des quatre bandes, + doublons :
  - **Lossless** (vert) : plein spectre, vrai lossless.
  - **HQ** (bleu) : >= 18 kHz, jouable sur un gros systeme (inclut le MP3 320).
  - **Douteux** (jaune) : 16-18 kHz, limite.
  - **Mauvais** (rouge) : < 16 kHz, bouillie.
- **Trois presets de qualite** (seuil minimum a garder, dans les Reglages) :
  - **DJ Club** (>= 18 kHz) - *defaut* : garde tout ce qui est jouable en club, MP3 320 inclus.
  - **Audiophile** (>= 20 kHz) : rejette les MP3 sous 320.
  - **Puriste** (lossless pur) : vrai lossless plein spectre uniquement.
- **Upgrade** : remplace tes fichiers sous le seuil par mieux, trouve sur Soulseek. DDD cherche FLAC, WAV et AIFF (beaucoup de DJ partagent en WAV/AIFF), avec **repli MP3 320 automatique** pour les pistes introuvables en lossless. Les MP3 sous 320 kbps sont **bannis systematiquement**, quel que soit le preset.
- **Recuperer favoris** : scrape ta wantlist Discogs / wishlist Bandcamp et la telecharge.
- **Une seule bibliotheque** : tout ce qui est valide atterrit dans `~/Music/DDD` (modifiable dans les Reglages), dedoublonne. Les rejets partent a la **corbeille** (recuperables), jamais supprimes en dur.

**Le filet de securite : le spectre fait loi.** Chaque telechargement est re-audite au spectre (FFT) ; **le format et le bitrate declares ne servent qu'a la recherche Soulseek, jamais a la decision de garder ou rejeter.** Le spectre ne ment pas, les tags si - c'est ce qui distingue un vrai 320 / lossless d'un upscale (un MP3 128 reencode en .flac ou .wav, ce que les filtres Soulseek ne voient pas). Un fichier n'est garde que s'il passe trois controles : **spectral** (au-dessus du seuil du preset, pas un upscale), **duree** (pas un extrait / preview) et **identite titre + artiste** (le bon morceau, pas un faux match). Sinon -> corbeille.

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

## Usage & responsabilite

DDD est un outil de gestion de **ta** bibliotheque (analyse qualite, organisation,
recuperation via Soulseek). Il **n'heberge, ne distribue et ne fournit aucun contenu** :
c'est un client qui automatise la recherche, comme un navigateur ou un client torrent.

Soulseek est un reseau peer-to-peer. Telecharger de la musique protegee sans autorisation
des ayants droit peut etre **illegal** selon ton pays (France : Code de la propriete
intellectuelle). **Tu es seul responsable de ton usage** et du respect du droit d'auteur.
Utilise DDD pour ce que tu as le droit d'utiliser : ta propre musique, tes productions,
tes promos / white-labels, le domaine public/CC, ou re-telecharger en lossless ce que tu
**possedes deja**.

> *DDD is a library-management tool; it hosts no content. Downloading copyrighted material
> without authorization may be illegal in your country. You are solely responsible for your
> use and for complying with copyright law.*

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

# Scanner un dossier : Lossless / HQ / Douteux / Mauvais ? bien nomme ? doublons ?
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
