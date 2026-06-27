# DDD - DigDigDig : image Docker "core" (CLI headless, sans GUI).
#
# Cible : NAS / serveur Linux sans ecran. Cas d'usage premier = `ddd scan`,
# l'audit lossless / anti-faux-FLAC d'une grosse librairie montee en volume.
#
#   docker build -t ddd .
#   docker run --rm -v /mnt/musique:/music ddd scan /music -o /music/ddd-scan.csv
#
# Marchent aussi : rename, sort, buy, scrape. Les commandes Soulseek
# (upgrade / acquire) ont besoin du binaire sldl Linux, PAS embarque ici
# -> image "full pipeline" a venir.

FROM python:3.12-slim

# libsndfile1 : backend natif de `soundfile` (decode WAV/FLAC/AIFF/MP3 pour
# l'analyse spectrale). Les wheels Linux de soundfile l'embarquent en general,
# on le met quand meme (insurance + arches non-x86).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsndfile1 \
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

# /music = librairie montee au run ; /app/outputs = rapports (montable pour les
# garder hors conteneur). Pas de VOLUME declare : les mounts marchent sans, et
# ca evite les volumes anonymes qui s'empilent.
ENTRYPOINT ["ddd"]
CMD ["--help"]
