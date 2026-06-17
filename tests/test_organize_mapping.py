"""Tests de la resolution style/genre -> dossier de vibe (PUR, sans reseau)."""

from __future__ import annotations

from ddd.core.organize import map_styles_to_folder as m, DEFAULT_GENRE_MAPPING


def test_exact_and_specific_win():
    assert m(["Acid House"]) == "ACID"
    assert m(["Acid Techno"]) == "ACID"          # "acid techno" > "techno"
    assert m(["Tech House"]) == "HOUSERZ"
    assert m(["Detroit Techno"]) == "TECHNO"
    assert m(["Progressive House"]) == "PROG"     # pas HOUSERZ


def test_longest_keyword_beats_broad_bucket():
    # "deep house" (DEEPWATER, 10) bat "house" (HOUSERZ, 5)
    assert m(["Deep House"]) == "DEEPWATER"
    # "minimal" (DEEPWATER, 7) bat "techno" (TECHNO, 6)
    assert m(["Minimal Techno"]) == "DEEPWATER"
    # "soulful house" (HOUSERZ, 13) bat "soul" (DISCO-FUNK, 4)
    assert m(["Soulful House"]) == "HOUSERZ"


def test_forward_only_broad_signal_stays_broad():
    # Regression : un style brut "House" ne doit PAS etre aspire vers "deep house".
    assert m(["House"]) == "HOUSERZ"
    assert m(["Techno"]) == "TECHNO"
    assert m(["Trance"]) == "TRANCE"
    assert m(["Soul"]) == "DISCO-FUNK"


def test_hyphen_and_space_normalization():
    assert m(["Psy-Trance"]) == "TRANCE"          # hyphen -> espace
    assert m(["Nu-Disco"]) == "DISCO-FUNK"
    assert m(["2-Step"]) == "GARAGE"
    assert m(["Goa Trance"]) == "TRANCE"


def test_trance_vs_prog():
    # "progressive trance" est range sous PROG par l'utilisateur -> bat "trance" (TRANCE)
    assert m(["Progressive Trance"]) == "PROG"
    assert m(["Uplifting Trance"]) == "TRANCE"


def test_genre_fallback_when_styles_empty():
    assert m([], ["House"]) == "HOUSERZ"
    assert m([], ["Electronic"]) is None          # "electronic" n'est dans aucun mot-cle


def test_no_match_and_empty():
    assert m(["Polka"]) is None
    assert m([], []) is None
    assert m(["", "  "]) is None


def test_styles_beat_genres_on_tie():
    # styles passes avant genres -> a longueur egale, le style gagne
    assert m(["Techno"], ["House"]) == "TECHNO"


def test_tie_broken_by_mapping_order():
    # meme mot-cle, meme longueur, deux dossiers -> le premier du mapping gagne
    custom = {"AAA": ["xyz"], "BBB": ["xyz"]}
    assert map_order(custom) == "AAA"


def map_order(custom):
    return m(["xyz"], mapping=custom)


def test_default_mapping_has_trance_folder():
    assert "TRANCE" in DEFAULT_GENRE_MAPPING
    assert set(DEFAULT_GENRE_MAPPING) == {
        "ACID", "DEEPWATER", "DISCO-FUNK", "GARAGE", "HOUSERZ",
        "PROG", "TECHNO", "BREAKS-ELECTRO", "TRANCE",
    }
