"""clear_run_staging : purge les dossiers de travail sldl d'un run fini, et RIEN d'autre.

Garde-fou anti-regression : on supprime le sous-dossier <staging>/<stem>/ (downloads
orphelins, .incomplete, _index.csv) + le CSV d'entree, mais jamais un contenu voisin
(autre run en cours, library, etc.).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import soulseek


def test_clear_run_staging_removes_workdirs_and_csv(tmp_path):
    staging = tmp_path / "staging"
    # 2 sous-dossiers de travail sldl + leurs CSV d'entree
    for stem in ("ddd_acquire", "ddd_acquire_mp3"):
        d = staging / stem
        d.mkdir(parents=True)
        (d / "Some Artist - Some Title.flac").write_bytes(b"junk")
        (d / "Half - Download.flac.incomplete").write_bytes(b"partial")
        (d / "_index.csv").write_text("filepath,state\n", encoding="utf-8")
        (staging / f"{stem}.csv").write_text("Artist,Title\n", encoding="utf-8")

    soulseek.clear_run_staging(staging, "ddd_acquire.csv", "ddd_acquire_mp3.csv")

    assert not (staging / "ddd_acquire").exists()
    assert not (staging / "ddd_acquire_mp3").exists()
    assert not (staging / "ddd_acquire.csv").exists()
    assert not (staging / "ddd_acquire_mp3.csv").exists()


def test_clear_run_staging_leaves_unrelated_content(tmp_path):
    staging = tmp_path / "staging"
    (staging / "ddd_acquire").mkdir(parents=True)
    (staging / "ddd_acquire" / "x.flac").write_bytes(b"junk")
    # contenu voisin a NE PAS toucher (ex. un run upgrade en parallele, ou le cache)
    keep_dir = staging / "ddd_upgrade"
    keep_dir.mkdir()
    (keep_dir / "keep.flac").write_bytes(b"keep")
    keep_file = staging / "unrelated.txt"
    keep_file.write_text("keep", encoding="utf-8")

    soulseek.clear_run_staging(staging, "ddd_acquire.csv")

    assert not (staging / "ddd_acquire").exists()
    assert (keep_dir / "keep.flac").exists()
    assert keep_file.exists()


def test_clear_run_staging_is_safe_when_missing(tmp_path):
    # rien a supprimer (run sans staging) -> pas d'exception
    soulseek.clear_run_staging(tmp_path / "nope", "ddd_acquire.csv")
