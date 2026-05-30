"""Fenetre native DDD (Flet).

GUI sur le coeur : choisir un dossier, scanner la qualite (vrai lossless ou non),
voir le resultat dans une liste filtrable, puis upgrader les fichiers flagges via
Soulseek. Reglages (token Discogs, login Soulseek) persistes via core.config.

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
from .core.scan import ScanRecord, duplicate_groups, scan_library

VERDICT_COLOR = {
    quality.FAKE: ft.Colors.RED_400,
    quality.SUSPICIOUS: ft.Colors.AMBER,
    quality.LOSSY: ft.Colors.BLUE_400,
    quality.AUTHENTIC: ft.Colors.GREEN_400,
    "ERROR": ft.Colors.GREY,
    "SKIPPED": ft.Colors.GREY,
}
VERDICT_LABEL = {
    quality.FAKE: "Faux lossless",
    quality.SUSPICIOUS: "Suspect (320k)",
    quality.LOSSY: "Lossy",
    quality.AUTHENTIC: "Vrai lossless",
    "ERROR": "Erreur",
    "SKIPPED": "Ignore",
}
UPGRADABLE = {quality.FAKE, quality.LOSSY, quality.SUSPICIOUS}


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


class AppState:
    def __init__(self) -> None:
        self.folder: Optional[str] = None
        self.records: List[ScanRecord] = []
        self.selected: set = set()
        self.busy: bool = False


def main(page: ft.Page) -> None:
    state = AppState()
    cfg = config_mod.load()

    page.title = f"DDD - DigDigDig  v{__version__}"
    _set_window_size(page, 1100, 760)
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 16

    # --- widgets partages ---------------------------------------------------
    folder_field = ft.TextField(label="Dossier de musique", expand=True, read_only=True,
                                value=cfg.get("last_folder", ""))
    status = ft.Text("Choisis un dossier puis Scanner.", color=ft.Colors.GREY_400)
    progress = ft.ProgressBar(value=0, visible=False)
    summary_row = ft.Row(wrap=True, spacing=8)
    table_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=2)
    dup_text = ft.Text("", color=ft.Colors.AMBER, selectable=True)

    filter_dd = ft.Dropdown(
        label="Filtre", width=240, value="upgradable",
        options=[
            ft.dropdown.Option(key="upgradable", text="A upgrader (fake/lossy/suspect)"),
            ft.dropdown.Option(key="all", text="Tout"),
            ft.dropdown.Option(key=quality.FAKE, text="Faux lossless"),
            ft.dropdown.Option(key=quality.LOSSY, text="Lossy"),
            ft.dropdown.Option(key=quality.SUSPICIOUS, text="Suspect (320k)"),
            ft.dropdown.Option(key=quality.AUTHENTIC, text="Vrai lossless"),
        ])
    apply_switch = ft.Switch(label="Remplacer pour de vrai", value=False)
    delete_switch = ft.Switch(label="Supprimer l'original", value=False)

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    # --- helpers ------------------------------------------------------------
    def set_busy(b: bool) -> None:
        state.busy = b
        progress.visible = b
        scan_btn.disabled = b
        upgrade_btn.disabled = b or not state.records
        browse_btn.disabled = b
        page.update()

    def current_excludes() -> list:
        return [s for s in (cfg.get("default_excludes", "") or "").split(",") if s] or ["PROD"]

    def render_summary() -> None:
        from collections import Counter
        summary_row.controls.clear()
        counts = Counter(r.quality.verdict for r in state.records)
        for verdict, n in counts.most_common():
            summary_row.controls.append(
                ft.Container(
                    content=ft.Text(f"{VERDICT_LABEL.get(verdict, verdict)} : {n}",
                                    color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                    bgcolor=VERDICT_COLOR.get(verdict, ft.Colors.GREY),
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
            return v in UPGRADABLE
        return v == f

    def _on_check(e) -> None:
        idx = e.control.data
        if e.control.value:
            state.selected.add(idx)
        else:
            state.selected.discard(idx)

    def render_table() -> None:
        table_col.controls.clear()
        shown = [(i, r) for i, r in enumerate(state.records) if _visible(r)]
        if not shown:
            table_col.controls.append(ft.Text("Aucun fichier pour ce filtre.",
                                              color=ft.Colors.GREY_400))
            page.update()
            return
        for idx, rec in shown:
            q = rec.quality
            checkbox = ft.Checkbox(value=idx in state.selected,
                                   disabled=q.verdict not in UPGRADABLE,
                                   data=idx, on_change=_on_check)
            badge = ft.Container(
                content=ft.Text(VERDICT_LABEL.get(q.verdict, q.verdict), size=11,
                                color=ft.Colors.WHITE),
                bgcolor=VERDICT_COLOR.get(q.verdict, ft.Colors.GREY),
                padding=ft.padding.symmetric(vertical=2, horizontal=8),
                border_radius=10, width=120)
            dup_tag = ft.Text("  [doublon]", size=11, color=ft.Colors.AMBER) \
                if rec.is_duplicate else ft.Text("")
            table_col.controls.append(
                ft.Row([
                    checkbox, badge,
                    ft.Text(f"{q.cutoff_hz:.0f} Hz", width=80, size=12, color=ft.Colors.GREY_400),
                    ft.Text(q.filename, expand=True, size=12, no_wrap=True),
                    dup_tag,
                ], spacing=8))
        page.update()

    # --- actions ------------------------------------------------------------
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
                n_up = sum(1 for r in state.records if r.quality.verdict in UPGRADABLE)
                status.value = f"{len(state.records)} fichiers analyses - {n_up} a upgrader."
            except Exception as ex:  # noqa: BLE001
                status.value = f"Erreur scan : {ex}"
            finally:
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def do_upgrade(_e) -> None:
        if not state.records:
            return
        chosen = [state.records[i] for i in sorted(state.selected)] if state.selected else \
                 [r for r in state.records if r.quality.verdict in UPGRADABLE]
        if not chosen:
            status.value = "Rien a upgrader (coche des fichiers ou change le filtre)."
            page.update()
            return

        def worker() -> None:
            set_busy(True)
            progress.value = None  # barre indeterminee (animee) pendant le download
            try:
                staging = paths.staging_dir() / "upgrade"
                log_path = paths.logs_dir() / "ddd_upgrade.log"

                def prog(*a) -> None:
                    if len(a) == 1:
                        status.value = str(a[0])[:90]
                        page.update()

                mode = "APPLY" if apply_switch.value else "DRY-RUN"
                status.value = f"Upgrade ({mode}) de {len(chosen)} fichiers via Soulseek..."
                page.update()
                outcomes = upgrade_mod.run_upgrade(
                    state.folder, root=paths.resource_base(), staging_dir=staging,
                    apply=apply_switch.value, delete_old=delete_switch.value,
                    scan_results=chosen, progress=prog, log_path=log_path)
                from collections import Counter
                c = Counter(o.action for o in outcomes)
                ok = c.get(upgrade_mod.ACT_REPLACED, 0) + c.get(upgrade_mod.ACT_WOULD_REPLACE, 0)
                rej = c.get(upgrade_mod.ACT_REJECTED_FAKE, 0)
                nf = c.get(upgrade_mod.ACT_NOT_FOUND, 0)
                verb = "remplaces" if apply_switch.value else "trouves (dry-run)"
                status.value = (f"Upgrade fini : {ok} {verb}, {rej} rejetes (upscale), "
                                f"{nf} introuvables.")
                # Apres un remplacement reel, re-scanner pour refleter les nouveaux verdicts
                if apply_switch.value and ok:
                    status.value += " Re-scan en cours..."
                    page.update()
                    state.selected.clear()
                    state.records = scan_library(state.folder, exclude_names=current_excludes())
                    render_summary()
                    render_table()
                    status.value = (f"Upgrade fini : {ok} {verb}, {rej} rejetes, "
                                    f"{nf} introuvables. Table rafraichie.")
            except soulseek.SoulseekError:
                status.value = ("Identifiants Soulseek requis : ouvre Reglages (engrenage en "
                                "haut a droite), saisis ton login/mot de passe, puis reessaie.")
                settings_body.visible = True
            except Exception as ex:  # noqa: BLE001
                status.value = f"Erreur upgrade : {ex}"
            finally:
                set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def select_all_visible(_e) -> None:
        for i, rec in enumerate(state.records):
            if _visible(rec) and rec.quality.verdict in UPGRADABLE:
                state.selected.add(i)
        render_table()

    def clear_selection(_e) -> None:
        state.selected.clear()
        render_table()

    # --- reglages -----------------------------------------------------------
    discogs_tok = ft.TextField(label="Token Discogs", password=True, can_reveal_password=True,
                               value=cfg.get("discogs_token", ""), width=360)
    slsk_user = ft.TextField(label="Soulseek user", value=cfg.get("soulseek_user", ""), width=200)
    slsk_pass = ft.TextField(label="Soulseek pass", password=True, can_reveal_password=True,
                             value=cfg.get("soulseek_pass", ""), width=200)

    def save_settings(_e) -> None:
        config_mod.set_many({
            "discogs_token": discogs_tok.value.strip(),
            "soulseek_user": slsk_user.value.strip(),
            "soulseek_pass": slsk_pass.value,
        })
        status.value = "Reglages sauvegardes."
        page.update()

    settings_body = ft.Column([
        ft.Text("Soulseek requis pour l'upgrade. Token Discogs pour scraper.",
                size=12, color=ft.Colors.GREY_400),
        ft.Row([slsk_user, slsk_pass]),
        discogs_tok,
        ft.FilledButton(text="Sauvegarder", on_click=save_settings),
    ], spacing=10, visible=False)

    def toggle_settings(_e) -> None:
        settings_body.visible = not settings_body.visible
        page.update()

    settings_panel = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.SETTINGS),
                    ft.TextButton(text="Reglages (creds)", on_click=toggle_settings)]),
            settings_body,
        ], spacing=6),
        padding=8, border=ft.border.all(1, ft.Colors.GREY_800), border_radius=8)

    # --- boutons ------------------------------------------------------------
    browse_btn = ft.FilledButton(text="Parcourir", icon=ft.Icons.FOLDER_OPEN, on_click=browse)
    scan_btn = ft.FilledButton(text="Scanner", icon=ft.Icons.SEARCH, on_click=do_scan)
    upgrade_btn = ft.FilledButton(text="Upgrader la selection", icon=ft.Icons.UPGRADE,
                                  on_click=do_upgrade, disabled=True)
    settings_btn = ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="Reglages (identifiants)",
                                 on_click=toggle_settings)
    check_all_btn = ft.TextButton(text="Tout cocher", on_click=select_all_visible)
    uncheck_all_btn = ft.TextButton(text="Tout decocher", on_click=clear_selection)
    filter_dd.on_change = lambda _e: render_table()

    # 1er lancement sans identifiants : deplie Reglages + invite (l'upgrade en a besoin)
    if not ((cfg.get("soulseek_user") or "").strip() and (cfg.get("soulseek_pass") or "")):
        settings_body.visible = True
        status.value = "Astuce : renseigne tes identifiants Soulseek (engrenage) pour pouvoir upgrader."

    # --- layout -------------------------------------------------------------
    page.add(
        ft.Row([ft.Icon(ft.Icons.MUSIC_NOTE, color=ft.Colors.BLUE_400),
                ft.Text("DDD - DigDigDig", size=22, weight=ft.FontWeight.BOLD),
                ft.Text("le crate digger qui creuse trois fois", size=12,
                        color=ft.Colors.GREY_400),
                ft.Container(expand=True), settings_btn],
               spacing=10),
        ft.Row([folder_field, browse_btn, scan_btn]),
        progress,
        status,
        ft.Divider(),
        summary_row,
        dup_text,
        ft.Row([filter_dd, check_all_btn, uncheck_all_btn, ft.Container(expand=True),
                apply_switch, delete_switch, upgrade_btn]),
        ft.Container(table_col, expand=True, border=ft.border.all(1, ft.Colors.GREY_800),
                     border_radius=8, padding=8),
        settings_panel,
    )


def run() -> None:
    """Point d'entree : lance la fenetre native."""
    ft.app(target=main)


if __name__ == "__main__":
    run()
