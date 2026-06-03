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

import threading
from pathlib import Path
from typing import List, Optional

import flet as ft

from . import __version__
from . import paths
from .core import config as config_mod
from .core import quality
from .core import soulseek
from .core import upgrade as upgrade_mod
from .core.naming import match_key
from .core.scan import ScanRecord, duplicate_groups, scan_library

# --- Palette sobre facon Soulseek/LimeWire (gris charbon, tons desatures) --------
BG = "#1E1E1E"          # fond fenetre
SURFACE = "#252525"     # cartes / panneaux
BORDER = "#3A3A3A"      # bordures discretes
TXT = "#D0D0D0"         # texte principal (blanc casse)
TXT_DIM = "#9A9A9A"     # texte secondaire
ACCENT = "#5C6B7A"      # accent slate desature (boutons natifs via seed)
SPIN = "#A0A0A0"        # spinners / progress, gris neutre

VERDICT_COLOR = {
    quality.LOSSLESS: "#6E7F5B",   # olive/vert - vrai lossless
    quality.HQ: "#5A7A8C",         # bleu sobre - jouable club
    quality.DOUTEUX: "#8C7A5A",    # taupe/jaune - limite
    quality.MAUVAIS: "#8C5A5A",    # brun-rouge - bouillie
    "ERROR": "#6E6E6E",
    "SKIPPED": "#6E6E6E",
}
VERDICT_LABEL = {
    quality.LOSSLESS: "Lossless",
    quality.HQ: "HQ",
    quality.DOUTEUX: "Douteux",
    quality.MAUVAIS: "Mauvais",
    "ERROR": "Erreur",
    "SKIPPED": "Ignore",
}


def _is_upgradable(qr, preset):
    return qr.verdict not in (quality.SKIPPED, quality.ERROR) and not quality.is_accepted(qr, preset)

