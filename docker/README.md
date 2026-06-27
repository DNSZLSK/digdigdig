# DDD dans Docker

Image **CLI headless** pour faire tourner DDD sur un NAS / serveur Linux sans
ecran. Cas d'usage premier : **`ddd scan`**, l'audit lossless / anti-faux-FLAC
d'une grosse librairie montee en volume.

## Build

```sh
docker build -t ddd .
```

## Scanner une librairie

```sh
docker run --rm -v /mnt/musique:/music ddd scan /music -o /music/ddd-scan.csv
```

- `-v /mnt/musique:/music` monte ta librairie dans le conteneur.
- `-o /music/ddd-scan.csv` ecrit le rapport a cote de ta musique. Sans `-o` il
  atterrit dans `/app/outputs` *dans* le conteneur et disparait a la sortie
  (voir "Garder les rapports" plus bas).

Le rapport CSV/JSON liste, par fichier : verdict qualite (LOSSLESS / HQ / douteux
/ mauvais, par cutoff spectral), nom vs tags ID3, et les doublons.

## Ce qui marche dans cette image

`scan`, `rename`, `sort`, `buy`, `scrape` : tout le coeur qui ne touche pas
Soulseek. Pour les commandes qui interrogent Discogs (`scrape`, `sort`), passe
ton token par l'environnement :

```sh
docker run --rm -e DISCOGS_TOKEN=xxxx -v /mnt/musique:/music ddd sort /music --apply
```

## Ce qui ne marche PAS (encore)

`upgrade` et `acquire` passent par Soulseek (binaire **sldl**), non embarque ici.
Une image "full pipeline" suivra : sldl Linux + creds par env
(`DDD_SOULSEEK_USER` / `DDD_SOULSEEK_PASS`) + mapping de ports + PUID/PGID pour
les droits des fichiers ecrits sur le NAS.

## Garder les rapports / donnees hors du conteneur

Tout ce que DDD ecrit (rapports, logs, caches) vit sous `/app`. Pour le persister,
monte un dossier hote sur `/app/outputs` :

```sh
docker run --rm \
  -v /mnt/musique:/music \
  -v "$PWD/ddd-data":/app/outputs \
  ddd scan /music
```
