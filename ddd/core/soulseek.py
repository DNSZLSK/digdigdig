"""Wrapper autour de sldl : creds, construction du CSV d'entree, run, lecture index.

Encapsule le binaire `bin/sldl/sldl.exe` (batch Soulseek download). Reutilise le
profil `lossless` de config/sldl.conf. Les creds sont lues depuis la config slskd
locale (pas de duplication), comme le faisait pipeline.ps1.
"""

from __future__ import annotations

import csv
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Colonnes que sldl auto-detecte dans un CSV d'entree
INPUT_FIELDS = ["Artist", "Title", "Length"]


def _no_window_kwargs() -> dict:
    """kwargs Popen/run pour ne PAS faire surgir de console noire sous Windows.

    sldl et taskkill sont des process console : sans ce flag ils heritent (ou
    ouvrent) une fenetre cmd qui clignote devant la GUI. Sur Mac/Linux : no-op.
    """
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


@dataclass
class WantItem:
    artist: str
    title: str
    length: Optional[int]      # secondes (aide length-tol de sldl), None si inconnu
    origin_path: str           # fichier a remplacer (le faux lossless)


@dataclass
class DownloadResult:
    artist: str
    title: str
    filepath: str              # vide si sldl n'a rien telecharge
    length: Optional[int]
    state: str
    failure_reason: str

    @property
    def downloaded(self) -> bool:
        return bool(self.filepath) and Path(self.filepath).exists()


class SoulseekError(RuntimeError):
    pass


# Lignes de sortie sldl qui signalent un echec FATAL (pas un simple miss de piste) :
# on stoppe net et on remonte un message clair au lieu de cracher la trace .NET.
_FATAL_MARKERS = (
    "Failed to start listening",
    "the IP and/or port may be in use",
    "Unhandled exception",
    "Login failed definitively",
    "Failed to ensure Soulseek connection",
    "Failed to initialize Soulseek client",
)


def _fatal_message(line: str) -> str:
    if "listening" in line or "port may be in use" in line:
        return ("Soulseek port 50300 already in use (an sldl/slskd is already running). "
                "Close it and try again.")
    if "Login failed" in line or "login failed" in line:
        return ("Soulseek login refused: invalid credentials, or another session "
                "(slskd) is already connected with this account.")
    return f"Soulseek error (sldl): {line.strip()[:120]}"


def default_sldl_exe(root: Path = None) -> Path:
    """Chemin du binaire sldl (resolution frozen-aware ; `root` ignore, garde pour compat)."""
    from .. import paths
    return paths.sldl_exe()


def read_soulseek_creds() -> Dict[str, str]:
    """Lit user/pass Soulseek.

    Priorite : env DDD_SOULSEEK_USER/PASS -> config ddd (creds saisies dans l'app)
    -> config slskd locale (legacy). La config ddd vient avant slskd pour que les
    identifiants entres dans la fenetre / via `ddd config set` soient utilises.
    """
    user = os.environ.get("DDD_SOULSEEK_USER")
    pwd = os.environ.get("DDD_SOULSEEK_PASS")
    if user and pwd:
        return {"user": user, "pass": pwd}

    try:
        from . import config as _config
        cfg = _config.load()
        cu, cp = cfg.get("soulseek_user"), cfg.get("soulseek_pass")
        if cu and cp:
            return {"user": cu, "pass": cp}
    except Exception:  # noqa: BLE001
        pass

    yml = _slskd_config_path()
    if not yml or not yml.exists():
        raise SoulseekError(
            "Soulseek account required: you need a (free) Soulseek login to download. "
            "Set it in the app (Settings), via `ddd config set soulseek_user/soulseek_pass`, "
            "DDD_SOULSEEK_USER/PASS, or install slskd. No account yet? -> slsknet.org"
        )
    content = yml.read_text(encoding="utf-8", errors="ignore")
    mu = re.search(r"(?ms)^soulseek:\s*\n\s*username:\s*(\S+)", content)
    mp = re.search(r"(?ms)^soulseek:\s*\n\s*username:[^\n]+\n\s*password:\s*(\S+)", content)
    if not mu or not mp:
        raise SoulseekError(f"couldn't parse user/pass in {yml}")
    return {"user": mu.group(1), "pass": mp.group(1)}


def _slskd_config_path() -> Optional[Path]:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "slskd" / "slskd.yml" if base else None
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "slskd" / "slskd.yml"
    return Path.home() / ".local" / "share" / "slskd" / "slskd.yml"


def stop_slskd() -> bool:
    """Arrete slskd s'il tourne (Soulseek = un seul login par compte). True si stoppe."""
    try:
        if platform.system() == "Windows":
            r = subprocess.run(["taskkill", "/IM", "slskd.exe", "/F"],
                               capture_output=True, text=True, **_no_window_kwargs())
            return r.returncode == 0
        r = subprocess.run(["pkill", "-f", "slskd"], capture_output=True, text=True)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.debug("stop_slskd: %r", e)
        return False


