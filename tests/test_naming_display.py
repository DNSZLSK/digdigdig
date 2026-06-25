"""Tests du nettoyage d'affichage des noms (naming.display_artist_title).

Couvre les cas vus en vrai dans la bibliotheque : [label/catalogue], prefixe promo
'Premiere_ ...', mot promo entre parentheses, suffixe '#N - Free download'. On verifie
aussi qu'on NE casse PAS les vraies versions (Original Mix)/(X Remix)/(feat. ...).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core.naming import display_artist_title as d, parse_filename


def test_strips_bracket_label_in_title_and_artist():
    assert d("Artist", "Title [TBX Records]") == ("Artist", "Title")
    assert d("[TBX Records] Artist", "Title") == ("Artist", "Title")
    assert d("Fennec", "Willy Nilly [thatpeopleplay.com]") == ("Fennec", "Willy Nilly")


def test_strips_premiere_prefix_glued_to_artist():
    assert d("Premiere_ Floorplan", "Never Grow Old") == ("Floorplan", "Never Grow Old")
    assert d("FREE DL: DJ X", "Banger") == ("DJ X", "Banger")


def test_recovers_real_artist_when_prefix_was_the_whole_field():
    # 'Premiere - Jeff Mills - The Bells' -> parse donne artist='Premiere'
    assert d("Premiere", "Jeff Mills - The Bells") == ("Jeff Mills", "The Bells")


def test_strips_promo_paren_and_trailing_suffix():
    assert d("Artist", "Cool Track (Premiere)") == ("Artist", "Cool Track")
    assert d("Artist", "Cool Track [Free DL]") == ("Artist", "Cool Track")
    # suffixe '#7 - Free download' (vu sur les rips Various Artists)
    assert d("VA", "Orient Express (Hawash edit) #7 - Free download")[1] == \
        "Orient Express (Hawash edit)"


def test_keeps_real_versions_untouched():
    assert d("Mr Fingers", "Mystery of Love (Original Mix)") == \
        ("Mr Fingers", "Mystery of Love (Original Mix)")
    assert d("Django Django", "Dont Touch That Dial (feat. Yuuko Sings) (Make A Dance Remix)") == \
        ("Django Django", "Dont Touch That Dial (feat. Yuuko Sings) (Make A Dance Remix)")
    assert d("Big Miz", "The Hadal Zone") == ("Big Miz", "The Hadal Zone")


def test_dedups_doubled_version_paren():
    assert d("Elfenberg", "Styggforsen (Fugue Mix) (Fugue Mix)") == \
        ("Elfenberg", "Styggforsen (Fugue Mix)")


def test_end_to_end_via_parse_filename():
    p = parse_filename("The Martinez Brothers - Space Jams - Betray My Heart [TMB MIX].flac")
    assert d(p.artist, p.title) == ("The Martinez Brothers", "Space Jams - Betray My Heart")
