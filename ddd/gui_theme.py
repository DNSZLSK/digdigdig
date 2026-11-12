"""Palette + helpers de presentation pour la fenetre DDD (theme sombre "club").

Module PUR (aucun import flet) : couleurs + petites fonctions de formatage de
chaines pour le tableau. Importable headless (le test de build GUI s'en sert).

Le miroir de cette palette vit dans docs/index.html (:root) si on veut que le site
colle a l'app. Sync optionnelle et separee (le site reste sombre pour l'instant).
"""

from __future__ import annotations

from .core import naming, quality

# --- Palette sombre "club" ---------------------------------------------------
# Fond quasi noir froid, accent lime acide (flyer rave), bandes saturees.
BG = "#111316"        # corps de l'app
SHELL = "#0B0C0E"     # pourtour
SURFACE = "#1A1D22"   # fond des pastilles BAND / petits panneaux
FIELD_BG = "#181B20"  # champs de saisie
LINE = "#2A2E35"      # filets / dividers / bordures
INK = "#E8E8E8"       # texte principal + bouton Scan + wordmark
INK_DIM = "#8B9099"   # texte secondaire
INK_FAINT = "#5E636C"  # en-tetes de colonnes en capitales
PINK = "#C6DD62"      # ACCENT lime (onglet actif, Upgrade, coches, focus) - nom garde pour compat
ON_ACCENT = "#111316"  # texte pose sur l'accent
ALERT = "#EC6156"     # statut probleme (sous la barre, rejets)
BTN_BG = "#252930"    # boutons d'action secondaires (Scan, Sort, Fetch, Identify)
BTN_FG = "#E8E8E8"    # texte de ces boutons
DOT_GREEN = "#9CCB5B"  # point "slsk connected"

# Bande qualite - tons VIFS pour les points de stats + la barre empilee + le statut.
GREEN = "#9CCB5B"
BLUE = "#55BDD8"
TAN = "#DDA84E"
BRICK = "#E0584C"
NEUTRAL = "#6B7079"
# Bande qualite - texte des pastilles BAND (memes tons vifs, lisibles sur fond sombre).
BAND_GREEN = GREEN
BAND_BLUE = BLUE
BAND_TAN = TAN
BAND_BRICK = BRICK

VERDICT_COLOR = {
    quality.LOSSLESS: GREEN,
    quality.HQ: BLUE,
    quality.DOUTEUX: TAN,
    quality.MAUVAIS: BRICK,
    "ERROR": NEUTRAL,
    "SKIPPED": NEUTRAL,
}
VERDICT_LABEL = {
    quality.LOSSLESS: "Wide",
    quality.HQ: "HQ",
    quality.DOUTEUX: "Iffy",
    quality.MAUVAIS: "Review",
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
    if q.confidence == "suspect":          # plein spectre mais artefacts codec -> a verifier
        return "suspect: codec artifacts -> verify", True
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
