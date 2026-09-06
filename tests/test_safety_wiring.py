from types import SimpleNamespace as NS

import pytest

from ddd import cli, gui
from ddd.core import quality, upgrade
from ddd.core.scan import ScanRecord
from test_gui_build import StubPage
from test_library_safety import result


@pytest.mark.parametrize("remove", [False, True])
def test_cli_upgrade_explicit_removal(tmp_path, monkeypatch, capsys, remove):
    captured = {}
    def run(folder, **kwargs):
        captured.update(kwargs)
        return []
    monkeypatch.setattr(upgrade, "run_upgrade", run)
    monkeypatch.setattr(cli.paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.paths, "outputs_dir", lambda: tmp_path)
    args = ["upgrade", str(tmp_path), "--download-dir", str(tmp_path)]
    if remove:
        args.append("--trash-original")
    assert cli.main(args) == 0
    assert captured["trash_original"] is remove
    assert ("originals retained" in capsys.readouterr().err) is not remove


def test_cli_import_reports_retained_and_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(upgrade, "import_folder", lambda *a, **kw:
                        dict(total=4, kept=1, duplicates=1, retained=1, errors=1, trashed=0))
    assert cli.main(["import", str(tmp_path), "--download-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "confirmed duplicates retained" in output
    assert "unreadable / failed, retained" in output
    assert "-> trash" not in output


def walk(control):
    yield control
    for child in control._get_children():
        yield from walk(child)


@pytest.mark.parametrize("remove", [False, True])
def test_gui_upgrade_option_and_retained_count(tmp_path, monkeypatch, remove):
    state = gui.AppState()
    monkeypatch.setattr(gui, "AppState", lambda: state)
    monkeypatch.setattr(gui.config_mod, "load", lambda: {"soulseek_user": "test", "soulseek_pass": "test"})
    monkeypatch.setattr(gui.paths, "download_dir", lambda *a: tmp_path)
    monkeypatch.setattr(gui.paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(gui.paths, "outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(gui.paths, "cache_dl_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(gui.atexit, "register", lambda *a: None)
    monkeypatch.delenv("DDD_UPDATE_CHECK", raising=False)
    page = StubPage()
    gui.main(page)
    controls = [item for root in page.controls for item in walk(root)]
    option = next(c for c in controls if getattr(c, "label", "") == "Trash originals after verified replacement")
    assert option.value is False
    option.value = remove
    button = next(c for c in controls if str(getattr(c, "text", "")).startswith("Upgrade selection"))
    state.folder = str(tmp_path)
    state.records = [ScanRecord(result(tmp_path / "Artist - Title.flac"), None, 1, 1)]
    state.selected = {0}
    captured = {}
    def run(folder, **kwargs):
        captured.update(kwargs)
        return [upgrade.UpgradeOutcome(upgrade.ACT_KEPT_BESIDE, "Artist", "Title", "original")]
    monkeypatch.setattr(upgrade, "run_upgrade", run)
    monkeypatch.setattr(gui, "scan_library", lambda *a, **kw: [])
    monkeypatch.setattr(gui.threading, "Thread", lambda target, **kw: NS(start=target))
    button.on_click(None)
    assert captured["trash_original"] is remove
    assert state.last_upgraded == 1
    assert option.disabled is False
    assert any("1 originals retained" in str(getattr(c, "value", "")) for c in controls if isinstance(c, gui.ft.Text))


def test_upgrade_in_library_does_not_deduplicate_against_itself(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Title.flac"
    source.write_bytes(b"old")
    qr = result(source, quality.DOUTEUX)
    qr.cutoff_hz = 16000
    captured = {}
    def download(items, **kwargs):
        captured.update(kwargs)
        captured["items"] = items
        return []
    monkeypatch.setattr(upgrade, "_download_pass", download)
    monkeypatch.setattr(upgrade.soulseek, "clear_run_staging", lambda *a: None)
    upgrade.run_upgrade(tmp_path, root=tmp_path, download_dir=tmp_path,
                        staging_dir=tmp_path / "cache", preset="dj_club", scan_results=[qr])
    assert len(captured["items"]) == 1
    assert captured["trash_origin"] is False
