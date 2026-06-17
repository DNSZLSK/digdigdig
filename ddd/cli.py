"""CLI DDD : scan / upgrade / sort / rename / buy / scrape / acquire / import / config / gui."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from . import paths
from .core import quality as quality_mod
from .core.quality import LOSSLESS, HQ, DOUTEUX, MAUVAIS
from .core import audit as audit_mod
from .core import config as config_mod
from .core.scan import (
    SCAN_RECORD_FIELDS, duplicate_groups, scan_library, write_csv, write_json,
)
from .core import upgrade as upgrade_mod
from .core import rename as rename_mod
from .core import organize as organize_mod
from .core import genre as genre_mod
from .core import stores as stores_mod
from .core import soulseek

# Ordre d'affichage du resume (du plus actionnable au moins)
VERDICT_ORDER = [LOSSLESS, HQ, DOUTEUX, MAUVAIS, "ERROR", "SKIPPED"]
VERDICT_LABEL = {
    LOSSLESS: "lossless (full spectrum)",
    HQ: "HQ (club-playable, >=18 kHz)",
    DOUTEUX: "iffy (16-18 kHz)",
    MAUVAIS: "bad (<16 kHz)",
    "ERROR": "analysis error",
    "SKIPPED": "skipped",
}

# Statuts de nommage a remonter (on n'affiche pas OK / NAME_ONLY = sans interet)
NAME_PROBLEM_ORDER = [audit_mod.TAG_MISMATCH, audit_mod.VERSION_MISMATCH, audit_mod.UNPARSEABLE]
NAME_PROBLEM_LABEL = {
    audit_mod.TAG_MISMATCH: "tags don't match the name",
    audit_mod.VERSION_MISMATCH: "name version != tag version",
    audit_mod.UNPARSEABLE: "name without 'Artist - Title'",
}


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _cmd_scan(args: argparse.Namespace) -> int:
    from collections import Counter

    folder = Path(args.folder)
    if not folder.exists():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 2

    def progress(i: int, total: int, f: Path) -> None:
        if args.verbose or i == total or i % 25 == 0:
            print(f"  [{i}/{total}] {f.name}", file=sys.stderr)

    print(f"Scanning {folder} ...", file=sys.stderr)
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
    print(f"{len(records)} audio files scanned\n")

    print("QUALITY")
    for verdict in VERDICT_ORDER:
        n = qsummary.get(verdict, 0)
        if n:
            print(f"  {verdict:<14} {n:>5}   {VERDICT_LABEL.get(verdict, '')}")

    name_problems = sum(nsummary.get(s, 0) for s in NAME_PROBLEM_ORDER)
    if name_problems:
        print("\nNAMING / TAGS")
        for status in NAME_PROBLEM_ORDER:
            n = nsummary.get(status, 0)
            if n:
                print(f"  {status:<18} {n:>5}   {NAME_PROBLEM_LABEL.get(status, '')}")

    if dup_groups:
        dup_files = sum(len(g) for g in dup_groups)
        wasted = sum(g[0].size_bytes * (len(g) - 1) for g in dup_groups)
        print(f"\nDUPLICATES: {len(dup_groups)} groups, {dup_files} files "
              f"(~{_human_size(wasted)} recoverable)")
        for g in dup_groups[:10]:
            print(f"  x{len(g)}  {_human_size(g[0].size_bytes)}")
            for r in g:
                print(f"        {r.quality.path}")
        if len(dup_groups) > 10:
            print(f"  ... +{len(dup_groups) - 10} more groups (see the CSV)")

    # Detail qualite a problemes (faux lossless en tete)
    preset = quality_mod.preset_from_config()
    flagged = [r for r in records if r.quality.verdict not in ("SKIPPED", "ERROR")
               and not quality_mod.is_accepted(r.quality, preset)]
    if flagged:
        print(f"\n  --- {len(flagged)} to check/replace ---")
        for r in sorted(flagged, key=lambda r: (r.quality.verdict, r.quality.cutoff_hz)):
            print(f"  {r.quality.verdict:<14} cutoff {r.quality.cutoff_hz:>7.0f} Hz  {r.quality.filename}")

    print(f"\nReport: {out_csv}")
    print(f"          {out_json}")
    return 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.exists():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 2

    root = paths.resource_base()
    staging = Path(args.staging) if args.staging else paths.cache_dl_dir()
    dl_dir = Path(args.download_dir) if args.download_dir else paths.download_dir(config_mod.load())
    log_path = paths.logs_dir() / "ddd_upgrade.log"

    preset = args.preset if getattr(args, "preset", None) else quality_mod.preset_from_config()

    def progress(*a) -> None:
        if len(a) == 3 and not args.verbose:
            i, total, f = a
            if i == total or i % 25 == 0:
                print(f"  scan [{i}/{total}] {Path(f).name}", file=sys.stderr)
        elif len(a) == 1:
            print(a[0], file=sys.stderr)        # ligne sldl
        elif args.verbose and len(a) == 3:
            print(f"  scan [{a[0]}/{a[1]}] {Path(a[2]).name}", file=sys.stderr)

    print(f"Upgrade of {folder}  ->  library {dl_dir}", file=sys.stderr)
    print("(real lossless added to the library; fake sources sent to trash)",
          file=sys.stderr)
    outcomes = upgrade_mod.run_upgrade(
        folder, root=root, staging_dir=staging, download_dir=dl_dir,
        preset=preset, exclude_names=args.exclude,
        limit=args.limit, profile=args.profile, progress=progress, log_path=log_path,
    )

    from collections import Counter
    counts = Counter(o.action for o in outcomes)
    print(f"\n=== Upgrade: {folder.name} ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")

    replaced = [o for o in outcomes if o.action in (upgrade_mod.ACT_REPLACED, upgrade_mod.ACT_WOULD_REPLACE)]
    if replaced:
        print(f"\n  --- {len(replaced)} AUTHENTIC upgrade(s) ---")
        for o in replaced:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")

    out_csv = paths.outputs_dir() / f"upgrade_{folder.name}.csv"
    _write_outcomes_csv(outcomes, out_csv)
    print(f"\nReport: {out_csv}")

    # Introuvables -> page de liens d'achat (helper commun a tous les points d'entree)
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), folder.name)
    if buy_html:
        n = sum(1 for o in outcomes if o.action == upgrade_mod.ACT_NOT_FOUND and o.title)
        print(f"{n} not found -> buy links: {buy_html}")
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


def _cmd_rename(args: argparse.Namespace) -> int:
    from collections import Counter

    folder = Path(args.folder)
    if not folder.exists():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 2

    rep = rename_mod.rename_folder(
        folder, apply=args.apply, dedup=args.dedup,
        exclude=args.exclude, outputs_dir=paths.outputs_dir(),
    )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"\n=== Rename: {folder.name} ===  [{mode}]")
    counts = Counter(o.action for o in rep.ops)
    for action in (rename_mod.REN, rename_mod.OK, rename_mod.SKIP, rename_mod.DUP):
        if counts.get(action):
            print(f"  {action:<5} {counts[action]:>4}")

    if rep.dups:
        nred = sum(len(g.redundant) for g in rep.dups)
        verb = "deleted" if (args.apply and args.dedup) else "to delete (add --dedup)"
        print(f"\nDUPLICATES: {len(rep.dups)} groups, {nred} copies {verb} "
              f"(~{_human_size(rep.wasted_bytes)})")

    renamed = rep.of(rename_mod.REN)
    if renamed:
        tag = "RENAMED" if args.apply else "WOULD RENAME"
        print(f"\n  --- {len(renamed)} {tag} ---")
        for o in renamed:
            print(f"  {Path(o.src).name}")
            print(f"     -> {Path(o.dst).name}   [{o.source}]")

    skipped = rep.of(rename_mod.SKIP)
    if skipped:
        print(f"\n  --- {len(skipped)} left as-is (low-confidence) ---")
        for o in skipped[:20]:
            print(f"  {Path(o.src).name}   ({o.reason})")
        if len(skipped) > 20:
            print(f"  ... +{len(skipped) - 20} more")

    if args.verbose:
        for o in rep.of(rename_mod.OK):
            print(f"  OK    {Path(o.src).name}")

    if rep.log_path:
        print(f"\nLog: {rep.log_path}")
    if not args.apply:
        print("\n(dry-run) Re-run with --apply to write; add --dedup to delete copies.")
    return 0


def _cmd_sort(args: argparse.Namespace) -> int:
    from collections import Counter

    # Destination = l'arbre DDD : --library -> config library_root -> bibliotheque download_dir
    # -> defaut ~/Music/DDD. Le dossier source (args.folder) est ce qu'on RANGE, pas la cible.
    library = (args.library or config_mod.get("library_root")
               or config_mod.get("download_dir") or str(paths.default_download_dir()))
    library = Path(library)
    folder = Path(args.folder) if args.folder else library
    if not folder.exists():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 2

    mapping = config_mod.get("genre_mapping") or organize_mod.DEFAULT_GENRE_MAPPING
    sources = args.source or config_mod.get("sort_sources") or list(genre_mod.DEFAULT_SOURCES)

    def progress(i: int, total: int, f: Path) -> None:
        if args.verbose or i == total or i % 25 == 0:
            print(f"  [{i}/{total}] {Path(f).name}", file=sys.stderr)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"Sort {folder}  ->  library {library}   [{mode}]", file=sys.stderr)
    print("(genre lookup: Discogs/MusicBrainz; loose tracks only; unmatched -> _INBOX)",
          file=sys.stderr)
    rep = organize_mod.sort_folder(
        folder, library_root=library, apply=args.apply, mapping=mapping,
        sources=sources, token=args.token or "", route_inbox=args.inbox,
        init_tree=args.init_tree, limit=args.limit, cache_dir=paths.genre_cache_dir(),
        outputs_dir=paths.outputs_dir(), progress=progress,
    )

    counts = Counter(o.action for o in rep.ops)
    print(f"\n=== Sort: {folder.name} ===  [{mode}]")
    for action in (organize_mod.MOVE, organize_mod.INBOX_ACT, organize_mod.SKIP, organize_mod.ERROR):
        if counts.get(action):
            print(f"  {action:<6} {counts[action]:>4}")

    moved = rep.of(organize_mod.MOVE)
    if moved:
        tag = "FILED" if args.apply else "WOULD FILE"
        print(f"\n  --- {len(moved)} {tag} by folder ---")
        for fol, n in Counter(o.folder for o in moved).most_common():
            print(f"  {fol:<16} {n:>4}")

    if args.verbose:
        for o in moved:
            print(f"  {o.folder:<16} {Path(o.src).name}   [{o.styles}]")
        for o in rep.of(organize_mod.INBOX_ACT):
            print(f"  {'_INBOX':<16} {Path(o.src).name}   [{o.styles or 'no genre found'}]")

    if rep.log_path:
        print(f"\nLog: {rep.log_path}")
    if not args.apply:
        print("\n(dry-run) Re-run with --apply to move the files.")
    return 0


def _cmd_buy(args: argparse.Namespace) -> int:
    import csv

    src = Path(args.source)
    if not src.exists():
        print(f"source not found: {src}", file=sys.stderr)
        return 2

    tracks = []
    if src.is_dir():
        from .core.scan import iter_audio_files
        from .core.naming import resolve_name
        for f in iter_audio_files(src, args.exclude):
            r = resolve_name(f)
            if r.title:
                tracks.append((r.artist, r.title))
        name = src.name
    else:
        with open(src, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if rows and "action" in rows[0]:              # rapport d'upgrade -> que les NOT_FOUND
            tracks = [(r.get("artist", ""), r.get("title", ""))
                      for r in rows if r.get("action") == upgrade_mod.ACT_NOT_FOUND]
        else:                                         # want-list -> toutes les lignes
            def _g(r, *keys):
                for k in keys:
                    if r.get(k):
                        return r[k]
                return ""
            tracks = [(_g(r, "Artist", "artist"), _g(r, "Title", "title")) for r in rows]
        name = src.stem

    tracks = [(a, t) for a, t in tracks if t]
    if not tracks:
        print("no usable track in the source.", file=sys.stderr)
        return 0

    out_html = paths.outputs_dir() / f"buy_{name}.html"
    out_csv = paths.outputs_dir() / f"buy_{name}.csv"
    stores_mod.write_buy_page(tracks, out_html, out_csv, heading=f"To buy - {name}")
    print(f"{len(tracks)} track(s) -> {out_html}")
    print(f"            {out_csv}")
    return 0


def _acquire_rows_now(rows) -> int:
    """Lance directement l'acquire sur des rows (hand-off `scrape --acquire`)."""
    from collections import Counter
    root = paths.resource_base()
    dl_dir = paths.download_dir(config_mod.load())
    log_path = paths.logs_dir() / "ddd_acquire.log"

    def progress(*a) -> None:
        if len(a) == 1:
            print(a[0], file=sys.stderr)

    print(f"\nStarting acquire on {len(rows)} tracks -> {dl_dir}", file=sys.stderr)
    outcomes = upgrade_mod.acquire_rows(
        rows, root=root, download_dir=dl_dir, staging_dir=paths.cache_dl_dir(),
        limit=0, profile="lossless-strict", progress=progress, log_path=log_path)
    counts = Counter(o.action for o in outcomes)
    print("\n=== Acquire ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), "acquire")
    if buy_html:
        print(f"\nNot found -> buy links: {buy_html}")
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    from .core import scrapers
    import csv

    source = args.source
    if source not in scrapers.SOURCES:
        print(f"unknown source: {source} (available: {', '.join(scrapers.SOURCES)})", file=sys.stderr)
        return 2

    # --acquire telecharge via Soulseek : verifie les creds AVANT de scraper, sinon on
    # scrape une longue liste (djset/playlist) pour echouer juste apres faute de compte.
    if getattr(args, "acquire", False):
        try:
            soulseek.read_soulseek_creds()
        except soulseek.SoulseekError as e:
            print(f"\n{e}", file=sys.stderr)
            return 1

    def progress(msg: str) -> None:
        print(f"  {msg}", file=sys.stderr)

    try:
        if source == "discogs":
            rows = scrapers.scrape_discogs(
                args.username, token=args.token or "",
                include_collection=args.include_collection, progress=progress)
        elif source == "djset":
            rows = scrapers.scrape_djset(args.username, progress=progress)
        else:
            rows = scrapers.scrape_bandcamp(
                args.username, expand_albums=not args.no_expand_albums, progress=progress)
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.out:
        out = Path(args.out)
    else:                                  # l'URL d'un djset casserait le nom de fichier
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.username)[-40:]
        out = paths.outputs_dir() / f"{source}_{safe}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=scrapers.ROW_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\n=== Scrape {source}: {args.username} ===")
    print(f"{len(rows)} tracks -> {out}")
    if getattr(args, "acquire", False) and rows:
        return _acquire_rows_now(rows)
    if not args.no_acquire and rows:
        print("\n(to download these tracks in real lossless: "
              f"ddd acquire \"{out}\")")
    return 0


