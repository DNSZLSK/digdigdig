"""Scrapers de favoris -> liste de pistes (Artist/Title/Length/...).

Chaque scraper expose `scrape(...) -> list[dict]` avec les colonnes :
Artist, Title, Album, Length, Year, Source, SourceUrl. Reutilise par la CLI
`ddd scrape` et la future GUI.
"""

from .discogs import scrape_discogs
from .bandcamp import scrape_bandcamp
from .djset import scrape_djset

# Colonnes CSV standard (ordre) partagees par tous les scrapers
ROW_FIELDS = ["Artist", "Title", "Album", "Length", "Year", "Source", "SourceUrl"]

SOURCES = {
    "discogs": scrape_discogs,
    "bandcamp": scrape_bandcamp,
    "djset": scrape_djset,
}

__all__ = ["scrape_discogs", "scrape_bandcamp", "scrape_djset", "ROW_FIELDS", "SOURCES"]
