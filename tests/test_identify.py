"""Tests de l'identification par empreinte (`ddd identify`).

fpcalc et AcoustID sont monkeypatches -> zero binaire, zero reseau. Cache sur tmp_path.
On verifie : parsing/tri des candidats, erreurs AcoustID (auth vs autre), seuil de
confiance, precedence de la cle API, cache (hit sans reseau, miss cache, erreur NON
cachee), et l'orchestration dossier (dry-run ne touche rien ; apply renomme les seuls
MATCH, sanitize, anti-collision).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ddd.core.identify as identify
from ddd.core import naming
from ddd.core.identify import Candidate, MATCH, LOW_CONFIDENCE, NO_MATCH, ERROR


# --- helpers -----------------------------------------------------------------

class _Resp:
    def __init__(self, payload, status_code=200):
        self._p = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:                    # imite requests (4xx/5xx -> HTTPError)
            raise identify.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


def _fake_fp(path, *, length=120, fpcalc=None):
    """Empreinte factice deterministe par nom de fichier (pas de binaire)."""
    return 100, f"fp-{Path(path).stem}"


def _fake_lookup_factory(mapping):
    def _lookup(fp, dur, *, api_key, **k):
        return mapping.get(fp, [])
    return _lookup


def _dummy_fpcalc(tmp_path) -> Path:
    exe = tmp_path / "fpcalc"
    exe.write_text("x")
    return exe


# --- parsing / lookup --------------------------------------------------------

def test_parse_results_skips_titleless_and_joins_artists():
    cands = identify._parse_results([
        {"score": 0.8, "recordings": [
            {"id": "1", "title": "", "artists": [{"name": "Z"}]},          # sans titre -> ignore
            {"id": "2", "title": "Real", "artists": [{"name": "A"}, {"name": "B"}]},
        ]},
    ])
    assert len(cands) == 1
    assert cands[0].title == "Real" and cands[0].artist == "A, B" and cands[0].recording_mbid == "2"


def test_lookup_parses_and_sorts_by_score(monkeypatch):
    payload = {"status": "ok", "results": [
        {"score": 0.5, "recordings": [{"id": "m1", "title": "Low", "artists": [{"name": "A"}]}]},
        {"score": 0.9, "recordings": [{"id": "m2", "title": "High", "artists": [{"name": "B"}]}]},
    ]}
    monkeypatch.setattr(identify.requests, "post", lambda *a, **k: _Resp(payload))
    cands = identify.lookup("fp", 100, api_key="k")
    assert [c.title for c in cands] == ["High", "Low"]      # tri par score desc
    assert cands[0].recording_mbid == "m2"


def test_lookup_invalid_key_raises_auth(monkeypatch):
    # AcoustID renvoie le corps JSON d'erreur en HTTP 400 -> lookup doit lire le code 4
    # AVANT que raise_for_status masque tout (sinon mauvaise cle == simple erreur reseau).
    monkeypatch.setattr(identify.requests, "post", lambda *a, **k: _Resp(
        {"status": "error", "error": {"code": 4, "message": "invalid API key"}}, status_code=400))
    with pytest.raises(identify.AcoustidAuthError):
        identify.lookup("fp", 100, api_key="bad")


def test_lookup_other_error_is_not_auth(monkeypatch):
    # empreinte invalide (code 6) aussi en HTTP 400 : erreur par-fichier, PAS fatale.
    monkeypatch.setattr(identify.requests, "post", lambda *a, **k: _Resp(
        {"status": "error", "error": {"code": 6, "message": "invalid fingerprint"}}, status_code=400))
    with pytest.raises(identify.AcoustidError) as ei:
        identify.lookup("fp", 100, api_key="k")
    assert not isinstance(ei.value, identify.AcoustidAuthError)


def test_lookup_non_json_surfaces_http_error(monkeypatch):
    class _Bad:
        status_code = 500

        def raise_for_status(self):
            raise identify.requests.HTTPError("HTTP 500")

        def json(self):
            raise ValueError("no json body")

    monkeypatch.setattr(identify.requests, "post", lambda *a, **k: _Bad())
    with pytest.raises(identify.requests.HTTPError):
        identify.lookup("fp", 100, api_key="k")


# --- seuil / cle -------------------------------------------------------------

def test_classify_threshold():
    assert identify._classify([Candidate(0.9, "A", "T")], 0.7)[0] == MATCH
    assert identify._classify([Candidate(0.3, "A", "T")], 0.7)[0] == LOW_CONFIDENCE
    assert identify._classify([], 0.7) == (NO_MATCH, None)


def test_resolve_api_key_precedence(monkeypatch):
    monkeypatch.delenv("ACOUSTID_API_KEY", raising=False)
    monkeypatch.setattr(identify.config, "get", lambda *a, **k: "cfgkey")
    assert identify.resolve_api_key("explicit") == "explicit"   # explicite gagne
    assert identify.resolve_api_key("") == "cfgkey"             # sinon config
    monkeypatch.setenv("ACOUSTID_API_KEY", "envkey")
    assert identify.resolve_api_key("") == "envkey"             # env passe avant config


# --- cache (fichier) ---------------------------------------------------------

def test_identify_file_cache_no_second_network(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup",
                        _fake_lookup_factory({"fp-x": [Candidate(0.9, "A", "T", "m")]}))
    f = tmp_path / "x.flac"
    f.write_bytes(b"")
    r1 = identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False)
    assert r1.status == MATCH and r1.best.title == "T"

    def boom(*a, **k):
        raise AssertionError("network must NOT be hit on a cache hit")

    monkeypatch.setattr(identify, "lookup", boom)
    r2 = identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False)
    assert r2.status == MATCH and r2.best.title == "T"


def test_identify_file_miss_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup", _fake_lookup_factory({}))    # miss
    f = tmp_path / "y.flac"
    f.write_bytes(b"")
    assert identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False).status == NO_MATCH

    def boom(*a, **k):
        raise AssertionError("a cached MISS must not re-hit the network")

    monkeypatch.setattr(identify, "lookup", boom)
    assert identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False).status == NO_MATCH


def test_identify_file_network_error_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)

    def neterr(*a, **k):
        raise identify.requests.RequestException("down")

    monkeypatch.setattr(identify, "lookup", neterr)
    f = tmp_path / "z.flac"
    f.write_bytes(b"")
    assert identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False).status == ERROR

    # erreur transitoire non cachee -> un re-run qui marche doit retrouver la piste
    monkeypatch.setattr(identify, "lookup",
                        _fake_lookup_factory({"fp-z": [Candidate(0.9, "A", "T")]}))
    assert identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False).status == MATCH


def test_identify_file_auth_error_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)

    def auth(*a, **k):
        raise identify.AcoustidAuthError("bad key")

    monkeypatch.setattr(identify, "lookup", auth)
    f = tmp_path / "a.flac"
    f.write_bytes(b"")
    with pytest.raises(identify.AcoustidAuthError):
        identify.identify_file(f, api_key="k", cache_dir=tmp_path, sleep=False)


def test_identify_file_fpcalc_error_is_error_result(tmp_path, monkeypatch):
    def boom_fp(path, *, length=120, fpcalc=None):
        raise identify.FpcalcError("corrupt file")

    monkeypatch.setattr(identify, "fingerprint_file", boom_fp)
    f = tmp_path / "c.flac"
    f.write_bytes(b"")
    r = identify.identify_file(f, api_key="k", sleep=False)
    assert r.status == ERROR and "corrupt" in r.note


# --- orchestration dossier ---------------------------------------------------

def test_identify_folder_missing_fpcalc_raises(tmp_path, monkeypatch):
    d = tmp_path / "lib"
    d.mkdir()
    with pytest.raises(identify.FpcalcError):
        identify.identify_folder(d, api_key="k", fpcalc=tmp_path / "nope")


def test_identify_folder_dry_run_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup", _fake_lookup_factory({
        "fp-YH1": [Candidate(0.95, "Aphex Twin", "Xtal", "m1")],
        "fp-YH2": [Candidate(0.40, "Maybe", "Guess", "m2")],
    }))
    d = tmp_path / "lib"
    d.mkdir()
    for n in ("YH1", "YH2", "YH3"):
        (d / f"{n}.flac").write_bytes(b"")

    res = identify.identify_folder(d, api_key="k", cache_dir=tmp_path,
                                   fpcalc=_dummy_fpcalc(tmp_path))
    by = {Path(r.path).name: r for r in res}
    assert by["YH1.flac"].status == MATCH
    assert by["YH1.flac"].proposed_name == "Aphex Twin - Xtal.flac"
    assert not by["YH1.flac"].applied
    assert (d / "YH1.flac").exists()                        # dry-run: rien renomme
    assert by["YH2.flac"].status == LOW_CONFIDENCE and by["YH2.flac"].proposed_name == ""
    assert by["YH3.flac"].status == NO_MATCH


def test_identify_folder_apply_renames_matches_only(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup", _fake_lookup_factory({
        "fp-YH1": [Candidate(0.95, "Aphex Twin", "Xtal", "m1")],
        "fp-YH2": [Candidate(0.40, "Maybe", "Guess", "m2")],       # low -> intact
        "fp-YH4": [Candidate(0.90, "AC/DC", "Thunder", "m4")],     # slash -> sanitize
    }))
    d = tmp_path / "lib"
    d.mkdir()
    for n in ("YH1", "YH2", "YH4"):
        (d / f"{n}.flac").write_bytes(b"")

    res = identify.identify_folder(d, api_key="k", cache_dir=tmp_path, apply=True,
                                   outputs_dir=tmp_path, fpcalc=_dummy_fpcalc(tmp_path))
    assert (d / "Aphex Twin - Xtal.flac").exists() and not (d / "YH1.flac").exists()
    assert (d / "YH2.flac").exists()                       # low-confidence: jamais applique
    assert (d / "AC_DC - Thunder.flac").exists()           # '/' assaini en '_'
    by = {Path(r.path).name: r for r in res}
    assert by["YH1.flac"].applied and by["YH1.flac"].new_path.endswith("Aphex Twin - Xtal.flac")
    assert (tmp_path / "identify_lib.csv").exists()         # journal d'annulation ecrit


def test_identify_folder_apply_collision_gets_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup", _fake_lookup_factory({
        "fp-A": [Candidate(0.95, "X", "Y", "m")],
        "fp-B": [Candidate(0.95, "X", "Y", "m")],          # meme titre -> collision
    }))
    d = tmp_path / "lib"
    d.mkdir()
    (d / "A.flac").write_bytes(b"")
    (d / "B.flac").write_bytes(b"")
    identify.identify_folder(d, api_key="k", apply=True, fpcalc=_dummy_fpcalc(tmp_path))
    assert (d / "X - Y.flac").exists()
    assert (d / "X - Y (1).flac").exists()                 # 2e occurrence suffixee


# --- hand-off acquire --------------------------------------------------------

def test_to_want_rows_only_confident_matches():
    res = [
        identify.IdentifyResult(path="a", status=MATCH,
                                best=Candidate(0.9, "Ar", "Ti", duration=200)),
        identify.IdentifyResult(path="b", status=LOW_CONFIDENCE, best=Candidate(0.4, "L", "M")),
        identify.IdentifyResult(path="c", status=NO_MATCH),
    ]
    assert identify.to_want_rows(res) == [
        {"Artist": "Ar", "Title": "Ti", "Length": 200, "Source": "identify"}]


def test_write_tags_graceful_on_non_audio(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"not really a flac")
    assert naming.write_tags(f, artist="A", title="B") is False


# --- apply_selected (confirmation piste-par-piste GUI) -----------------------

def test_apply_selected_renames_and_tags(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "YH1.flac").write_bytes(b"")
    (d / "YH2.flac").write_bytes(b"")
    res = identify.apply_selected([
        (str(d / "YH1.flac"), "Aphex Twin", "Xtal"),
        (str(d / "YH2.flac"), "AC/DC", "Thunder"),       # '/' -> assaini
    ])
    assert (d / "Aphex Twin - Xtal.flac").exists() and not (d / "YH1.flac").exists()
    assert (d / "AC_DC - Thunder.flac").exists()
    assert all(ok for _p, ok, _n in res)


def test_apply_selected_collision_gets_suffix(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "A.flac").write_bytes(b"")
    (d / "B.flac").write_bytes(b"")
    identify.apply_selected([(str(d / "A.flac"), "X", "Y"), (str(d / "B.flac"), "X", "Y")])
    assert (d / "X - Y.flac").exists() and (d / "X - Y (1).flac").exists()


# --- validation / calibration du seuil ---------------------------------------

def test_label_match_three_way():
    # exact -> correct
    assert identify._label_match("Mr. Ho", "Angel Number 909",
                                 Candidate(0.99, "Mr. Ho", "Angel Number 909")) == identify.LBL_CORRECT
    # meme morceau, version differente (remix vs original) -> diff-version
    assert identify._label_match("Aphex Twin", "Xtal (Remix)",
                                 Candidate(0.9, "Aphex Twin", "Xtal")) == identify.LBL_DIFF_VERSION
    # mauvais morceau (acid -> audiobook) -> wrong
    assert identify._label_match("A xus", "When I Fall",
                                 Candidate(0.85, "Rick Riordan", "Teil 11")) == identify.LBL_WRONG
    # pas de candidat -> no-match
    assert identify._label_match("X", "Y", None) == identify.LBL_NO_MATCH


def test_label_match_tolerates_spelling_variant():
    # cas REEL rencontre en validation : le fichier dit "Uforic Undulance", MusicBrainz
    # "Uforic Undulence" (1 lettre) = MEME morceau a 0.977 -> correct, surtout PAS wrong
    # (un labeleur exact fausserait la precision des tranches hautes).
    assert identify._label_match("Eat Static", "Uforic Undulance",
                                 Candidate(0.977, "Eat Static", "Uforic Undulence")) == identify.LBL_CORRECT


def test_summarize_validation_recommends_boundary_above_false_positives():
    # 30 vrais a 0.97, 5 faux a 0.82, 2 no-match. Le seuil le plus BAS qui atteint 99% doit
    # etre 0.85 (juste au-dessus des faux a 0.82), PAS 0.95 -> maximise le recall sans sacrifier
    # la precision. C'est tout l'interet de la calibration sur donnees vs deviner.
    rows = ([identify.ValidateRow(f"c{i}", "A", "T", 0.97, "A", "T", identify.LBL_CORRECT)
             for i in range(30)]
            + [identify.ValidateRow(f"w{i}", "A", "T", 0.82, "B", "Z", identify.LBL_WRONG)
               for i in range(5)]
            + [identify.ValidateRow(f"n{i}", "A", "T", 0.0, "", "", identify.LBL_NO_MATCH)
               for i in range(2)])
    s = identify.summarize_validation(rows, target=0.99, min_n=20)
    assert (s["n_total"], s["n_match"], s["n_no_match"]) == (37, 35, 2)
    top = [b for b in s["bands"] if b["lo"] == 0.95][0]
    assert top["n"] == 30 and top["correct"] == 30 and top["precision"] == 1.0
    assert s["recommended"] == 0.85


def test_validate_folder_labels_against_known_names(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "fingerprint_file", _fake_fp)
    monkeypatch.setattr(identify, "lookup", _fake_lookup_factory({
        "fp-Aphex Twin - Xtal": [Candidate(0.98, "Aphex Twin", "Xtal")],                 # correct
        "fp-Mr Fingers - Can You Feel It": [Candidate(0.9, "Wrong Guy", "Other Song")],  # wrong
    }))
    d = tmp_path / "lib"
    d.mkdir()
    (d / "Aphex Twin - Xtal.flac").write_bytes(b"")
    (d / "Mr Fingers - Can You Feel It.flac").write_bytes(b"")
    (d / "badslug.flac").write_bytes(b"")            # nom non fiable -> hors verite terrain

    rows = identify.validate_folder(d, api_key="k", cache_dir=tmp_path,
                                    fpcalc=_dummy_fpcalc(tmp_path))
    by = {Path(r.path).name: r for r in rows}
    assert "badslug.flac" not in by                  # non-confident -> jamais verite terrain
    assert by["Aphex Twin - Xtal.flac"].label == identify.LBL_CORRECT
    assert by["Mr Fingers - Can You Feel It.flac"].label == identify.LBL_WRONG
