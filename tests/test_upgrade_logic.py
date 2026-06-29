"""Logique d'upgrade/acquire sans reseau (modele bibliotheque downloads/ + corbeille).

On simule sldl (run_sldl/read_index), le re-audit spectral (analyze_file) et la corbeille
(trash.send_to_trash mocke). Valide :
  - run_upgrade depose ce qui passe le seuil dans downloads/ et envoie le source sous le seuil a la corbeille,
  - les upscales (download sous le seuil) sont rejetes (candidat -> corbeille),
  - acquire_rows depose ce qui passe le seuil dans downloads/ (cle match_key), dedoublonne,
  - les gardes _reject_reason (trop court / mauvais match / vrai renomme / collab),
  - import_folder garde ce qui passe le seuil, envoie le reste a la corbeille,
  - is_accepted : meme fichier, deux presets, deux verdicts d'acceptation,
  - le ban universel MP3 < 320 (jamais accepte, meme en DJ Club),
  - le repli MP3 320 (2e passe) recupere un introuvable en lossless.
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
            return _qr(p, quality.LOSSLESS, cutoff=22050.0)
        if "Upscale" in p:
            return _qr(p, quality.DOUTEUX, cutoff=16000.0)
        return real_analyze(p)
    up.quality.analyze_file = fake_analyze

    trashed = []
    up.trash.send_to_trash = lambda p: (trashed.append(str(p)), True)[1]

    scan = [
        _qr(r"C:\lib\Artist A - Good.wav", quality.DOUTEUX),    # -> REPLACED (depose + source corbeille)
        _qr(r"C:\lib\Artist B - Upscale.wav", quality.DOUTEUX), # -> REJECTED_FAKE (candidat corbeille)
        _qr(r"C:\lib\Artist C - Rare.wav", quality.DOUTEUX),    # -> NOT_FOUND
        _qr(r"C:\lib\groove 2 me.wav", quality.DOUTEUX),        # sans ' - ' -> recherche titre-seul
        _qr(r"C:\lib\Artist D - Real.flac", quality.LOSSLESS, cutoff=22050.0),  # accepte -> hors want-list
    ]

    # === run_upgrade : depot bibliotheque + corbeille (preset dj_club, pas de repli MP3) ===
    _mk(cache / "Artist A - Good.flac")
    _mk(cache / "Artist B - Upscale.flac")
    outcomes = up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib,
                              scan_results=scan, preset="dj_club", fallback_profile=None)
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
    assert all(o.original != r"C:\lib\Artist D - Real.flac" for o in outcomes), "le deja-accepte (LOSSLESS) hors want-list"
    print("OK run_upgrade : accepte depose, source sous seuil + upscale a la corbeille, fallback titre-seul")

    # === dedup : un fichier deja dans la bibliotheque -> DUPLICATE, source intacte ===
    trashed.clear()
    _mk(lib / "Artist C - Rare.flac")     # on a deja Artist C dans la bibliotheque
    out2 = up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib,
                          scan_results=[_qr(r"C:\lib\Artist C - Rare.wav", quality.DOUTEUX)],
                          preset="dj_club", fallback_profile=None)
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
                             scan_results=recs, preset="dj_club", fallback_profile=None)
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
                              preset="dj_club", fallback_profile=None,
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

    q_ok = _qr(str(cache / "x.flac"), quality.LOSSLESS, cutoff=22050.0)
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_ok) is None
    q_short = _qr(str(cache / "x.flac"), quality.LOSSLESS, cutoff=22050.0, duration=61.0)
    assert up._reject_reason(itm, _dl("Daft Punk - Around the World"), q_short)[0] == up.ACT_TOO_SHORT
    assert up._reject_reason(itm, _dl("Adventureland Bazaar - Aladdins Other Lamp"), q_ok)[0] == up.ACT_WRONG_MATCH
    kraml = WantItem("Andre Kraml Feat Schad Privat & Schad Privat", "Safari (Original Mix)", None, "")
    assert up._reject_reason(kraml, _dl("Andre Kraml - Safari"), q_ok) is None, "vrai renomme -> garde"
    assert up._reject_reason(kraml, _dl("Andre Kraml - Different Song"), q_ok)[0] == up.ACT_WRONG_MATCH
    collab = WantItem("Daft Punk vs Stardust", "Music Sounds Better", None, "")
    assert up._reject_reason(collab, _dl("Stardust - Music Sounds Better With You"), q_ok) is None, "collab -> garde"
    print("OK _reject_reason : court / mauvais match / renomme / collab")

    # === _reject_reason durci : artiste vs CHAMP du candidat, titres courts, version ===
    from ddd.core.tokenize import loose_tokens
    assert loose_tokens("2 ME") == ["2", "me"], "loose garde chiffres + mots courts"
    # data-loss reel : 'DJ Schema - 2 ME' ne doit PAS etre remplace par 'Theo Meier - Schema (Remix)'
    # ('schema' est dans le TITRE du candidat, pas son artiste -> ne doit plus valider)
    schema = WantItem("DJ Schema", "2 ME", None, "")
    assert up._reject_reason(schema, _dl("Theo Meier - Schema (T.M.A Remix)"), q_ok)[0] == up.ACT_WRONG_MATCH, \
        "un mot du titre du candidat ne doit plus valider l'artiste demande"
    # mais le BON fichier (titre court identique, bon artiste) passe via le repli loose
    assert up._reject_reason(schema, _dl("DJ Schema - 2 ME"), q_ok) is None, \
        "le vrai fichier a titre court doit passer (repli loose)"
    # original demande, remix recu -> rejet sur la version
    bedouin = WantItem("Bedouin", "Safari", None, "")
    assert up._reject_reason(bedouin, _dl("Bedouin - Safari (Pete Tong Remix)"), q_ok)[0] == up.ACT_WRONG_MATCH, \
        "original demande != remix recu"
    # meme titre, autre artiste (reprise) -> rejet sur l'artiste
    closer = WantItem("Gerd Janson", "Closer", None, "")
    assert up._reject_reason(closer, _dl("Aphex Twin - Closer"), q_ok)[0] == up.ACT_WRONG_MATCH, \
        "meme titre par un autre artiste (reprise) rejete"
    print("OK _reject_reason durci : bug Schema, titre court, version, reprise")

    # === normalize_artist_title : VA / prefixe vinyle / dedup / idempotent ===
    from ddd.core.naming import normalize_artist_title as _norm
    assert _norm("Various Artists", "Zumo - Iamthecomputer") == ("Zumo", "Iamthecomputer")
    assert _norm("ildec", "A1 ildec - Voice From Nowhere") == ("ildec", "Voice From Nowhere")
    assert _norm("VA", "B2 Maua - Sirens") == ("Maua", "Sirens")
    assert _norm("Daft Punk", "Around the World") == ("Daft Punk", "Around the World")
    _r = _norm("Various", "X - Y - Z")
    assert _norm(*_r) == _r, "normalize doit etre idempotent"
    print("OK normalize_artist_title : VA / prefixe / dedup / idempotent")

    # === import_folder : au-dessus du seuil garde, reste corbeille ===
    src_imp = base / "_src"
    lib4 = base / "_lib4"
    for d in (src_imp, lib4):
        d.mkdir(exist_ok=True)
    real_a = _mk(src_imp / "Foo - RealTrack.flac")
    fake_b = _mk(src_imp / "Bar - FakeTrack.wav")
    recs_imp = [
        ScanRecord(quality=_qr(str(real_a), quality.LOSSLESS, cutoff=22050.0), naming=None, size_bytes=0, dup_count=1),
        ScanRecord(quality=_qr(str(fake_b), quality.DOUTEUX), naming=None, size_bytes=0, dup_count=1),
    ]
    up.scan_library = lambda src, **k: recs_imp     # mock le scan reel
    trashed.clear()
    stats = up.import_folder(src_imp, lib4, preset="dj_club")
    assert stats["kept"] == 1 and stats["trashed"] == 1, stats
    assert (lib4 / "Foo - RealTrack.flac").exists(), "accepte deplace en bibliotheque"
    assert str(fake_b) in trashed, "sous le seuil -> corbeille"
    print("OK import_folder : accepte garde, reste corbeille")

    # === build_plan : nom sans separateur -> requete construite depuis les TAGS ===
    up.read_tags = lambda p: ({"artist": "Gary Beck", "title": "Get Down", "album": ""}
                              if "gary" in str(p).lower() else {"artist": "", "title": "", "album": ""})
    plan = up.build_plan([_qr(r"C:\lib\gary-beck-get-down.mp3", quality.DOUTEUX)], preset="dj_club")
    assert plan.items and plan.items[0].artist == "Gary Beck" and plan.items[0].title == "Get Down", \
        f"nom sans ' - ' doit utiliser les tags : {plan.items}"
    # sans tag exploitable -> titre-seul depuis le nom (fallback)
    plan2 = up.build_plan([_qr(r"C:\lib\Mysterious Title.wav", quality.DOUTEUX)], preset="dj_club")
    assert plan2.items and plan2.items[0].artist == "" and plan2.items[0].title == "Mysterious Title", \
        f"sans tag -> titre-seul depuis le nom : {plan2.items}"
    print("OK build_plan : tags d'abord, puis titre-seul en fallback")

    # === is_accepted : meme fichier HQ, deux presets, deux resultats ===
    hq = _qr(r"C:\lib\track.flac", quality.HQ, cutoff=19000.0)
    hq.est_source_bitrate = 320
    assert quality.is_accepted(hq, "dj_club"), "HQ 19 kHz accepte en DJ Club"
    assert not quality.is_accepted(hq, "audiophile"), "HQ 19 kHz refuse en Audiophile (<20 kHz)"
    assert not quality.is_accepted(hq, "puriste"), "HQ refuse en Puriste (pas plein spectre)"
    loss = _qr(r"C:\lib\real.flac", quality.LOSSLESS, cutoff=22050.0)
    loss.est_source_bitrate = 0
    assert all(quality.is_accepted(loss, p) for p in ("dj_club", "audiophile", "puriste")), \
        "LOSSLESS accepte par tous les presets"
    bad = _qr(r"C:\lib\bad.wav", quality.MAUVAIS, cutoff=14000.0)
    assert not any(quality.is_accepted(bad, p) for p in ("dj_club", "audiophile", "puriste")), \
        "MAUVAIS refuse par tous les presets"
    # build_plan : le MEME HQ est skippe en dj_club (accepte) mais candidat en puriste
    assert not up.build_plan([hq], preset="dj_club").items, "HQ accepte en DJ Club -> pas de candidat"
    assert up.build_plan([hq], preset="puriste").items, "HQ candidat en Puriste"
    print("OK is_accepted : HQ accepte DJ Club / refuse Audiophile+Puriste ; build_plan suit le preset")

    # === ban universel MP3 < 320 (jamais accepte, meme en DJ Club) ===
    mp3_192 = _qr(r"C:\lib\lo.mp3", quality.MAUVAIS, cutoff=19000.0, fclass="lossy")
    mp3_192.container_bitrate = 192
    assert not any(quality.is_accepted(mp3_192, p) for p in ("dj_club", "audiophile", "puriste")), \
        "MP3 192 kbps banni partout, meme a cutoff 19 kHz"
    mp3_320 = _qr(r"C:\lib\hi.mp3", quality.HQ, cutoff=20000.0, fclass="lossy")
    mp3_320.container_bitrate = 320
    assert quality.is_accepted(mp3_320, "dj_club"), "MP3 320 (cutoff 20 kHz) accepte en DJ Club"
    print("OK ban MP3 < 320 : 192 refuse partout, 320 accepte en DJ Club")

    # === repli MP3 320 (2e passe) : introuvable en lossless -> recupere en mp3-fallback ===
    lib5 = base / "_lib5"
    lib5.mkdir(exist_ok=True)
    _mk(cache / "Fallback Artist - Tune.mp3")

    def fb_index(_idx):
        # rien en 1ere passe (lossless-strict), un MP3 320 en 2e passe (mp3-fallback)
        if fb_state["profile"] == "mp3-fallback":
            return [DownloadResult("Fallback Artist", "Tune",
                                   str(cache / "Fallback Artist - Tune.mp3"), 300, "1", "0")]
        return []
    fb_state = {"profile": None}
    real_run = soulseek.run_sldl
    def spy_run(*a, **k):
        fb_state["profile"] = k.get("profile")
        return 0
    soulseek.run_sldl = spy_run
    soulseek.read_index = fb_index

    def fb_analyze(p):
        return _qr(str(p), quality.HQ, cutoff=20000.0, fclass="lossy")
    up.quality.analyze_file = fb_analyze

    fb_out = up.acquire_rows([{"Artist": "Fallback Artist", "Title": "Tune"}],
                             root=ROOT, download_dir=lib5, staging_dir=cache, preset="dj_club")
    fb_actions = [o.action for o in fb_out]
    assert up.ACT_ACQUIRED in fb_actions, f"le repli MP3 320 doit recuperer l'introuvable : {fb_actions}"
    assert (lib5 / "Fallback Artist - Tune.mp3").exists(), "le MP3 320 du repli est depose"
    soulseek.run_sldl = real_run
    print("OK repli MP3 320 : 2e passe recupere un introuvable en lossless")

    # === modes "cible format" : le preset choisit le profil de recherche sldl ===
    assert quality.search_profiles_for("dj_club") == ("lossless-strict", "mp3-fallback")
    assert quality.search_profiles_for("puriste") == ("lossless-strict", None), "Purist durci : pas de repli"
    assert quality.search_profiles_for("mp3_320") == ("mp3-only", None)
    assert quality.search_profiles_for("wav_aiff") == ("wav-aiff-only", None)
    assert quality.search_profiles_for("flac_only") == ("flac-only", None)
    assert quality.search_profiles_for("inconnu") == ("lossless-strict", "mp3-fallback"), "inconnu -> defaut"

    # is_accepted : mp3_320 garde le 320 (>= 18 kHz) ; wav_aiff/flac_only ne gardent que le plein
    # spectre (un 320 reste candidat -> re-cherche dans le format cible) ; un vrai lossless est garde
    # par tous (jamais transcode).
    mp3_320_ok = _qr(r"C:\lib\hi.mp3", quality.HQ, cutoff=20000.0, fclass="lossy")
    mp3_320_ok.container_bitrate = 320
    assert quality.is_accepted(mp3_320_ok, "mp3_320"), "MP3 320 garde en mode MP3 320"
    assert not quality.is_accepted(mp3_320_ok, "flac_only"), "un 320 reste candidat en FLAC only"
    assert not quality.is_accepted(mp3_320_ok, "wav_aiff"), "un 320 reste candidat en WAV/AIFF only"
    real_loss = _qr(r"C:\lib\real.flac", quality.LOSSLESS, cutoff=22050.0)
    real_loss.est_source_bitrate = 0
    assert all(quality.is_accepted(real_loss, p) for p in ("mp3_320", "wav_aiff", "flac_only")), \
        "un vrai lossless est garde par tous les modes"

    # spy : run_upgrade derive le profil sldl du preset (mp3_320 -> mp3-only ; flac_only -> flac-only),
    # sans repli (un seul appel sldl).
    seen_profiles = []
    def spy_profile(*a, **k):
        seen_profiles.append(k.get("profile"))
        return 0
    soulseek.run_sldl = spy_profile
    soulseek.read_index = lambda _idx: []     # rien trouve -> NOT_FOUND, pas d'audit
    lib6 = base / "_lib6"
    lib6.mkdir(exist_ok=True)
    up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib6,
                   scan_results=[_qr(r"C:\lib\X - Y.mp3", quality.MAUVAIS, fclass="lossy")],
                   preset="mp3_320")
    assert seen_profiles == ["mp3-only"], f"mp3_320 -> mp3-only sans repli : {seen_profiles}"
    seen_profiles.clear()
    up.run_upgrade("C:\\lib", root=ROOT, staging_dir=cache, download_dir=lib6,
                   scan_results=[_qr(r"C:\lib\X - Z.mp3", quality.MAUVAIS, fclass="lossy")],
                   preset="flac_only")
    assert seen_profiles == ["flac-only"], f"flac_only -> flac-only : {seen_profiles}"
    print("OK modes cible format : search_profiles_for + is_accepted + profil derive du preset")

    # --- cleanup ---
    import shutil
    shutil.rmtree(base, ignore_errors=True)
    print("\nOK - toutes les assertions passent")


if __name__ == "__main__":
    main()
