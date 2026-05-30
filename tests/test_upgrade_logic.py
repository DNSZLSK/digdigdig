"""Test de la logique d'upgrade sans reseau : on simule sldl + le re-audit.

Valide que run_upgrade :
  - remplace (ou would-replace) un download AUTHENTIC,
  - REJETTE un download qui revient en upscale (FAKE/LOSSY) - le coeur de la valeur,
  - rapporte NOT_FOUND quand sldl ne ramene rien,
  - ignore les noms non parseables.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality, soulseek, upgrade as up
from ddd.core.quality import QualityResult
from ddd.core.scan import ScanRecord


def _qr(path, verdict, cutoff=16000.0, fclass="lossless_container"):
    return QualityResult(
        path=path, filename=Path(path).name, ext=Path(path).suffix.lower(),
        format_class=fclass, sample_rate=44100, channels=2, duration_s=300.0,
        cutoff_hz=cutoff, cutoff_std_hz=0.0, hf_energy_ratio=0.0,
        est_source_bitrate=160, container_bitrate=1411,
        verdict=verdict, confidence="high", reason="test",
    )


def main():
    tmp = ROOT / "staging" / "_test_upgrade"
    tmp.mkdir(parents=True, exist_ok=True)

    # Fichiers "originaux" simules (faux lossless dans la biblio)
    scan = [
        _qr(r"C:\lib\Artist A - Good.wav", quality.FAKE),       # sldl ramenera un vrai -> REPLACE
        _qr(r"C:\lib\Artist B - Upscale.wav", quality.FAKE),    # sldl ramenera un upscale -> REJECT
        _qr(r"C:\lib\Artist C - Rare.wav", quality.FAKE),       # introuvable -> NOT_FOUND
        _qr(r"C:\lib\NoArtist.wav", quality.FAKE),              # non parseable
        _qr(r"C:\lib\Artist D - Real.flac", quality.AUTHENTIC), # deja bon -> hors want-list
    ]

    # Faux downloads sur disque
    good = tmp / "Artist A - Good.flac"
    bad = tmp / "Artist B - Upscale.flac"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    # Monkeypatch : pas de reseau, pas de slskd
    soulseek.stop_slskd = lambda: False
    soulseek.read_soulseek_creds = lambda: {"user": "t", "pass": "t"}
    soulseek.run_sldl = lambda *a, **k: 0

    def fake_index(_):
        return [
            soulseek.DownloadResult("Artist A", "Good", str(good), 300, "1", "0"),
            soulseek.DownloadResult("Artist B", "Upscale", str(bad), 300, "1", "0"),
            # Artist C absent de l'index -> NOT_FOUND
        ]
    soulseek.read_index = fake_index

    # Re-audit simule : A authentique, B upscale
    real_analyze = quality.analyze_file
    def fake_analyze(p):
        p = str(p)
        if p == str(good):
            return _qr(p, quality.AUTHENTIC, cutoff=22050.0)
        if p == str(bad):
            return _qr(p, quality.FAKE, cutoff=16000.0)
        return real_analyze(p)
    up.quality.analyze_file = fake_analyze

    outcomes = up.run_upgrade(
        "C:\\lib", root=ROOT, staging_dir=tmp,
        scan_results=scan, apply=False,
    )

    print("%-16s %-10s %-9s %s" % ("ACTION", "ARTIST", "CUTOFF", "NOTE"))
    print("-" * 80)
    by_action = {}
    for o in outcomes:
        by_action[o.action] = o
        print("%-16s %-10s %-9s %s" % (o.action, o.artist, o.new_cutoff_hz, o.note[:46]))

    # Assertions
    assert by_action.get(up.ACT_WOULD_REPLACE), "Artist A devrait etre WOULD_REPLACE"
    assert by_action[up.ACT_WOULD_REPLACE].artist == "Artist A"
    assert by_action.get(up.ACT_REJECTED_FAKE), "Artist B devrait etre REJECTED_FAKE"
    assert by_action[up.ACT_REJECTED_FAKE].artist == "Artist B"
    assert by_action.get(up.ACT_NOT_FOUND), "Artist C devrait etre NOT_FOUND"
    assert by_action.get(up.ACT_UNPARSEABLE), "NoArtist devrait etre UNPARSEABLE"
    # Le fichier authentique deja en place ne doit PAS etre dans la want-list
    assert all(o.original != r"C:\lib\Artist D - Real.flac" for o in outcomes)

    # Chemin GUI : run_upgrade doit accepter des ScanRecord (verdict/chemin dans .quality),
    # pas seulement des QualityResult. Non-regression du crash "'ScanRecord' has no verdict".
    scan_records = [ScanRecord(quality=q, naming=None, size_bytes=0, dup_count=1) for q in scan]
    gui_outcomes = up.run_upgrade(
        "C:\\lib", root=ROOT, staging_dir=tmp,
        scan_results=scan_records, apply=False,
    )
    gui_actions = {o.action for o in gui_outcomes}
    assert up.ACT_WOULD_REPLACE in gui_actions, "GUI/ScanRecord : Artist A devrait etre WOULD_REPLACE"
    assert up.ACT_REJECTED_FAKE in gui_actions, "GUI/ScanRecord : Artist B devrait etre REJECTED_FAKE"
    assert up.ACT_UNPARSEABLE in gui_actions, "GUI/ScanRecord : NoArtist devrait etre UNPARSEABLE"
    print("OK - chemin GUI (ScanRecord) accepte, plus de crash sur .verdict")

    # cleanup
    good.unlink(); bad.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass

    print("\nOK - toutes les assertions passent")


if __name__ == "__main__":
    main()
