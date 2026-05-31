"""Boucle d'upgrade : scan -> want-list -> sldl -> re-audit -> remplacement.

Le coeur de la feature #3 : prendre les faux lossless / lossy d'une bibliotheque,
chercher un vrai lossless sur Soulseek, et NE garder que ce qui passe a nouveau le
detecteur de qualite en AUTHENTIC (les filtres min-bitrate/format de sldl ne
detectent PAS un upscale - d'ou le re-audit obligatoire).

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
from .naming import match_key, parse_filename
from .scan import scan_folder
from . import soulseek
from .soulseek import WantItem

logger = logging.getLogger(__name__)

# Verdicts qui declenchent une tentative d'upgrade (par defaut)
UPGRADE_VERDICTS = {quality.FAKE, quality.LOSSY}

# Actions du rapport d'upgrade
ACT_REPLACED = "REPLACED"
ACT_WOULD_REPLACE = "WOULD_REPLACE"     # dry-run : upgrade trouve et valide
ACT_KEPT_BESIDE = "KEPT_BESIDE"         # telecharge+valide mais original garde (pas d'apply, ou collision)
ACT_REJECTED_FAKE = "REJECTED_FAKE"     # sldl a ramene un upscale -> jete
ACT_NOT_FOUND = "NOT_FOUND"             # sldl n'a rien trouve
ACT_UNPARSEABLE = "UNPARSEABLE"         # nom de fichier sans artist/title exploitable
ACT_ACQUIRED = "ACQUIRED"               # nouvelle piste authentique gardee en inbox (acquire)


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


def build_plan(scan_results, verdicts: Sequence[str] = ()) -> UpgradePlan:
    """A partir des resultats de scan, construit la want-list (fichiers a upgrader).

    Accepte indifferemment des QualityResult (chemin CLI via scan_folder) ou des
    ScanRecord (chemin GUI via scan_library) : le ScanRecord porte verdict/chemin/duree
    dans .quality, on normalise donc chaque element avant lecture.
    """
    wanted = set(verdicts) if verdicts else UPGRADE_VERDICTS
    plan = UpgradePlan()
    for r in scan_results:
        q = getattr(r, "quality", r)   # ScanRecord -> .quality ; QualityResult -> lui-meme
        if q.verdict not in wanted:
            continue
        parsed = parse_filename(q.path)
        if not parsed.parseable:
            plan.unparseable.append(UpgradeOutcome(
                action=ACT_UNPARSEABLE, artist=parsed.artist, title=parsed.title,
                original=q.path, note="nom sans 'Artiste - Titre' exploitable",
            ))
            continue
        length = int(q.duration_s) if getattr(q, "duration_s", 0) else None
        key = match_key(parsed.artist, parsed.title)
        # premiere occurrence gagne (evite d'ecraser la cible en cas de doublon de nom)
        plan.origin_by_key.setdefault(key, q.path)
        plan.items.append(WantItem(parsed.artist, parsed.title, length, q.path))
    return plan


def _replace_in_place(original: str, new_file: str, apply: bool, delete_old: bool) -> UpgradeOutcome:
    """Place le nouveau fichier a cote de l'original (meme dossier, son vrai nom sldl)."""
    orig = Path(original)
    src = Path(new_file)
    dest = orig.parent / src.name

    if not apply:
        return UpgradeOutcome(
            action=ACT_WOULD_REPLACE, artist="", title="",
            original=original, new_file=new_file,
            note=f"dry-run : copierait vers {dest}" + (" + supprimerait l'original" if delete_old else ""),
        )

    if dest.exists() and dest.resolve() != src.resolve():
        return UpgradeOutcome(
            action=ACT_KEPT_BESIDE, artist="", title="",
            original=original, new_file=new_file,
            note=f"destination existe deja : {dest} (rien ecrase)",
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    note = f"copie -> {dest}"
    if delete_old and orig.exists() and orig.resolve() != dest.resolve():
        orig.unlink()
        note += " ; original supprime"
    return UpgradeOutcome(
        action=ACT_REPLACED, artist="", title="",
        original=original, new_file=str(dest), note=note,
    )


def run_upgrade(
    folder,
    *,
    root: Path,
    staging_dir,
    verdicts: Sequence[str] = (),
    exclude_names: Sequence[str] = (),
    apply: bool = False,
    delete_old: bool = False,
    limit: int = 0,
    profile: str = "lossless-strict",
    scan_results=None,
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
) -> List[UpgradeOutcome]:
    """Pipeline complet d'upgrade. Retourne la liste des issues par fichier.

    apply=False (defaut) : telecharge + re-audite mais ne touche pas la bibliotheque.
    apply=True : remplace en place les originaux par les downloads AUTHENTIC.
    """
    root = Path(root)
    staging_dir = Path(staging_dir)

    if scan_results is None:
        scan_results = scan_folder(folder, exclude_names=exclude_names, progress=progress)

    plan = build_plan(scan_results, verdicts)
    outcomes: List[UpgradeOutcome] = list(plan.unparseable)
    if on_item:   # statut final immediat pour les noms illisibles (jamais telecharges)
        for o in plan.unparseable:
            on_item(o.original, "done", o.action)

    if not plan.items:
        logger.info("aucun fichier a upgrader")
        return outcomes

    # download + re-audit (boucle factorisee, partagee avec acquire_rows)
    results = download_and_audit(
        plan.items, root=root, staging_dir=staging_dir, profile=profile,
        limit=limit, progress=progress, on_item=on_item, on_proc=on_proc,
        cancel=cancel, log_path=log_path,
    )

    for it, dl, q in results:
        base = UpgradeOutcome(action="", artist=it.artist, title=it.title, original=it.origin_path)

        if dl is None or not dl.downloaded:
            base.action = ACT_NOT_FOUND
            base.note = "sldl n'a ramene aucun fichier"
            outcomes.append(base)
            if on_item:
                on_item(_item_id(it), "done", base.action)
            continue

        base.new_file = dl.filepath
        base.new_verdict = q.verdict
        base.new_cutoff_hz = q.cutoff_hz

        # LE point clef : re-auditer le download (sldl ne detecte pas les upscales)
        if q.verdict != quality.AUTHENTIC:
            base.action = ACT_REJECTED_FAKE
            base.note = f"download non-authentique ({q.verdict}, cutoff {q.cutoff_hz:.0f} Hz) : {q.reason}"
            outcomes.append(base)
            if on_item:
                on_item(_item_id(it), "done", base.action)
            continue

        res = _replace_in_place(it.origin_path, dl.filepath, apply, delete_old)
        res.artist, res.title = it.artist, it.title
        res.new_verdict, res.new_cutoff_hz = q.verdict, q.cutoff_hz
        outcomes.append(res)
        if on_item:
            on_item(_item_id(it), "done", res.action)

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
    inbox_dir,
    limit: int = 0,
    profile: str = "lossless-strict",
    progress: Optional[Callable] = None,
    on_item: Optional[Callable] = None,
    on_proc: Optional[Callable] = None,
    cancel: Optional[Callable] = None,
    log_path=None,
) -> List[UpgradeOutcome]:
    """Telecharge une want-list scrapee (dicts Artist/Title/Length) vers un inbox.

    Acquisition de NOUVELLES pistes (pas de remplacement) : on garde seulement les
    downloads AUTHENTIC, les autres sont signales mais laisses de cote.
    """
    items: List[WantItem] = []
    for r in rows:
        artist = (r.get("Artist") or "").strip()
        title = (r.get("Title") or "").strip()
        if not artist or not title:
            continue
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

    outcomes: List[UpgradeOutcome] = []
    if not items:
        return outcomes

    results = download_and_audit(
        items, root=root, staging_dir=inbox_dir, profile=profile,
        limit=0, csv_name="ddd_acquire.csv", progress=progress,
        on_item=on_item, on_proc=on_proc, cancel=cancel, log_path=log_path,
    )
    for it, dl, q in results:
        base = UpgradeOutcome(action="", artist=it.artist, title=it.title, original="")
        if dl is None or not dl.downloaded:
            base.action = ACT_NOT_FOUND
            base.note = "sldl n'a ramene aucun fichier"
        elif q.verdict != quality.AUTHENTIC:
            base.action = ACT_REJECTED_FAKE
            base.new_file, base.new_verdict, base.new_cutoff_hz = dl.filepath, q.verdict, q.cutoff_hz
            base.note = f"non-authentique ({q.verdict}, cutoff {q.cutoff_hz:.0f} Hz)"
        else:
            base.action = ACT_ACQUIRED
            base.new_file, base.new_verdict, base.new_cutoff_hz = dl.filepath, q.verdict, q.cutoff_hz
            base.note = f"garde en inbox: {dl.filepath}"
        outcomes.append(base)
        if on_item:
            on_item(_item_id(it), "done", base.action)
    return outcomes
