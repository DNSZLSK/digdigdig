"""Tri auto d'un dossier de tracks vers les dossiers de vibe (par lookup de genre).

Chaque fichier : nom -> 'Artiste - Titre' -> lookup de style/genre (Discogs puis
MusicBrainz, voir `genre.py`) -> mappe vers un dossier de vibe via une table editable
(`DEFAULT_GENRE_MAPPING`, surchargeable en config). Ce qui n'a pas de correspondance
fiable va dans `_INBOX/` pour un tri manuel. Dry-run par defaut (apply=False) : rien
n'est deplace tant qu'on ne passe pas `apply=True` (meme convention que `rename`).

Le mapping et la fonction de resolution sont PURS (sans reseau) -> testables hors-ligne.
Le moteur `sort_folder` (plus bas) cable le lookup + le deplacement anti-collision.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import genre
from .fsutil import safe_move
from .naming import normalize_artist_title, parse_filename, read_tags, search_title
from .scan import AUDIO_EXTS

INBOX = "_INBOX"

# Actions d'une operation de tri (facon rename.py : REN/OK/SKIP/DUP)
MOVE = "MOVE"        # match fiable -> deplace (ou le serait en dry-run) dans <dossier>
INBOX_ACT = "INBOX"  # pas de match fiable -> route vers _INBOX (si route_inbox)
SKIP = "SKIP"        # laisse sur place (nom illisible, ou pas de match et route_inbox=False)
ERROR = "ERROR"      # deplacement echoue (apply uniquement)

# Mapping par defaut genre/style -> dossier de vibe. Fourni par l'utilisateur (DJ) ;
# surchargeable via config `genre_mapping`. ORDRE = priorite pour departager une
# egalite de longueur de mot-cle (le plus haut gagne). Mots-cles en minuscules ;
# le matching unifie hyphens/espaces (voir `_norm`), donc "psy trance" attrape
# le style Discogs "Psy-Trance", "nu-disco" attrape "Nu Disco", etc.
DEFAULT_GENRE_MAPPING: Dict[str, List[str]] = {
    "ACID":           ["acid house", "acid techno", "acid"],
    "DEEPWATER":      ["deep house", "dub techno", "minimal", "dub", "hypnotic",
                       "balearic", "ambient", "downtempo", "microhouse"],
    "DISCO-FUNK":     ["disco", "nu-disco", "funk", "boogie", "soul", "cosmic disco"],
    "GARAGE":         ["uk garage", "garage", "speed garage", "2-step", "bassline"],
    "HOUSERZ":        ["house", "tech house", "jackin house", "tribal house",
                       "afro house", "soulful house"],
    "PROG":           ["progressive house", "progressive trance", "melodic house",
                       "melodic techno"],
    "TECHNO":         ["techno", "detroit techno", "hard techno", "industrial"],
    "BREAKS-ELECTRO": ["breakbeat", "breaks", "electro", "broken beat", "idm",
                       "electronica"],
    "TRANCE":         ["trance", "psy trance", "goa trance", "uplifting trance"],
}


@dataclass
class SortOp:
    action: str
    src: str
    dst: str = ""            # destination finale (avec suffixe de collision eventuel)
    folder: str = ""         # dossier de vibe choisi, ou "_INBOX", ou ""
    styles: str = ""         # ", ".join des styles trouves (pour le rapport / la GUI)
    source: str = ""         # discogs | musicbrainz | "" (miss) — provenance du lookup
    reason: str = ""
    applied: bool = False


@dataclass
class SortReport:
    ops: List[SortOp] = field(default_factory=list)
    applied: bool = False
    log_path: str = ""

    def of(self, action: str) -> List[SortOp]:
        return [o for o in self.ops if o.action == action]


_NORM_RE = re.compile(r"[\s\-_/]+")


def _norm(s: str) -> str:
    """Minuscule + hyphens/underscores/slashes -> espace + espaces compresses.

    Unifie les variantes d'ecriture des styles (Psy-Trance/Psy Trance, Nu-Disco/Nu Disco,
    2-Step/2 Step, Funk / Soul) pour un matching robuste par sous-chaine.
    """
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def _seq_match(sig_tokens: List[str], kw_tokens: List[str]) -> bool:
    """True si la suite de mots-cles est une sous-suite CONTIGUE des mots du signal.

    Match par mots entiers (pas par sous-chaine) : "electro" ne matche PAS "electronic"
    (le genre parapluie), mais "house" matche bien "deep house". C'est ce qui evite que
    le genre Discogs brut "Electronic" (present sur quasi tout) tombe dans BREAKS-ELECTRO.
    """
    n = len(kw_tokens)
    if n == 0:
        return False
    return any(sig_tokens[i:i + n] == kw_tokens for i in range(len(sig_tokens) - n + 1))


def map_styles_to_folder(
    styles: Sequence[str],
    genres: Sequence[str] = (),
    mapping: Optional[Dict[str, Sequence[str]]] = None,
) -> Optional[str]:
    """Choisit le dossier de vibe pour des styles/genres donnes (PUR, sans reseau).

    Un mot-cle est retenu s'il forme une suite de mots CONTIGUE dans un signal normalise.
    Gagnant = mot-cle le plus LONG (le plus specifique) -> "deep house" (DEEPWATER) bat
    "house" (HOUSERZ) sur le style "Deep House", mais un style brut "House" ne tombe que
    sur "house" -> HOUSERZ. Egalite de longueur tranchee par l'ordre des dossiers dans le
    mapping, puis par rang du signal (styles avant genres). Aucun match -> None.
    """
    if mapping is None:
        mapping = DEFAULT_GENRE_MAPPING
    signals = [toks for toks in (_norm(x).split() for x in list(styles) + list(genres)) if toks]
    if not signals:
        return None
    folder_index = {f: i for i, f in enumerate(mapping)}
    best_rank = None
    best_folder = None
    for sig_i, sig_tokens in enumerate(signals):
        for folder, keywords in mapping.items():
            for kw in keywords:
                kwn = _norm(kw)
                if _seq_match(sig_tokens, kwn.split()):
                    rank = (len(kwn), -folder_index[folder], -sig_i)
                    if best_rank is None or rank > best_rank:
                        best_rank, best_folder = rank, folder
    return best_folder


# ---- Moteur de tri ----------------------------------------------------------

def init_library_tree(
    library_root,
    mapping: Optional[Dict[str, Sequence[str]]] = None,
) -> List[Path]:
    """Cree les dossiers de vibe (+ _INBOX) sous la racine. Idempotent (exist_ok)."""
    mapping = mapping or DEFAULT_GENRE_MAPPING
    library_root = Path(library_root)
    created: List[Path] = []
    for name in list(mapping.keys()) + [INBOX]:
        d = library_root / name
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
    return created


def _write_log(report: "SortReport", path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "from", "to", "folder", "styles", "source", "applied", "reason"])
        for o in report.ops:
            w.writerow([o.action, o.src, o.dst, o.folder, o.styles, o.source, int(o.applied), o.reason])
    return str(path)


def sort_folder(
    src=None,
    *,
    library_root,
    apply: bool = False,
    mapping: Optional[Dict[str, Sequence[str]]] = None,
    sources: Sequence[str] = genre.DEFAULT_SOURCES,
    token: str = "",
    route_inbox: bool = True,
    init_tree: bool = False,
    limit: int = 0,
    cache_dir=None,
    outputs_dir=None,
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    lookup: Optional[Callable] = None,
) -> SortReport:
    """Trie les tracks EN VRAC d'un dossier vers les dossiers de vibe (dry-run par defaut).

    Non recursif a dessein : ne traite que les fichiers directement dans `src`, donc ne
    touche jamais aux sous-dossiers (les dossiers de vibe deja remplis, ni les dossiers
    perso type MOUSTAKI/PLANETE Z). `src` defaut = `library_root` (ranger le tas lui-meme).
    Chaque fichier : nom -> 'Artiste - Titre' -> lookup -> dossier de vibe, sinon _INBOX
    (si `route_inbox`). Nom illisible (pas de 'Artiste - Titre') -> SKIP, laisse sur place,
    aucun appel reseau. apply=False : rien n'est deplace, le rapport montre le plan.
    """
    mapping = mapping or DEFAULT_GENRE_MAPPING
    lookup = lookup or genre.lookup_genre
    library_root = Path(library_root)
    src = Path(src) if src else library_root

    if init_tree:
        init_library_tree(library_root, mapping)

    report = SortReport(applied=apply)
    if not src.is_dir():
        return report

    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if limit > 0:
        files = files[:limit]
    total = len(files)

    for i, f in enumerate(files, 1):
        if cancel and cancel():
            break
        parsed = parse_filename(str(f))
        # Le tag genre ID3 et les styles Discogs/MB sont COMBINES dans un seul match : le plus
        # specifique gagne (mots entiers), donc un tag generique "House" ne write jamais par-dessus
        # un Discogs "Deep House". Le tag peut etre multi-valeur ("Acid House, Minimal") -> on split.
        tag_genre = (read_tags(f).get("genre") or "").strip()
        tag_signals = [s.strip() for s in re.split(r"[;,]", tag_genre) if s.strip()]

        artist, title = normalize_artist_title(parsed.artist, parsed.title)
        title = search_title(title)
        name_ok = bool(parsed.parseable and artist and title)

        if name_ok:
            # Nom exploitable : Discogs/MB (cache disque) + le tag, le plus specifique tranche.
            gr = lookup(artist, title, sources=sources, token=token, cache_dir=cache_dir)
            folder = map_styles_to_folder(list(gr.styles) + tag_signals, gr.genres, mapping)
            source = gr.source or ("id3" if folder else "")
            styles_str = ", ".join(dict.fromkeys(list(gr.styles or gr.genres) + tag_signals))
        else:
            # Nom illisible : pas de requete possible -> le tag genre est la seule chance
            # (avant ces fichiers etaient SKIP sans rien tenter).
            folder = map_styles_to_folder(tag_signals, (), mapping) if tag_signals else None
            source = "id3" if folder else ""
            styles_str = tag_genre if folder else ""

        # 3. Decision : dossier trouve -> MOVE ; nom illisible sans dossier -> SKIP sur place
        #    (on ne deplace jamais un fichier qu'on ne sait pas identifier) ; sinon miss -> _INBOX.
        if folder:
            target, action, reason = folder, MOVE, ""
        elif not name_ok:
            target, action, reason = "", SKIP, "name without 'Artist - Title'"
        elif route_inbox:
            target, action, reason = INBOX, INBOX_ACT, ""
        else:
            target, action, reason = "", SKIP, "no confident genre match"

        if action == SKIP:
            op = SortOp(SKIP, str(f), styles=styles_str, source=source, reason=reason)
        else:
            try:
                dest = safe_move(f, library_root / target, dry_run=not apply)
                op = SortOp(action, str(f), str(dest), folder=target,
                            styles=styles_str, source=source, applied=apply)
            except OSError as e:  # noqa: BLE001
                op = SortOp(ERROR, str(f), folder=target, styles=styles_str,
                            source=source, reason=f"move failed: {e}")
        report.ops.append(op)
        if on_item:
            on_item(str(f), "done", op.action)
        if progress:
            progress(i, total, f)

    if apply and outputs_dir is not None:
        report.log_path = _write_log(report, Path(outputs_dir) / f"sort_{src.name}.csv")
    return report