def stop_orphan_sldl() -> bool:
    """Tue d'eventuels sldl.exe orphelins d'un run precedent.

    Un sldl reste parfois vivant (loggue, port d'ecoute Soulseek 50300 ouvert) apres
    une fermeture brutale de l'app. Le sldl suivant ne peut alors PAS binder le port
    -> 'Failed to start listening on 0.0.0.0:50300' -> crash. On nettoie avant de lancer.
    """
    try:
        if platform.system() == "Windows":
            r = subprocess.run(["taskkill", "/IM", "sldl.exe", "/F"],
                               capture_output=True, text=True, **_no_window_kwargs())
            return r.returncode == 0
        r = subprocess.run(["pkill", "-f", "sldl"], capture_output=True, text=True)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.debug("stop_orphan_sldl: %r", e)
        return False


def clear_run_staging(staging_dir, *csv_names) -> None:
    """Supprime les dossiers de travail sldl d'un run TERMINE (et leurs CSV d'entree).

    Ce qui reste dans `<staging>/<stem>/` apres un run fini est du dechet : les fichiers
    valides ont deja ete deplaces vers la bibliotheque, les rejetes envoyes a la corbeille
    -> il ne reste que des candidats orphelins (sldl en telecharge parfois plusieurs), des
    `.incomplete` et le `_index.csv`. Sans nettoyage, `.cache-dl` grossit sans fin.

    A n'appeler QUE sur un run NON annule : sur annulation/exception on garde tout pour
    permettre la reprise (sldl `skip-existing`) du run interrompu.
    """
    staging_dir = Path(staging_dir)
    for name in csv_names:
        shutil.rmtree(staging_dir / Path(name).stem, ignore_errors=True)
        try:
            (staging_dir / name).unlink(missing_ok=True)
        except OSError as e:
            logger.debug("clear_run_staging unlink %s: %r", name, e)


def write_input_csv(items: Sequence[WantItem], path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INPUT_FIELDS)
        writer.writeheader()
        for it in items:
            writer.writerow({
                "Artist": it.artist,
                "Title": it.title,
                "Length": it.length if it.length else "",
            })
    return path


def run_sldl(
    input_csv,
    staging_dir,
    *,
    root: Path,
    profile: str = "lossless",
    creds: Optional[Dict[str, str]] = None,
    limit: int = 0,
    sldl_exe: Optional[Path] = None,
    config_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
    on_line=None,
    on_proc=None,
) -> int:
    """Lance sldl en mode batch CSV. Retourne le code de sortie.

    Ne masque pas les erreurs reseau : c'est l'appelant qui decide quoi faire d'un
    code != 0 (souvent partiel : certaines pistes trouvees, d'autres non).
    """
    input_csv = Path(input_csv)
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    from .. import paths
    sldl_exe = Path(sldl_exe) if sldl_exe else paths.sldl_exe()
    config_path = Path(config_path) if config_path else paths.sldl_config()
    if not sldl_exe.exists():
        raise SoulseekError(f"sldl not found: {sldl_exe}")
    creds = creds or read_soulseek_creds()

    args = [
        str(sldl_exe), str(input_csv),
        "--input-type", "csv",
        "--user", creds["user"],
        "--pass", creds["pass"],
        "--config", str(config_path),
        "--profile", profile,
        "--path", str(staging_dir),
    ]
    if limit > 0:
        args += ["-n", str(limit)]

    safe = " ".join(a if a != creds["pass"] else "***" for a in args)
    logger.info("sldl: %s", safe)

    log_fh = open(log_path, "a", encoding="utf-8") if log_path else None
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                **_no_window_kwargs())
        if on_proc:
            on_proc(proc)   # remet le handle a l'appelant (bouton Annuler -> proc.terminate())
        fatal_line = None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if on_line:
                on_line(line)
            else:
                print(line, file=sys.stderr)
            if log_fh:
                log_fh.write(line + "\n")
            if fatal_line is None and any(m in line for m in _FATAL_MARKERS):
                fatal_line = line   # 1ere ligne d'echec fatal (port/login/crash)
        proc.wait()
        if fatal_line is not None:
            raise SoulseekError(_fatal_message(fatal_line))
        return proc.returncode
    finally:
        if log_fh:
            log_fh.close()


def index_path_for(input_csv, staging_dir) -> Path:
    """sldl ecrit son index sous <staging>/<stem-du-csv>/_index.csv."""
    return Path(staging_dir) / Path(input_csv).stem / "_index.csv"


def read_index(index_csv) -> List[DownloadResult]:
    """Lit le _index.csv de sldl (filepath,artist,title,length,state,failurereason)."""
    index_csv = Path(index_csv)
    if not index_csv.exists():
        return []
    out: List[DownloadResult] = []
    with open(index_csv, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            length = row.get("length") or ""
            try:
                length_i = int(float(length)) if length else None
            except ValueError:
                length_i = None
            out.append(DownloadResult(
                artist=row.get("artist", ""),
                title=row.get("title", ""),
                filepath=row.get("filepath", "") or "",
                length=length_i,
                state=row.get("state", ""),
                failure_reason=row.get("failurereason", ""),
            ))
    return out
