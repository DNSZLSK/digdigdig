"""Boucle d'upgrade : scan -> want-list -> sldl -> re-audit -> remplacement.

Le coeur de la feature #3 : prendre tout ce qui est sous le plancher de qualite choisi
(preset : DJ Club / Audiophile / Puriste, ou un mode cible-format MP3 320 / WAV-AIFF / FLAC)
dans une bibliotheque, chercher mieux sur Soulseek, et NE garder que ce qui repasse le detecteur
AU-DESSUS du plancher (les filtres min-bitrate/format de sldl ne detectent PAS un upscale - d'ou
le re-audit obligatoire). Le preset fixe AUSSI la cible de recherche (cf quality.search_profiles_for) :
en DJ Club/Audiophile, si rien n'est trouve en lossless, une 2e passe retente en MP3 320 (jamais
sous 320) ; les modes cible-format cherchent directement leur format, sans repli.

Les candidats acceptes sont deposes dans la bibliotheque. Les originaux sont
conserves par defaut ; trash_original=True autorise leur retrait apres depot verifie.
"""

from __future__ import annotations

import logging
import os
import math
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import quality
from .fsutil import verified_deposit
from .content import duplicate_paths
from .naming import match_key, parse_filename, normalize_artist_title, resolve_name, read_tags, search_title
from .scan import scan_folder, scan_library, AUDIO_EXTS
from .tokenize import version_key
from . import soulseek
from . import trash
from .soulseek import WantItem

logger = logging.getLogger(__name__)

# Sentinelle : profil/repli non fournis par l'appelant -> derives du preset
# (quality.search_profiles_for). A distinguer de fallback_profile=None, qui DESACTIVE le
# repli (ce que font les tests, et ce que la CLI --profile peut forcer).
_DERIVE = object()

# Actions du rapport d'upgrade
ACT_REPLACED = "REPLACED"
ACT_WOULD_REPLACE = "WOULD_REPLACE"     # dry-run : upgrade trouve et valide
ACT_KEPT_BESIDE = "KEPT_BESIDE"         # telecharge+valide mais original garde (pas d'apply, ou collision)
ACT_REJECTED_FAKE = "REJECTED_FAKE"     # sldl a ramene un upscale -> jete
ACT_NOT_FOUND = "NOT_FOUND"             # sldl n'a rien trouve
ACT_UNPARSEABLE = "UNPARSEABLE"         # nom de fichier sans artist/title exploitable
ACT_ACQUIRED = "ACQUIRED"               # nouvelle piste authentique gardee en inbox (acquire)
ACT_TOO_SHORT = "TOO_SHORT"             # download trop court (preview/sample) -> jete
ACT_WRONG_MATCH = "WRONG_MATCH"         # mauvais titre/artiste (match fuzzy foireux) -> jete
ACT_DUPLICATE = "DUPLICATE"             # deja present (dans la liste ou deja dans l'inbox) -> saute
ACT_ALREADY_GOOD = "ALREADY_GOOD"       # cochee a la main mais deja au-dessus de la barre -> rien a upgrader

# Garde-fous post-download (sldl tourne en fuzzy ; c'est DDD qui filtre intelligemment)
MIN_DURATION_S = 90        # < 90 s = quasi sûr un extrait / preview Soulseek
CHUNK_SIZE = 25            # taille des lots sldl : feedback par piste periodique sur gros batch


