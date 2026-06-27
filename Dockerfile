# DDD - DigDigDig : image Docker pipeline complet (CLI headless, sans GUI).
#
# Cible : NAS / serveur Linux sans ecran. Couvre tout le pipeline :
#   - DIG      : scrape (Discogs / Bandcamp / djset)
#   - DOWNLOAD : upgrade / acquire via Soulseek (binaire sldl embarque)
#   - DETECT   : scan (audit lossless / anti-faux-FLAC par cutoff spectral)
#   + rename / sort / buy.
#
#   docker build -t ddd .
#   docker run --rm -v /mnt/musique:/music ddd scan /music -o /music/ddd-scan.csv
#   docker run --rm -e DDD_SOULSEEK_USER=xxx -e DDD_SOULSEEK_PASS=yyy \
#       -v /mnt/musique:/music ddd upgrade /music --apply
#
# Image x86_64 : le binaire sldl upstream n'existe qu'en linux-x64 (pas d'arm64).

# --- Stage 1 : recuperation du binaire Soulseek (sldl) -----------------------
# Meme upstream / version que le build macOS (fiso64/sockseek v2.6.0). Le binaire
# linux-x64 est self-contained (.NET embarque, ~11 Mo zippe) : aucun runtime .NET
# a installer, juste libicu au runtime (cf. stage final). Isole dans un stage
# jetable pour ne laisser ni curl/unzip ni le zip dans l'image finale.
FROM debian:trixie-slim AS sldl-fetch
ARG SLDL_VERSION=v2.6.0
ARG SLDL_ASSET=sldl_linux-x64.zip
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && curl -fSL -o /tmp/sldl.zip \
      "https://github.com/fiso64/sockseek/releases/download/${SLDL_VERSION}/${SLDL_ASSET}" \
 && unzip -o /tmp/sldl.zip -d /tmp/sldl_x \
 && sldl_bin="$(find /tmp/sldl_x -type f -name sldl | head -n1)" \
 && test -n "$sldl_bin" \
 && mkdir -p /opt/sldl \
 && cp -a "$(dirname "$sldl_bin")/." /opt/sldl/ \
 && chmod +x /opt/sldl/sldl

# --- Stage 2 : image finale --------------------------------------------------
FROM python:3.12-slim

# libsndfile1 : backend natif de `soundfile` (decode WAV/FLAC/AIFF/MP3 pour
#   l'analyse spectrale). Les wheels Linux de soundfile l'embarquent en general,
#   on le met quand meme (insurance + arches non-x86).
# libicu**   : requis par sldl (.NET self-contained), sinon crash globalisation
#   au demarrage ("Couldn't find a valid ICU package"). Le soname change selon
#   la Debian de base (72 sur bookworm, 76 sur trixie, ...) : on installe donc
#   le plus haut libicuNN disponible plutot qu'un numero en dur, pour survivre
#   aux bumps de l'image de base sans rien casser.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libsndfile1 \
      "$(apt-cache pkgnames libicu | grep -E '^libicu[0-9]+$' | sort -V | tail -n1)" \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Couche deps (cachee tant que pyproject / version ne bougent pas) ---
# La version est lue depuis ddd/__init__.py (setuptools attr:), il doit donc
# etre present pour resoudre le paquet. Install en editable : le package reste
# physiquement sous /app/ddd, donc paths.data_base() == /app (outputs / logs /
# staging predictibles et montables) sans toucher au coeur. PYTHONPATH=/app est
# la ceinture aux bretelles de l'editable : imports garantis depuis /app quel
# que soit le mode de finder setuptools.
ENV PYTHONPATH=/app
COPY pyproject.toml README.md ./
COPY ddd/__init__.py ./ddd/__init__.py
RUN pip install --no-cache-dir -e .

# --- Couche source (change souvent) ---
COPY ddd/ ./ddd/
COPY config/ ./config/

# Binaire Soulseek -> /app/bin/sldl/sldl, exactement ou paths.sldl_exe() le
# cherche (resource_base() == /app hors-frozen). Active upgrade / acquire.
COPY --from=sldl-fetch /opt/sldl/ ./bin/sldl/

# /music = librairie montee au run ; /app/outputs = rapports (montable pour les
# garder hors conteneur). Soulseek (upgrade/acquire) : creds par env
# DDD_SOULSEEK_USER / DDD_SOULSEEK_PASS. Pas de VOLUME declare : les mounts
# marchent sans, et ca evite les volumes anonymes qui s'empilent.
ENTRYPOINT ["ddd"]
CMD ["--help"]
