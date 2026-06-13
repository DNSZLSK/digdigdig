"""CLI DDD : `ddd scan <dossier>` (et, a venir, scrape / upgrade / deploy)."""

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
from .core import stores as stores_mod

# Ordre d'affichage du resume (du plus actionnable au moins)
VERDICT_ORDER = [LOSSLESS, HQ, DOUTEUX, MAUVAIS, "ERROR", "SKIPPED"]
VERDICT_LABEL = {
    LOSSLESS: "lossless (plein spectre)",
    HQ: "HQ (jouable club, >=18 kHz)",
    DOUTEUX: "douteux (16-18 kHz)",
    MAUVAIS: "mauvais (<16 kHz)",
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
    preset = quality_mod.preset_from_config()
    flagged = [r for r in records if r.quality.verdict not in ("SKIPPED", "ERROR")
               and not quality_mod.is_accepted(r.quality, preset)]
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

    print(f"Upgrade de {folder}  ->  bibliotheque {dl_dir}", file=sys.stderr)
    print("(vrais lossless deposes dans la bibliotheque ; faux sources envoyes a la corbeille)",
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
        print(f"\n  --- {len(replaced)} upgrade(s) AUTHENTIQUE(s) ---")
        for o in replaced:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")

    out_csv = paths.outputs_dir() / f"upgrade_{folder.name}.csv"
    _write_outcomes_csv(outcomes, out_csv)
    print(f"\nRapport : {out_csv}")

    # Introuvables -> page de liens d'achat (helper commun a tous les points d'entree)
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), folder.name)
    if buy_html:
        n = sum(1 for o in outcomes if o.action == upgrade_mod.ACT_NOT_FOUND and o.title)
        print(f"{n} introuvable(s) -> liens d'achat : {buy_html}")
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
        print(f"dossier introuvable: {folder}", file=sys.stderr)
        return 2

    rep = rename_mod.rename_folder(
        folder, apply=args.apply, dedup=args.dedup,
        exclude=args.exclude, outputs_dir=paths.outputs_dir(),
    )

    mode = "APPLIQUE" if args.apply else "DRY-RUN"
    print(f"\n=== Rename: {folder.name} ===  [{mode}]")
    counts = Counter(o.action for o in rep.ops)
    for action in (rename_mod.REN, rename_mod.OK, rename_mod.SKIP, rename_mod.DUP):
        if counts.get(action):
            print(f"  {action:<5} {counts[action]:>4}")

    if rep.dups:
        nred = sum(len(g.redundant) for g in rep.dups)
        verb = "supprimees" if (args.apply and args.dedup) else "a supprimer (ajoute --dedup)"
        print(f"\nDOUBLONS : {len(rep.dups)} groupes, {nred} copies {verb} "
              f"(~{_human_size(rep.wasted_bytes)})")

    renamed = rep.of(rename_mod.REN)
    if renamed:
        tag = "RENOMME" if args.apply else "RENOMMERAIT"
        print(f"\n  --- {len(renamed)} {tag} ---")
        for o in renamed:
            print(f"  {Path(o.src).name}")
            print(f"     -> {Path(o.dst).name}   [{o.source}]")

    skipped = rep.of(rename_mod.SKIP)
    if skipped:
        print(f"\n  --- {len(skipped)} laisse(s) tel(s) quel(s) (resolution peu fiable) ---")
        for o in skipped[:20]:
            print(f"  {Path(o.src).name}   ({o.reason})")
        if len(skipped) > 20:
            print(f"  ... +{len(skipped) - 20} autres")

    if args.verbose:
        for o in rep.of(rename_mod.OK):
            print(f"  OK    {Path(o.src).name}")

    if rep.log_path:
        print(f"\nJournal : {rep.log_path}")
    if not args.apply:
        print("\n(dry-run) Relance avec --apply pour ecrire ; ajoute --dedup pour supprimer les copies.")
    return 0