def _chunks(seq, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _existing_keys(folder, exclude_paths=()) -> set:
    """match_key des pistes deja presentes (comme fichiers audio) dans un dossier.

    Sert a ne PAS re-telecharger ce qu'on a deja (acquire relance / inbox rempli).
    """
    folder = Path(folder)
    keys = set()
    excluded = {Path(p).resolve() for p in exclude_paths}
    if not folder.exists():
        return keys
    for p in folder.rglob("*"):
        if p.resolve() not in excluded and p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            parsed = parse_filename(str(p))
            if parsed.parseable:
                # MEME normalisation que la want-list et que existing.add au depot
                # (normalize_artist_title) -> meme espace de cles, pas de faux negatif au re-run.
                na, nt = normalize_artist_title(parsed.artist, parsed.title)
                if na and nt:
                    keys.add(match_key(na, nt))
    return keys


def _words(value):
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return tuple(re.findall(r"[^\W_]+", "".join(c for c in normalized if not unicodedata.combining(c))))


def _artist_names(value):
    # Match a complete collaborator, including short names and non-Latin scripts.
    parts = re.split(r"\s*(?:,|&|/|\+|\b(?:feat\.?|ft\.?|featuring|vs\.?|with)\b|\s[xX]\s)\s*", value, flags=re.I)
    return {words for part in parts if (words := _words(part))}


def _identity_title(value):
    value = re.sub(r"(?i)\s*[([]?\b(?:feat\.?|ft\.?|featuring)\b.*$", "", value)
    value = search_title(value)
    value = re.sub(r"[([{](.*?)[])}]",
                   lambda m: " " if version_key(m[1]) or _words(m[1]) in (("original",), ("original", "mix")) else m[0], value)
    return _words(value)


def _version_identity(value):
    # Generic Original Mix is equivalent to no version. Named remixes stay distinct.
    value = re.sub(r"(?i)[([]\s*original(?: mix| version)?\s*[])]", "", value)
    qualifiers = re.findall(r"[([{](.*?)[])}]", value)
    versions = tuple(_words(v) for v in qualifiers if version_key(v))
    return version_key(value), versions


def _reject_reason(it, dl, q, preset=quality.DEFAULT_PRESET):
    """Check measured duration, complete title, one complete collaborator and version.

    Unknown duration/artist fails closed. A known short recording is allowed when
    its measured duration matches within 10% (at least two seconds tolerance).
    """
    dur = getattr(q, "duration_s", 0) or 0
    if not math.isfinite(dur) or dur <= 0:
        return ACT_WRONG_MATCH, "download duration unavailable"
    if it.length and it.length > 0:
        tolerance = max(2.0, it.length * 0.10)
        if abs(dur - it.length) > tolerance:
            return ACT_WRONG_MATCH, f"duration mismatch: expected {it.length}s, received {dur:.1f}s"
    elif dur < MIN_DURATION_S:
        return ACT_TOO_SHORT, f"too short ({dur:.0f}s < {MIN_DURATION_S}s): review required"

    cand = parse_filename(dl.filepath)
    title_ok = bool(_identity_title(it.title)) and _identity_title(it.title) == _identity_title(cand.title)
    requested_artists = _artist_names(it.artist)
    artist_ok = bool(cand.parseable and requested_artists & _artist_names(cand.artist))
    ver_ok = _version_identity(it.title) == _version_identity(cand.title)

    if not (title_ok and artist_ok and ver_ok):
        bits = []
        if not title_ok:
            bits.append("title differs")
        if not artist_ok:
            bits.append("artist missing")
        if not ver_ok:
            bits.append(f"version {version_key(it.title) or 'orig'}!={version_key(cand.title) or 'orig'}")
        return ACT_WRONG_MATCH, f"wrong match ({', '.join(bits)}): {Path(dl.filepath).name}"

    if not quality.is_accepted(q, preset):
        return ACT_REJECTED_FAKE, (f"download below the bar ({q.verdict}, "
                                   f"cutoff {q.cutoff_hz:.0f} Hz, preset {preset}): {q.reason}")
    return None


def _item_id(it) -> str:
    """Identifiant stable d'un WantItem pour le statut par ligne de la GUI.

    Upgrade : le fichier d'origine (origin_path, == ScanRecord.quality.path).
    Acquire : pas d'origine -> cle artiste/titre normalisee. La GUI DOIT construire
    ses cles de ligne avec exactement match_key(artist, title) pour que ca matche.
    """
    return it.origin_path or match_key(it.artist, it.title)


@dataclass
class UpgradeOutcome:
    action: str
    artist: str
    title: str
    original: str
    new_file: str = ""
    new_verdict: str = ""
    new_cutoff_hz: float = 0.0
    note: str = ""

    def as_dict(self) -> Dict:
        return asdict(self)


@dataclass
class UpgradePlan:
    """Want-list + correspondance cle -> fichier original a remplacer."""
    items: List[WantItem] = field(default_factory=list)
    origin_by_key: Dict[str, str] = field(default_factory=dict)
    unparseable: List[UpgradeOutcome] = field(default_factory=list)
    already_good: List[UpgradeOutcome] = field(default_factory=list)  # deja au-dessus de la barre


def build_plan(scan_results, preset: str = quality.DEFAULT_PRESET, forced: bool = False) -> UpgradePlan:
    """A partir des resultats de scan, construit la want-list (fichiers a upgrader).

    Sont candidats tous les fichiers analysables qui NE passent PAS le seuil du
    preset (`quality.is_accepted`). Accepte indifferemment des QualityResult (chemin
    CLI via scan_folder) ou des ScanRecord (chemin GUI via scan_library) : le
    ScanRecord porte verdict/chemin/duree dans .quality, on normalise avant lecture.
    """
    plan = UpgradePlan()
    for r in scan_results:
        q = getattr(r, "quality", r)   # ScanRecord -> .quality ; QualityResult -> lui-meme
        if q.verdict in (quality.SKIPPED, quality.ERROR):
            continue                   # pas un fichier audio exploitable
        if not forced and quality.is_accepted(q, preset):
            # Deja au-dessus du seuil : rien a upgrader -- SAUF clic manuel (`forced`), qui
            # override (l'user dit "je veux mieux" meme sur une track deja bonne). Sinon on
            # l'ENREGISTRE (au lieu de la dropper en silence) pour que l'appelant remonte un
            # statut clair ("already good") au lieu de la laisser figee sur "queued..." cote GUI.
            plan.already_good.append(UpgradeOutcome(
                action=ACT_ALREADY_GOOD, artist="", title=q.filename,
                original=q.path, note="already above the quality bar"))
            continue
        # Resolveur de nom commun (nom propre -> tag-titre 'Artiste - Titre' -> tags -> deslug).
        # Coupe le cas piege ou l'artist tag est un compilateur ("Tibor Tury") et le vrai
        # couple est dans le tag titre ("John Kano - Havana Funk") -> recherche enfin valide.
        r = resolve_name(q.path, tags=read_tags(q.path))
        # Nettoie le titre POUR LA RECHERCHE (vire [label, annee], annee, '*') : la requete
        # sldl ET la validation _reject_reason partagent ce titre. Le rename, lui, garde le
        # crochet (il appelle resolve_name directement).
        artist, title = r.artist, search_title(r.title)
        if not title:
            plan.unparseable.append(UpgradeOutcome(
                action=ACT_UNPARSEABLE, artist=artist, title=title,
                original=q.path, note="empty / unreadable filename, no tag",
            ))
            continue
        # artist encore vide (ni nom ni tags) -> recherche TITRE-SEUL en dernier recours ;
        # plus risque mais findable ; les gardes (couverture titre + duree + spectral) filtrent.
        length = int(q.duration_s) if getattr(q, "duration_s", 0) else None
        key = match_key(artist, title)
        # premiere occurrence gagne (evite d'ecraser la cible en cas de doublon de nom)
        plan.origin_by_key.setdefault(key, q.path)
        plan.items.append(WantItem(artist, title, length, q.path))
    return plan


def _is_within(path, parent) -> bool:
    """True si `path` est sous `parent` (resolu, insensible a la casse -> Windows-safe)."""
    try:
        p = os.path.normcase(str(Path(path).resolve()))
        par = os.path.normcase(str(Path(parent).resolve()))
        return p == par or p.startswith(par + os.sep)
    except (OSError, ValueError):
        return False


def _deposit(src, download_dir) -> Path:
    """Deplace un download VALIDE dans la bibliotheque downloads/ (son vrai nom sldl).

    `downloads/` ne contient donc que du lossless verifie. Collision de nom (rare) ->
    suffixe ' (n)'. Delegue le deplacement a `fsutil.safe_move`. Retourne le chemin final.
    """
    return verified_deposit(src, download_dir)


def _finalize_download(it, dl, q, *, preset, download_dir, existing, trash_origin):
    """Evalue un download re-audite : depose s'il passe le seuil, sinon corbeille.

    Retourne (UpgradeOutcome, deposed: bool). `trash_origin=True` (upgrade) envoie
    aussi le fichier source a la corbeille et marque REPLACED ; sinon (acquire)
    marque ACQUIRED. NOT_FOUND si rien n'a ete telecharge.
    """
    base = UpgradeOutcome(action="", artist=it.artist, title=it.title, original=it.origin_path)
    if dl is None or not dl.downloaded:
        base.action = ACT_NOT_FOUND
        base.note = "sldl returned no file"
        return base, False
    if it.origin_path and Path(dl.filepath).resolve() == Path(it.origin_path).resolve():
        base.action = ACT_WRONG_MATCH
        base.note = "download points to original; retained"
        return base, False
    base.new_file, base.new_verdict, base.new_cutoff_hz = dl.filepath, q.verdict, q.cutoff_hz
    rej = _reject_reason(it, dl, q, preset)
    if rej:
        base.action, base.note = rej
        trash.send_to_trash(dl.filepath)          # candidat rejete -> corbeille
        return base, False
    # Keep the old file until the complete candidate has been copied and verified.
    in_place = bool(it.origin_path and _is_within(it.origin_path, download_dir))
    target = Path(it.origin_path).parent if in_place else download_dir
    final = _deposit(dl.filepath, target)
    existing.add(match_key(it.artist, it.title))
    base.new_file = str(final)
    if it.origin_path:
        removed = trash_origin and trash.send_to_trash(it.origin_path)
        base.action = ACT_REPLACED if removed else ACT_KEPT_BESIDE
        base.note = ("verified copy installed; original sent to trash" if removed else
                     "verified copy installed; original retained" +
                     (" (trash failed)" if trash_origin else ""))
    else:
        base.action = ACT_ACQUIRED
        base.note = f"added to the library: {final}"
    return base, True


def _download_pass(
    items, *, root, staging_dir, download_dir, existing, preset, profile,
    fallback_profile, trash_origin, csv_name, fallback_csv_name,
    progress, on_item, on_proc, cancel, log_path, on_chunk=None,
) -> List[UpgradeOutcome]:
    """Telecharge `items` lot par lot. Pour CHAQUE lot de 25 : passe 1 lossless/WAV/AIFF,
    puis tout de suite passe 2 MP3 320 (fallback_profile) sur les introuvables DU LOT.
    Re-audit + seuil a chaque passe via _finalize_download. Resultats progressifs (pas de
    collecte globale des NOT_FOUND -> feedback immediat). Retourne un outcome par item.

    `on_chunk(idx, total_chunks)` (optionnel) est appele en tete de chaque lot : la GUI
    s'en sert pour un compteur sobre "Lot idx/total" + une barre determinee (pas anime).
    """
    outcomes: List[UpgradeOutcome] = []
    total_chunks = (len(items) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for idx, chunk in enumerate(_chunks(items, CHUNK_SIZE), start=1):
        if cancel and cancel():
            break
        if on_chunk:
            on_chunk(idx, total_chunks)
        logger.info("chunk %d/%d, %d items", idx, total_chunks, len(chunk))
        results = download_and_audit(
            chunk, root=root, staging_dir=staging_dir, profile=profile, csv_name=csv_name,
            progress=progress, on_item=on_item, on_proc=on_proc, cancel=cancel, log_path=log_path,
        )
        chunk_not_found: List = []
        for it, dl, q in results:
            outcome, _ = _finalize_download(
                it, dl, q, preset=preset, download_dir=download_dir,
                existing=existing, trash_origin=trash_origin)
            if outcome.action == ACT_NOT_FOUND and fallback_profile:
                chunk_not_found.append(it)        # passe 2 MP3 juste apres, pas a la fin du run
                if on_item:                       # sort de 'Recherche...' -> 'Repli MP3' (pas empile)
                    on_item(_item_id(it), "fallback")
            else:
                outcomes.append(outcome)
                if on_item:
                    on_item(_item_id(it), "done", outcome.action)

        if not chunk_not_found:
            continue
        if cancel and cancel():                   # annule -> on marque les restes NOT_FOUND
            for it in chunk_not_found:
                outcomes.append(UpgradeOutcome(
                    action=ACT_NOT_FOUND, artist=it.artist, title=it.title,
                    original=it.origin_path, note="sldl returned no file"))
                if on_item:
                    on_item(_item_id(it), "done", ACT_NOT_FOUND)
            continue
        fb_results = download_and_audit(          # passe 2 : MP3 320 sur les misses DU LOT
            chunk_not_found, root=root, staging_dir=staging_dir, profile=fallback_profile,
            csv_name=fallback_csv_name, progress=progress, on_item=on_item,
            on_proc=on_proc, cancel=cancel, log_path=log_path,
        )
        for it, dl, q in fb_results:
            outcome, _ = _finalize_download(
                it, dl, q, preset=preset, download_dir=download_dir,
                existing=existing, trash_origin=trash_origin)
            outcomes.append(outcome)
            if on_item:
                on_item(_item_id(it), "done", outcome.action)
    return outcomes


def run_upgrade(
    folder,
    *,
    root: Path,
    staging_dir,
    download_dir,
    preset: Optional[str] = None,
    exclude_names: Sequence[str] = (),
    limit: int = 0,
    profile: Optional[str] = None,
    fallback_profile=_DERIVE,
    scan_results=None,
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
    on_chunk: Optional[Callable] = None,
    forced: bool = False,
    trash_original: bool = False,
) -> List[UpgradeOutcome]:
    """Download and verify candidates, retaining originals unless explicitly requested.

    A retained original is reported as KEPT_BESIDE. Removal failure also retains it
    and is recorded in the outcome note. In-library upgrades stay in their folder.
    """
    root = Path(root)
    staging_dir = Path(staging_dir)
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    preset = preset or quality.preset_from_config()
    # Profil de recherche + repli derives du preset, sauf si l'appelant les force.
    _dp, _df = quality.search_profiles_for(preset)
    if profile is None:
        profile = _dp
    if fallback_profile is _DERIVE:
        fallback_profile = _df

    if scan_results is None:
        scan_results = scan_folder(folder, exclude_names=exclude_names, progress=progress)

    plan = build_plan(scan_results, preset, forced=forced)
    # Pistes jamais telechargees -> statut final immediat (sinon la ligne GUI reste figee
    # sur "queued...") : noms illisibles ET pistes deja au-dessus de la barre.
    skipped = plan.unparseable + plan.already_good
    outcomes: List[UpgradeOutcome] = list(skipped)
    if on_item:
        for o in skipped:
            on_item(o.original, "done", o.action)

    # Dedoublonnage a l'entree : ce qui est already in library -> DUPLICATE.
    # On ne touche PAS au source dans ce cas (pas de check de version -> jamais de
    # suppression a l'aveugle sur un simple match de cle).
    existing = _existing_keys(download_dir, (it.origin_path for it in plan.items))
    to_dl: List[WantItem] = []
    for it in plan.items:
        if not forced and match_key(it.artist, it.title) in existing:
            # forced (clic manuel) = override total : on ne dedoublonne PAS contre la lib, sinon
            # une track deja rangee dedans se ferait jeter en "already in library" sans chercher.
            outcomes.append(UpgradeOutcome(action=ACT_DUPLICATE, artist=it.artist, title=it.title,
                                           original=it.origin_path, note="already in library"))
            if on_item:
                on_item(_item_id(it), "done", ACT_DUPLICATE)
        else:
            to_dl.append(it)
    if limit > 0:
        to_dl = to_dl[:limit]
    if not to_dl:
        return outcomes

    outcomes += _download_pass(
        to_dl, root=root, staging_dir=staging_dir, download_dir=download_dir,
        existing=existing, preset=preset, profile=profile, fallback_profile=fallback_profile,
        trash_origin=trash_original, csv_name="ddd_upgrade.csv", fallback_csv_name="ddd_upgrade_mp3.csv",
        progress=progress, on_item=on_item, on_proc=on_proc, cancel=cancel, log_path=log_path,
        on_chunk=on_chunk,
    )
    if not (cancel and cancel()):   # run fini -> purge le staging (garde tout si annule, pour reprise)
        soulseek.clear_run_staging(staging_dir, "ddd_upgrade.csv", "ddd_upgrade_mp3.csv")
    return outcomes


def download_and_audit(
    items: Sequence[WantItem],
    *,
    root: Path,
    staging_dir,
    profile: str = "lossless-strict",
    limit: int = 0,
    creds: Optional[Dict] = None,
    csv_name: str = "ddd_upgrade.csv",
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
):
    """Telecharge des WantItems via sldl puis re-audite chaque download.

    Brique partagee : retourne une liste de tuples
    (WantItem, DownloadResult|None, QualityResult|None). q vaut None si rien n'a
    ete telecharge pour cet item.
    """
    root = Path(root)
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    input_csv = staging_dir / csv_name
    soulseek.write_input_csv(items, input_csv)

    creds = creds or soulseek.read_soulseek_creds()

    if on_item:
        for it in items:
            on_item(_item_id(it), "searching")

    code = soulseek.run_sldl(
        input_csv, staging_dir, root=root, profile=profile, creds=creds,
        limit=limit, log_path=log_path, on_line=(progress if progress else None),
        on_proc=on_proc,
    )
    logger.info("sldl exit code: %s", code)

    index = soulseek.read_index(soulseek.index_path_for(input_csv, staging_dir))
    by_key = {match_key(d.artist, d.title): d for d in index}

    out = []
    for it in items:
        if cancel and cancel():            # annule : on n'audite pas le reste
            out.append((it, None, None))
            continue
        dl = by_key.get(match_key(it.artist, it.title))
        if on_item and dl and dl.downloaded:
            on_item(_item_id(it), "auditing")
        q = quality.analyze_file(dl.filepath) if (dl and dl.downloaded) else None
        out.append((it, dl, q))
    return out


def acquire_rows(
    rows: Sequence[Dict],
    *,
    root: Path,
    download_dir,
    staging_dir=None,
    limit: int = 0,
    preset: Optional[str] = None,
    profile: Optional[str] = None,
    fallback_profile=_DERIVE,
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
    on_chunk: Optional[Callable] = None,
) -> List[UpgradeOutcome]:
    """Telecharge une want-list scrapee (dicts Artist/Title/Length) et DEPOSE les vrais
    lossless valides dans la bibliotheque `download_dir`. Les candidats rejetes (fake/
    court/mauvais match) partent a la corbeille. Dedoublonne contre la bibliotheque et
    la liste. `staging_dir` = cache transitoire (defaut: <download_dir>/.cache-dl).
    """
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(staging_dir) if staging_dir else (download_dir / ".cache-dl")
    preset = preset or quality.preset_from_config()
    # Profil de recherche + repli derives du preset, sauf si l'appelant les force.
    _dp, _df = quality.search_profiles_for(preset)
    if profile is None:
        profile = _dp
    if fallback_profile is _DERIVE:
        fallback_profile = _df

    outcomes: List[UpgradeOutcome] = []
    existing = _existing_keys(download_dir)   # already in library -> on saute
    seen: set = set()                         # doublons a l'interieur de la want-list
    items: List[WantItem] = []
    for r in rows:
        artist, title = normalize_artist_title(r.get("Artist") or "", r.get("Title") or "")
        if not artist or not title:
            continue
        key = match_key(artist, title)
        if key in existing or key in seen:
            note = "already in library" if key in existing else "duplicate in the list"
            outcomes.append(UpgradeOutcome(action=ACT_DUPLICATE, artist=artist, title=title,
                                           original="", note=note))
            if on_item:
                on_item(key, "done", ACT_DUPLICATE)
            continue
        seen.add(key)
        length = None
        raw = r.get("Length")
        if raw not in (None, ""):
            try:
                length = int(float(raw))
            except (ValueError, TypeError):
                length = None
        items.append(WantItem(artist, title, length, ""))

    if limit > 0:
        items = items[:limit]

    if not items:
        return outcomes

    outcomes += _download_pass(
        items, root=root, staging_dir=staging_dir, download_dir=download_dir,
        existing=existing, preset=preset, profile=profile, fallback_profile=fallback_profile,
        trash_origin=False, csv_name="ddd_acquire.csv", fallback_csv_name="ddd_acquire_mp3.csv",
        progress=progress, on_item=on_item, on_proc=on_proc, cancel=cancel, log_path=log_path,
        on_chunk=on_chunk,
    )
    if not (cancel and cancel()):   # run fini -> purge le staging (garde tout si annule, pour reprise)
        soulseek.clear_run_staging(staging_dir, "ddd_acquire.csv", "ddd_acquire_mp3.csv")
    return outcomes


def import_folder(
    src,
    download_dir,
    *,
    preset: Optional[str] = None,
    exclude_names: Sequence[str] = (),
    progress: Optional[Callable] = None,
) -> Dict[str, int]:
    """Move accepted, unique files; retain rejects, errors and confirmed duplicates.

    Never scan the destination as source, including a destination nested in src.
    No file is trashed by import. Unknown quality is a review decision, not deletion.
    """
    src, download_dir = Path(src).resolve(), Path(download_dir).resolve()
    stats = dict(total=0, kept=0, duplicates=0, trashed=0, retained=0, errors=0)
    if _is_within(src, download_dir):
        return stats
    download_dir.mkdir(parents=True, exist_ok=True)
    preset = preset or quality.preset_from_config()
    records = [r for r in scan_library(src, exclude_names=exclude_names, progress=progress)
               if not _is_within(r.quality.path, download_dir)]
    stats["total"] = len(records)
    existing_files = list(p for p in download_dir.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    for rec in records:
        q = rec.quality
        if q.verdict in (quality.ERROR, quality.SKIPPED):
            stats["errors"] += 1
            continue
        if not quality.is_accepted(q, preset):
            stats["retained"] += 1
            continue
        source = Path(q.path)
        if any(source in group for group in duplicate_paths([source, *existing_files])):
            stats["duplicates"] += 1
            continue
        try:
            final = _deposit(source, download_dir)
        except OSError as exc:
            logger.warning("import failed for %s: %s", source, exc)
            stats["errors"] += 1
            continue
        existing_files.append(final)
        stats["kept"] += 1
    logger.info("import_folder %s -> %s", src, stats)
    return stats
