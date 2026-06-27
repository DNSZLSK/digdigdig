# DDD dans Docker

Image **CLI headless** : tout le pipeline DDD (DIG / DOWNLOAD / DETECT) sur un
NAS / serveur Linux sans ecran, sans la fenetre graphique. Le binaire Soulseek
`sldl` est embarque, donc `upgrade` / `acquire` marchent aussi.

## Build

```sh
docker build -t ddd .
```

Image x86_64 (le binaire `sldl` upstream n'existe qu'en linux-x64).

## Scanner une librairie (audit lossless / anti-faux-FLAC)

```sh
docker run --rm -v /mnt/musique:/music ddd scan /music -o /music/ddd-scan.csv
```

- `-v /mnt/musique:/music` monte ta librairie dans le conteneur.
- `-o /music/ddd-scan.csv` ecrit le rapport a cote de ta musique. Sans `-o` il
  atterrit dans `/app/outputs` *dans* le conteneur et disparait a la sortie
  (voir "Garder les rapports" plus bas).

Le rapport CSV/JSON liste, par fichier : verdict qualite (LOSSLESS / HQ / douteux
/ mauvais, par cutoff spectral), nom vs tags ID3, et les doublons.

## Telecharger en vrai lossless via Soulseek (upgrade / acquire)

Passe tes creds Soulseek par l'environnement, et pointe la bibliotheque de sortie
sur ton volume monte avec `--download-dir` pour recuperer les fichiers :

```sh
docker run --rm \
  -e DDD_SOULSEEK_USER=tonuser -e DDD_SOULSEEK_PASS=tonpass \
  -v /mnt/musique:/music \
  ddd upgrade /music --download-dir /music --apply
```

`upgrade` re-audite chaque download et ne garde que le vrai AUTHENTIC (les filtres
sldl ne voient pas les upscales, le re-audit si). Sans `--download-dir`, la sortie
va dans `~/Music/DDD` *dans* le conteneur (donc perdue a la sortie). `acquire
<wantlist.csv>` telecharge une want-list (issue de `scrape`) de la meme facon.

Note : les downloads Soulseek sont volontairement throttle (anti-ban), un gros run
prend du temps - c'est normal, pas un blocage.

## Tout ce qui marche

`scan`, `upgrade`, `acquire`, `scrape`, `rename`, `sort`, `buy`. Les commandes qui
interrogent Discogs (`scrape`, `sort`) prennent le token par l'environnement :

```sh
docker run --rm -e DISCOGS_TOKEN=xxxx -v /mnt/musique:/music ddd sort /music --apply
```

## Garder les rapports / donnees hors du conteneur

Tout ce que DDD ecrit (rapports, logs, caches) vit sous `/app`. Pour le persister,
monte un dossier hote sur `/app/outputs` :

```sh
docker run --rm \
  -v /mnt/musique:/music \
  -v "$PWD/ddd-data":/app/outputs \
  ddd scan /music
```
