"""Tests du moteur de renommage : fichiers temporaires reels, corbeille mockee."""

from __future__ import annotations

from pathlib import Path

import ddd.core.rename as rn
from ddd.core.naming import ResolvedName


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.write_bytes(data)
    return p


def test_sanitize_illegal_chars():
    assert rn._sanitize('a/b:c?.mp3') == "a_b_c_.mp3"
    assert rn._sanitize("Trailing dot.") == "Trailing dot"


def test_dry_run_changes_nothing(tmp_path):
    # '[ABC123]' est un code label que clean() retire -> RENOMMERAIT, mais dry-run ne touche rien.
    f = _touch(tmp_path / "Artist - Title [ABC123].mp3")
    rep = rn.rename_folder(tmp_path, apply=False)
    rens = rep.of(rn.REN)
    assert rens and Path(rens[0].dst).name == "Artist - Title.mp3"
    assert f.exists(), "dry-run ne doit RIEN renommer sur disque"
    assert not (tmp_path / "Artist - Title.mp3").exists()


def test_apply_renames_and_writes_log(tmp_path):
    f = _touch(tmp_path / "Artist - Title [ABC123].mp3")
    rep = rn.rename_folder(tmp_path, apply=True, outputs_dir=tmp_path / "out")
    assert (tmp_path / "Artist - Title.mp3").exists()
    assert not f.exists()
    assert rep.log_path and Path(rep.log_path).exists()


def test_collision_gets_suffix(tmp_path):
    # Deux fichiers (tailles differentes -> pas des doublons) resolvent au meme nom.
    _touch(tmp_path / "Artist - Title [ABC1].mp3", b"a")
    _touch(tmp_path / "Artist - Title [ZZ9].mp3", b"bb")
    rn.rename_folder(tmp_path, apply=True, outputs_dir=tmp_path / "out")
    names = {p.name for p in tmp_path.glob("*.mp3")}
    assert "Artist - Title.mp3" in names
    assert "Artist - Title (1).mp3" in names


def test_non_confident_is_skipped_not_renamed(tmp_path, monkeypatch):
    f = _touch(tmp_path / "slug-file-here.mp3")
    monkeypatch.setattr(rn, "resolve_name",
                        lambda p: ResolvedName("Wrong", "Guess", "tags", False))
    rep = rn.rename_folder(tmp_path, apply=True, outputs_dir=tmp_path / "out")
    assert f.exists(), "resolution non fiable -> aucun rename"
    assert rep.of(rn.SKIP) and not rep.of(rn.REN)


def test_dedup_trashes_redundant_keeps_cleanest(tmp_path, monkeypatch):
    same = b"identical-bytes" * 1000
    _touch(tmp_path / "track.mp3", same)
    _touch(tmp_path / "track - Copie.mp3", same)
    _touch(tmp_path / "track - Copie - Copie.mp3", same)
    trashed = []
    monkeypatch.setattr(rn.trash, "send_to_trash", lambda p: (trashed.append(str(p)), True)[1])
    monkeypatch.setattr(rn, "resolve_name", lambda p: ResolvedName("", "", "deslug", False))
    rep = rn.rename_folder(tmp_path, apply=True, dedup=True, outputs_dir=tmp_path / "out")
    assert len(trashed) == 2, f"2 copies a la corbeille : {trashed}"
    assert (tmp_path / "track.mp3").exists(), "le keep (0 'copie') reste"
    assert rep.dups and len(rep.dups[0].redundant) == 2


def test_dedup_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    same = b"zzz" * 5000
    _touch(tmp_path / "a.mp3", same)
    _touch(tmp_path / "a - Copie.mp3", same)
    trashed = []
    monkeypatch.setattr(rn.trash, "send_to_trash", lambda p: (trashed.append(str(p)), True)[1])
    rep = rn.rename_folder(tmp_path, apply=False, dedup=True)
    assert not trashed, "dry-run ne supprime rien"
    assert (tmp_path / "a - Copie.mp3").exists()
    assert rep.dups and len(rep.dups[0].redundant) == 1
