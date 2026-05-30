"""CLI DDD : `ddd scan <dossier>` (et, a venir, scrape / upgrade / deploy)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from . import paths
from .core.quality import AUTHENTIC, FAKE, LOSSY, SUSPICIOUS
from .core import audit as audit_mod
from .core import config as config_mod
from .core.scan import (
    SCAN_RECORD_FIELDS, duplicate_groups, scan_library, write_csv, write_json,
)
from .core import upgrade as upgrade_mod

# Ordre d'affichage du resume (du plus actionnable au moins)
VERDICT_ORDER = [FAKE, SUSPICIOUS, LOSSY, AUTHENTIC, "ERROR", "SKIPPED"]
VERDICT_LABEL = {
    FAKE: "faux lossless (source lossy)",
    SUSPICIOUS: "suspect (320 kbps probable)",
    LOSSY: "lossy (candidat upgrade)",
    AUTHENTIC: "vrai lossless",
    "ERROR": "erreur d'analyse",
    "SKIPPED": "ignore",
}

# Statuts de nommage a remonter (on n'affiche pas OK / NAME_ONLY = sans interet)
NAME_PROBLEM_ORDER = [audit_mod.TAG_MISMATCH, audit_mod.VERSION_MISMATCH, audit_mod.UNPARSEABLE]
NAME_PROBLEM_LABEL = {
    audit_mod.TAG_MISMATCH: "tags ne collent pas au nom",
    audit_mod.VERSION_MISMATCH: "version nom != version tag",
    audit_mod.UNPARSEABLE: "nom sans 'Artiste - Titre'",
}


def _human_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.1f} Go"


def _cmd_scan(args: argparse.Namespace) -> int:
    from collections import Counter

    folder = Path(args.folder)
    if not folder.exists():
        print(f"dossier introuvable: {folder}", file=sys.stderr)
        return 2

    def progress(i: int, total: int, f: Path) -> None:
        if args.verbose or i == total or i % 25 == 0:
            print(f"  [{i}/{total}] {f.name}", file=sys.stderr)

    print(f"Scan de {folder} ...", file=sys.stderr)
    records = scan_library(folder, exclude_names=args.exclude, progress=progress)

    out_csv = Path(args.out) if args.out else paths.outputs_dir() / f"scan_{folder.name}.csv"
    out_json = out_csv.with_suffix(".json")
    write_csv(records, out_csv, SCAN_RECORD_FIELDS)
    write_json(records, out_json)

    qsummary = Counter(r.quality.verdict for r in records)
    nsummary = Counter(r.naming.status for r in records)
    dup_groups = duplicate_groups(records)

    print()
    print(f"=== Scan: {folder.name} ===")
    print(f"{len(records)} fichiers audio analyses\n")

    print("QUALITE")
    for verdict in VERDICT_ORDER:
        n = qsummary.get(verdict, 0)
        if n:
            print(f"  {verdict:<14} {n:>5}   {VERDICT_LABEL.get(verdict, '')}")

    name_problems = sum(nsummary.get(s, 0) for s in NAME_PROBLEM_ORDER)
    if name_problems:
        print("\nNOMMAGE / TAGS")
        for status in NAME_PROBLEM_ORDER:
            n = nsummary.get(status, 0)
            if n:
                print(f"  {status:<18} {n:>5}   {NAME_PROBLEM_LABEL.get(status, '')}")

    if dup_groups:
        dup_files = sum(len(g) for g in dup_groups)
        wasted = sum(g[0].size_bytes * (len(g) - 1) for g in dup_groups)
        print(f"\nDOUBLONS : {len(dup_groups)} groupes, {dup_files} fichiers "
              f"(~{_human_size(wasted)} recuperables)")
        for g in dup_groups[:10]:
            print(f"  x{len(g)}  {_human_size(g[0].size_bytes)}")
            for r in g:
                print(f"        {r.quality.path}")
        if len(dup_groups) > 10:
            print(f"  ... +{len(dup_groups) - 10} autres groupes (voir le CSV)")

    # Detail qualite a problemes (faux lossless en tete)
    flagged = [r for r in records if r.quality.verdict in (FAKE, SUSPICIOUS)]
    if flagged:
        print(f"\n  --- {len(flagged)} a verifier/remplacer ---")
        for r in sorted(flagged, key=lambda r: (r.quality.verdict, r.quality.cutoff_hz)):
            print(f"  {r.quality.verdict:<14} cutoff {r.quality.cutoff_hz:>7.0f} Hz  {r.quality.filename}")

    print(f"\nRapport : {out_csv}")
    print(f"          {out_json}")
    return 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.exists():
        print(f"dossier introuvable: {folder}", file=sys.stderr)
        return 2

    root = paths.resource_base()
    staging = Path(args.staging) if args.staging else paths.staging_dir() / "upgrade"
    log_path = paths.logs_dir() / "ddd_upgrade.log"

    verdicts = []
    if not args.lossy_only:
        verdicts.append(FAKE)
    verdicts.append(LOSSY) if not args.fake_only else None
    if args.include_suspicious:
        verdicts.append(SUSPICIOUS)

    def progress(*a) -> None:
        if len(a) == 3 and not args.verbose:
            i, total, f = a
            if i == total or i % 25 == 0:
                print(f"  scan [{i}/{total}] {Path(f).name}", file=sys.stderr)
        elif len(a) == 1:
            print(a[0], file=sys.stderr)        # ligne sldl
        elif args.verbose and len(a) == 3:
            print(f"  scan [{a[0]}/{a[1]}] {Path(a[2]).name}", file=sys.stderr)

    mode = "APPLY (remplacement reel)" if args.apply else "DRY-RUN (aucune modif)"
    print(f"Upgrade de {folder}  [{mode}]", file=sys.stderr)
    outcomes = upgrade_mod.run_upgrade(
        folder, root=root, staging_dir=staging,
        verdicts=verdicts, exclude_names=args.exclude,
        apply=args.apply, delete_old=args.delete_old,
        limit=args.limit, profile=args.profile, progress=progress, log_path=log_path,
    )

    from collections import Counter
    counts = Counter(o.action for o in outcomes)
    print(f"\n=== Upgrade: {folder.name} ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")

    replaced = [o for o in outcomes if o.action in (upgrade_mod.ACT_REPLACED, upgrade_mod.ACT_WOULD_REPLACE)]
    if replaced:
        print(f"\n  --- {len(replaced)} upgrade(s) AUTHENTIQUE(s) ---")
        for o in replaced:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")

    out_csv = paths.outputs_dir() / f"upgrade_{folder.name}.csv"
    _write_outcomes_csv(outcomes, out_csv)
    print(f"\nRapport : {out_csv}")
    if not args.apply:
        print("(dry-run : relance avec --apply pour remplacer reellement)")
    return 0


def _write_outcomes_csv(outcomes, path) -> None:
    import csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["action", "artist", "title", "new_verdict", "new_cutoff_hz", "original", "new_file", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for o in outcomes:
            row = o.as_dict()
            w.writerow({k: row.get(k, "") for k in fields})


def _cmd_scrape(args: argparse.Namespace) -> int:
    from .core import scrapers
    import csv

    source = args.source
    if source not in scrapers.SOURCES:
        print(f"source inconnue: {source} (dispo: {', '.join(scrapers.SOURCES)})", file=sys.stderr)
        return 2

    def progress(msg: str) -> None:
        print(f"  {msg}", file=sys.stderr)

    try:
        if source == "discogs":
            rows = scrapers.scrape_discogs(
                args.username, token=args.token or "",
                include_collection=args.include_collection, progress=progress)
        else:
            rows = scrapers.scrape_bandcamp(
                args.username, expand_albums=not args.no_expand_albums, progress=progress)
    except (ValueError, RuntimeError) as e:
        print(f"ERREUR: {e}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else paths.outputs_dir() / f"{source}_{args.username}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=scrapers.ROW_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\n=== Scrape {source}: {args.username} ===")
    print(f"{len(rows)} pistes -> {out}")
    if not args.no_acquire and rows:
        print("\n(pour telecharger ces pistes en vrai lossless : "
              f"ddd acquire \"{out}\")")
    return 0


def _cmd_acquire(args: argparse.Namespace) -> int:
    """Telecharge une want-list (CSV scrape) en vrai lossless vers un inbox."""
    import csv
    from collections import Counter

    src = Path(args.csv)
    if not src.exists():
        print(f"CSV introuvable: {src}", file=sys.stderr)
        return 2

    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("CSV vide", file=sys.stderr)
        return 1

    root = paths.resource_base()
    inbox = Path(args.inbox) if args.inbox else paths.staging_dir() / "inbox"
    log_path = paths.logs_dir() / "ddd_acquire.log"

    def progress(*a) -> None:
        if len(a) == 1:
            print(a[0], file=sys.stderr)

    print(f"Acquire {len(rows)} pistes -> {inbox}", file=sys.stderr)
    outcomes = upgrade_mod.acquire_rows(
        rows, root=root, inbox_dir=inbox, limit=args.limit,
        profile=args.profile, progress=progress, log_path=log_path)

    counts = Counter(o.action for o in outcomes)
    print(f"\n=== Acquire: {src.name} ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")
    acquired = [o for o in outcomes if o.action == upgrade_mod.ACT_ACQUIRED]
    if acquired:
        print(f"\n  --- {len(acquired)} piste(s) AUTHENTIQUE(s) en inbox ---")
        for o in acquired:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    if args.action == "show":
        cfg = config_mod.load()
        print(f"Config: {config_mod.config_path()}")
        if not cfg:
            print("  (vide)")
        for k in config_mod.KNOWN_KEYS:
            if k in cfg:
                val = cfg[k]
                if "pass" in k or "token" in k:
                    val = "***" + str(val)[-4:] if val else ""
                print(f"  {k} = {val}")
        return 0
    if args.action == "set":
        if not args.key or args.value is None:
            print("usage: ddd config set <key> <value>", file=sys.stderr)
            return 2
        p = config_mod.set_value(args.key, args.value)
        print(f"OK -> {p}")
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ddd", description="DDD - DigDigDig")
    parser.add_argument("--version", action="version", version=f"ddd {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="scanner la qualite d'un dossier (vrai lossless ou non)")
    p_scan.add_argument("folder", help="dossier a scanner")
    p_scan.add_argument("-o", "--out", help="chemin du rapport CSV (defaut: outputs/scan_<dossier>.csv)")
    p_scan.add_argument("-x", "--exclude", action="append", default=[],
                        metavar="NOM", help="nom de sous-dossier a ignorer (repetable, ex: -x PROD)")
    p_scan.add_argument("-v", "--verbose", action="store_true", help="afficher chaque fichier")
    p_scan.set_defaults(func=_cmd_scan)

    p_up = sub.add_parser("upgrade",
                          help="chercher un vrai lossless sur Soulseek pour les fichiers flagges")
    p_up.add_argument("folder", help="dossier a upgrader")
    p_up.add_argument("--apply", action="store_true",
                      help="remplacer reellement (defaut: dry-run, telecharge + re-audite seulement)")
    p_up.add_argument("--delete-old", action="store_true",
                      help="avec --apply, supprimer l'original une fois remplace")
    p_up.add_argument("-x", "--exclude", action="append", default=["PROD"],
                      metavar="NOM", help="sous-dossier a ignorer (defaut: PROD). Repetable.")
    p_up.add_argument("--fake-only", action="store_true", help="n'upgrader que les FAKE_LOSSLESS")
    p_up.add_argument("--lossy-only", action="store_true", help="n'upgrader que les LOSSY")
    p_up.add_argument("--include-suspicious", action="store_true",
                      help="inclure aussi les SUSPICIOUS (320 kbps probable)")
    p_up.add_argument("-n", "--limit", type=int, default=0,
                      help="limiter a N pistes (smoke test)")
    p_up.add_argument("--staging", help="dossier de staging (defaut: staging/upgrade)")
    p_up.add_argument("--profile", default="lossless-strict",
                      help="profil sldl (defaut: lossless-strict = FLAC uniquement)")
    p_up.add_argument("-v", "--verbose", action="store_true", help="afficher chaque fichier scanne")
    p_up.set_defaults(func=_cmd_upgrade)

    p_sc = sub.add_parser("scrape", help="scraper tes favoris (Discogs/Bandcamp) -> CSV want-list")
    p_sc.add_argument("source", choices=["discogs", "bandcamp"], help="source a scraper")
    p_sc.add_argument("username", help="ton nom d'utilisateur sur la source")
    p_sc.add_argument("-o", "--out", help="CSV de sortie (defaut: outputs/<source>_<user>.csv)")
    p_sc.add_argument("--token", help="token Discogs (sinon $DISCOGS_TOKEN ou config ddd)")
    p_sc.add_argument("--include-collection", action="store_true", help="Discogs: aussi la collection")
    p_sc.add_argument("--no-expand-albums", action="store_true", help="Bandcamp: garder les albums entiers")
    p_sc.add_argument("--no-acquire", action="store_true", help="ne pas suggerer l'etape acquire")
    p_sc.set_defaults(func=_cmd_scrape)

    p_ac = sub.add_parser("acquire", help="telecharger une want-list CSV en vrai lossless (inbox)")
    p_ac.add_argument("csv", help="CSV want-list (sortie de scrape)")
    p_ac.add_argument("--inbox", help="dossier de destination (defaut: staging/inbox)")
    p_ac.add_argument("-n", "--limit", type=int, default=0, help="limiter a N pistes")
    p_ac.add_argument("--profile", default="lossless-strict", help="profil sldl")
    p_ac.set_defaults(func=_cmd_acquire)

    p_cfg = sub.add_parser("config", help="gerer la config (creds, cible)")
    p_cfg.add_argument("action", choices=["show", "set"])
    p_cfg.add_argument("key", nargs="?", help="cle (ex: discogs_token)")
    p_cfg.add_argument("value", nargs="?", help="valeur")
    p_cfg.set_defaults(func=_cmd_config)

    p_gui = sub.add_parser("gui", help="lancer la fenetre native (Flet)")
    p_gui.set_defaults(func=_cmd_gui)
    return parser


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import run
    except ImportError as e:
        print(f"GUI indisponible (flet manquant ? pip install 'flet>=0.24,<0.30'): {e}",
              file=sys.stderr)
        return 1
    run()
    return 0


def _force_utf8_stdout() -> None:
    """Evite les UnicodeEncodeError sur consoles Windows cp1252 (accents tags/noms)."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf:
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def main(argv=None) -> int:
    _force_utf8_stdout()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