def _cmd_buy(args: argparse.Namespace) -> int:
    import csv

    src = Path(args.source)
    if not src.exists():
        print(f"source introuvable: {src}", file=sys.stderr)
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
        print("aucune track exploitable dans la source.", file=sys.stderr)
        return 0

    out_html = paths.outputs_dir() / f"buy_{name}.html"
    out_csv = paths.outputs_dir() / f"buy_{name}.csv"
    stores_mod.write_buy_page(tracks, out_html, out_csv, heading=f"A acheter - {name}")
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

    print(f"\nLancement acquire sur {len(rows)} pistes -> {dl_dir}", file=sys.stderr)
    outcomes = upgrade_mod.acquire_rows(
        rows, root=root, download_dir=dl_dir, staging_dir=paths.cache_dl_dir(),
        limit=0, profile="lossless-strict", progress=progress, log_path=log_path)
    counts = Counter(o.action for o in outcomes)
    print("\n=== Acquire ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), "acquire")
    if buy_html:
        print(f"\nIntrouvables -> liens d'achat : {buy_html}")
    return 0


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
        elif source == "djset":
            rows = scrapers.scrape_djset(args.username, progress=progress)
        else:
            rows = scrapers.scrape_bandcamp(
                args.username, expand_albums=not args.no_expand_albums, progress=progress)
    except (ValueError, RuntimeError) as e:
        print(f"ERREUR: {e}", file=sys.stderr)
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
    print(f"{len(rows)} pistes -> {out}")
    if getattr(args, "acquire", False) and rows:
        return _acquire_rows_now(rows)
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
    dl_dir = Path(args.download_dir) if args.download_dir else paths.download_dir(config_mod.load())
    log_path = paths.logs_dir() / "ddd_acquire.log"

    def progress(*a) -> None:
        if len(a) == 1:
            print(a[0], file=sys.stderr)

    print(f"Acquire {len(rows)} pistes -> bibliotheque {dl_dir}", file=sys.stderr)
    outcomes = upgrade_mod.acquire_rows(
        rows, root=root, download_dir=dl_dir, staging_dir=paths.cache_dl_dir(),
        limit=args.limit, profile=args.profile, progress=progress, log_path=log_path)

    counts = Counter(o.action for o in outcomes)
    print(f"\n=== Acquire: {src.name} ===")
    for action, n in counts.most_common():
        print(f"  {action:<16} {n:>5}")
    acquired = [o for o in outcomes if o.action == upgrade_mod.ACT_ACQUIRED]
    if acquired:
        print(f"\n  --- {len(acquired)} piste(s) AUTHENTIQUE(s) dans la bibliotheque ---")
        for o in acquired:
            print(f"  {o.artist} - {o.title}  cutoff {o.new_cutoff_hz:.0f} Hz")
    buy_html = stores_mod.write_unfindable(outcomes, paths.outputs_dir(), src.stem)
    if buy_html:
        print(f"\nIntrouvables -> liens d'achat : {buy_html}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    """Migre un dossier dans la bibliotheque : lossless -> download_dir, reste -> corbeille."""
    src = Path(args.folder)
    if not src.exists():
        print(f"dossier introuvable: {src}", file=sys.stderr)
        return 2
    dl_dir = Path(args.download_dir) if args.download_dir else paths.download_dir(config_mod.load())

    def progress(i: int, total: int, f: Path) -> None:
        if args.verbose or i == total or i % 25 == 0:
            print(f"  [{i}/{total}] {Path(f).name}", file=sys.stderr)

    print(f"Import de {src}  ->  bibliotheque {dl_dir}", file=sys.stderr)
    stats = upgrade_mod.import_folder(src, dl_dir, exclude_names=args.exclude, progress=progress)
    print(f"\n=== Import: {src.name} ===")
    print(f"  {stats['kept']:>5}  vrais lossless deplaces dans la bibliotheque")
    print(f"  {stats['duplicates']:>5}  doublons (deja dans la bibliotheque) -> corbeille")
    print(f"  {stats['trashed']:>5}  non-lossless -> corbeille")
    print(f"  {stats['total']:>5}  fichiers scannes au total")
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
    p_up.add_argument("--download-dir", help="bibliotheque lossless cible (defaut: config / ~/Music/DDD)")
    p_up.add_argument("-x", "--exclude", action="append", default=["PROD"],
                      metavar="NOM", help="sous-dossier a ignorer (defaut: PROD). Repetable.")
    p_up.add_argument("--preset", choices=["dj_club", "audiophile", "puriste"],
                      help="seuil qualite (defaut: config, dj_club)")
    p_up.add_argument("-n", "--limit", type=int, default=0,
                      help="limiter a N pistes (smoke test)")
    p_up.add_argument("--staging", help="cache transitoire de download (defaut: .cache-dl)")
    p_up.add_argument("--profile", default="lossless-strict",
                      help="profil sldl (defaut: lossless-strict = FLAC uniquement)")
    p_up.add_argument("-v", "--verbose", action="store_true", help="afficher chaque fichier scanne")
    p_up.set_defaults(func=_cmd_upgrade)

    p_sc = sub.add_parser("scrape", help="scraper favoris/tracklists -> CSV want-list (-> acquire)")
    p_sc.add_argument("source", choices=["discogs", "bandcamp", "djset"], help="source a scraper")
    p_sc.add_argument("username", metavar="USER_OU_URL",
                      help="pseudo (discogs/bandcamp) OU url du set (djset: YouTube/1001Tracklists)")
    p_sc.add_argument("-o", "--out", help="CSV de sortie (defaut: outputs/<source>_<user>.csv)")
    p_sc.add_argument("--token", help="token Discogs (sinon $DISCOGS_TOKEN ou config ddd)")
    p_sc.add_argument("--include-collection", action="store_true", help="Discogs: aussi la collection")
    p_sc.add_argument("--no-expand-albums", action="store_true", help="Bandcamp: garder les albums entiers")
    p_sc.add_argument("--acquire", action="store_true",
                      help="enchainer direct sur l'acquire (telecharge les tracks trouvees)")
    p_sc.add_argument("--no-acquire", action="store_true", help="ne pas suggerer l'etape acquire")
    p_sc.set_defaults(func=_cmd_scrape)

    p_ac = sub.add_parser("acquire", help="telecharger une want-list CSV en vrai lossless (bibliotheque)")
    p_ac.add_argument("csv", help="CSV want-list (sortie de scrape)")
    p_ac.add_argument("--download-dir", help="bibliotheque cible (defaut: config / ~/Music/DDD)")
    p_ac.add_argument("-n", "--limit", type=int, default=0, help="limiter a N pistes")
    p_ac.add_argument("--profile", default="lossless-strict", help="profil sldl")
    p_ac.set_defaults(func=_cmd_acquire)

    p_im = sub.add_parser("import",
                          help="migrer un dossier dans la bibliotheque (lossless garde, reste corbeille)")
    p_im.add_argument("folder", help="dossier a importer/trier")
    p_im.add_argument("--download-dir", help="bibliotheque cible (defaut: config / ~/Music/DDD)")
    p_im.add_argument("-x", "--exclude", action="append", default=[], metavar="NOM",
                      help="sous-dossier a ignorer (repetable)")
    p_im.add_argument("-v", "--verbose", action="store_true", help="afficher chaque fichier")
    p_im.set_defaults(func=_cmd_import)

    p_ren = sub.add_parser("rename",
                           help="renommer un dossier en 'Artiste - Titre' (depuis nom + tags)")
    p_ren.add_argument("folder", help="dossier a renommer")
    p_ren.add_argument("--apply", action="store_true",
                       help="ecrire les renommages (defaut: dry-run, rien n'est touche)")
    p_ren.add_argument("--dedup", action="store_true",
                       help="envoyer les copies byte-identiques a la corbeille (garde 1 exemplaire)")
    p_ren.add_argument("-x", "--exclude", action="append", default=[], metavar="NOM",
                       help="sous-dossier a ignorer (repetable)")
    p_ren.add_argument("-v", "--verbose", action="store_true", help="afficher aussi les fichiers deja propres")
    p_ren.set_defaults(func=_cmd_rename)

    p_buy = sub.add_parser("buy",
                           help="liens d'achat (Discogs + Bandcamp) pour des tracks introuvables")
    p_buy.add_argument("source", help="dossier, rapport upgrade CSV, ou want-list CSV")
    p_buy.add_argument("-x", "--exclude", action="append", default=[], metavar="NOM",
                       help="sous-dossier a ignorer (si source = dossier)")
    p_buy.set_defaults(func=_cmd_buy)

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
