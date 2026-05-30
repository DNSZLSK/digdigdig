"""Tests du scan fusionne : merge qualite+nommage et detection de doublons (taille)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality, audit
from ddd.core.quality import QualityResult
from ddd.core.audit import NameAudit
from ddd.core.scan import ScanRecord, duplicate_groups, SCAN_RECORD_FIELDS


def _rec(path, verdict, status, size):
    q = QualityResult(
        path=path, filename=Path(path).name, ext=Path(path).suffix.lower(),
        format_class="lossless_container", sample_rate=44100, channels=2,
        duration_s=300.0, cutoff_hz=16000.0, cutoff_std_hz=0.0, hf_energy_ratio=0.0,
        est_source_bitrate=160, container_bitrate=1411,
        verdict=verdict, confidence="high", reason="test",
    )
    n = NameAudit(
        path=path, filename=Path(path).name, name_artist="A", name_title="T",
        tag_artist="A", tag_title="T", artist_coverage=1.0, title_coverage=1.0,
        name_version="", tag_version="", status=status, reason="test",
    )
    return ScanRecord(q, n, size, 0)  # dup_count recompute by hand below


def main():
    # 3 fichiers : 2 partagent une taille (doublon), 1 unique
    recs = [
        _rec(r"C:\lib\A\x.wav", quality.FAKE, audit.OK, 5_000_000),
        _rec(r"C:\lib\B\x.wav", quality.FAKE, audit.OK, 5_000_000),   # meme taille que le 1er
        _rec(r"C:\lib\C\y.wav", quality.AUTHENTIC, audit.OK, 7_000_000),
    ]
    # recompute dup_count comme le fait scan_library
    from collections import Counter
    sizes = Counter(r.size_bytes for r in recs)
    for r in recs:
        r.dup_count = sizes[r.size_bytes]

    groups = duplicate_groups(recs)
    assert len(groups) == 1, f"attendu 1 groupe de doublons, obtenu {len(groups)}"
    assert len(groups[0]) == 2, "le groupe doit contenir 2 fichiers"
    assert recs[0].is_duplicate and recs[1].is_duplicate
    assert not recs[2].is_duplicate

    # as_dict expose bien les champs du CSV fusionne
    d = recs[0].as_dict()
    for field in SCAN_RECORD_FIELDS:
        assert field in d, f"champ manquant dans as_dict: {field}"
    assert d["quality_verdict"] == quality.FAKE
    assert d["name_status"] == audit.OK
    assert d["dup_count"] == 2

    print("OK - merge qualite+nommage + detection doublons : assertions passent")


if __name__ == "__main__":
    main()
