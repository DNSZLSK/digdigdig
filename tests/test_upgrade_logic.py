"""Logique d'upgrade/acquire sans reseau (modele bibliotheque downloads/ + corbeille).

On simule sldl (run_sldl/read_index), le re-audit spectral (analyze_file) et la corbeille
(trash.send_to_trash mocke). Valide :
  - run_upgrade depose le vrai lossless dans downloads/ et envoie le FAUX source a la corbeille,
  - les upscales (download non-authentique) sont rejetes (candidat -> corbeille),
  - acquire_rows depose les AUTHENTIC dans downloads/ (cle match_key), dedoublonne,
  - les gardes _reject_reason (trop court / mauvais match / vrai renomme / collab),
  - import_folder garde les AUTHENTIC, envoie le reste a la corbeille.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import quality, soulseek, upgrade as up
from ddd.core.quality import QualityResult
from ddd.core.scan import ScanRecord
from ddd.core.naming import match_key
from ddd.core.soulseek import WantItem, DownloadResult


def _qr(path, verdict, cutoff=16000.0, fclass="lossless_container", duration=300.0):
    return QualityResult(
        path=path, filename=Path(path).name, ext=Path(path).suffix.lower(),
        format_class=fclass, sample_rate=44100, channels=2, duration_s=duration,
        cutoff_hz=cutoff, cutoff_std_hz=0.0, hf_energy_ratio=0.0,
        est_source_bitrate=160, container_bitrate=1411,
        verdict=verdict, confidence="high", reason="test",
    )


def _mk(p: Path) -> Path:
    p.write_bytes(b"x")
    return p


def main():
    base = ROOT / "staging" / "_test_upgrade"
    cache = base / "_cache"
    lib = base / "_lib"
    for d in (cache, lib):
        d.mkdir(parents=True, exist_ok=True)

    # --- monkeypatch : pas de reseau, pas de process tue, pas de vraie corbeille ---
    soulseek.stop_slskd = lambda: False
    soulseek.stop_orphan_sldl = lambda: False          # NE PAS taskkill sldl pendant les tests
    soulseek.read_soulseek_creds = lambda: {"user": "t", "pass": "t"}
    soulseek.run_sldl = lambda *a, **k: 0

    def fake_index(_idx):
        # sldl "ramene" Good (authentique) et Upscale (faux), pas Artist C
        return [
            DownloadResult("Artist A", "Good", str(cache / "Artist A - Good.flac"), 300, "1", "0"),
            DownloadResult("Artist B", "Upscale", str(cache / "Artist B - Upscale.flac"), 300, "1", "0"),
        ]
    soulseek.read_index = fake_index

    real_analyze = quality.analyze_file
    def fake_analyze(p):
        p = str(p)
        if "Good" in p:
            return _qr(p, quality.AUTHENTIC, cutoff=22050.0)
        if "Upscale" in p:
            return _qr(p, quality.FAKE, cutoff=16000.0)
        return real_analyze(p)
    up.quality.analyze_file = fake_analyze

    trashed = []
    up.trash.send_to_trash = lambda p: (trashed.append(str(p)), True)[1]

    scan = [
        _qr(r"C:\lib\Artist A - Good.wav", quality.FAKE),       # -> REPLACED (depose + source corbeille)
        _qr(r"C:\lib\Artist B - Upscale.wav", quality.FAKE),    # -> REJECTED_FAKE (candidat corbeille)
        _qr(r"C:\lib\Artist C - Rare.wav", quality.FAKE),       # -> NOT_FOUND
        _qr(r"C:\lib\groove 2 me.wav", quality.FAKE),           # sans ' - ' -> recherche titre-seul
        _qr(r"C:\lib\Artist D - Real.flac", quality.AUTHENTIC), # hors want-list
    ]

    # === run_upgrade : depot bibliotheque + corbeille ===
    _mk(cache / "Artist A - Good.flac")
    _mk(cache / "Artist B - Upscale.flac")
    outcomes = up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib,
                              scan_results=scan)
    by = {o.action: o for o in outcomes}
    assert by[up.ACT_REPLACED].artist == "Artist A"
    assert (lib / "Artist A - Good.flac").exists(), "le vrai lossless doit etre depose en bibliotheque"
    assert r"C:\lib\Artist A - Good.wav" in trashed, "le faux SOURCE doit partir a la corbeille"
    assert by[up.ACT_REJECTED_FAKE].artist == "Artist B"
    assert str(cache / "Artist B - Upscale.flac") in trashed, "le candidat upscale -> corbeille"
    assert up.ACT_NOT_FOUND in by, "Artist C introuvable"
    # 'groove 2 me.wav' (sans separateur) doit etre CHERCHE en titre-seul, pas skippe
    assert any(o.original == r"C:\lib\groove 2 me.wav" for o in outcomes), \
        "un fichier sans ' - ' doit etre cherche (titre-seul), pas ignore"
    assert all(o.action != up.ACT_UNPARSEABLE for o in outcomes), "plus de skip sur nom sans separateur"
    assert all(o.original != r"C:\lib\Artist D - Real.flac" for o in outcomes), "le deja-AUTHENTIC hors want-list"
    print("OK run_upgrade : vrai lossless depose, faux source + upscale a la corbeille, fallback titre-seul")

    # === dedup : un fichier deja dans la bibliotheque -> DUPLICATE, source intacte ===
    trashed.clear()
    _mk(lib / "Artist C - Rare.flac")     # on a deja Artist C dans la bibliotheque
    out2 = up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib,
                          scan_results=[_qr(r"C:\lib\Artist C - Rare.wav", quality.FAKE)])
    assert any(o.action == up.ACT_DUPLICATE for o in out2), "deja en bibliotheque -> DUPLICATE"
    assert r"C:\lib\Artist C - Rare.wav" not in trashed, "un doublon ne doit PAS supprimer le source a l'aveugle"
    print("OK run_upgrade : dedup bibliotheque (DUPLICATE) sans toucher au source")

    # === chemin GUI : run_upgrade accepte des ScanRecord (non-regression) ===
    lib2 = base / "_lib2"
    lib2.mkdir(exist_ok=True)
    _mk(cache / "Artist A - Good.flac")
    _mk(cache / "Artist B - Upscale.flac")
    recs = [ScanRecord(quality=q, naming=None, size_bytes=0, dup_count=1) for q in scan]
    gui_out = up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib2,
                             scan_results=recs)
    gui_actions = {o.action for o in gui_out}
    assert up.ACT_REPLACED in gui_actions and up.ACT_REJECTED_FAKE in gui_actions
    print("OK run_upgrade : chemin GUI (ScanRecord) sans crash")

    # === acquire_rows : depose en bibliotheque, cle match_key, dedup liste ===
    lib3 = base / "_lib3"
    lib3.mkdir(exist_ok=True)
    _mk(cache / "Artist A - Good.flac")
    _mk(cache / "Artist B - Upscale.flac")
    acq_events = []
    acq_rows = [
        {"Artist": "Artist A", "Title": "Good"},      # -> ACQUIRED (depose)
        {"Artist": "artist a", "Title": "GOOD"},       # meme match_key -> DUPLICATE (liste)
        {"Artist": "Artist B", "Title": "Upscale"},   # -> REJECTED_FAKE
        {"Artist": "Artist C", "Title": "Rare"},      # -> NOT_FOUND
    ]
    acq_out = up.acquire_rows(acq_rows, root=ROOT, download_dir=lib3, staging_dir=cache,
                              on_item=lambda k, ph, d="": acq_events.append((k, ph, d)))
    acq_by = [o.action for o in acq_out]
    assert up.ACT_ACQUIRED in acq_by and (lib3 / "Artist A - Good.flac").exists()
    assert acq_by.count(up.ACT_DUPLICATE) == 1, f"un doublon de liste attendu : {acq_by}"
    assert up.ACT_REJECTED_FAKE in acq_by and up.ACT_NOT_FOUND in acq_by
    done = {k: d for (k, ph, d) in acq_events if ph == "done"}
    assert done[match_key("Artist A", "Good")] == up.ACT_ACQUIRED
    assert done[match_key("Artist C", "Rare")] == up.ACT_NOT_FOUND
    print("OK acquire_rows : depot bibliotheque + on_item keye match_key + dedup liste")

    # === _reject_reason : trop court / mauvais match / vrai renomme / collab ===
    itm = WantItem("Daft Punk", "Around the World", None, "")

    def _dl(name):
        return DownloadResult("x", "y", str(cache / f"{name}.flac"), 300, "1", "0")

    q_ok = _qr(str(cache / "x.flac"), quality.AUTHENTIC, cutoff=22050.0)
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_ok) is None
    q_short = _qr(str(cache / "x.flac"), quality.AUTHENTIC, cutoff=22050.0, duration=61.0)
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_short)[0] == up.ACT_TOO_SHORT
    assert up._reject_reason(itm, _dl("Adventureland Bazaar - Aladdins Other Lamp"), q_ok)[0] == up.ACT_WRONG_MATCH
    kraml = WantItem("Andre Kraml Feat Schad Privat & Schad Privat", "Safari (Original Mix)", None, "")
    assert up._reject_reason(kraml, _dl("Andre Kraml - Safari"), q_ok) is None, "vrai renomme -> garde"
    assert up._reject_reason(kraml, _dl("Andre Kraml - Different Song"), q_ok)[0] == up.ACT_WRONG_MATCH
    collab = WantItem("Daft Punk vs Stardust", "Music Sounds Better", None, "")
    assert up._reject_reason(collab, _dl("Stardust - Music Sounds Better With You"), q_ok) is None, "collab -> garde"
    print("OK _reject_reason : court / mauvais match / renomme / collab")

    # === normalize_artist_title : VA / prefixe vinyle / dedup / idempotent ===
    from ddd.core.naming import normalize_artist_title as _norm
    assert _norm("Various Artists", "Zumo - Iamthecomputer") == ("Zumo", "Iamthecomputer")
    assert _norm("ildec", "A1 ildec - Voice From Nowhere") == ("ildec", "Voice From Nowhere")
    assert _norm("VA", "B2 Maua - Sirens") == ("Maua", "Sirens")
    assert _norm("Daft Punk", "Around the World") == ("Daft Punk", "Around the World")
    _r = _norm("Various", "X - Y - Z")
    assert _norm(*_r) == _r, "normalize doit etre idempotent"
    print("OK normalize_artist_title : VA / prefixe / dedup / idempotent")

    # === import_folder : AUTHENTIC garde, reste corbeille ===
    src_imp = base / "_src"
    lib4 = base / "_lib4"
    for d in (src_imp, lib4):
        d.mkdir(exist_ok=True)
    real_a = _mk(src_imp / "Foo - RealTrack.flac")
    fake_b = _mk(src_imp / "Bar - FakeTrack.wav")
    recs_imp = [
        ScanRecord(quality=_qr(str(real_a), quality.AUTHENTIC), naming=None, size_bytes=0, dup_count=1),
        ScanRecord(quality=_qr(str(fake_b), quality.FAKE), naming=None, size_bytes=0, dup_count=1),
    ]
    up.scan_library = lambda src, **k: recs_imp     # mock le scan reel
    trashed.clear()
    stats = up.import_folder(src_imp, lib4)
    assert stats["kept"] == 1 and stats["trashed"] == 1, stats
    assert (lib4 / "Foo - RealTrack.flac").exists(), "AUTHENTIC deplace en bibliotheque"
    assert str(fake_b) in trashed, "non-lossless -> corbeille"
    print("OK import_folder : AUTHENTIC garde, reste corbeille")

    # === build_plan : nom sans separateur -> requete construite depuis les TAGS ===
    up._read_tags = lambda p: ({"artist": "Gary Beck", "title": "Get Down", "album": ""}
                               if "gary" in str(p).lower() else {"artist": "", "title": "", "album": ""})
    plan = up.build_plan([_qr(r"C:\lib\gary-beck-get-down.mp3", quality.FAKE)])
    assert plan.items and plan.items[0].artist == "Gary Beck" and plan.items[0].title == "Get Down", \
        f"nom sans ' - ' doit utiliser les tags : {plan.items}"
    # sans tag exploitable -> titre-seul depuis le nom (fallback)
    plan2 = up.build_plan([_qr(r"C:\lib\Mysterious Title.wav", quality.FAKE)])
    assert plan2.items and plan2.items[0].artist == "" and plan2.items[0].title == "Mysterious Title", \
        f"sans tag -> titre-seul depuis le nom : {plan2.items}"
    print("OK build_plan : tags d'abord, puis titre-seul en fallback")

    # --- cleanup ---
    import shutil
    shutil.rmtree(base, ignore_errors=True)
    print("\nOK - toutes les assertions passent")


if __name__ == "__main__":
    main()
