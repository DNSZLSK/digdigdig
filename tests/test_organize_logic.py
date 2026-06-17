"""Tests du moteur de tri : fichiers temporaires reels, lookup injecte (zero reseau)."""

from __future__ import annotations

from pathlib import Path

import ddd.core.organize as org
from ddd.core.fsutil import safe_move
from ddd.core.genre import GenreResult


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def fake_lookup(artist, title, **kw):
    """Artist A -> Acid House (=> ACID) ; tout le reste -> miss (=> _INBOX)."""
    if artist.lower() == "artist a":
        return GenreResult(styles=["Acid House"], source="discogs", query=f"{artist} - {title}")
    return GenreResult(query=f"{artist} - {title}")


def _pile(tmp_path):
    src = tmp_path / "pile"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    a = _touch(src / "Artist A - Title.mp3")
    b = _touch(src / "Artist B - Other.mp3")
    s = _touch(src / "slugfilehere.mp3")          # pas de ' - ' -> SKIP
    return src, lib, a, b, s


def test_dry_run_moves_nothing(tmp_path):
    src, lib, a, b, s = _pile(tmp_path)
    rep = org.sort_folder(src, library_root=lib, apply=False, lookup=fake_lookup)
    by = {o.src: o for o in rep.ops}
    assert by[str(a)].action == org.MOVE and by[str(a)].folder == "ACID"
    assert by[str(b)].action == org.INBOX_ACT and by[str(b)].folder == org.INBOX
    assert by[str(s)].action == org.SKIP
    # rien ne bouge, aucun dossier cree
    assert a.exists() and b.exists() and s.exists()
    assert not (lib / "ACID").exists() and not (lib / "_INBOX").exists()
    assert rep.applied is False


def test_apply_files_and_writes_log(tmp_path):
    src, lib, a, b, s = _pile(tmp_path)
    out = tmp_path / "out"
    rep = org.sort_folder(src, library_root=lib, apply=True, lookup=fake_lookup, outputs_dir=out)
    assert (lib / "ACID" / "Artist A - Title.mp3").exists()
    assert (lib / "_INBOX" / "Artist B - Other.mp3").exists()
    assert s.exists(), "le slug illisible reste sur place"
    assert not (src / "Artist A - Title.mp3").exists()
    assert rep.log_path and Path(rep.log_path).exists()


def test_collision_gets_suffix(tmp_path):
    src, lib, a, b, s = _pile(tmp_path)
    _touch(lib / "ACID" / "Artist A - Title.mp3", b"already-here")   # dest pris, autres octets
    org.sort_folder(src, library_root=lib, apply=True, lookup=fake_lookup)
    names = {p.name for p in (lib / "ACID").glob("*.mp3")}
    assert "Artist A - Title.mp3" in names
    assert "Artist A - Title (1).mp3" in names


def test_no_inbox_leaves_miss_in_place(tmp_path):
    src, lib, a, b, s = _pile(tmp_path)
    rep = org.sort_folder(src, library_root=lib, apply=True, route_inbox=False, lookup=fake_lookup)
    assert b.exists() and not (lib / "_INBOX").exists()
    assert any(o.src == str(b) and o.action == org.SKIP for o in rep.ops)
    # Artist A est tout de meme classe
    assert (lib / "ACID" / "Artist A - Title.mp3").exists()


def test_init_tree_creates_all_folders(tmp_path):
    lib = tmp_path / "lib"
    created = org.init_library_tree(lib)
    for name in list(org.DEFAULT_GENRE_MAPPING) + [org.INBOX]:
        assert (lib / name).is_dir()
    assert len(created) == len(org.DEFAULT_GENRE_MAPPING) + 1


def test_non_recursive_ignores_subfolders(tmp_path):
    # un dossier perso (curated) sous src ne doit PAS etre touche
    src, lib, a, b, s = _pile(tmp_path)
    curated = _touch(src / "MOUSTAKI" / "Artist A - Title.mp3", b"curated")
    org.sort_folder(src, library_root=lib, apply=True, lookup=fake_lookup)
    assert curated.exists(), "le tri ne descend pas dans les sous-dossiers"


def test_default_src_is_library_root(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    a = _touch(lib / "Artist A - Title.mp3")
    org.sort_folder(library_root=lib, apply=True, lookup=fake_lookup)   # src omis -> lib
    assert (lib / "ACID" / "Artist A - Title.mp3").exists()
    assert not a.exists()


def test_safe_move_dry_run_and_collision(tmp_path):
    src = _touch(tmp_path / "a.mp3", b"x")
    dest_dir = tmp_path / "d"
    d = safe_move(src, dest_dir, dry_run=True)
    assert d == dest_dir / "a.mp3"
    assert src.exists() and not dest_dir.exists(), "dry-run ne cree ni ne deplace rien"

    d2 = safe_move(src, dest_dir)
    assert d2 == dest_dir / "a.mp3" and d2.exists() and not src.exists()

    src2 = _touch(tmp_path / "a.mp3", b"y")
    d3 = safe_move(src2, dest_dir)
    assert d3 == dest_dir / "a (1).mp3" and d3.exists()
