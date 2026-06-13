"""Tests du resolveur de nom (offline, tags injectes -> pas de fichiers reels).

Cas tires du dossier reel 'Tibor Tury' : le piege ou l'artist tag est un compilateur
et le vrai couple est dans le tag titre, + les gardes anti-tag-faux et anti-slug.
"""

from __future__ import annotations

from ddd.core.naming import resolve_name


def _r(name, **tags):
    full = {"artist": "", "title": "", "album": "", **tags}
    return resolve_name(name, tags=full)


def test_name_rule_keeps_clean_filename():
    # Nom deja 'Artiste - Titre', aucun tag -> on garde le nom.
    r = _r("Eddie Richards - Xtrk [SOCO Audio, 2001].mp3")
    assert (r.artist, r.title) == ("Eddie Richards", "Xtrk [SOCO Audio, 2001]")
    assert r.source == "name" and r.confident


def test_title_tag_split_strips_year():
    # Mixtape : artist tag = 'Tibor Tury' (compilateur), vrai couple dans le tag titre.
    r = _r("john-kano-havana-funk-percussion-mix-1997.mp3",
           artist="Tibor Tury", title="John Kano - Havana  Funk (Percussion Mix) (1997)")
    assert (r.artist, r.title) == ("John Kano", "Havana Funk (Percussion Mix)")
    assert r.source == "title-tag" and r.confident


def test_title_tag_unicode_dash():
    # Tiret unicode + marque invisible mojibake dans le tag titre -> doit quand meme couper.
    # LRM (U+200E) + caractere de remplacement (U+FFFD) entoure d'espaces = un tiret casse.
    mojibake = " " + chr(0x200e) + chr(0xfffd) + " "
    r = _r("elektrik-soul-the-supreme-soul-team.mp3",
           artist="Some Label", title="Elektrik Soul" + mojibake + "The Supreme Soul Team")
    assert (r.artist, r.title) == ("Elektrik Soul", "The Supreme Soul Team")
    assert r.confident


def test_clean_tags_type_b():
    # Tags propres, l'artiste apparait dans le slug -> fiable.
    r = _r("soul-station-fool-for-love-mad-moses-original-mix.mp3",
           artist="Soul Station", title="Fool For Love (Mad Moses Mix)")
    assert (r.artist, r.title) == ("Soul Station", "Fool For Love (Mad Moses Mix)")
    assert r.source == "tags" and r.confident


def test_dedup_consecutive_parens():
    r = _r("mell-ground-odissey-european-mix.mp3",
           artist="Mell Ground", title="Odissey (European Mix) (European Mix)")
    assert r.title == "Odissey (European Mix)" and r.confident


def test_false_tag_no_title_overlap_not_confident():
    # Tag auto faux : 'Mandrill' n'a aucun rapport avec le slug -> cherchable mais pas renomme.
    r = _r("the-origin-of-dance-the-golden-sun-2002.mp3",
           artist="Mandrill", title="Mandrill (Album Version)")
    assert (r.artist, r.title) == ("Mandrill", "Mandrill (Album Version)")
    assert not r.confident


def test_false_tag_artist_conflict_not_confident():
    # Titre generique qui recouvre, MAIS artiste en conflit avec le nom -> non fiable.
    r = _r("dj-assasins-i-like-it-1995.mp3",
           artist="The Players Association", title="I Like It")
    assert not r.confident


def test_copie_resolves_via_tags_not_word_copie():
    # '... - Copie' parserait en titre='Copie' ; les tags doivent gagner.
    r = _r("am-dl-the-tribal-sound-of-south-italy-the-bar-dub - Copie.mp3",
           artist="A.M. & D.L", title="The tribal sound of south of Italy")
    assert r.artist == "A.M. & D.L" and r.title == "The tribal sound of south of Italy"
    assert "copie" not in r.title.lower()


def test_title_only_when_no_tags():
    r = _r("Mysterious Title.wav")
    assert (r.artist, r.title) == ("", "Mysterious Title")
    assert not r.confident


def test_slug_without_tags_deslugified_title_only():
    r = _r("john-kano-havana-funk.mp3")
    assert r.artist == "" and r.title == "John Kano Havana Funk"
    assert r.source == "deslug" and not r.confident


def test_search_title_strips_label_year_keeps_mix():
    from ddd.core.naming import search_title
    assert search_title("Thirtyfive [Circus Company, 2009]") == "Thirtyfive"
    assert (search_title("Million Dollar Feeling (Gerds Old School Mix) [Delusions Of Grandeur, 2010]")
            == "Million Dollar Feeling (Gerds Old School Mix)")
    assert search_title("I Want You (Original Version) (1996)") == "I Want You (Original Version)"
    assert search_title("Some Track *") == "Some Track"
