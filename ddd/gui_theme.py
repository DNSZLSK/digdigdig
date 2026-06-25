"""Palette + helpers de presentation pour la fenetre DDD (theme creme "crate digger").

Module PUR (aucun import flet) : couleurs + petites fonctions de formatage de
chaines pour le tableau. Importable headless (le test de build GUI s'en sert).

Le miroir de cette palette vit dans docs/index.html (:root) si on veut que le site
colle a l'app. Sync optionnelle et separee (le site reste sombre pour l'instant).
"""

from __future__ import annotations

from .core import naming, quality

# --- Palette creme "crate digger" -------------------------------------------
# Valeurs EXACTES reprises du mockup HTML de reference (Desktop/DigDigDig.html).
BG = "#F6EEDD"        # corps de l'app (ivoire chaud, UNIFORME : pas de carte grise)
SHELL = "#E1D2B6"     # pourtour / barre de titre du mockup (beige plus dense)
SURFACE = "#EFE6D2"   # fond des pastilles BAND
FIELD_BG = "#FBF5E8"  # champs de saisie (un poil plus clair que le corps)
LINE = "#E2D5BC"      # filets / dividers / bordures discretes
INK = "#1F1A14"       # texte principal + bouton Scan + wordmark (brun presque noir)
INK_DIM = "#8A7E68"   # texte secondaire (artiste, cutoff, tagline, footer)
INK_FAINT = "#A2967E"  # en-tetes de colonnes en capitales
PINK = "#C4476B"      # accent crimson (onglet actif, Upgrade, coches, statut probleme)
DOT_GREEN = "#3DA56A"  # point "slsk connected" (vert plus vif que la bande)

# Bande qualite - tons VIFS pour les points de stats + la barre empilee + le statut.
GREEN = "#5A8C6E"
BLUE = "#5E7D9C"
TAN = "#A8843F"
BRICK = "#B06258"
NEUTRAL = "#9A8E78"
# Bande qualite - tons SOMBRES pour le TEXTE des pastilles BAND (lisible sur #EFE6D2).
BAND_GREEN = "#4A7D5E"
BAND_BLUE = "#4F6C8A"
BAND_TAN = "#8A6F3A"
BAND_BRICK = "#9E5A52"

VERDICT_COLOR = {
    quality.LOSSLESS: GREEN,
    quality.HQ: BLUE,
    quality.DOUTEUX: TAN,
    quality.MAUVAIS: BRICK,
    "ERROR": NEUTRAL,
    "SKIPPED": NEUTRAL,
}
VERDICT_LABEL = {
    quality.LOSSLESS: "Lossless",
    quality.HQ: "HQ",
    quality.DOUTEUX: "Iffy",
    quality.MAUVAIS: "Bad",
    "ERROR": "Error",
    "SKIPPED": "Skipped",
}
# Texte des pastilles BAND (tons sombres) ; le fond de pastille est UNIFORME (#EFE6D2).
BAND_TEXT = {
    quality.LOSSLESS: BAND_GREEN,
    quality.HQ: BAND_BLUE,
    quality.DOUTEUX: BAND_TAN,
    quality.MAUVAIS: BAND_BRICK,
    "ERROR": NEUTRAL,
    "SKIPPED": NEUTRAL,
}
BAND_BG = {v: SURFACE for v in (quality.LOSSLESS, quality.HQ, quality.DOUTEUX,
                                quality.MAUVAIS, "ERROR", "SKIPPED")}


def band_label(verdict: str) -> str:
    """Libelle court majuscule pour la pastille BAND. SKIPPED/ERROR -> 'SCAN'."""
    if verdict in (quality.SKIPPED, quality.ERROR):
        return "SCAN"
    return VERDICT_LABEL.get(verdict, verdict).upper()


def format_label(q) -> str:
    """Format lisible pour la colonne FORMAT : '.flac' / '.wav' / 'mp3 320' / '-'."""
    if q.verdict in (quality.SKIPPED, quality.ERROR):
        return "-"
    ext = (q.ext or "").lower()
    if q.format_class == "lossy":
        codec = ext.lstrip(".") or "mp3"
        kbps = round(q.container_bitrate or 0)
        return f"{codec} {kbps}" if kbps else codec
    return ext or "-"


def track_title_artist(rec) -> tuple:
    """(titre, artiste) pour la colonne TRACK : nom propre -> tags -> filename, NETTOYES
    pour l'affichage via naming.display_artist_title (retire [label/catalogue], prefixe
    promo 'Premiere_ ...', mots promo entre parentheses)."""
    n = rec.naming
    raw_title = n.name_title or n.tag_title or ""
    raw_artist = n.name_artist or n.tag_artist or ""
    artist, title = naming.display_artist_title(raw_artist, raw_title)
    if not title:
        title = naming.search_title((rec.quality.filename or "").rsplit(".", 1)[0])
    return title, artist


def status_oneliner(rec, preset: str) -> tuple:
    """(texte, is_problem) : une ligne composee 'qualite . sort' decrivant l'etat AU REPOS.

    Pendant un upgrade, la cellule est ecrasee live par les libelles de phase/action.
    is_problem=True -> le fichier est sous la barre (candidat upgrade) -> texte en rose.
    """
    q = rec.quality
    v = q.verdict
    ext = (q.ext or "").lower()
    if v in (quality.SKIPPED, quality.ERROR):
        return (q.reason or "not analysed"), False
    if quality.is_accepted(q, preset):
        if v == quality.LOSSLESS:
            if ext == ".wav":
                word = "real WAV"
            elif ext in (".aiff", ".aif"):
                word = "real AIFF"
            else:
                word = "real lossless"
            return f"{word} · kept", False
        if q.format_class == "lossy":
            return f"{format_label(q)} · club-ok · kept", False
        return "club-ok · kept", False
    # Sous la barre -> candidat a l'upgrade.
    if v == quality.MAUVAIS:
        if q.format_class == "lossless_container":
            return "upscale -> trash", True
        return "low bitrate -> trash", True
    if q.format_class == "lossless_container":
        return "fake lossless -> hunting slsk...", True
    return "below bar -> hunting slsk...", True
