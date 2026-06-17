"""Test CLI `ddd sort` : cablage parseur -> handler -> moteur (lookup monkeypatche)."""

from __future__ import annotations

from pathlib import Path

import ddd.cli as cli
import ddd.core.genre as genre
from ddd.core import organize
from ddd.core.genre import GenreResult


def test_sort_dry_run_exit0(tmp_path, monkeypatch, capsys):
    src = tmp_path / "pile"
    src.mkdir()
    (src / "Mr Fingers - Mystery.mp3").write_bytes(b"x")
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(genre, "lookup_genre",
                        lambda a, t, **k: GenreResult(styles=["Acid House"], source="discogs"))
    monkeypatch.setattr(cli.paths, "genre_cache_dir", lambda: tmp_path / "gc")

    rc = cli.main(["sort", str(src), "--library", str(lib)])
    cap = capsys.readouterr()
    assert rc == 0
    assert "DRY-RUN" in (cap.out + cap.err)
    assert "ACID" in cap.out
    assert (src / "Mr Fingers - Mystery.mp3").exists(), "dry-run ne deplace rien"
    assert not (lib / "ACID").exists()


def test_sort_defaults_library_to_download_dir(tmp_path, monkeypatch):
    # ni --library ni config -> tombe sur download_dir / ~/Music/DDD (plus d'erreur)
    monkeypatch.setattr(cli.config_mod, "get", lambda *a, **k: "")
    captured = {}

    def fake_sort(src, **kw):
        captured.update(kw)
        return organize.SortReport()

    monkeypatch.setattr(cli.organize_mod, "sort_folder", fake_sort)
    src = tmp_path / "pile"
    src.mkdir()
    rc = cli.main(["sort", str(src)])
    assert rc == 0
    assert Path(captured["library_root"]) == cli.paths.default_download_dir()
