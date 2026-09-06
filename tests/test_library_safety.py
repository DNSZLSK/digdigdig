from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
import soundfile as sf

from ddd.core import fsutil, quality, scan, upgrade
from ddd.core.content import duplicate_paths
from ddd.core.soulseek import WantItem


def result(path, verdict=quality.LOSSLESS, duration=300):
    return quality.QualityResult(
        str(path), Path(path).name, Path(path).suffix, "lossless_container",
        44100, 2, duration, 22000, 0, 0, 0, 1000, verdict, "uncertain", "test")


def test_import_itself_or_subfolder_is_noop(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Title.flac"
    source.write_bytes(b"original")
    monkeypatch.setattr(upgrade, "scan_library", lambda *a, **kw: pytest.fail("must not scan itself"))
    for origin in (tmp_path, tmp_path / "subfolder"):
        stats = upgrade.import_folder(origin, tmp_path)
        assert stats["total"] == stats["trashed"] == 0
    assert source.read_bytes() == b"original"


def test_import_retains_errors_rejects_and_real_duplicates(tmp_path, monkeypatch):
    source, library = tmp_path / "source", tmp_path / "library"
    source.mkdir(); library.mkdir()
    records = []
    for name, verdict, data in [
        ("error", quality.ERROR, b"unreadable"),
        ("review", quality.DOUTEUX, b"filtered"),
        ("copy", quality.LOSSLESS, b"same content"),
        ("Artist - Title", quality.LOSSLESS, b"different content"),
    ]:
        path = source / f"{name}.flac"
        path.write_bytes(data)
        qr = result(path, verdict)
        if verdict == quality.DOUTEUX:
            qr.cutoff_hz = 16000
        records.append(NS(quality=qr))
    (library / "Artist - Title.flac").write_bytes(b"same content")
    monkeypatch.setattr(upgrade, "scan_library", lambda *a, **kw: records)
    monkeypatch.setattr(upgrade.trash, "send_to_trash", lambda *a: pytest.fail("import must not trash"))
    stats = upgrade.import_folder(source, library, preset="dj_club")
    assert stats == dict(total=4, kept=1, duplicates=1, retained=1, errors=1, trashed=0)
    assert (library / "Artist - Title (1).flac").read_bytes() == b"different content"
    assert (source / "copy.flac").exists()


def test_import_excludes_nested_destination(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    existing = library / "Artist - Title.flac"
    existing.write_bytes(b"keep")
    monkeypatch.setattr(upgrade, "scan_library", lambda *a, **kw: [NS(quality=result(existing))])
    assert upgrade.import_folder(tmp_path, library)["total"] == 0
    assert existing.read_bytes() == b"keep"


@pytest.mark.parametrize("failure", ["copy", "verify"])
def test_failed_deposit_retains_original_and_candidate(tmp_path, monkeypatch, failure):
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir(); staging.mkdir()
    original = library / "Artist - Title.flac"
    original.write_bytes(b"old")
    candidate = staging / original.name
    candidate.write_bytes(b"new")
    if failure == "copy":
        def fail(*args):
            raise OSError("disk full")
        monkeypatch.setattr(fsutil.shutil, "copyfileobj", fail)
    else:
        monkeypatch.setattr(fsutil, "file_hash", lambda p: str(p))
    monkeypatch.setattr(upgrade.trash, "send_to_trash", lambda *a: pytest.fail("original must survive"))
    with pytest.raises(OSError):
        upgrade._finalize_download(
            WantItem("Artist", "Title", 300, str(original)),
            NS(filepath=str(candidate), downloaded=True), result(candidate),
            preset="dj_club", download_dir=library, existing=set(), trash_origin=True)
    assert original.read_bytes() == b"old"
    assert candidate.read_bytes() == b"new"
    assert list(library.iterdir()) == [original]


@pytest.mark.parametrize("remove,trash_success", [(False, False), (True, False), (True, True)])
def test_original_removed_only_after_verified_install(tmp_path, monkeypatch, remove, trash_success):
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir(); staging.mkdir()
    original = library / "Artist - Title.flac"
    original.write_bytes(b"old")
    candidate = staging / original.name
    candidate.write_bytes(b"new")
    calls = []
    def trash(path):
        assert (library / "Artist - Title (1).flac").read_bytes() == b"new"
        assert original.read_bytes() == b"old"
        calls.append(path)
        return trash_success
    monkeypatch.setattr(upgrade.trash, "send_to_trash", trash)
    outcome, installed = upgrade._finalize_download(
        WantItem("Artist", "Title", 300, str(original)),
        NS(filepath=str(candidate), downloaded=True), result(candidate),
        preset="dj_club", download_dir=library, existing=set(), trash_origin=remove)
    assert installed
    assert bool(calls) == remove
    assert outcome.action == (upgrade.ACT_REPLACED if remove and trash_success else upgrade.ACT_KEPT_BESIDE)
    if remove and not trash_success:
        assert "trash failed" in outcome.note


@pytest.mark.parametrize("artist,title,duration", [
    ("John Other", "Title", 300), ("John Smith", "Title Extra", 300),
    ("John Smith", "Title", 100), ("John Smith", "Title", 700),
    ("John Smith", "Title", 0), ("John Smith", "Title", float("nan")),
    ("John Smith", "Title (Someone Remix)", 300),
])
def test_final_gate_rejects_wrong_identity_or_duration(artist, title, duration):
    candidate = f"{artist} - {title}.flac"
    reason = upgrade._reject_reason(WantItem("John Smith", "Title", 300, ""),
                                    NS(filepath=candidate), result(candidate, duration=duration))
    assert reason and reason[0] == upgrade.ACT_WRONG_MATCH


def test_final_gate_distinguishes_named_remixes():
    reason = upgrade._reject_reason(
        WantItem("Artist", "Title (Alice Remix)", 300, ""),
        NS(filepath="Artist - Title (Bob Remix).flac"), result("x.flac"))
    assert reason and reason[0] == upgrade.ACT_WRONG_MATCH


def test_subtitle_is_part_of_identity():
    reason = upgrade._reject_reason(
        WantItem("Artist", "Title (Part One)", 300, ""),
        NS(filepath="Artist - Title (Part Two).flac"), result("x.flac"))
    assert reason and reason[0] == upgrade.ACT_WRONG_MATCH


def test_wide_lossy_container_cannot_satisfy_purist():
    qr = result("wide.mp3")
    qr.format_class = "lossy"
    assert quality.is_accepted(qr, "dj_club")
    assert not quality.is_accepted(qr, "puriste")


def test_index_pointing_to_original_cannot_trash_it(tmp_path, monkeypatch):
    original = tmp_path / "Artist - Title.flac"
    original.write_bytes(b"original")
    monkeypatch.setattr(upgrade.trash, "send_to_trash", lambda *a: pytest.fail("original must survive"))
    outcome, installed = upgrade._finalize_download(
        WantItem("Artist", "Title", 300, str(original)),
        NS(filepath=str(original), downloaded=True), result(original, quality.ERROR),
        preset="dj_club", download_dir=tmp_path, existing=set(), trash_origin=True)
    assert outcome.action == upgrade.ACT_WRONG_MATCH and not installed
    assert original.read_bytes() == b"original"


@pytest.mark.parametrize("artist,candidate", [("Daft Punk vs Stardust", "Stardust"),
                                             ("中田ヤスタカ", "中田ヤスタカ"), ("A", "A")])
def test_final_gate_accepts_complete_collaborator_and_short_known_track(artist, candidate):
    assert upgrade._reject_reason(
        WantItem(artist, "Title", 60, ""), NS(filepath=f"{candidate} - Title.flac"),
        result("x.flac", duration=60)) is None


def test_content_not_size_defines_duplicates(tmp_path, monkeypatch):
    files = [tmp_path / f"{i}.wav" for i in range(3)]
    for path, data in zip(files, [b"AAAA", b"BBBB", b"AAAA"]):
        path.write_bytes(data)
    assert duplicate_paths(files) == [[files[0], files[2]]]
    monkeypatch.setattr(scan, "analyze_file", result)
    monkeypatch.setattr(scan.audit_mod, "audit_file", lambda p: None)
    records = scan.scan_library(tmp_path)
    assert [r.dup_count for r in records] == [2, 1, 2]
    assert len(scan.duplicate_groups(records)) == 1


@pytest.mark.parametrize("detector", ["legacy", "forensic"])
def test_direct_filtered_flac_is_not_claimed_proven_lossy(tmp_path, detector):
    sr = 44100
    data = np.random.default_rng(42).normal(0, .1, (sr * 3, 2))
    spectrum = np.fft.rfft(data, axis=0)
    spectrum[np.fft.rfftfreq(len(data), 1 / sr) > 15000] = 0
    path = tmp_path / "direct.flac"
    sf.write(path, np.fft.irfft(spectrum, n=len(data), axis=0), sr, subtype="PCM_16")
    qr = quality.analyze_file(path, detector=detector)
    assert qr.confidence == "uncertain"
    assert "source lossy" not in qr.reason
    assert not quality.is_accepted(qr, "dj_club")  # The user's bandwidth threshold still applies.
