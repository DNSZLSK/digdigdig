"""Cache de scan : un fichier inchange n'est pas re-analyse ; toute modification,
changement de moteur, erreur ou cache casse retombe sur l'analyse complete."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from ddd.core import quality, scan


def _qr(path, verdict=quality.HQ, cutoff=19500.0):
    p = Path(path)
    return quality.QualityResult(
        path=str(p), filename=p.name, ext=p.suffix, format_class="lossless_container",
        sample_rate=44100, channels=2, duration_s=30.0, cutoff_hz=cutoff, cutoff_std_hz=0.0,
        hf_energy_ratio=0.1, est_source_bitrate=0, container_bitrate=900,
        verdict=verdict, confidence="high", reason="test")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("DDD_SCAN_CACHE", str(tmp_path / "cache" / "scan.sqlite3"))
    monkeypatch.setattr(scan, "_current_detector", lambda: "legacy")
    d = tmp_path / "lib"
    d.mkdir()
    for name in ("A - One.flac", "B - Two.flac", "C - Three.wav"):
        (d / name).write_bytes(name.encode() * 10)
    calls = []

    def fake_analyze(f, detector=None):
        calls.append(Path(f).name)
        return _qr(f)

    monkeypatch.setattr(scan, "analyze_file", fake_analyze)
    return d, calls


def test_second_scan_reuses_unchanged_files(lib):
    d, calls = lib
    first = scan.scan_library(d)
    assert len(calls) == 3
    calls.clear()
    second = scan.scan_library(d)
    assert calls == []
    assert [r.quality.as_dict() for r in second] == [r.quality.as_dict() for r in first]


def test_modified_file_is_reanalyzed(lib):
    d, calls = lib
    scan.scan_library(d)
    calls.clear()
    (d / "B - Two.flac").write_bytes(b"replaced by a better copy" * 50)
    scan.scan_library(d)
    assert calls == ["B - Two.flac"]


def test_new_file_only_is_analyzed(lib):
    d, calls = lib
    scan.scan_library(d)
    calls.clear()
    (d / "D - Four.flac").write_bytes(b"new")
    scan.scan_folder(d)
    assert calls == ["D - Four.flac"]


def test_detector_change_invalidates(lib, monkeypatch):
    d, calls = lib
    scan.scan_library(d)
    calls.clear()
    monkeypatch.setattr(scan, "_current_detector", lambda: "forensic")
    scan.scan_library(d)
    assert len(calls) == 3


def test_errors_are_not_cached(lib, monkeypatch):
    d, calls = lib
    monkeypatch.setattr(scan, "analyze_file",
                        lambda f, detector=None: calls.append(Path(f).name) or _qr(f, quality.ERROR))
    scan.scan_library(d)
    calls.clear()
    scan.scan_library(d)
    assert len(calls) == 3        # une erreur (I/O reseau...) est retentee au scan suivant


def test_no_cache_flag_bypasses(lib):
    d, calls = lib
    scan.scan_library(d)
    calls.clear()
    scan.scan_library(d, use_cache=False)
    assert len(calls) == 3


def test_broken_cache_file_never_breaks_scan(lib, tmp_path):
    d, calls = lib
    bad = tmp_path / "cache" / "scan.sqlite3"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"this is not a sqlite database" * 100)
    records = scan.scan_library(d)
    assert len(records) == 3 and len(calls) == 3
    calls.clear()
    scan.scan_library(d)          # cache reconstruit -> les fichiers sont repris
    assert calls == []
    assert bad.with_suffix(".corrupt").exists()


def test_cache_disabled_by_env(lib, monkeypatch, tmp_path):
    d, calls = lib
    monkeypatch.setenv("DDD_SCAN_CACHE", "0")
    scan.scan_library(d)
    scan.scan_library(d)
    assert len(calls) == 6
    assert not (tmp_path / "cache").exists()
