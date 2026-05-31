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

    events = []  # (origin_path, phase, detail) emis pour le statut par ligne de la GUI
    outcomes = up.run_upgrade(
        "C:\\lib", root=ROOT, staging_dir=tmp,
        scan_results=scan, apply=False,
        on_item=lambda p, ph, d="": events.append((p, ph, d)),
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

    # Statut par ligne (on_item) : chaque fichier de la want-list passe par "searching",
    # les telecharges par "auditing", et tous finissent par un "done" avec la bonne action.
    done = {path: detail for (path, phase, detail) in events if phase == "done"}
    searching = {path for (path, phase, _d) in events if phase == "searching"}
    auditing = {path for (path, phase, _d) in events if phase == "auditing"}
    assert searching == {r"C:\lib\Artist A - Good.wav", r"C:\lib\Artist B - Upscale.wav",
                         r"C:\lib\Artist C - Rare.wav"}, f"searching inattendu : {searching}"
    assert auditing == {r"C:\lib\Artist A - Good.wav", r"C:\lib\Artist B - Upscale.wav"}, \
        f"auditing inattendu : {auditing}"
    assert done[r"C:\lib\Artist A - Good.wav"] == up.ACT_WOULD_REPLACE
    assert done[r"C:\lib\Artist B - Upscale.wav"] == up.ACT_REJECTED_FAKE
    assert done[r"C:\lib\Artist C - Rare.wav"] == up.ACT_NOT_FOUND
    assert done[r"C:\lib\NoArtist.wav"] == up.ACT_UNPARSEABLE  # statut final meme sans download
    print("OK - on_item emet searching/auditing/done par ligne")

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

    # --- acquire_rows : meme feedback par piste, cle = match_key(artist, titre) ---
    from ddd.core.naming import match_key
    inbox = tmp / "_inbox"   # inbox VIDE (sinon le dedup verrait good/bad deja dans tmp)
    inbox.mkdir(exist_ok=True)
    acq_events = []
    acq_rows = [
        {"Artist": "Artist A", "Title": "Good"},      # -> ACQUIRED
        {"Artist": "Artist B", "Title": "Upscale"},   # -> REJECTED_FAKE
        {"Artist": "Artist C", "Title": "Rare"},      # -> NOT_FOUND
    ]
    acq_out = up.acquire_rows(
        acq_rows, root=ROOT, inbox_dir=inbox,
        on_item=lambda k, ph, d="": acq_events.append((k, ph, d)),
    )
    acq_by_action = {o.action: o for o in acq_out}
    assert up.ACT_ACQUIRED in acq_by_action, "Artist A devrait etre ACQUIRED"
    assert up.ACT_REJECTED_FAKE in acq_by_action, "Artist B devrait etre REJECTED_FAKE"
    assert up.ACT_NOT_FOUND in acq_by_action, "Artist C devrait etre NOT_FOUND"
    acq_done = {k: d for (k, ph, d) in acq_events if ph == "done"}
    # les cles emises DOIVENT etre match_key (sinon la GUI ne matche jamais ses lignes)
    assert acq_done[match_key("Artist A", "Good")] == up.ACT_ACQUIRED
    assert acq_done[match_key("Artist B", "Upscale")] == up.ACT_REJECTED_FAKE
    assert acq_done[match_key("Artist C", "Rare")] == up.ACT_NOT_FOUND
    acq_searching = {k for (k, ph, _d) in acq_events if ph == "searching"}
    assert acq_searching == {match_key("Artist A", "Good"), match_key("Artist B", "Upscale"),
                             match_key("Artist C", "Rare")}
    print("OK - acquire_rows emet on_item par piste, keye par match_key")

    # --- gardes post-download : trop court / mauvais match (reutilise tokenize) ---
    from ddd.core.soulseek import WantItem, DownloadResult
    itm = WantItem("Daft Punk", "Around the World", None, "")

    def _dl(name):  # DownloadResult dont seul le nom de fichier compte ici
        return DownloadResult("x", "y", str(tmp / f"{name}.flac"), 300, "1", "0")

    q_ok = _qr(str(tmp / "x.flac"), quality.AUTHENTIC, cutoff=22050.0)  # duration_s=300
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_ok) is None, \
        "bon match, long, authentique -> garde"
    q_short = _qr(str(tmp / "x.flac"), quality.AUTHENTIC, cutoff=22050.0)
    q_short.duration_s = 61
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_short)[0] == up.ACT_TOO_SHORT, \
        "61s -> trop court"
    assert up._reject_reason(itm, _dl("Adventureland Bazaar - Aladdins Other Lamp"), q_ok)[0] \
        == up.ACT_WRONG_MATCH, "nom sans rapport -> mauvais match"
    # vrai resultat juste renomme : requete verbeuse (feat + artistes secondaires +
    # (Original Mix)) vs fichier partage simple -> doit etre GARDE grace au noyau normalise
    kraml = WantItem("Andre Kraml Feat Schad Privat & Schad Privat", "Safari (Original Mix)",
                     None, "")
    assert up._reject_reason(kraml, _dl("Andre Kraml - Safari"), q_ok) is None, \
        "vrai track renomme (noyau artiste+titre couvert) -> garde"
    # meme artiste, autre titre -> doit etre rejete (le titre porte la decision)
    assert up._reject_reason(kraml, _dl("Andre Kraml - Different Song"), q_ok)[0] \
        == up.ACT_WRONG_MATCH, "meme artiste mais autre titre -> mauvais match"
    # collab nommee par l'AUTRE membre : un seul artiste present suffit
    collab = WantItem("Daft Punk vs Stardust", "Music Sounds Better", None, "")
    assert up._reject_reason(collab, _dl("Stardust - Music Sounds Better With You"), q_ok) is None, \
        "collab nommee par l'autre artiste -> garde (titre couvert + 1 artiste present)"
    print("OK - _reject_reason : trop court + mauvais match + vrai renomme + collab")

    # --- acquire dedup : doublon dans la liste saute (par match_key) ---
    dup_events = []
    dup_rows = [
        {"Artist": "Artist A", "Title": "Good"},   # 1er -> traite
        {"Artist": "artist a", "Title": "GOOD"},   # meme match_key -> DUPLICATE saute
        {"Artist": "Artist C", "Title": "Rare"},   # -> NOT_FOUND
    ]
    dup_out = up.acquire_rows(
        dup_rows, root=ROOT, inbox_dir=inbox,
        on_item=lambda k, ph, d="": dup_events.append((k, ph, d)),
    )
    dup_actions = [o.action for o in dup_out]
    assert dup_actions.count(up.ACT_DUPLICATE) == 1, f"un seul doublon attendu : {dup_actions}"
    # le doublon ne doit PAS avoir ete telecharge (pas de searching pour la 2e occurrence)
    assert sum(1 for (_k, ph, _d) in dup_events if ph == "searching") == 2, \
        "seuls les 2 items uniques doivent passer en recherche"
    print("OK - acquire dedup : doublon de la liste saute, pas re-telecharge")

    # --- double-negation 'Garder les originaux' : sens de delete_old explicitement teste ---
    # GUI : keep_switch ON  -> delete_old=False -> l'original RESTE
    #       keep_switch OFF -> delete_old=True  -> l'original est SUPPRIME
    # Le download arrive depuis le staging (autre dossier), pose a cote de l'original.
    dl_dir = tmp / "_dl"
    dl_dir.mkdir(exist_ok=True)
    orig_keep = tmp / "keep_me.wav"
    new_keep = dl_dir / "Keep Artist - Keep.flac"
    orig_keep.write_bytes(b"old"); new_keep.write_bytes(b"new")
    up._replace_in_place(str(orig_keep), str(new_keep), apply=True, delete_old=False)
    assert orig_keep.exists(), "keep ON (delete_old=False) : l'original doit rester"
    assert (tmp / "Keep Artist - Keep.flac").exists(), "le nouveau lossless doit etre pose a cote"

    orig_del = tmp / "delete_me.wav"
    new_del = dl_dir / "Del Artist - Del.flac"
    orig_del.write_bytes(b"old"); new_del.write_bytes(b"new")
    up._replace_in_place(str(orig_del), str(new_del), apply=True, delete_old=True)
    assert not orig_del.exists(), "keep OFF (delete_old=True) : l'original doit etre supprime"
    print("OK - sens de delete_old verifie (keep ON garde, keep OFF supprime)")

    # cleanup
    good.unlink(); bad.unlink()
    for f in (orig_keep, new_keep, orig_del, new_del,
              tmp / "Keep Artist - Keep.flac", tmp / "Del Artist - Del.flac"):
        try:
            f.unlink()
        except OSError:
            pass
    for d in (dl_dir, inbox):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        tmp.rmdir()
    except OSError:
        pass

    print("\nOK - toutes les assertions passent")


if __name__ == "__main__":
    main()
