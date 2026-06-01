"""Boucle d'upgrade : scan -> want-list -> sldl -> re-audit -> remplacement.

Le coeur de la feature #3 : prendre tout ce qui est sous le seuil de qualite choisi
(preset DJ Club / Audiophile / Puriste) dans une bibliotheque, chercher mieux sur
Soulseek, et NE garder que ce qui repasse le detecteur AU-DESSUS du seuil (les
filtres min-bitrate/format de sldl ne detectent PAS un upscale - d'ou le re-audit
obligatoire). Si rien n'est trouve en lossless/WAV/AIFF, une 2e passe automatique
retente en MP3 320 (jouable club), jamais sous 320.

Le remplacement est opt-in (apply=True). Par defaut : telecharge en staging,
re-audite, et rapporte ce qui SERAIT remplace, sans toucher la bibliotheque.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import quality
from .naming import match_key, parse_filename, normalize_artist_title
from .audit import _read_tags
from .scan import scan_folder, scan_library, AUDIO_EXTS
from .tokenize import get_tokens, token_coverage, core_title_tokens
from . import soulseek
from . import trash
from .soulseek import WantItem

logger = logging.getLogger(__name__)

# Profil sldl de repli (2e passe) quand rien n'est trouve en lossless/WAV/AIFF.
FALLBACK_PROFILE = "mp3-fallback"

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

# Garde-fous post-download (sldl tourne en fuzzy ; c'est DDD qui filtre intelligemment)
MIN_DURATION_S = 90        # < 90 s = quasi sûr un extrait / preview Soulseek
MIN_TITLE_COVERAGE = 0.6   # le fichier recu doit couvrir >=60% des tokens du titre (noyau)
CHUNK_SIZE = 25            # taille des lots sldl : feedback par piste periodique sur gros batch


def _chunks(seq, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _existing_keys(folder) -> set:
    """match_key des pistes deja presentes (comme fichiers audio) dans un dossier.

    Sert a ne PAS re-telecharger ce qu'on a deja (acquire relance / inbox rempli).
    """
    folder = Path(folder)
    keys = set()
    if not folder.exists():
        return keys
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            parsed = parse_filename(str(p))
            if parsed.parseable:
                # MEME normalisation que la want-list et que existing.add au depot
                # (normalize_artist_title) -> meme espace de cles, pas de faux negatif au re-run.
                na, nt = normalize_artist_title(parsed.artist, parsed.title)
                if na and nt:
                    keys.add(match_key(na, nt))
    return keys


def _reject_reason(it, dl, q, preset=quality.DEFAULT_PRESET):
    """Raison de rejet d'un download (action, note) ou None s'il est bon.

    Ordre : trop court (preview) -> mauvais match (titre/artiste) -> sous le seuil
    qualite du preset (upscale/lossy/MP3<320). Le re-audit spectral ne verifie QUE
    la qualite, pas l'identite ni la duree.

    Identite : le TITRE est le discriminant principal -> couverture du noyau du titre
    (sans (Original Mix)/feat) >= 60% ; ca reconnait "Andre Kraml - Safari" pour la requete
    "Andre Kraml Feat ... - Safari (Original Mix)" et rejette "Aladdin's Other Lamp".
    L'ARTISTE n'est qu'un garde-fou (eviter le meme titre par un autre artiste = reprise) :
    on exige juste qu'AU MOINS un des artistes demandes (n'importe quel collaborateur)
    soit present dans le nom -> tolere les collabs nommees par l'autre membre.
    """
    dur = getattr(q, "duration_s", 0) or 0
    if 0 < dur < MIN_DURATION_S:
        return ACT_TOO_SHORT, f"trop court ({dur:.0f}s < {MIN_DURATION_S}s) : preview/sample probable"

    found = set(get_tokens(Path(dl.filepath).stem))
    t_cov = token_coverage(core_title_tokens(it.title), found)    # -1 = titre non jugeable
    artist_req = get_tokens(it.artist)   # tous les collaborateurs (presence, pas couverture)
    artist_ok = (not artist_req) or any(tok in found for tok in artist_req)
    if (0 <= t_cov < MIN_TITLE_COVERAGE) or not artist_ok:
        return ACT_WRONG_MATCH, (f"mauvais match (titre {t_cov:.0%}, artiste "
                                 f"{'ok' if artist_ok else 'absent'}) : {Path(dl.filepath).name}")

    if not quality.is_accepted(q, preset):
        return ACT_REJECTED_FAKE, (f"download sous le seuil ({q.verdict}, "
                                   f"cutoff {q.cutoff_hz:.0f} Hz, preset {preset}) : {q.reason}")
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


def build_plan(scan_results, preset: str = quality.DEFAULT_PRESET) -> UpgradePlan:
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
        if quality.is_accepted(q, preset):
            continue                   # deja au-dessus du seuil -> rien a faire
        parsed = parse_filename(q.path)
        artist, title = normalize_artist_title(parsed.artist, parsed.title)  # VA/prefixe/dup
        if not artist:
            # Pas d'artiste depuis le NOM -> tags embarques (ID3/Vorbis) : bien plus precis
            # que le titre-seul (ex: "gary-beck-get-down.mp3" -> tags "Gary Beck / Get Down").
            tags = _read_tags(q.path)
            t_artist = (tags.get("artist") or "").strip()
            t_title = (tags.get("title") or "").strip()
            if t_artist and t_title:
                artist, title = normalize_artist_title(t_artist, t_title)
            elif t_title:
                title = normalize_artist_title("", t_title)[1] or title
        if not title:
            plan.unparseable.append(UpgradeOutcome(
                action=ACT_UNPARSEABLE, artist=artist, title=title,
                original=q.path, note="nom de fichier vide / illisible, aucun tag",
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


def _deposit(src, download_dir) -> Path:
    """Deplace un download VALIDE dans la bibliotheque downloads/ (son vrai nom sldl).

    `downloads/` ne contient donc que du lossless verifie. En cas de collision de nom
    (rare), suffixe ' (n)'. Retourne le chemin final.
    """
    src = Path(src)
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = download_dir / src.name
    i = 1
    while dest.exists() and dest.resolve() != src.resolve():
        dest = download_dir / f"{src.stem} ({i}){src.suffix}"
        i += 1
    if dest.resolve() != src.resolve():
        shutil.move(str(src), str(dest))
    return dest


def _finalize_download(it, dl, q, *, preset, download_dir, existing, trash_origin):
    """Evalue un download re-audite : depose s'il passe le seuil, sinon corbeille.

    Retourne (UpgradeOutcome, deposed: bool). `trash_origin=True` (upgrade) envoie
    aussi le fichier source a la corbeille et marque REPLACED ; sinon (acquire)
    marque ACQUIRED. NOT_FOUND si rien n'a ete telecharge.
    """
    base = UpgradeOutcome(action="", artist=it.artist, title=it.title, original=it.origin_path)
    if dl is None or not dl.downloaded:
        base.action = ACT_NOT_FOUND
        base.note = "sldl n'a ramene aucun fichier"
        return base, False
    base.new_file, base.new_verdict, base.new_cutoff_hz = dl.filepath, q.verdict, q.cutoff_hz
    rej = _reject_reason(it, dl, q, preset)
    if rej:
        base.action, base.note = rej
        trash.send_to_trash(dl.filepath)          # candidat rejete -> corbeille
        return base, False
    final = _deposit(dl.filepath, download_dir)
    existing.add(match_key(it.artist, it.title))
    base.new_file = str(final)
    if trash_origin:
        trash.send_to_trash(it.origin_path)       # le faux source -> corbeille
        base.action = ACT_REPLACED
        base.note = "depose dans la bibliotheque ; original a la corbeille"
    else:
        base.action = ACT_ACQUIRED
        base.note = f"depose dans la bibliotheque: {final}"
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
                    original=it.origin_path, note="sldl n'a ramene aucun fichier"))
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
    profile: str = "lossless-strict",
    fallback_profile: Optional[str] = FALLBACK_PROFILE,
    scan_results=None,
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
    on_chunk: Optional[Callable] = None,
) -> List[UpgradeOutcome]:
    """Upgrade : pour chaque faux/lossy, telecharge un vrai lossless valide, le DEPOSE
    dans la bibliotheque `download_dir`, et envoie le fichier source (le faux) a la
    corbeille. Pas de remplacement en place. `staging_dir` = cache transitoire (sldl
    telecharge la avant validation). Retourne le rapport par fichier.
    """
    root = Path(root)
    staging_dir = Path(staging_dir)
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    preset = preset or quality.preset_from_config()

    if scan_results is None:
        scan_results = scan_folder(folder, exclude_names=exclude_names, progress=progress)

    plan = build_plan(scan_results, preset)
    outcomes: List[UpgradeOutcome] = list(plan.unparseable)
    if on_item:   # statut final immediat pour les noms illisibles (jamais telecharges)
        for o in plan.unparseable:
            on_item(o.original, "done", o.action)

    # Dedoublonnage a l'entree : ce qui est deja dans la bibliotheque -> DUPLICATE.
    # On ne touche PAS au source dans ce cas (pas de check de version -> jamais de
    # suppression a l'aveugle sur un simple match de cle).
    existing = _existing_keys(download_dir)
    to_dl: List[WantItem] = []
    for it in plan.items:
        if match_key(it.artist, it.title) in existing:
            outcomes.append(UpgradeOutcome(action=ACT_DUPLICATE, artist=it.artist, title=it.title,
                                           original=it.origin_path, note="deja dans la bibliotheque"))
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
        trash_origin=True, csv_name="ddd_upgrade.csv", fallback_csv_name="ddd_upgrade_mp3.csv",
        progress=progress, on_item=on_item, on_proc=on_proc, cancel=cancel, log_path=log_path,
        on_chunk=on_chunk,
    )
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

    soulseek.stop_slskd()
    soulseek.stop_orphan_sldl()   # tue un sldl fige d'un run precedent (sinon port 50300 bloque)
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
    profile: str = "lossless-strict",
    fallback_profile: Optional[str] = FALLBACK_PROFILE,
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

    outcomes: List[UpgradeOutcome] = []
    existing = _existing_keys(download_dir)   # deja dans la bibliotheque -> on saute
    seen: set = set()                         # doublons a l'interieur de la want-list
    items: List[WantItem] = []
    for r in rows:
        artist, title = normalize_artist_title(r.get("Artist") or "", r.get("Title") or "")
        if not artist or not title:
            continue
        key = match_key(artist, title)
        if key in existing or key in seen:
            note = "deja dans la bibliotheque" if key in existing else "doublon dans la liste"
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
    return outcomes


def import_folder(
    src,
    download_dir,
    *,
    preset: Optional[str] = None,
    exclude_names: Sequence[str] = (),
    progress: Optional[Callable] = None,
) -> Dict[str, int]:
    """Migre un dossier existant dans la bibliotheque : scanne `src`, DEPLACE ce qui
    passe le seuil du preset (dedoublonne par match_key) vers `download_dir`, envoie
    le reste (sous le seuil) a la corbeille. Reversible. Retourne un bilan.
    """
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    preset = preset or quality.preset_from_config()
    records = scan_library(src, exclude_names=exclude_names, progress=progress)
    existing = _existing_keys(download_dir)
    stats = {"total": len(records), "kept": 0, "duplicates": 0, "trashed": 0}
    for rec in records:
        q = rec.quality
        if quality.is_accepted(q, preset):
            parsed = parse_filename(q.path)
            key = match_key(parsed.artist, parsed.title) if parsed.parseable else None
            if key and key in existing:
                trash.send_to_trash(q.path)        # vrai lossless mais doublon -> corbeille
                stats["duplicates"] += 1
            else:
                _deposit(q.path, download_dir)
                if key:
                    existing.add(key)
                stats["kept"] += 1
        else:
            trash.send_to_trash(q.path)            # pas un vrai lossless -> corbeille
            stats["trashed"] += 1
    logger.info("import_folder %s -> %s", src, stats)
    return stats