# Statut live par ligne. Tuple = (libelle, couleur, ring_anime).
# ring_anime=True -> petit spinner visible (phase en cours) ; False -> etat final fige.
PHASE_LABEL = {
    "queued": ("En file...", TXT_DIM, True),
    "searching": ("Recherche Soulseek...", "#A0A8B0", True),
    "auditing": ("Verif spectrale...", "#9A8C6B", True),
    "cancelled": ("Annule", TXT_DIM, False),
}
ACTION_LABEL = {
    upgrade_mod.ACT_REPLACED: ("Remplace ✓", "#6E7F5B", False),
    upgrade_mod.ACT_WOULD_REPLACE: ("Trouve ✓", "#6E7F5B", False),
    upgrade_mod.ACT_KEPT_BESIDE: ("Garde a cote ✓", "#6E7F5B", False),
    upgrade_mod.ACT_ACQUIRED: ("Gardee en inbox ✓", "#6E7F5B", False),
    upgrade_mod.ACT_REJECTED_FAKE: ("Upscale rejete ✗", "#A0785A", False),
    upgrade_mod.ACT_TOO_SHORT: ("Trop court ✗", "#A0785A", False),
    upgrade_mod.ACT_WRONG_MATCH: ("Mauvais match ✗", "#A0785A", False),
    upgrade_mod.ACT_NOT_FOUND: ("Introuvable ✗", "#7A7A7A", False),
    upgrade_mod.ACT_UNPARSEABLE: ("Nom illisible", "#7A7A7A", False),
    upgrade_mod.ACT_DUPLICATE: ("Deja present", "#7A7A7A", False),
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
        self.acquire_row_status: dict = {}    # acquire, keye par match_key(artist, titre)


def main(page: ft.Page) -> None:
    state = AppState()
    cfg = config_mod.load()

    def _preset() -> str:
        return config_mod.load().get("quality_preset", quality.DEFAULT_PRESET)

    page.title = f"DDD - DigDigDig  v{__version__}"
    _set_window_size(page, 1100, 760)
    _icon = paths.app_icon()
    if _icon.exists():
        _set_window_icon(page, _icon)
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ACCENT)
    page.bgcolor = BG
    page.padding = 16

    # --- widgets partages (barre d'etat en bas) -----------------------------
    status = ft.Text("Choisis un dossier puis Scanner.", color=TXT_DIM)
    progress = ft.ProgressBar(value=0, visible=False, color=SPIN)

    file_picker = ft.FilePicker()
    dl_picker = ft.FilePicker()   # pour choisir le dossier bibliotheque dans Reglages
    page.overlay.extend([file_picker, dl_picker])

    # ====================================================================
    #  Helpers partages
    # ====================================================================
    def set_busy(b: bool) -> None:
        """Une seule operation a la fois : verrouille les actions des 2 onglets."""
        state.busy = b
        progress.visible = b
        for btn in (browse_btn, scan_btn, acquire_btn, dl_browse_btn):
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

    def set_cell(status_map: dict, key: str, label: str, color, ring_on: bool) -> None:
        cell = status_map.get(key)
        if not cell:
            return
        ring, txt = cell
        txt.value, txt.color, ring.visible = label, color, ring_on

    def make_on_item(status_map: dict):
        """Fabrique un callback on_item(key, phase, detail) qui met a jour status_map."""
        def on_item(key, phase, detail="") -> None:
            if phase == "done":
                label, color, ring_on = ACTION_LABEL.get(detail, (detail, TXT_DIM, False))
            else:
                label, color, ring_on = PHASE_LABEL.get(phase, (phase, TXT_DIM, False))
            set_cell(status_map, key, label, color, ring_on)
            page.update()
        return on_item

    def on_proc(proc) -> None:
        state.active_proc = proc

    def is_cancelled() -> bool:
        return state.cancel_requested

    def make_status_cell(label: str = "", ring_on: bool = False):
        """Cellule statut (spinner + texte) reutilisee par les 2 tableaux."""
        ring = ft.ProgressRing(width=14, height=14, stroke_width=2, color=SPIN, visible=ring_on)
        txt = ft.Text(label, size=11, no_wrap=True, color=TXT_DIM)
        return ring, txt, ft.Row([ring, txt], spacing=6, width=170,
                                  alignment=ft.MainAxisAlignment.START)

    def do_cancel(_e) -> None:
        state.cancel_requested = True
        lib_cancel_btn.disabled = True
        acq_cancel_btn.disabled = True
        status.value = "Annulation en cours... (arret du telechargement)"
        proc = state.active_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        page.update()

    def current_excludes() -> list:
        return [s for s in (cfg.get("default_excludes", "") or "").split(",") if s] or ["PROD"]

    # ====================================================================
    #  Onglet 1 : Bibliotheque (scan + upgrade)
    # ====================================================================
    folder_field = ft.TextField(label="Dossier de musique", expand=True, read_only=True,
                                value=cfg.get("last_folder", ""))
    summary_row = ft.Row(wrap=True, spacing=8)
    table_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=2)
    dup_text = ft.Text("", color="#9A8C6B", selectable=True)

    filter_dd = ft.Dropdown(
        label="Filtre", width=240, value="upgradable",
        options=[
            ft.dropdown.Option(key="upgradable", text="A upgrader (sous le seuil)"),
            ft.dropdown.Option(key="all", text="Tout"),
            ft.dropdown.Option(key=quality.LOSSLESS, text="Lossless"),
            ft.dropdown.Option(key=quality.HQ, text="HQ"),
            ft.dropdown.Option(key=quality.DOUTEUX, text="Douteux"),
            ft.dropdown.Option(key=quality.MAUVAIS, text="Mauvais"),
        ])

    def render_summary() -> None:
        from collections import Counter
        summary_row.controls.clear()
        counts = Counter(r.quality.verdict for r in state.records)
        for verdict, n in counts.most_common():
            summary_row.controls.append(
                ft.Container(
                    content=ft.Text(f"{VERDICT_LABEL.get(verdict, verdict)} : {n}",
                                    color=TXT, weight=ft.FontWeight.BOLD, size=12),
                    bgcolor=VERDICT_COLOR.get(verdict, "#6E6E6E"),
                    padding=ft.padding.symmetric(vertical=6, horizontal=12), border_radius=14))
        groups = duplicate_groups(state.records)
        if groups:
            wasted = sum(g[0].size_bytes * (len(g) - 1) for g in groups)
            dup_text.value = (f"Doublons : {len(groups)} groupes, "
                              f"{sum(len(g) for g in groups)} fichiers "
                              f"(~{wasted // (1024 * 1024)} Mo recuperables)")
        else:
            dup_text.value = ""

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

    def render_table() -> None:
        table_col.controls.clear()
        state.row_status = {}
        shown = [(i, r) for i, r in enumerate(state.records) if _visible(r)]
        if not shown:
            table_col.controls.append(ft.Text("Aucun fichier pour ce filtre.", color=TXT_DIM))
            page.update()
            return
        for idx, rec in shown:
            q = rec.quality
            checkbox = ft.Checkbox(value=idx in state.selected,
                                   disabled=not _is_upgradable(q, _preset()),
                                   data=idx, on_change=_on_check)
            badge = ft.Container(
                content=ft.Text(VERDICT_LABEL.get(q.verdict, q.verdict), size=11, color=TXT),
                bgcolor=VERDICT_COLOR.get(q.verdict, "#6E6E6E"),
                padding=ft.padding.symmetric(vertical=2, horizontal=8),
                border_radius=10, width=120)
            dup_tag = ft.Text("  [doublon]", size=11, color="#9A8C6B") \
                if rec.is_duplicate else ft.Text("")
            ring, txt, status_cell = make_status_cell()
            state.row_status[q.path] = (ring, txt)
            table_col.controls.append(
                ft.Row([
                    checkbox, badge, status_cell,
                    ft.Text(f"{q.cutoff_hz:.0f} Hz", width=80, size=12, color=TXT_DIM),
                    ft.Text(q.filename, expand=True, size=12, no_wrap=True, color=TXT),
                    dup_tag,
                ], spacing=8))
        page.update()

    def on_folder_picked(e) -> None:
        if e.path:
            folder_field.value = e.path
            state.folder = e.path
            config_mod.set_value("last_folder", e.path)
            page.update()

    file_picker.on_result = on_folder_picked

    def browse(_e) -> None:
        file_picker.get_directory_path(dialog_title="Choisir le dossier de musique")

    def do_scan(_e) -> None:
        if state.busy:
            return
        folder = folder_field.value
        if not folder or not Path(folder).exists():
            status.value = "Dossier invalide."
            page.update()
            return
        state.folder = folder
        state.selected.clear()

        def worker() -> None:
            set_busy(True)
            try:
                def prog(i: int, total: int, f: Path) -> None:
                    progress.value = i / total if total else None
                    status.value = f"Scan... {i}/{total}"
                    page.update()

                state.records = scan_library(folder, exclude_names=current_excludes(), progress=prog)
                render_summary()
                render_table()
                n_up = sum(1 for r in state.records if _is_upgradable(r.quality, _preset()))
                status.value = f"{len(state.records)} fichiers analyses - {n_up} a upgrader."
            except Exception as ex:  # noqa: BLE001
                status.value = f"Erreur scan : {ex}"
            finally:
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def do_upgrade(_e) -> None:
        if state.busy or not state.records:
            return
        chosen = [state.records[i] for i in sorted(state.selected)] if state.selected else \
                 [r for r in state.records if _is_upgradable(r.quality, _preset())]
        if not chosen:
            status.value = "Rien a upgrader (coche des fichiers ou change le filtre)."
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
                        page.update()

                on_item = make_on_item(state.row_status)
                status.value = f"Upgrade de {len(chosen)} fichiers via Soulseek..."
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
                dup_txt = f", {dup} deja en bibliotheque" if dup else ""
                if state.cancel_requested:
                    for rec in chosen:   # lignes jamais finies (ring encore actif) -> Annule
                        cell = state.row_status.get(rec.quality.path)
                        if cell and cell[0].visible:
                            set_cell(state.row_status, rec.quality.path, *PHASE_LABEL["cancelled"])
                    summary = (f"Upgrade annule : {ok} en bibliotheque, {rej} rejetes, "
                               f"{nf} introuvables{dup_txt} (partiel).")
                else:
                    summary = (f"Upgrade fini : {ok} deposes en bibliotheque (faux -> corbeille), "
                               f"{rej} rejetes, {nf} introuvables{dup_txt}.")
                status.value = summary
                _banner(summary, bool(ok) and not state.cancel_requested)
                # Re-scanner pour refleter les nouveaux verdicts
                if ok and not state.cancel_requested:
                    status.value = summary + " Re-scan en cours..."
                    page.update()
                    state.selected.clear()
                    state.records = scan_library(state.folder, exclude_names=current_excludes())
                    render_summary()
                    render_table()
                    status.value = summary + " Table rafraichie."
            except soulseek.SoulseekError as e:
                status.value = str(e)   # message clair (creds manquants / port occupe / login refuse)
                settings_panel.visible = True
            except Exception as ex:  # noqa: BLE001
                status.value = f"Erreur upgrade : {ex}"
            finally:
                lib_cancel_btn.visible = False
                state.active_proc = None
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def select_all_visible(_e) -> None:
        for i, rec in enumerate(state.records):
            if _visible(rec) and _is_upgradable(rec.quality, _preset()):
                state.selected.add(i)
        render_table()

    def clear_selection(_e) -> None:
        state.selected.clear()
        render_table()

    # ====================================================================
    #  Onglet 2 : Recuperer favoris (scrape Discogs/Bandcamp + acquire)
    # ====================================================================
    source_dd = ft.Dropdown(
        label="Source", width=200, value="discogs",
        options=[ft.dropdown.Option(key="discogs", text="Discogs"),
                 ft.dropdown.Option(key="bandcamp", text="Bandcamp"),
                 ft.dropdown.Option(key="djset", text="Set DJ (URL)")])
    discogs_collection_cb = ft.Checkbox(label="Inclure la collection", value=False, visible=True)
    bandcamp_expand_cb = ft.Checkbox(label="Developper les albums", value=True, visible=False)
    djset_url = ft.TextField(label="URL du set (YouTube / 1001TL) ou fichier tracklist",
                             visible=False, width=480)
    acquire_table_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=2)

    def on_source_change(_e) -> None:
        src = source_dd.value
        discogs_collection_cb.visible = src == "discogs"
        bandcamp_expand_cb.visible = src == "bandcamp"
        djset_url.visible = src == "djset"
        page.update()

    source_dd.on_change = on_source_change

    def render_acquire_table(rows) -> None:
        acquire_table_col.controls.clear()
        state.acquire_row_status = {}
        shown = [r for r in rows
                 if (r.get("Artist") or "").strip() and (r.get("Title") or "").strip()]
        if not shown:
            acquire_table_col.controls.append(ft.Text("Aucune piste exploitable.", color=TXT_DIM))
            page.update()
            return
        for r in shown:
            artist, title = r["Artist"].strip(), r["Title"].strip()
            key = match_key(artist, title)   # MEME normalisation que upgrade._item_id
            ring, txt, status_cell = make_status_cell("En file...", ring_on=True)
            state.acquire_row_status[key] = (ring, txt)
            acquire_table_col.controls.append(
                ft.Row([status_cell,
                        ft.Text(f"{artist} - {title}", expand=True, size=12, no_wrap=True,
                                color=TXT)], spacing=8))
        page.update()

    def do_acquire(_e) -> None:
        if state.busy:
            return
        source = source_dd.value
        # Tous les identifiants vivent dans Reglages. On relit a chaud (l'user a pu
        # les saisir apres le lancement) et on bloque avec un message clair si manquants.
        creds = config_mod.load()
        token = ""
        if source == "discogs":
            username = (creds.get("discogs_username") or "").strip()
            token = (creds.get("discogs_token") or "").strip()
            if not username or not token:
                status.value = ("Identifiants Discogs manquants - renseigne username + token "
                                "dans Reglages (engrenage en haut a droite).")
                settings_panel.visible = True
                page.update()
                return
        elif source == "bandcamp":
            username = (creds.get("bandcamp_username") or "").strip()
            if not username:
                status.value = ("Identifiant Bandcamp manquant - renseigne ton username "
                                "dans Reglages (engrenage en haut a droite).")
                settings_panel.visible = True
                page.update()
                return
        else:                                   # djset : pas de creds, juste l'URL / le fichier
            username = (djset_url.value or "").strip()
            if not username:
                status.value = "Entre l'URL du set (YouTube / 1001TL) ou un fichier tracklist."
                page.update()
                return
        dest = paths.download_dir(config_mod.load())   # bibliotheque (Reglages)

        def worker() -> None:
            set_busy(True)
            state.cancel_requested = False
            state.active_proc = None
            acq_cancel_btn.visible = True
            acq_cancel_btn.disabled = False
            progress.value = None
            acquire_table_col.controls.clear()
            page.update()
            try:
                from .core import scrapers

                def prog(*a) -> None:
                    if a:
                        status.value = str(a[0])[:90]
                        page.update()

                status.value = f"Recuperation {source} : {username[:60]}..."
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
                    status.value = f"Aucune piste trouvee pour {username} sur {source}."
                    return
                state.acquire_rows = rows
                render_acquire_table(rows)
                status.value = f"{len(rows)} pistes -> telechargement en vrai lossless..."
                page.update()

                on_item = make_on_item(state.acquire_row_status)
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
                dup_txt = f", {dup} doublons sautes" if dup else ""
                if state.cancel_requested:
                    for cell in state.acquire_row_status.values():
                        if cell[0].visible:
                            cell[1].value, cell[1].color, cell[0].visible = "Annule", TXT_DIM, False
                    summary = (f"Recuperation annulee : {acq} gardees, {rej} rejetees, "
                               f"{nf} introuvables{dup_txt} (partiel).")
                else:
                    summary = (f"Recuperation finie : {acq} en bibliotheque, {rej} rejetees "
                               f"(upscale/court/mauvais match), {nf} introuvables{dup_txt}.")
                status.value = summary
                _banner(summary, bool(acq) and not state.cancel_requested)
            except ValueError as ex:  # token Discogs manquant, etc.
                status.value = f"Recuperation impossible : {ex}"
                if "token" in str(ex).lower():
                    settings_panel.visible = True
            except soulseek.SoulseekError as e:
                status.value = str(e)   # message clair (creds manquants / port occupe / login refuse)
                settings_panel.visible = True
            except Exception as ex:  # noqa: BLE001
                status.value = f"Erreur : {ex}"
            finally:
                acq_cancel_btn.visible = False
                state.active_proc = None
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

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
        label="Dossier bibliotheque (downloads lossless)", expand=True, read_only=True,
        value=cfg.get("download_dir", "") or str(paths.default_download_dir()))

    def on_dl_picked(e) -> None:
        if e.path:
            dl_dir_field.value = e.path
            config_mod.set_value("download_dir", e.path)
            cfg["download_dir"] = e.path
            page.update()

    dl_picker.on_result = on_dl_picked

    def browse_dl(_e) -> None:
        dl_picker.get_directory_path(dialog_title="Dossier bibliotheque (lossless verifie)")

    dl_browse_btn = ft.FilledButton(text="Parcourir", icon=ft.Icons.FOLDER_OPEN, on_click=browse_dl)
    preset_dd = ft.Dropdown(
        label="Qualite minimale", width=320,
        value=cfg.get("quality_preset", "dj_club"),
        options=[
            ft.dropdown.Option(key="dj_club", text="DJ Club (>=18 kHz, MP3 320 inclus)"),
            ft.dropdown.Option(key="audiophile", text="Audiophile (>=20 kHz)"),
            ft.dropdown.Option(key="puriste", text="Puriste (lossless pur)"),
        ])

    def save_settings(_e) -> None:
        vals = {
            "soulseek_user": slsk_user.value.strip(),
            "soulseek_pass": slsk_pass.value,
            "discogs_username": discogs_user.value.strip(),
            "discogs_token": discogs_tok.value.strip(),
            "bandcamp_username": bandcamp_user.value.strip(),
            "download_dir": (dl_dir_field.value or "").strip(),
            "quality_preset": preset_dd.value,
        }
        config_mod.set_many(vals)
        cfg.update(vals)   # garde le cache en memoire frais (relu aussi a chaud par do_acquire)
        status.value = "Reglages sauvegardes."
        page.update()

    settings_body = ft.Column([
        ft.Text("Soulseek : requis pour telecharger (upgrade + favoris). "
                "Discogs : username + token (discogs.com/settings/developers). "
                "Bandcamp : username seul (scrape public).", size=12, color=TXT_DIM),
        ft.Row([slsk_user, slsk_pass], wrap=True),
        ft.Row([discogs_user, discogs_tok], wrap=True),
        ft.Row([bandcamp_user], wrap=True),
        ft.Text("Tout ce que DDD valide (upgrade + favoris) est depose ici ; les faux/rejets "
                "vont a la corbeille.", size=12, color=TXT_DIM),
        ft.Row([dl_dir_field, dl_browse_btn]),
        ft.Text("Seuil de qualite : en dessous, un fichier est candidat a l'upgrade.",
                size=12, color=TXT_DIM),
        ft.Row([preset_dd], wrap=True),
        ft.FilledButton(text="Sauvegarder", on_click=save_settings),
    ], spacing=10)

    def toggle_settings(_e) -> None:
        settings_panel.visible = not settings_panel.visible
        page.update()

    settings_panel = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.SETTINGS, size=18, color=TXT_DIM),
                    ft.Text("Reglages", size=13, weight=ft.FontWeight.BOLD, color=TXT_DIM)],
                   spacing=6),
            settings_body,
        ], spacing=6),
        padding=8, border=ft.border.all(1, BORDER), border_radius=8, bgcolor=SURFACE,
        visible=False)

    # ====================================================================
    #  Boutons
    # ====================================================================
    browse_btn = ft.FilledButton(text="Parcourir", icon=ft.Icons.FOLDER_OPEN, on_click=browse)
    scan_btn = ft.FilledButton(text="Scanner", icon=ft.Icons.SEARCH, on_click=do_scan)
    upgrade_btn = ft.FilledButton(text="Upgrader la selection", icon=ft.Icons.UPGRADE,
                                  on_click=do_upgrade, disabled=True)
    lib_cancel_btn = ft.OutlinedButton(text="Annuler", icon=ft.Icons.CANCEL,
                                       on_click=do_cancel, visible=False)
    check_all_btn = ft.TextButton(text="Tout cocher", on_click=select_all_visible)
    uncheck_all_btn = ft.TextButton(text="Tout decocher", on_click=clear_selection)
    filter_dd.on_change = lambda _e: render_table()

    acquire_btn = ft.FilledButton(text="Recuperer & telecharger", icon=ft.Icons.DOWNLOAD,
                                  on_click=do_acquire)
    acq_cancel_btn = ft.OutlinedButton(text="Annuler", icon=ft.Icons.CANCEL,
                                       on_click=do_cancel, visible=False)
    settings_btn = ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="Reglages (identifiants)",
                                 on_click=toggle_settings)

    # 1er lancement sans identifiants : deplie Reglages + invite (tout en a besoin)
    if not ((cfg.get("soulseek_user") or "").strip() and (cfg.get("soulseek_pass") or "")):
        settings_panel.visible = True
        status.value = "Astuce : renseigne tes identifiants Soulseek (engrenage) pour digger."

    # ====================================================================
    #  Layout : barre fine (engrenage) + onglets + barre d'etat
    # ====================================================================
    library_tab = ft.Container(
        content=ft.Column([
            ft.Row([folder_field, browse_btn, scan_btn]),
            summary_row,
            dup_text,
            ft.Row([filter_dd, check_all_btn, uncheck_all_btn, upgrade_btn, lib_cancel_btn],
                   wrap=True, spacing=8, run_spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(table_col, expand=True, border=ft.border.all(1, BORDER),
                         border_radius=8, padding=8),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6))

    acquire_tab = ft.Container(
        content=ft.Column([
            ft.Row([source_dd, djset_url, acquire_btn, acq_cancel_btn],
                   wrap=True, spacing=8, run_spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([discogs_collection_cb, bandcamp_expand_cb], wrap=True),
            ft.Container(acquire_table_col, expand=True, border=ft.border.all(1, BORDER),
                         border_radius=8, padding=8),
        ], expand=True, spacing=10),
        padding=ft.padding.only(top=6))

    # Pas de bandeau-titre (redondant avec la barre de fenetre). L'engrenage Reglages
    # est pose en HAUT A DROITE, superpose (Stack) sur la zone vide de la barre d'onglets
    # -> point d'acces fixe et visible, sans consommer de hauteur. Le panneau toggle en bas.
    page.add(
        ft.Stack([
            ft.Tabs(selected_index=0, expand=True, tabs=[
                ft.Tab(text="Bibliotheque", content=library_tab),
                ft.Tab(text="Recuperer favoris", content=acquire_tab),
            ]),
            ft.Container(settings_btn, top=0, right=0),
        ], expand=True),
        progress,
        status,
        settings_panel,
    )


def run() -> None:
    """Point d'entree : lance la fenetre native."""
    ft.app(target=main)


if __name__ == "__main__":
    run()