def _cmd_acquire(args: argparse.Namespace) -> int:
    """Telecharge une want-list (CSV scrape) en vrai lossless vers un inbox."""
    import csv
    from collections import Counter

    src = Path(args.csv)
    if not src.exists():
        print(f"CSV not found: {src}", file=sys.stderr)
        return 2

    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("empty CSV", file=sys.stderr)
        return 1

    root = paths.resource_base()
    dl_dir = Path(args.download_dir) if args.download_dir else paths.download_dir(config_mod.load())
    log_path = paths.logs_dir() / "ddd_acquire.log"

    def progress(*a) -> None:
        if len(a) == 1:
            print(a[0], file=sys.stderr)

    print(f"Acquire {len(rows)} tracks -> library {dl_dir}", file=sys.stderr)
    outcomes = upgrade_mod.acquire_rows(
        rows, root=root, download_dir=dl_dir, staging_dir=paths.cache_dl_dir(),
        limit=args.limit, profile=args.profile, progress=progress, log_path=log_path)

    counts = Counter(o.action for o in outcomes)
    print(f"\n=== Acquire: {src.name} ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")
    acquired = [o for o in outcomes if o.action == upgrade_mod.ACT_ACQUIRED]
    if acquired:
        print(f"\n  --- {len(acquired)} AUTHENTIC track(s) in the library ---")
        for o in acquired:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), src.stem)
    if buy_html:
        print(f"\nNot found -> buy links: {buy_html}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    """Migre un dossier dans la bibliotheque : lossless -> download_dir, reste -> corbeille."""
    src = Path(args.folder)
    if not src.exists():
        print(f"folder not found: {src}", file=sys.stderr)
        return 2
    dl_dir = Path(args.download_dir) if args.download_dir else paths.download_dir(config_mod.load())

    def progress(i: int, total: int, f: Path) -> None:
        if args.verbose or i == total or i % 25 == 0:
            print(f"  [{i}/{total}] {Path(f).name}", file=sys.stderr)

    print(f"Import of {src}  ->  library {dl_dir}", file=sys.stderr)
    stats = upgrade_mod.import_folder(src, dl_dir, exclude_names=args.exclude, progress=progress)
    print(f"\n=== Import: {src.name} ===")
    print(f"  {stats['kept']:>5}  real lossless moved to the library")
    print(f"  {stats['duplicates']:>5}  duplicates (already in library) -> trash")
    print(f"  {stats['trashed']:>5}  non-lossless -> trash")
    print(f"  {stats['total']:>5}  files scanned in total")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    if args.action == "show":
        cfg = config_mod.load()
        print(f"Config: {config_mod.config_path()}")
        if not cfg:
            print("  (empty)")
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

    p_scan = sub.add_parser("scan", help="scan a folder's quality (real lossless or not)")
    p_scan.add_argument("folder", help="folder to scan")
    p_scan.add_argument("-o", "--out", help="CSV report path (default: outputs/scan_<folder>.csv)")
    p_scan.add_argument("-x", "--exclude", action="append", default=[],
                        metavar="NAME", help="subfolder name to ignore (repeatable, e.g. -x PROD)")
    p_scan.add_argument("-v", "--verbose", action="store_true", help="show each file")
    p_scan.set_defaults(func=_cmd_scan)

    p_up = sub.add_parser("upgrade",
                          help="find real lossless on Soulseek for flagged files")
    p_up.add_argument("folder", help="folder to upgrade")
    p_up.add_argument("--download-dir", help="target lossless library (default: config / ~/Music/DDD)")
    p_up.add_argument("-x", "--exclude", action="append", default=["PROD"],
                      metavar="NAME", help="subfolder to ignore (default: PROD). Repeatable.")
    p_up.add_argument("--preset", choices=["dj_club", "audiophile", "puriste"],
                      help="quality bar (default: config, dj_club)")
    p_up.add_argument("-n", "--limit", type=int, default=0,
                      help="limit to N tracks (smoke test)")
    p_up.add_argument("--staging", help="transient download cache (default: .cache-dl)")
    p_up.add_argument("--profile", default="lossless-strict",
                      help="sldl profile (default: lossless-strict = FLAC only)")
    p_up.add_argument("-v", "--verbose", action="store_true", help="show each scanned file")
    p_up.set_defaults(func=_cmd_upgrade)

    p_sc = sub.add_parser("scrape", help="scrape favorites/tracklists -> CSV want-list (-> acquire)")
    p_sc.add_argument("source", choices=["discogs", "bandcamp", "djset"], help="source to scrape")
    p_sc.add_argument("username", metavar="USER_OR_URL",
                      help="username (discogs/bandcamp) OR URL (djset: YouTube set/channel/playlist, 1001Tracklists)")
    p_sc.add_argument("-o", "--out", help="output CSV (default: outputs/<source>_<user>.csv)")
    p_sc.add_argument("--token", help="Discogs token (else $DISCOGS_TOKEN or ddd config)")
    p_sc.add_argument("--include-collection", action="store_true", help="Discogs: also the collection")
    p_sc.add_argument("--no-expand-albums", action="store_true", help="Bandcamp: keep whole albums")
    p_sc.add_argument("--acquire", action="store_true",
                      help="chain straight into acquire (downloads the tracks found)")
    p_sc.add_argument("--no-acquire", action="store_true", help="don't suggest the acquire step")
    p_sc.set_defaults(func=_cmd_scrape)

    p_ac = sub.add_parser("acquire", help="download a CSV want-list in real lossless (library)")
    p_ac.add_argument("csv", help="CSV want-list (scrape output)")
    p_ac.add_argument("--download-dir", help="target library (default: config / ~/Music/DDD)")
    p_ac.add_argument("-n", "--limit", type=int, default=0, help="limit to N tracks")
    p_ac.add_argument("--profile", default="lossless-strict", help="sldl profile")
    p_ac.set_defaults(func=_cmd_acquire)

    p_im = sub.add_parser("import",
                          help="migrate a folder into the library (lossless kept, the rest trashed)")
    p_im.add_argument("folder", help="folder to import/sort")
    p_im.add_argument("--download-dir", help="target library (default: config / ~/Music/DDD)")
    p_im.add_argument("-x", "--exclude", action="append", default=[], metavar="NAME",
                      help="subfolder to ignore (repeatable)")
    p_im.add_argument("-v", "--verbose", action="store_true", help="show each file")
    p_im.set_defaults(func=_cmd_import)

    p_ren = sub.add_parser("rename",
                           help="rename a folder to 'Artist - Title' (from name + tags)")
    p_ren.add_argument("folder", help="folder to rename")
    p_ren.add_argument("--apply", action="store_true",
                       help="write the renames (default: dry-run, nothing touched)")
    p_ren.add_argument("--dedup", action="store_true",
                       help="send byte-identical copies to trash (keep 1)")
    p_ren.add_argument("-x", "--exclude", action="append", default=[], metavar="NAME",
                       help="subfolder to ignore (repeatable)")
    p_ren.add_argument("-v", "--verbose", action="store_true", help="also show files already clean")
    p_ren.set_defaults(func=_cmd_rename)

    p_sort = sub.add_parser("sort",
                            help="auto-file loose tracks into your vibe folders (genre lookup)")
    p_sort.add_argument("folder", nargs="?",
                        help="folder of loose tracks to sort (default: the library root)")
    p_sort.add_argument("--apply", action="store_true",
                        help="move the files (default: dry-run, nothing touched)")
    p_sort.add_argument("--library", help="library root where the vibe folders live (else config)")
    p_sort.add_argument("--no-inbox", dest="inbox", action="store_false",
                        help="leave unmatched tracks in place instead of routing to _INBOX")
    p_sort.add_argument("--source", action="append", choices=["discogs", "musicbrainz"],
                        default=[], metavar="SRC",
                        help="lookup source(s), in order (repeatable; default: discogs, musicbrainz)")
    p_sort.add_argument("--init-tree", action="store_true",
                        help="create the vibe folders (+ _INBOX) under the library root first")
    p_sort.add_argument("--token", help="Discogs token (else $DISCOGS_TOKEN or ddd config)")
    p_sort.add_argument("-n", "--limit", type=int, default=0,
                        help="limit to N tracks (smoke test)")
    p_sort.add_argument("-v", "--verbose", action="store_true",
                        help="show each file + its styles")
    p_sort.set_defaults(func=_cmd_sort)

    p_buy = sub.add_parser("buy",
                           help="buy links (Discogs + Bandcamp) for tracks not found")
    p_buy.add_argument("source", help="folder, upgrade report CSV, or want-list CSV")
    p_buy.add_argument("-x", "--exclude", action="append", default=[], metavar="NAME",
                       help="subfolder to ignore (if source = folder)")
    p_buy.set_defaults(func=_cmd_buy)

    p_cfg = sub.add_parser("config", help="manage config (creds, target)")
    p_cfg.add_argument("action", choices=["show", "set"])
    p_cfg.add_argument("key", nargs="?", help="key (e.g. discogs_token)")
    p_cfg.add_argument("value", nargs="?", help="value")
    p_cfg.set_defaults(func=_cmd_config)

    p_gui = sub.add_parser("gui", help="launch the native window (Flet)")
    p_gui.set_defaults(func=_cmd_gui)
    return parser


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import run
    except ImportError as e:
        print(f"GUI unavailable (flet missing? pip install 'flet>=0.24,<0.30'): {e}",
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
    try:
        return args.func(args)
    except soulseek.SoulseekError as e:
        # No Soulseek account/creds, sldl missing, login refused... -> clear message, no traceback.
        print(f"\n{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
