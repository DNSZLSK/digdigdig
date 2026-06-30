"""Garde-fou : clic manuel (forced) = override total.

Une track cochee a la main doit etre cherchee/upgradee MEME si elle passe deja le seuil
(is_accepted) ET MEME si elle est deja dans la bibliotheque (dedup). Et si son fichier source
est deja DANS la lib, l'upgrade se depose SUR PLACE (dans son sous-dossier), pas a la racine.
Le re-audit spectral reste le garde-fou (seul un download valide remplace).
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import upgrade as up, quality, soulseek
from ddd.core.soulseek import DownloadResult


def _mk(p: Path, data=b"\x00" * 32):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _qr(path, verdict, cutoff, ext, bitrate, fclass):
    return quality.QualityResult(
        path=str(path), filename=Path(path).name, ext=ext, format_class=fclass,
        sample_rate=44100, channels=2, duration_s=300.0, cutoff_hz=cutoff,
        cutoff_std_hz=0.0, hf_energy_ratio=0.0, est_source_bitrate=0,
        container_bitrate=bitrate, verdict=verdict, confidence="high", reason="t")


def main():
    base = ROOT / "staging" / "_test_forced"
    shutil.rmtree(base, ignore_errors=True)
    lib = base / "lib"
    acid = lib / "ACID"
    cache = base / "cache"
    acid.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    # une track HQ deja RANGEE dans la lib (lib/ACID) -> normalement 'already good' + 'already in library'
    src = acid / "Artist X - Tune.mp3"
    _mk(src)
    src_qr = _qr(src, quality.HQ, 19000.0, ".mp3", 320, "lossy")

    # --- monkeypatch : pas de reseau / process / vraie corbeille ---
    soulseek.stop_slskd = lambda: False
    soulseek.stop_orphan_sldl = lambda: False
    soulseek.read_soulseek_creds = lambda: {"user": "t", "pass": "t"}
    soulseek.run_sldl = lambda *a, **k: 0
    dl_flac = cache / "Artist X - Tune.flac"
    _mk(dl_flac)
    soulseek.read_index = lambda _i: [DownloadResult("Artist X", "Tune", str(dl_flac), 300, "1", "0")]
    up.quality.analyze_file = lambda p: _qr(p, quality.LOSSLESS, 22050.0, ".flac", 1000, "lossless_container")
    trashed = []
    up.trash.send_to_trash = lambda p: trashed.append(str(p))

    # 1) SANS forced : HQ accepte -> already_good, rien telecharge, original intact
    out_auto = up.run_upgrade(str(acid), root=ROOT, staging_dir=cache, download_dir=lib,
                              scan_results=[src_qr], preset="dj_club", fallback_profile=None, forced=False)
    assert any(o.action == up.ACT_ALREADY_GOOD for o in out_auto), "sans forced : HQ -> already_good"
    assert not any(o.action == up.ACT_REPLACED for o in out_auto), "sans forced : rien remplace"
    assert not trashed, "sans forced : on ne touche pas l'original"

    # 2) AVEC forced : bypass is_accepted ET dedup -> telecharge, re-audit LOSSLESS, REMPLACE IN-PLACE
    trashed.clear()
    _mk(dl_flac)   # re-cree le download (la passe precedente a pu le deplacer)
    out_f = up.run_upgrade(str(acid), root=ROOT, staging_dir=cache, download_dir=lib,
                           scan_results=[src_qr], preset="dj_club", fallback_profile=None, forced=True)
    assert any(o.action == up.ACT_REPLACED for o in out_f), "forced : doit chercher+remplacer malgre HQ+in-library"
    assert (acid / "Artist X - Tune.flac").exists(), "upgrade in-place : le FLAC reste dans ACID"
    assert not (lib / "Artist X - Tune.flac").exists(), "ne doit PAS atterrir a la racine de la lib"
    assert str(src) in trashed, "l'original HQ part a la corbeille"
    print("OK forced : clic manuel bypass is_accepted + dedup ; upgrade depose IN-PLACE dans le sous-dossier")


if __name__ == "__main__":
    main()
