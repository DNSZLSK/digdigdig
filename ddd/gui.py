"""Fenetre native DDD (Flet).

Deux onglets :
  - Bibliotheque : choisir un dossier, scanner la qualite (vrai lossless ou non),
    voir le resultat dans une liste filtrable, puis upgrader les fichiers flagges
    via Soulseek (re-audit spectral anti-upscale).
  - Recuperer favoris : scraper sa want-list Discogs / wishlist Bandcamp et
    telecharger les pistes en vrai lossless vers un dossier inbox.

Reglages (token Discogs, login Soulseek) persistes via core.config.

Lancement : `ddd gui` ou `python -m ddd gui`. Le coeur etant pur Python, la meme
fenetre tourne sur Windows / Mac / Linux.

Cible Flet 0.28.x (ligne stable). La 0.85+ ("Flet 1.0", alpha) a des constructeurs
incompatibles - voir pyproject [project.optional-dependencies].gui.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
from pathlib import Path
from typing import List, Optional

import flet as ft

from . import __version__
from . import paths
from . import gui_theme as theme
from .core import config as config_mod
from .core import quality
from .core import soulseek
from .core import upgrade as upgrade_mod
from .core import organize as organize_mod
from .core import stores as stores_mod
from .core import updates as updates_mod
from .core.naming import match_key
from .core.scan import ScanRecord, duplicate_groups, scan_library

# --- Palette creme "crate digger" (definie dans gui_theme, miroir docs/index.html) ---
# On garde les memes NOMS qu'avant (BG/SURFACE/BORDER/TXT/...) pour ne pas reecrire
# tous les `color=`/`bgcolor=` du fichier : seules les valeurs basculent vers le creme.
BG = theme.BG               # fond fenetre + zone table (ivoire chaud, uniforme)
SURFACE = theme.SURFACE     # pastilles / petits panneaux
FIELD_BG = theme.FIELD_BG   # fond des champs de saisie
BORDER = theme.LINE         # filets / dividers discrets
TXT = theme.INK             # texte principal (brun presque noir)
TXT_DIM = theme.INK_DIM     # texte secondaire (brun-gris chaud)
TXT_FAINT = theme.INK_FAINT  # en-tetes de colonnes (capitales pales)
ACCENT = theme.PINK         # accent crimson (Upgrade + onglet actif + coches)
PINK = theme.PINK           # alias explicite (coches, bordure focus, statut probleme)
SPIN = theme.PINK           # spinners / progress, sur l'accent

# Plafond de lignes CONSTRUITES dans le tableau : le ListView virtualise l'affichage mais
# pas la construction Python (~12 widgets/ligne) ni le page.update() -> a 10k-95k lignes ca
# fige/OOM. On cappe l'AFFICHAGE (l'upgrade en masse, lui, traite tout state.records).
MAX_TABLE_ROWS = 1000

# Polices custom (servies depuis ddd/assets/fonts via ft.app(assets_dir=...)).
FONT_SLAB = "Anton"         # wordmark DIGDIGDIG (display Anton, repli sur le defaut si absente)
FONT_MONO = "DMMono"        # tagline + pastilles + cutoff (DM Mono, repli si absente)

# Formulaire de retours (Google Form / Tally) : case "je kiffe l'app" + suggestions.
# Aucun compte requis cote user. Colle l'URL de TON formulaire ici une fois cree.
FEEDBACK_URL = ("https://docs.google.com/forms/d/e/"
                "1FAIpQLSfcKbhE67BWQsq2SdHMQItMrCc-seh6BSbwJj5VletoY2t_GA/viewform")

VERDICT_COLOR = theme.VERDICT_COLOR
VERDICT_LABEL = theme.VERDICT_LABEL
BAND_BG = theme.BAND_BG

# Largeurs des colonnes du tableau (header + lignes alignes).
COL_CHECK = 38
COL_FMT = 96
COL_CUT = 84
COL_BAND = 84
COL_STATUS = 232


def _is_audio_row(qr) -> bool:
    """Vrai si la ligne est un vrai fichier audio analysable (pas un SKIPPED/ERROR).

    Les checkboxes ne sont verrouillees que pour ces non-audio. Tout vrai fichier reste
    cochable a la main, MEME s'il passe deja la barre du preset : l'user veut pouvoir
    choisir track par track (re-grab une track qu'il juge mauvaise a l'oreille meme si
    le cutoff la dit bonne). `_is_upgradable` reste le "sous la barre" (selection par
    defaut + compteur + filtre 'upgradable'), il ne pilote plus le disabled.
    """
    return qr.verdict not in (quality.SKIPPED, quality.ERROR)


def _is_upgradable(qr, preset):
    return _is_audio_row(qr) and not quality.is_accepted(qr, preset)

# Statut live par ligne. Tuple = (libelle, couleur, ring_anime).
# ring_anime=True -> petit spinner visible (phase en cours) ; False -> etat final fige.
PHASE_LABEL = {
    "queued": ("queued...", TXT_DIM, True),
    "searching": ("hunting slsk...", theme.BLUE, True),
    "auditing": ("spectral check...", theme.TAN, True),
    "cancelled": ("cancelled", TXT_DIM, False),
}
ACTION_LABEL = {
    upgrade_mod.ACT_REPLACED: ("replaced ✓", theme.GREEN, False),
    upgrade_mod.ACT_WOULD_REPLACE: ("found ✓", theme.GREEN, False),
    upgrade_mod.ACT_KEPT_BESIDE: ("kept beside ✓", theme.GREEN, False),
    upgrade_mod.ACT_ACQUIRED: ("kept in inbox ✓", theme.GREEN, False),
    upgrade_mod.ACT_REJECTED_FAKE: ("upscale -> trash ✗", theme.PINK, False),
    upgrade_mod.ACT_TOO_SHORT: ("too short ✗", theme.PINK, False),
    upgrade_mod.ACT_WRONG_MATCH: ("wrong match ✗", theme.PINK, False),
    upgrade_mod.ACT_NOT_FOUND: ("not found ✗", TXT_DIM, False),
    upgrade_mod.ACT_UNPARSEABLE: ("unreadable name", TXT_DIM, False),
    upgrade_mod.ACT_DUPLICATE: ("already there", TXT_DIM, False),
}


def _count_rejected(counter) -> int:
    """Total des downloads jetes a la verif (upscale + trop court + mauvais match)."""
    return (counter.get(upgrade_mod.ACT_REJECTED_FAKE, 0)
            + counter.get(upgrade_mod.ACT_TOO_SHORT, 0)
            + counter.get(upgrade_mod.ACT_WRONG_MATCH, 0))


def _set_window_size(page, w: int, h: int) -> None:
    """Taille fenetre compatible API recente (page.window.width) et ancienne."""
    win = getattr(page, "window", None)
    if win is not None and hasattr(win, "width"):
        win.width, win.height = w, h
        return
    try:
        page.window_width, page.window_height = w, h
    except Exception:  # noqa: BLE001
        pass


def _set_window_icon(page, ico) -> None:
    """Icone de la fenetre (logo DDD), API recente (page.window.icon) ou ancienne."""
    try:
        win = getattr(page, "window", None)
        if win is not None and hasattr(win, "icon"):
            win.icon = str(ico)
        else:
            page.window_icon = str(ico)
    except Exception:  # noqa: BLE001
        pass


def _font_map() -> dict:
    """Polices custom presentes dans ddd/assets/fonts -> {nom logique: chemin relatif}.

    On n'enregistre QUE les fichiers presents : si une police manque, Flet retombe
    sur le defaut (le wordmark reste lisible, juste pas slab). Chemins relatifs a
    l'assets_dir passe a ft.app().
    """
    fonts_dir = paths.gui_assets_dir() / "fonts"
    candidates = {FONT_SLAB: "Anton-Regular.ttf", FONT_MONO: "DMMono-Regular.ttf"}
    return {name: f"fonts/{fn}" for name, fn in candidates.items()
            if (fonts_dir / fn).exists()}


class AppState:
    def __init__(self) -> None:
        self.folder: Optional[str] = None
        self.records: List[ScanRecord] = []
        self.selected: set = set()
        self.busy: bool = False
        self.cancel_requested: bool = False
        self.active_proc = None        # handle du process sldl en cours (pour Annuler)
        # cle -> (ProgressRing, Text) des cellules statut (reconstruit a chaque render)
        self.row_status: dict = {}            # bibliotheque, keye par chemin
        self.acquire_rows: list = []
        self.acquire_row_status: dict = {}    # favoris, keye par match_key(artist, titre)
        self.djset_row_status: dict = {}      # onglet YouTube set, meme keyage
        self.sort_report = None               # dernier plan de tri (dry-run) en attente d'apply
        # Compteurs du recap pied de page (mis a jour par scan / upgrade).
        self.last_upgraded: int = 0
        self.last_buylinks: int = 0


def main(page: ft.Page) -> None:
    # La fenetre est prete : on ferme le splash de demarrage affiche par le .exe
    # (pyi_splash). Absent en dev / build sans splash (macOS) -> on ignore.
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:  # noqa: BLE001
        pass

    state = AppState()
    cfg = config_mod.load()

    def _preset() -> str:
        return config_mod.load().get("quality_preset", quality.DEFAULT_PRESET)

    page.title = "DDD - DigDigDig"
    _set_window_size(page, 1120, 780)
    _icon = paths.app_icon()
    if _icon.exists():
        _set_window_icon(page, _icon)
    page.theme_mode = ft.ThemeMode.LIGHT
    # ColorScheme explicite et 100% creme -> Material n'injecte plus ses gris froids.
    # On force AUSSI la famille surface_container* (fonds des dropdowns/menus/dialogs)
    # sinon ces composants retombent sur des gris par defaut qui cassent l'ivoire.
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=ACCENT, on_primary="#FFFFFF",
            secondary=ACCENT, on_secondary="#FFFFFF",
            error=theme.BRICK, on_error="#FFFFFF",
            background=BG, on_background=TXT,
            surface=BG, on_surface=TXT,
            surface_variant=SURFACE, on_surface_variant=TXT_DIM,
            surface_tint=BG, outline=BORDER, outline_variant=BORDER,
            surface_bright=FIELD_BG, surface_dim=SURFACE,
            surface_container_lowest=FIELD_BG, surface_container_low=BG,
            surface_container=SURFACE, surface_container_high=SURFACE,
            inverse_surface=TXT, on_inverse_surface=BG),
        scrollbar_theme=ft.ScrollbarTheme(thumb_color=BORDER))
    _fonts = _font_map()
    if _fonts:
        page.fonts = _fonts
    page.bgcolor = BG
    page.padding = ft.padding.symmetric(horizontal=22, vertical=12)

    # --- widgets partages (barre d'etat en bas) -----------------------------
    status = ft.Text("Pick a folder, then Scan.", color=TXT_DIM)
    progress = ft.ProgressBar(value=0, visible=False, color=ACCENT)

    file_picker = ft.FilePicker()
    dl_picker = ft.FilePicker()   # pour choisir le dossier bibliotheque dans Reglages
    page.overlay.extend([file_picker, dl_picker])

    # Indicateur "slsk connected" (point + texte) : signal cheap, local, pas de reseau.
    # Vert = des creds Soulseek sont lisibles (env / config ddd / slskd) ; gris sinon.
    def _slsk_connected() -> bool:
        try:
            soulseek.read_soulseek_creds()
            return True
        except Exception:  # noqa: BLE001
            return False

    slsk_dot = ft.Container(width=9, height=9, border_radius=5, bgcolor=theme.NEUTRAL)
    slsk_txt = ft.Text("", size=12, color=TXT_DIM, font_family=FONT_MONO)

    def _refresh_slsk() -> None:
        on = _slsk_connected()
        slsk_dot.bgcolor = theme.DOT_GREEN if on else theme.NEUTRAL
        slsk_txt.value = "slsk connected" if on else "slsk offline"

    # ====================================================================
    #  Helpers partages
    # ====================================================================
    def set_busy(b: bool) -> None:
        """Une seule operation a la fois : verrouille les actions des 2 onglets."""
        state.busy = b
        progress.visible = b
        for btn in (browse_btn, sort_browse_btn, scan_btn, acquire_btn, djset_fetch_btn,
                    dl_browse_btn, sort_btn, sort_apply_btn):
            btn.disabled = b
        upgrade_btn.disabled = b or not state.records
        source_dd.disabled = b
        page.update()

    def _banner(msg: str, ok: bool) -> None:
        """Bandeau de fin (SnackBar), vert eteint si succes, gris sinon."""
        bg = "#3C4A3A" if ok else SURFACE
        try:
            page.open(ft.SnackBar(ft.Text(msg, color=TXT), bgcolor=bg, duration=8000))
        except Exception:  # noqa: BLE001  (fallback API Flet plus ancienne)
            page.snack_bar = ft.SnackBar(ft.Text(msg, color=TXT), bgcolor=bg)
            page.snack_bar.open = True
            page.update()

    def _open_url(url: str) -> None:
        """Ouvre une URL dans le navigateur (defensif : fallback hors contexte Flet)."""
        try:
            page.launch_url(url)
        except Exception:  # noqa: BLE001
            import webbrowser
            webbrowser.open(url)

    def open_feedback(_e=None) -> None:
        """Bouton coeur : ouvre le formulaire de retours (like + suggestions)."""
        if "XXXX" in FEEDBACK_URL:
            _banner("Feedback form not set up yet (see FEEDBACK_URL).", False)
            return
        _open_url(FEEDBACK_URL)

    # --- Notification de mise a jour (notification SEULE, pas d'auto-update) -------------
    update_banner_txt = ft.Text("", size=12, color=TXT, font_family=FONT_MONO)
    update_banner = ft.Container(
        ft.Row([ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=15, color=ACCENT),
                update_banner_txt,
                ft.Icon(ft.Icons.OPEN_IN_NEW, size=13, color=TXT_DIM)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        on_click=lambda _e: _open_url(updates_mod.RELEASES_PAGE),
        tooltip="Open the download page (GitHub releases)",
        visible=False, bgcolor=SURFACE, border=ft.border.all(1, ACCENT),
        border_radius=8, padding=ft.padding.symmetric(horizontal=12, vertical=8),
        margin=ft.margin.only(bottom=2))

    # --- Liens d'achat pour les introuvables (helper commun upgrade + acquire) ----
    buy_state = {"url": None}

    def _open_buy(_e=None) -> None:
        if buy_state["url"]:
            _open_url(buy_state["url"])

    buy_btn = ft.TextButton("", visible=False, on_click=_open_buy)

    def show_buy_links(outcomes, name: str) -> None:
        """Ecrit la page des introuvables (NOT_FOUND) et affiche un lien cliquable, ou cache."""
        try:
            html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), name)
        except Exception:  # noqa: BLE001
            html = None
        n = sum(1 for o in outcomes if getattr(o, "action", "") == upgrade_mod.ACT_NOT_FOUND
                and getattr(o, "title", ""))
        state.last_buylinks += n
        if html:
            buy_btn.text = f"{n} not found -> buy links (Discogs / Bandcamp)"
            buy_state["url"] = html.as_uri()
            buy_btn.visible = True
        else:
            buy_btn.visible = False

    def set_cell(status_map: dict, key: str, label: str, color, ring_on: bool):
        """Met a jour une cellule statut. Renvoie (ring, txt) touches, ou None."""
        cell = status_map.get(key)
        if not cell:
            return None
        ring, txt = cell
        txt.value, txt.color, ring.visible = label, color, ring_on
        return ring, txt

    def make_on_item(status_map: dict):
        """Fabrique un callback on_item(key, phase, detail) qui met a jour status_map.

        Update SCOPE a la cellule (ring + txt), pas page.update() : sur une grosse
        table, page.update() re-diff tout l'arbre A CHAQUE item et etrangle le worker
        de download (bug connu). On ne rafraichit que les 2 controles touches.
        """
        def on_item(key, phase, detail="") -> None:
            if phase == "done":
                label, color, ring_on = ACTION_LABEL.get(detail, (detail, TXT_DIM, False))
            else:
                label, color, ring_on = PHASE_LABEL.get(phase, (phase, TXT_DIM, False))
            cell = set_cell(status_map, key, label, color, ring_on)
            if cell is not None:
                try:
                    cell[0].update()
                    cell[1].update()
                except Exception:  # noqa: BLE001  (cellule pas encore montee)
                    pass
        return on_item

    def on_proc(proc) -> None:
        state.active_proc = proc

    def is_cancelled() -> bool:
        return state.cancel_requested

    def make_status_cell(label: str = "", ring_on: bool = False, width: int = 170, color=None):
        """Cellule statut (spinner + texte) reutilisee par les tableaux."""
        ring = ft.ProgressRing(width=13, height=13, stroke_width=2, color=SPIN, visible=ring_on)
        txt = ft.Text(label, size=11, no_wrap=True, color=color or TXT_DIM, font_family=FONT_MONO)
        return ring, txt, ft.Row([ring, txt], spacing=6, width=width,
                                  alignment=ft.MainAxisAlignment.START)

    def do_cancel(_e) -> None:
        state.cancel_requested = True
        lib_cancel_btn.disabled = True
        acq_cancel_btn.disabled = True
        dj_cancel_btn.disabled = True
        status.value = "Cancelling... (stopping the download)"
        proc = state.active_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        page.update()

    def _kill_sldl_on_exit(_e=None) -> None:
        """Filet de securite a la fermeture de la fenetre : tue le sldl en cours s'il y en
        a un. Sinon il survit orphelin et tient le port 50300 -> ralentit/bloque le run
        suivant (le zombie recurrent). Le kill en tete de chaque lot le rattrape aussi,
        mais autant ne jamais le creer."""
        proc = state.active_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        soulseek.stop_orphan_sldl()

    page.on_disconnect = _kill_sldl_on_exit   # fenetre fermee / client deconnecte
    atexit.register(_kill_sldl_on_exit)       # repli : sortie du process

    def current_excludes() -> list:
        return [s for s in (cfg.get("default_excludes", "") or "").split(",") if s] or ["PROD"]

    # ====================================================================
    #  Onglet 1 : Bibliotheque (scan + upgrade)
    # ====================================================================
    folder_field = ft.TextField(width=360, dense=True, text_size=13, color=TXT,
                                prefix_icon=ft.Icons.FOLDER, hint_text="~/Music/incoming",
                                value=cfg.get("last_folder", ""), bgcolor=FIELD_BG,
                                border_color=BORDER, border_radius=8, focused_border_color=PINK)
    # Miroir du dossier pour l'onglet Sort (un controle ne vit que dans un onglet).
    sort_folder_field = ft.TextField(width=360, dense=True, text_size=13, color=TXT,
                                     prefix_icon=ft.Icons.FOLDER, hint_text="folder to sort",
                                     value=cfg.get("last_folder", ""), bgcolor=FIELD_BG,
                                     border_color=BORDER, border_radius=8, focused_border_color=PINK)
    summary_row = ft.Row(wrap=True, spacing=14)
    quality_bar = ft.Container(content=ft.Row(spacing=0, controls=[]), height=8,
                               border_radius=4, bgcolor=BORDER,
                               clip_behavior=ft.ClipBehavior.HARD_EDGE)
    table_col = ft.ListView(expand=True, spacing=0)
    dup_text = ft.Text("", color=TXT_DIM, size=11, selectable=True)

    # Libelle du preset (deux lignes, a droite de la toolbar) : "keep >= N kHz / PRESET".
    preset_l1 = ft.Text("", size=11, color=TXT_DIM, font_family=FONT_MONO,
                        text_align=ft.TextAlign.RIGHT)
    preset_l2 = ft.Text("", size=10, weight=ft.FontWeight.W_600, color=TXT_DIM,
                        font_family=FONT_MONO, text_align=ft.TextAlign.RIGHT,
                        style=ft.TextStyle(letter_spacing=1))
    preset_label = ft.Column([preset_l1, preset_l2], spacing=0,
                             horizontal_alignment=ft.CrossAxisAlignment.END)

    def _refresh_preset_label() -> None:
        p = _preset()
        # Ligne 1 = ce que DDD vise/garde, ligne 2 = nom court du mode (cf Reglages).
        l1, l2 = {
            "dj_club":    ("keep >= 18 kHz", "DJ CLUB"),
            "audiophile": ("keep >= 20 kHz", "AUDIOPHILE"),
            "puriste":    ("pure lossless", "PURIST"),
            "mp3_320":    ("target MP3 320", "MP3 320"),
            "wav_aiff":   ("target WAV/AIFF", "WAV/AIFF"),
            "flac_only":  ("target FLAC", "FLAC ONLY"),
        }.get(p, ("keep >= 18 kHz", "DJ CLUB"))
        preset_l1.value, preset_l2.value = l1, l2

    def _upgrade_count() -> int:
        if state.selected:
            return len(state.selected)
        return sum(1 for r in state.records if _is_upgradable(r.quality, _preset()))

    def _refresh_upgrade_count() -> None:
        upgrade_btn.text = f"Upgrade selection · {_upgrade_count()}"

    filter_dd = ft.Dropdown(
        label="Filter", width=220, value="upgradable", text_size=12,
        options=[
            ft.dropdown.Option(key="upgradable", text="To upgrade (below the bar)"),
            ft.dropdown.Option(key="all", text="All"),
            ft.dropdown.Option(key=quality.LOSSLESS, text="Lossless"),
            ft.dropdown.Option(key=quality.HQ, text="HQ"),
            ft.dropdown.Option(key=quality.DOUTEUX, text="Iffy"),
            ft.dropdown.Option(key=quality.MAUVAIS, text="Bad"),
        ])

    def _dot(color) -> ft.Container:
        return ft.Container(width=9, height=9, border_radius=5, bgcolor=color)

    _BANDS = (quality.LOSSLESS, quality.HQ, quality.DOUTEUX, quality.MAUVAIS)

    def render_summary() -> None:
        from collections import Counter
        summary_row.controls.clear()
        quality_bar.content.controls.clear()
        counts = Counter(r.quality.verdict for r in state.records)
        for v in _BANDS:
            n = counts.get(v, 0)
            if n:
                summary_row.controls.append(
                    ft.Row([_dot(VERDICT_COLOR[v]),
                            ft.Text(f"{VERDICT_LABEL[v]} {n}", size=12, color=TXT)], spacing=5))
        groups = duplicate_groups(state.records)
        if groups:
            summary_row.controls.append(ft.Text(f"·  {len(groups)} dupes", size=12, color=TXT_DIM))
            wasted = sum(g[0].size_bytes * (len(g) - 1) for g in groups)
            dup_text.value = (f"{sum(len(g) for g in groups)} duplicate files in "
                              f"{len(groups)} groups (~{wasted // (1024 * 1024)} MB recoverable)")
        else:
            dup_text.value = ""
        for v in _BANDS:
            n = counts.get(v, 0)
            if n:
                quality_bar.content.controls.append(ft.Container(expand=n, bgcolor=VERDICT_COLOR[v]))
        if not quality_bar.content.controls:        # rien scanne -> barre vide neutre
            quality_bar.content.controls.append(ft.Container(expand=1, bgcolor=BORDER))

    def _visible(rec: ScanRecord) -> bool:
        f = filter_dd.value
        v = rec.quality.verdict
        if f == "all":
            return True
        if f == "upgradable":
            return _is_upgradable(rec.quality, _preset())
        return v == f

    def _on_check(e) -> None:
        idx = e.control.data
        if e.control.value:
            state.selected.add(idx)
        else:
            state.selected.discard(idx)
        _refresh_upgrade_count()
        try:
            upgrade_btn.update()
        except Exception:  # noqa: BLE001
            pass

    def _format_pill(q) -> ft.Container:
        pill = ft.Container(
            ft.Text(theme.format_label(q), size=12, color=TXT, font_family=FONT_MONO, no_wrap=True),
            padding=ft.padding.symmetric(vertical=2, horizontal=6),
            bgcolor=FIELD_BG, border=ft.border.all(1, BORDER), border_radius=4)
        return ft.Container(pill, width=COL_FMT, alignment=ft.alignment.center_left)

    def _band_pill(q) -> ft.Container:
        v = q.verdict
        label = theme.band_label(v)
        col = theme.BAND_TEXT.get(v, theme.NEUTRAL)
        if getattr(q, "confidence", "") in ("suspect", "uncertain"):
            label += " ?"                 # plein spectre mais douteux (artefacts / zone grise)
            col = theme.BAND_TAN
        pill = ft.Container(
            ft.Text(label, size=10, weight=ft.FontWeight.W_600,
                    color=col, font_family=FONT_MONO, no_wrap=True),
            padding=ft.padding.symmetric(vertical=3, horizontal=8),
            bgcolor=BAND_BG.get(v, SURFACE), border_radius=4)
        return ft.Container(pill, width=COL_BAND, alignment=ft.alignment.center_left)

    def _hcell(label, width=None, expand=False) -> ft.Text:
        return ft.Text(label, size=10, weight=ft.FontWeight.W_600, color=TXT_FAINT,
                       font_family=FONT_MONO, width=width, expand=expand, no_wrap=True,
                       style=ft.TextStyle(letter_spacing=1))

    table_header = ft.Container(
        ft.Row([
            ft.Container(width=COL_CHECK),
            _hcell("TRACK", expand=True),
            _hcell("FORMAT", width=COL_FMT),
            _hcell("CUTOFF", width=COL_CUT),
            _hcell("BAND", width=COL_BAND),
            _hcell("STATUS", width=COL_STATUS),
        ], spacing=12),
        padding=ft.padding.symmetric(vertical=8, horizontal=4))

    def render_table() -> None:
        table_col.controls.clear()
        state.row_status = {}
        shown = [(i, r) for i, r in enumerate(state.records) if _visible(r)]
        if not shown:
            table_col.controls.append(ft.Text("No files for this filter.", color=TXT_DIM))
            _refresh_upgrade_count()
            page.update()
            return
        preset = _preset()
        capped = shown[:MAX_TABLE_ROWS]   # ne pas construire des dizaines de milliers de lignes
        for idx, rec in capped:
            q = rec.quality
            checkbox = ft.Container(
                ft.Checkbox(value=idx in state.selected, disabled=not _is_audio_row(q),
                            data=idx, on_change=_on_check, fill_color=PINK, check_color=BG),
                width=COL_CHECK)
            title, artist = theme.track_title_artist(rec)
            track_cell = ft.Column(
                [ft.Text(title or q.filename, size=14, weight=ft.FontWeight.W_600,
                         color=TXT, no_wrap=True),
                 ft.Text(artist or "-", size=11, color=TXT_DIM, no_wrap=True)],
                spacing=3, expand=True)
            cut = f"{q.cutoff_hz / 1000:.1f} kHz" if q.cutoff_hz else "-"
            st_text, st_problem = theme.status_oneliner(rec, preset)
            ring, txt, status_cell = make_status_cell(
                st_text, width=COL_STATUS, color=(PINK if st_problem else TXT_DIM))
            state.row_status[q.path] = (ring, txt)
            row = ft.Row([
                checkbox, track_cell, _format_pill(q),
                ft.Text(cut, width=COL_CUT, size=12, color=TXT_DIM, font_family=FONT_MONO),
                _band_pill(q), status_cell,
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            table_col.controls.append(ft.Container(
                row, padding=ft.padding.symmetric(vertical=10, horizontal=4),
                border=ft.border.only(bottom=ft.BorderSide(1, BORDER))))
        if len(shown) > len(capped):   # affichage tronque : on le DIT (jamais en silence)
            table_col.controls.append(ft.Container(
                ft.Text(f"Showing {len(capped)} of {len(shown)} matches. Narrow the filter to see "
                        f"the rest - Upgrade still processes every match, not just shown rows.",
                        size=12, color=TXT_DIM),
                padding=ft.padding.symmetric(vertical=10, horizontal=4)))
        _refresh_upgrade_count()
        page.update()

    def _sync_folder(value: str) -> None:
        # Aligne les deux onglets (Library/Sort) + state + config quand le chemin
        # est saisi/colle a la main, pas seulement via Browse (le picker natif Flet
        # est KO sur certains macOS -> il faut pouvoir taper le chemin).
        folder_field.value = value
        sort_folder_field.value = value
        state.folder = value
        config_mod.set_value("last_folder", value)

    def on_folder_picked(e) -> None:
        if e.path:
            _sync_folder(e.path)
            page.update()

    def on_folder_typed(e) -> None:
        _sync_folder((e.control.value or "").strip())
        page.update()

    file_picker.on_result = on_folder_picked
    folder_field.on_blur = on_folder_typed
    sort_folder_field.on_blur = on_folder_typed

    def browse(_e) -> None:
        file_picker.get_directory_path(dialog_title="Choose the music folder")

    def do_scan(_e) -> None:
        if state.busy:
            return
        folder = folder_field.value
        if not folder or not Path(folder).exists():
            status.value = "Invalid folder."
            page.update()
            return
        state.folder = folder
        state.selected.clear()

        def worker() -> None:
            progress.value = None              # barre animee pendant l'enumeration de l'arbre
            status.value = "Listing files..."
            set_busy(True)                     # rend la barre visible + push
            try:
                def prog(i: int, total: int, f: Path) -> None:
                    progress.value = i / total if total else None
                    status.value = f"Scanning {Path(f).parent.name}  {i}/{total}"
                    # maj a chaque fichier : 2 widgets via control.update(), jamais page.update() (cf throttle)
                    try:
                        progress.update()
                        status.update()
                    except Exception:  # noqa: BLE001
                        pass

                state.records = scan_library(folder, exclude_names=current_excludes(), progress=prog)
                render_summary()
                render_table()
                _refresh_preset_label()
                n_up = sum(1 for r in state.records if _is_upgradable(r.quality, _preset()))
                status.value = f"{len(state.records)} files scanned - {n_up} to upgrade."
                _refresh_footer()
            except Exception as ex:  # noqa: BLE001
                status.value = f"Scan error: {ex}"
            finally:
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def do_upgrade(_e) -> None:
        if state.busy or not state.records:
            return
        chosen = [state.records[i] for i in sorted(state.selected)] if state.selected else \
                 [r for r in state.records if _is_upgradable(r.quality, _preset())]
        if not chosen:
            status.value = "Nothing to upgrade (check files or change the filter)."
            page.update()
            return

        def worker() -> None:
            set_busy(True)
            state.cancel_requested = False
            state.active_proc = None
            lib_cancel_btn.visible = True
            lib_cancel_btn.disabled = False
            progress.value = None  # barre indeterminee (animee) pendant le download
            for rec in chosen:
                set_cell(state.row_status, rec.quality.path, *PHASE_LABEL["queued"])
            page.update()
            try:
                staging = paths.cache_dl_dir()
                dl_dir = paths.download_dir(config_mod.load())
                log_path = paths.logs_dir() / "ddd_upgrade.log"

                def prog(*a) -> None:
                    if len(a) == 1:
                        status.value = str(a[0])[:90]
                        try:
                            status.update()
                        except Exception:  # noqa: BLE001
                            pass

                on_item = make_on_item(state.row_status)
                status.value = f"Upgrading {len(chosen)} files via Soulseek..."
                page.update()
                # Vrais lossless -> bibliotheque downloads/, faux source -> corbeille.
                outcomes = upgrade_mod.run_upgrade(
                    state.folder, root=paths.resource_base(), staging_dir=staging,
                    download_dir=dl_dir, scan_results=chosen, progress=prog, on_item=on_item,
                    on_proc=on_proc, cancel=is_cancelled, log_path=log_path)
                from collections import Counter
                c = Counter(o.action for o in outcomes)
                ok = c.get(upgrade_mod.ACT_REPLACED, 0)
                rej = _count_rejected(c)
                nf = c.get(upgrade_mod.ACT_NOT_FOUND, 0)
                dup = c.get(upgrade_mod.ACT_DUPLICATE, 0)
                dup_txt = f", {dup} already in library" if dup else ""
                if state.cancel_requested:
                    for rec in chosen:   # lignes jamais finies (ring encore actif) -> Annule
                        cell = state.row_status.get(rec.quality.path)
                        if cell and cell[0].visible:
                            set_cell(state.row_status, rec.quality.path, *PHASE_LABEL["cancelled"])
                    summary = (f"Upgrade cancelled: {ok} in library, {rej} rejected, "
                               f"{nf} not found{dup_txt} (partial).")
                else:
                    summary = (f"Upgrade done: {ok} added to library (fakes -> trash), "
                               f"{rej} rejected, {nf} not found{dup_txt}.")
                status.value = summary
                state.last_upgraded += ok
                show_buy_links(outcomes, Path(state.folder).name)
                _banner(summary, bool(ok) and not state.cancel_requested)
                _refresh_footer()
                # Re-scanner pour refleter les nouveaux verdicts
                if ok and not state.cancel_requested:
                    status.value = summary + " Re-scanning..."
                    page.update()
                    state.selected.clear()
                    state.records = scan_library(state.folder, exclude_names=current_excludes())
                    render_summary()
                    render_table()
                    _refresh_footer()
                    status.value = summary + " Table refreshed."
            except soulseek.SoulseekError as e:
                status.value = str(e)   # message clair (creds manquants / port occupe / login refuse)
                settings_panel.visible = True
            except Exception as ex:  # noqa: BLE001
                status.value = f"Upgrade error: {ex}"
            finally:
                lib_cancel_btn.visible = False
                state.active_proc = None
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def select_all_visible(_e) -> None:
        # "Check all" coche toutes les lignes audio visibles (coherent avec les
        # checkboxes maintenant toutes actives). Le "juste sous la barre" reste le
        # defaut quand RIEN n'est coche (do_upgrade), pas ce bouton.
        for i, rec in enumerate(state.records):
            if _visible(rec) and _is_audio_row(rec.quality):
                state.selected.add(i)
        render_table()

    def clear_selection(_e) -> None:
        state.selected.clear()
        render_table()

    # --- Tri par genre : range les tracks EN VRAC du dossier dans des sous-dossiers
    #     de vibe (ACID/DEEPWATER/...) via lookup Discogs/MusicBrainz. Dry-run d'abord,
    #     l'user voit le plan, puis Apply deplace (le cache rend le 2e passage rapide).
    sort_table_col = ft.ListView(expand=True, spacing=2)

    def render_sort_table(ops) -> None:
        sort_table_col.controls.clear()
        if not ops:
            sort_table_col.controls.append(
                ft.Text("No loose tracks to sort in this folder.", color=TXT_DIM))
            page.update()
            return
        for o in ops:
            if o.action == organize_mod.MOVE:
                label, color = o.folder, theme.GREEN
            elif o.action == organize_mod.INBOX_ACT:
                label, color = "_INBOX", theme.TAN
            else:
                label, color = "left as-is", theme.NEUTRAL
            badge = ft.Container(
                content=ft.Text(label, size=11, color="#FFFFFF", no_wrap=True, font_family=FONT_MONO),
                bgcolor=color, padding=ft.padding.symmetric(vertical=2, horizontal=8),
                border_radius=8, width=140)
            styles = ft.Text(o.styles or "", size=11, color=TXT_DIM, width=220, no_wrap=True)
            name = ft.Text(Path(o.src).name, expand=True, size=12, no_wrap=True, color=TXT)
            sort_table_col.controls.append(
                ft.Row([badge, styles, name], spacing=8,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER))
        page.update()

    def _run_sort(apply: bool) -> None:
        folder = sort_folder_field.value or folder_field.value or state.folder
        if not folder or not Path(folder).exists():
            status.value = "Pick a folder first (Browse)."
            page.update()
            return
        state.folder = folder

        def worker() -> None:
            set_busy(True)
            state.cancel_requested = False
            sort_cancel_btn.visible = not apply      # le dry-run (lookups reseau) est annulable
            sort_cancel_btn.disabled = False
            progress.value = None
            sort_table_col.controls.clear()          # vide la table -> page.update() leger pendant la boucle
            cfg2 = config_mod.load()
            # Destination = l'ARBRE DDD (config library_root -> sinon la bibliotheque download_dir),
            # JAMAIS le dossier source : on transvase le tas vers DDD/ACID, DDD/DEEPWATER, ...
            lib_root = (cfg2.get("library_root") or cfg2.get("download_dir")
                        or str(paths.default_download_dir()))
            status.value = (f"Applying sort -> {lib_root} ..." if apply
                            else f"Sort preview -> {lib_root}  (genre lookup; first run can take a moment)...")
            page.update()
            try:
                mapping = cfg2.get("genre_mapping") or organize_mod.DEFAULT_GENRE_MAPPING
                token = (cfg2.get("discogs_token") or "").strip()

                def prog(i: int, total: int, f: Path) -> None:
                    progress.value = i / total if total else None
                    status.value = f"{'Sorting' if apply else 'Looking up'}... {i}/{total}"
                    # maj a chaque fichier (parite avec le scan) : 2 widgets via control.update()
                    try:
                        progress.update()
                        status.update()
                    except Exception:  # noqa: BLE001
                        pass

                rep = organize_mod.sort_folder(
                    folder, library_root=lib_root, apply=apply, mapping=mapping,
                    token=token, cache_dir=paths.genre_cache_dir(),
                    outputs_dir=(paths.outputs_dir() if apply else None),
                    progress=prog, cancel=is_cancelled)
                state.sort_report = None if apply else rep
                render_sort_table(rep.ops)

                from collections import Counter
                c = Counter(o.action for o in rep.ops)
                moved = c.get(organize_mod.MOVE, 0)
                inbox = c.get(organize_mod.INBOX_ACT, 0)
                skip = c.get(organize_mod.SKIP, 0)
                if apply:
                    summary = f"Sorted: {moved} filed into folders, {inbox} -> _INBOX, {skip} left as-is."
                    _banner(summary, bool(moved or inbox))
                    sort_apply_btn.visible = False
                else:
                    summary = (f"Preview: {moved} would be filed, {inbox} -> _INBOX, "
                               f"{skip} left as-is. Review, then Apply sort.")
                    sort_apply_btn.visible = bool(moved or inbox) and not state.cancel_requested
                status.value = summary
            except Exception as ex:  # noqa: BLE001
                status.value = f"Sort error: {ex}"
            finally:
                sort_cancel_btn.visible = False
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def do_sort(_e) -> None:
        if not state.busy:
            _run_sort(apply=False)

    def do_sort_apply(_e) -> None:
        if not state.busy and state.sort_report is not None:
            _run_sort(apply=True)

    # ====================================================================
    #  Onglet 2 : Recuperer favoris (scrape Discogs/Bandcamp + acquire)
    # ====================================================================
    source_dd = ft.Dropdown(
        label="Source", width=200, value="discogs",
        options=[ft.dropdown.Option(key="discogs", text="Discogs"),
                 ft.dropdown.Option(key="bandcamp", text="Bandcamp")])
    discogs_collection_cb = ft.Checkbox(label="Include collection", value=False, visible=True)
    bandcamp_expand_cb = ft.Checkbox(label="Expand albums", value=True, visible=False)
    djset_url = ft.TextField(label="Set / channel / playlist URL (or 1001TL / tracklist file)",
                             width=560, hint_text="YouTube set or playlist URL, 1001TL, or a file")
    acquire_table_col = ft.ListView(expand=True, spacing=2)
    djset_table_col = ft.ListView(expand=True, spacing=2)

    def on_source_change(_e) -> None:
        src = source_dd.value
        discogs_collection_cb.visible = src == "discogs"
        bandcamp_expand_cb.visible = src == "bandcamp"
        page.update()

    source_dd.on_change = on_source_change

    def render_acquire_into(table_target, status_map: dict, rows) -> None:
        """Rend une want-list dans le tableau passe (favoris OU YouTube set)."""
        table_target.controls.clear()
        status_map.clear()
        shown = [r for r in rows
                 if (r.get("Artist") or "").strip() and (r.get("Title") or "").strip()]
        if not shown:
            table_target.controls.append(ft.Text("No usable tracks.", color=TXT_DIM))
            page.update()
            return
        for r in shown:
            artist, title = r["Artist"].strip(), r["Title"].strip()
            key = match_key(artist, title)   # MEME normalisation que upgrade._item_id
            ring, txt, status_cell = make_status_cell("queued...", ring_on=True)
            status_map[key] = (ring, txt)
            table_target.controls.append(
                ft.Row([status_cell,
                        ft.Text(f"{artist} - {title}", expand=True, size=12, no_wrap=True,
                                color=TXT)], spacing=8))
        page.update()

    def _resolve_acquire(source: str):
        """(username, token) depuis Reglages (ou l'URL pour djset). (None, None) si manquant."""
        creds = config_mod.load()
        if source == "discogs":
            username = (creds.get("discogs_username") or "").strip()
            token = (creds.get("discogs_token") or "").strip()
            if not username or not token:
                status.value = ("Discogs credentials missing - enter username + token "
                                "in Settings (gear, top right).")
                settings_panel.visible = True
                page.update()
                return None, None
            return username, token
        if source == "bandcamp":
            username = (creds.get("bandcamp_username") or "").strip()
            if not username:
                status.value = ("Bandcamp username missing - enter it in Settings (gear, top right).")
                settings_panel.visible = True
                page.update()
                return None, None
            return username, ""
        username = (djset_url.value or "").strip()      # djset : l'URL / le fichier
        if not username:
            status.value = "Enter the set / channel / playlist URL (YouTube / 1001TL) or a tracklist file."
            page.update()
            return None, None
        return username, ""

    def _start_acquire(source, username, token, table_target, status_map, cancel_btn) -> None:
        """Worker partage scrape -> acquire (favoris Discogs/Bandcamp + YouTube set)."""
        # Acquire telecharge via Soulseek : on verifie les creds AVANT de scraper, sinon
        # djset scrape une longue playlist puis echoue seulement au moment du download.
        try:
            soulseek.read_soulseek_creds()
        except soulseek.SoulseekError as e:
            status.value = str(e)
            settings_panel.visible = True
            page.update()
            return
        dest = paths.download_dir(config_mod.load())   # bibliotheque (Reglages)

        def worker() -> None:
            set_busy(True)
            state.cancel_requested = False
            state.active_proc = None
            cancel_btn.visible = True
            cancel_btn.disabled = False
            progress.value = None
            table_target.controls.clear()
            page.update()
            try:
                from .core import scrapers

                def prog(*a) -> None:
                    if a:
                        status.value = str(a[0])[:90]
                        try:
                            status.update()
                        except Exception:  # noqa: BLE001
                            pass

                status.value = f"Fetching {source}: {username[:60]}..."
                page.update()
                if source == "discogs":
                    rows = scrapers.scrape_discogs(
                        username, token=token,
                        include_collection=discogs_collection_cb.value, progress=prog)
                elif source == "bandcamp":
                    rows = scrapers.scrape_bandcamp(
                        username, expand_albums=bandcamp_expand_cb.value, progress=prog)
                else:                           # djset : `username` porte l'URL / le chemin
                    rows = scrapers.scrape_djset(username, progress=prog)

                if not rows:
                    status.value = f"No tracks found for {username} on {source}."
                    return
                state.acquire_rows = rows
                render_acquire_into(table_target, status_map, rows)
                status.value = f"{len(rows)} tracks -> downloading in real lossless..."
                page.update()

                on_item = make_on_item(status_map)
                outcomes = upgrade_mod.acquire_rows(
                    rows, root=paths.resource_base(), download_dir=dest,
                    staging_dir=paths.cache_dl_dir(),
                    progress=prog, on_item=on_item, on_proc=on_proc, cancel=is_cancelled,
                    log_path=paths.logs_dir() / "ddd_acquire.log")
                from collections import Counter
                c = Counter(o.action for o in outcomes)
                acq = c.get(upgrade_mod.ACT_ACQUIRED, 0)
                rej = _count_rejected(c)
                nf = c.get(upgrade_mod.ACT_NOT_FOUND, 0)
                dup = c.get(upgrade_mod.ACT_DUPLICATE, 0)
                dup_txt = f", {dup} duplicates skipped" if dup else ""
                if state.cancel_requested:
                    for cell in status_map.values():
                        if cell[0].visible:
                            cell[1].value, cell[1].color, cell[0].visible = "cancelled", TXT_DIM, False
                    summary = (f"Fetch cancelled: {acq} kept, {rej} rejected, "
                               f"{nf} not found{dup_txt} (partial).")
                else:
                    summary = (f"Fetch done: {acq} in library, {rej} rejected "
                               f"(upscale/short/wrong match), {nf} not found{dup_txt}.")
                status.value = summary
                show_buy_links(outcomes, source)
                _banner(summary, bool(acq) and not state.cancel_requested)
                _refresh_footer()
            except ValueError as ex:  # token Discogs manquant, etc.
                status.value = f"Can't fetch: {ex}"
                if "token" in str(ex).lower():
                    settings_panel.visible = True
            except soulseek.SoulseekError as e:
                status.value = str(e)   # message clair (creds manquants / port occupe / login refuse)
                settings_panel.visible = True
            except Exception as ex:  # noqa: BLE001
                status.value = f"Error: {ex}"
            finally:
                cancel_btn.visible = False
                state.active_proc = None
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def do_acquire(_e) -> None:
        if state.busy:
            return
        source = source_dd.value
        username, token = _resolve_acquire(source)
        if username is None:
            return
        _start_acquire(source, username, token, acquire_table_col,
                       state.acquire_row_status, acq_cancel_btn)

    def do_acquire_djset(_e) -> None:
        if state.busy:
            return
        username, _ = _resolve_acquire("djset")
        if username is None:
            return
        _start_acquire("djset", username, "", djset_table_col,
                       state.djset_row_status, dj_cancel_btn)

    # ====================================================================
    #  Reglages (panneau masque, declenche par l'engrenage)
    # ====================================================================
    slsk_user = ft.TextField(label="Soulseek user", value=cfg.get("soulseek_user", ""), width=240)
    slsk_pass = ft.TextField(label="Soulseek pass", password=True, can_reveal_password=True,
                             value=cfg.get("soulseek_pass", ""), width=240)
    discogs_user = ft.TextField(label="Discogs username", value=cfg.get("discogs_username", ""),
                                width=240)
    discogs_tok = ft.TextField(label="Discogs token", password=True, can_reveal_password=True,
                               value=cfg.get("discogs_token", ""), width=240)
    bandcamp_user = ft.TextField(label="Bandcamp username", value=cfg.get("bandcamp_username", ""),
                                 width=240)
    dl_dir_field = ft.TextField(
        label="Library folder (lossless downloads)", expand=True,
        value=cfg.get("download_dir", "") or str(paths.default_download_dir()))

    def on_dl_picked(e) -> None:
        if e.path:
            dl_dir_field.value = e.path
            config_mod.set_value("download_dir", e.path)
            cfg["download_dir"] = e.path
            page.update()

    dl_picker.on_result = on_dl_picked

    def browse_dl(_e) -> None:
        dl_picker.get_directory_path(dialog_title="Library folder (verified lossless)")

    dl_browse_btn = ft.FilledButton(text="Browse", icon=ft.Icons.FOLDER_OPEN, on_click=browse_dl)
    preset_dd = ft.Dropdown(
        label="Quality / target", width=320,
        value=cfg.get("quality_preset", "dj_club"),
        options=[
            ft.dropdown.Option(key="dj_club", text="DJ Club (>=18 kHz, MP3 320 included)"),
            ft.dropdown.Option(key="audiophile", text="Audiophile (>=20 kHz)"),
            ft.dropdown.Option(key="puriste", text="Purist (pure lossless)"),
            ft.dropdown.Option(key="mp3_320", text="MP3 320 only (vintage / mobile)"),
            ft.dropdown.Option(key="wav_aiff", text="WAV/AIFF only (uncompressed)"),
            ft.dropdown.Option(key="flac_only", text="FLAC only"),
        ])

    detector_dd = ft.Dropdown(
        label="Detection engine", width=320,
        value=cfg.get("detector", "legacy"),
        options=[
            ft.dropdown.Option(key="legacy", text="Legacy (spectral cutoff)"),
            ft.dropdown.Option(key="forensic", text="Forensic (cutoff + codec artifacts)"),
        ])

    mapping_field = ft.TextField(
        label="Genre -> folder mapping (JSON, advanced)",
        value=json.dumps(cfg.get("genre_mapping") or organize_mod.DEFAULT_GENRE_MAPPING,
                         ensure_ascii=False, indent=2),
        multiline=True, min_lines=3, max_lines=6, expand=True, text_size=11)

    # Statut inline du panneau Reglages : visible meme Reglages ouverts (contrairement au
    # point slsk du header, masque avec main_view) -> feedback du Save (validation + connexion).
    settings_status = ft.Text("", size=12, color=TXT_DIM)

    def save_settings(_e) -> None:
        su, sp = slsk_user.value.strip(), slsk_pass.value
        if bool(su) != bool(sp):   # un seul des deux champs rempli -> creds inutilisables
            settings_status.value = "Soulseek: username AND password required (or leave both empty)."
            settings_status.color = ACCENT
            settings_status.update()
            return
        vals = {
            "soulseek_user": su,
            "soulseek_pass": sp,
            "discogs_username": discogs_user.value.strip(),
            "discogs_token": discogs_tok.value.strip(),
            "bandcamp_username": bandcamp_user.value.strip(),
            "download_dir": (dl_dir_field.value or "").strip(),
            "quality_preset": preset_dd.value,
            "detector": detector_dd.value,
        }
        msg = "Settings saved."
        raw = (mapping_field.value or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("expected a JSON object {folder: [keywords]}")
                vals["genre_mapping"] = parsed
            except (json.JSONDecodeError, ValueError) as e:
                msg = f"Saved, but genre mapping NOT updated (invalid JSON: {e})."
        config_mod.set_many(vals)
        cfg.update(vals)   # garde le cache en memoire frais (relu aussi a chaud par do_acquire)
        _refresh_slsk()           # creds peut-etre saisies -> le point passe au vert
        _refresh_preset_label()   # le preset a pu changer -> "keep >= N kHz / PRESET"
        connected = _slsk_connected()   # feedback visible sans fermer Reglages (le point est cache)
        settings_status.value = ("Soulseek: connected." if connected
                                 else "Soulseek: offline - check username + password.")
        settings_status.color = theme.DOT_GREEN if connected else TXT_DIM
        status.value = msg
        page.update()

    settings_body = ft.Column([
        ft.Text("Soulseek: required to download (upgrade + favorites). "
                "Discogs: username + token (discogs.com/settings/developers). "
                "Bandcamp: username only (public scrape).", size=12, color=TXT_DIM),
        ft.Row([slsk_user, slsk_pass], wrap=True),
        ft.Row([discogs_user, discogs_tok], wrap=True),
        ft.Row([bandcamp_user], wrap=True),
        ft.Text("Everything DDD validates (upgrade + favorites) lands here; fakes/rejects "
                "go to the trash.", size=12, color=TXT_DIM),
        ft.Row([dl_dir_field, dl_browse_btn]),
        ft.Text("Quality bar (what DDD keeps); the MP3 320 / WAV-AIFF / FLAC modes also set "
                "what it searches for. Below the bar -> candidate for upgrade.",
                size=12, color=TXT_DIM),
        ft.Row([preset_dd, detector_dd], wrap=True),
        ft.Text("Sort: genre -> your vibe folders. Edit only to retune; the default covers most.",
                size=12, color=TXT_DIM),
        ft.Row([mapping_field]),
        ft.FilledButton(text="Save", on_click=save_settings),
        settings_status,
    ], spacing=10)

    def toggle_settings(_e) -> None:
        opening = not settings_panel.visible
        settings_panel.visible = opening
        main_view.visible = not opening       # Reglages prend toute la fenetre ; la croix ferme
        page.update()

    close_settings_btn = ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Close settings",
                                        on_click=toggle_settings)

    # Panneau Reglages : pas de hauteur fixe (ne coupe plus le contenu). Il occupe la place
    # via expand et scrolle si besoin ; quand il est ouvert, on cache la vue principale derriere
    # (main_view.visible=False) -> plein ecran propre, ferme par la croix (le bouton X).
    settings_panel = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.SETTINGS, size=18, color=TXT_DIM),
                    ft.Text("Settings", size=13, weight=ft.FontWeight.BOLD, color=TXT_DIM),
                    ft.Container(expand=True), close_settings_btn],
                   spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            settings_body,
        ], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=8, border=ft.border.all(1, BORDER), border_radius=8, bgcolor=SURFACE,
        visible=False, expand=True)

    # ====================================================================
    #  Boutons
    # ====================================================================
    # Styles : Scan/Fetch = encre (presque noir), Upgrade/Apply = rose, Browse = contour.
    _btn_shape = ft.RoundedRectangleBorder(radius=8)
    _btn_pad = ft.padding.symmetric(horizontal=18, vertical=13)
    _btn_txt = ft.TextStyle(weight=ft.FontWeight.BOLD, size=13)
    _ink_style = ft.ButtonStyle(bgcolor=TXT, color=BG, shape=_btn_shape, padding=_btn_pad,
                                text_style=_btn_txt)
    _pink_style = ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF", shape=_btn_shape,
                                 padding=_btn_pad, text_style=_btn_txt)
    _outline_style = ft.ButtonStyle(color=TXT, bgcolor=FIELD_BG, side=ft.BorderSide(1, BORDER),
                                    shape=_btn_shape, padding=ft.padding.symmetric(horizontal=14,
                                    vertical=13), text_style=_btn_txt)

    browse_btn = ft.OutlinedButton(text="Browse", icon=ft.Icons.FOLDER_OPEN,
                                   on_click=browse, style=_outline_style)
    sort_browse_btn = ft.OutlinedButton(text="Browse", icon=ft.Icons.FOLDER_OPEN,
                                        on_click=browse, style=_outline_style)
    scan_btn = ft.FilledButton(text="Scan", icon=ft.Icons.SEARCH, on_click=do_scan,
                               style=_ink_style)
    upgrade_btn = ft.FilledButton(text="Upgrade selection · 0", icon=ft.Icons.UPGRADE,
                                  on_click=do_upgrade, disabled=True, style=_pink_style)
    lib_cancel_btn = ft.OutlinedButton(text="Cancel", icon=ft.Icons.CANCEL,
                                       on_click=do_cancel, visible=False)
    sort_btn = ft.FilledButton(
        text="Sort by genre", icon=ft.Icons.SORT, on_click=do_sort, style=_ink_style,
        tooltip="File loose tracks into vibe subfolders (ACID, DEEPWATER, ...) here, by genre lookup")
    sort_apply_btn = ft.FilledButton(text="Apply sort", icon=ft.Icons.CHECK,
                                     on_click=do_sort_apply, visible=False, style=_pink_style)
    sort_cancel_btn = ft.OutlinedButton(text="Cancel", icon=ft.Icons.CANCEL,
                                        on_click=do_cancel, visible=False)
    check_all_btn = ft.TextButton(text="Check all", on_click=select_all_visible)
    uncheck_all_btn = ft.TextButton(text="Uncheck all", on_click=clear_selection)
    filter_dd.on_change = lambda _e: render_table()

    acquire_btn = ft.FilledButton(text="Fetch & download", icon=ft.Icons.DOWNLOAD,
                                  on_click=do_acquire, style=_ink_style)
    acq_cancel_btn = ft.OutlinedButton(text="Cancel", icon=ft.Icons.CANCEL,
                                       on_click=do_cancel, visible=False)
    djset_fetch_btn = ft.FilledButton(text="Fetch & download", icon=ft.Icons.DOWNLOAD,
                                      on_click=do_acquire_djset, style=_ink_style)
    dj_cancel_btn = ft.OutlinedButton(text="Cancel", icon=ft.Icons.CANCEL,
                                      on_click=do_cancel, visible=False)
    feedback_btn = ft.IconButton(icon=ft.Icons.FAVORITE_BORDER, icon_color=TXT_DIM,
                                 tooltip="Like the app / a suggestion", on_click=open_feedback)
    settings_btn = ft.IconButton(icon=ft.Icons.SETTINGS, icon_color=TXT_DIM,
                                 tooltip="Settings (credentials)", on_click=toggle_settings)

    # ====================================================================
    #  Header : wordmark slab + tagline + statut slsk + engrenage
    # ====================================================================
    wordmark = ft.Text("DIGDIGDIG", font_family=FONT_SLAB, size=30,
                       weight=ft.FontWeight.BOLD, color=TXT,
                       style=ft.TextStyle(letter_spacing=1))
    tagline = ft.Text("the crate digger that digs x3", font_family=FONT_MONO,
                      size=11, color=TXT_DIM)
    header = ft.Container(
        content=ft.Row([
            # Anton (wordmark) a une grosse boite de ligne : en END-align sa boite-bas colle
            # au bas, mais les capitales remontent. On releve la tagline d'un cran (padding bas
            # sur un wrapper) pour que le bas des lettres des deux textes tombe sur la meme ligne.
            ft.Row([wordmark, ft.Container(tagline, padding=ft.padding.only(bottom=5))],
                   spacing=14, vertical_alignment=ft.CrossAxisAlignment.END),
            ft.Container(expand=True),
            ft.Container(
                ft.Row([slsk_dot, slsk_txt], spacing=7,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                on_click=toggle_settings, tooltip="Configure Soulseek (settings)",
                padding=ft.padding.symmetric(horizontal=4, vertical=2), border_radius=6),
            feedback_btn, settings_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
        padding=ft.padding.only(left=4, right=4, top=4, bottom=8))

    # Zone table : FLUSH sur l'ivoire (PAS de carte grise) ; juste un filet sous l'en-tete
    # et un divider faible par ligne (les lignes portent leur propre bordure basse).
    def _table_surface(header_row, body) -> ft.Container:
        inner = ([header_row, ft.Divider(height=1, thickness=1, color=BORDER), body]
                 if header_row else [body])
        return ft.Container(content=ft.Column(inner, spacing=0, expand=True),
                            expand=True, bgcolor=BG)

    # ====================================================================
    #  Onglets
    # ====================================================================
    library_tab = ft.Container(
        content=ft.Column([
            ft.Row([folder_field, browse_btn, scan_btn,
                    ft.Container(expand=True), preset_label, upgrade_btn],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            summary_row,
            quality_bar,
            # PAS de wrap=True ici : un enfant expand (le spacer) dans un Wrap fait jeter
            # un layout error a Flutter -> toute la zone table devient un carre gris
            # (ErrorWidget release-mode #C3C3C2). Row normal -> l'expand est valide.
            ft.Row([filter_dd, check_all_btn, uncheck_all_btn, lib_cancel_btn,
                    ft.Container(expand=True), dup_text],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _table_surface(table_header, table_col),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6), expand=True, bgcolor=BG)

    acquire_tab = ft.Container(
        content=ft.Column([
            ft.Row([source_dd, acquire_btn, acq_cancel_btn],
                   wrap=True, spacing=8, run_spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([discogs_collection_cb, bandcamp_expand_cb], wrap=True),
            _table_surface(None, acquire_table_col),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6), expand=True, bgcolor=BG)

    djset_tab = ft.Container(
        content=ft.Column([
            ft.Text("Paste a YouTube set or playlist URL (each video = a track), a 1001TL page, "
                    "or a tracklist file. DDD scrapes it, then downloads in real lossless.",
                    size=12, color=TXT_DIM),
            ft.Row([djset_url, djset_fetch_btn, dj_cancel_btn],
                   wrap=True, spacing=8, run_spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _table_surface(None, djset_table_col),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6), expand=True, bgcolor=BG)

    sort_tab = ft.Container(
        content=ft.Column([
            ft.Text("File the loose tracks in this folder into your vibe subfolders "
                    "(ACID, DEEPWATER, ...) by genre lookup. Preview first, then Apply.",
                    size=12, color=TXT_DIM),
            ft.Row([sort_folder_field, sort_browse_btn, sort_btn, sort_apply_btn, sort_cancel_btn],
                   wrap=True, spacing=8, run_spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _table_surface(None, sort_table_col),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6), expand=True, bgcolor=BG)

    # Nav custom (PAS ft.Tabs : sa TabBarView donne une hauteur non bornee a son contenu
    # -> expand casse et un fond gris Material transparait. Ici le contenu vit dans un
    # Container creme que l'on pilote a la main -> fond uniforme + expand correct).
    _tab_defs = [("Library", library_tab), ("Get favorites", acquire_tab),
                 ("YouTube set", djset_tab), ("Sort by genre", sort_tab)]
    tab_content = ft.Container(content=library_tab, expand=True, bgcolor=BG)
    tab_texts: list = []
    tab_buttons: list = []

    def _select_tab(idx: int) -> None:
        tab_content.content = _tab_defs[idx][1]
        for j, t in enumerate(tab_texts):
            active = j == idx
            t.color = TXT if active else TXT_DIM
            t.weight = ft.FontWeight.BOLD if active else ft.FontWeight.W_500
            tab_buttons[j].border = ft.border.only(
                bottom=ft.BorderSide(3, ACCENT if active else "transparent"))
        page.update()

    for _i, (_name, _) in enumerate(_tab_defs):
        _t = ft.Text(_name, size=14, color=TXT if _i == 0 else TXT_DIM,
                     weight=ft.FontWeight.BOLD if _i == 0 else ft.FontWeight.W_500)
        _b = ft.Container(_t, on_click=(lambda e, k=_i: _select_tab(k)),
                          padding=ft.padding.only(top=2, bottom=8),
                          border=ft.border.only(bottom=ft.BorderSide(
                              3, ACCENT if _i == 0 else "transparent")))
        tab_texts.append(_t)
        tab_buttons.append(_b)
    nav = ft.Row(tab_buttons, spacing=26)

    main_view = ft.Column([header, update_banner, nav, tab_content], expand=True, spacing=10)

    # ====================================================================
    #  Pied de page : recap de session + dossier bibliotheque
    # ====================================================================
    footer_left = ft.Text("", size=11, color=TXT_DIM, font_family=FONT_MONO)
    footer_right = ft.Text("", size=11, color=TXT_DIM, font_family=FONT_MONO)

    def _refresh_footer() -> None:
        scanned = len(state.records)
        below = sum(1 for r in state.records if _is_upgradable(r.quality, _preset()))
        footer_left.value = (f"{scanned} scanned · {below} below bar · "
                             f"{state.last_upgraded} upgraded · {state.last_buylinks} -> buy-links")
        try:
            lib = paths.download_dir(config_mod.load())
        except Exception:  # noqa: BLE001
            lib = ""
        footer_right.value = f"library: {lib}  ·  v{__version__}"

    footer = ft.Row([footer_left, ft.Container(expand=True), footer_right],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # Etat initial des libelles dynamiques (avant tout scan).
    _refresh_slsk()
    _refresh_preset_label()
    _refresh_upgrade_count()
    _refresh_footer()

    # 1er lancement sans identifiants : deplie Reglages (plein ecran) + invite.
    if not ((cfg.get("soulseek_user") or "").strip() and (cfg.get("soulseek_pass") or "")):
        settings_panel.visible = True
        main_view.visible = False
        status.value = "Tip: enter your Soulseek credentials (gear) to start digging."

    page.add(main_view, progress, status, footer, buy_btn, settings_panel)

    # Notif de mise a jour au lancement : thread daemon, timeout court, fail-silent. Gate sur
    # build fige (ou env DDD_UPDATE_CHECK=1 pour tester en dev) -> pas de ping a chaque run dev.
    def _check_updates() -> None:
        tag = updates_mod.check_for_update(__version__)
        if not tag:
            return
        update_banner_txt.value = f"Version {tag} available - click to download (you have v{__version__})"
        update_banner.visible = True
        try:
            update_banner.update()
        except Exception:  # noqa: BLE001
            pass

    if paths.is_frozen() or os.environ.get("DDD_UPDATE_CHECK"):
        threading.Thread(target=_check_updates, daemon=True).start()


def run() -> None:
    """Point d'entree : lance la fenetre native."""
    from .core import singleton
    # Couvre aussi le lancement dev (`ddd gui`) ; idempotent si entry.py a deja verrouille.
    if not singleton.acquire("DDD"):
        singleton.focus_existing()
        return
    ft.app(target=main, assets_dir=str(paths.gui_assets_dir()))


if __name__ == "__main__":
    run()
