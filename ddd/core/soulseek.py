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
import socket
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


def _extract_sldl_reason(lines: Sequence[str]) -> str:
    """Tire la VRAIE raison de l'echec depuis la cascade sldl.

    sldl loggue en cascade : une ligne generique ('Login failed definitively') PUIS la
    raison reelle ('Failed to ensure ... : <reason>') PUIS l'exception interne
    ('---> Soulseek.XxxException: <reason>'). On veut cette derniere, la plus precise :
    c'est elle qui dit si c'est un port occupe (ListenException) ou un login refuse.
    """
    for ln in lines:
        m = re.search(r"Soulseek\.\w+Exception:\s*(.+)$", ln)
        if m:
            return m.group(1).strip()
    for key in ("Failed to ensure Soulseek connection and login:",
                "Failed to initialize Soulseek client:"):
        for ln in lines:
            if key in ln:
                tail = ln.split(key, 1)[1].strip()
                pre = "Soulseek login failed:"   # prefixe redondant a virer si present
                return tail[len(pre):].strip() if tail.startswith(pre) else tail
    return ""


def _fatal_message(line: str, context: Optional[Sequence[str]] = None) -> str:
    """Traduit l'echec fatal sldl en message clair, en montrant la VRAIE raison.

    `context` = les lignes de la cascade autour de l'echec, pas seulement la 1ere ligne
    generique ('Login failed definitively') : c'est plus bas que sldl ecrit la cause
    reelle (port occupe vs creds refuses). On ne devine plus 'slskd' au pif, on lit.
    """
    lines = list(context) if context else [line]
    reason = _extract_sldl_reason(lines) or line.strip()
    reason_short = reason[:160]
    low = " \n".join(lines).lower()

    # Port d'ecoute 50300 occupe (ListenException) : un autre Soulseek/sldl tient le port.
    if "listenexception" in low or "start listening" in low or "port may be in use" in low:
        return ("Soulseek can't open its listen port. DDD already auto-tries several "
                "ports, so this is unusual: a port is either held by another Soulseek app "
                "(sldl/slskd or a previous DDD run - check the tray and Task Manager) or "
                "reserved by the OS (Hyper-V/WSL/Docker; check with 'netsh int ipv4 show "
                f"excludedportrange protocol=tcp'). [sldl: {reason_short}]")

    # Login refuse par le serveur : identifiants faux.
    if "loginrejected" in low or "rejected the login" in low or "invalid username or password" in low:
        return ("Soulseek refused the login: wrong username or password. Re-check your "
                "credentials in Settings (no typo, no stray space), or create a fresh "
                f"account in a Soulseek client and use those. [sldl: {reason_short}]")

    # Echec de login, raison non reconnue : on montre la vraie ligne sldl, sans inventer.
    if "login failed" in low or "login" in low:
        return ("Soulseek login failed. Usually wrong username/password, or the same "
                "account is logged in from another Soulseek client (one login per "
                f"account - quit it and retry). [sldl: {reason_short}]")

    return f"Soulseek error (sldl): {reason_short}"


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


# --- Choix du port d'ecoute Soulseek -----------------------------------------
# sldl/slskd ecoutent par defaut sur 50300, qui tombe dans la plage ephemere Windows
# (49152-65535) que Hyper-V/WSL/Docker RESERVENT par blocs. Le bind y echoue alors
# (WSAEACCES) avec 'Failed to start listening', meme si netstat ne montre rien. On
# bind-teste avant de lancer sldl et on retombe sur un port libre SOUS la plage
# ephemere, passe a sldl via --listen-port (la CLI override le config) -> plus besoin
# de faire editer le .conf a la main (ce qui debloquait le testeur Windows).
DEFAULT_LISTEN_PORT = 50300
_LISTEN_PORT_FALLBACKS = (21300, 31300, 41300, 2234, 11733)


def _port_bindable(port: int) -> bool:
    """True si un nouveau socket d'ecoute peut s'ouvrir sur ce port maintenant.

    Bind sur 0.0.0.0 SANS SO_REUSEADDR, exactement comme sldl : si ce bind passe, celui
    de sldl passera ; s'il echoue (port pris OU reserve par l'OS), sldl echouerait pareil.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_listen_port(preferred: int = DEFAULT_LISTEN_PORT) -> int:
    """Port d'ecoute bindable pour sldl : le prefere s'il marche, sinon un repli.

    Essaie `preferred`, puis des ports fixes sous la plage ephemere (a l'abri des
    reservations Hyper-V/WSL/Docker), puis en dernier recours un port libre attribue
    par l'OS. Evite l'echec 'Failed to start listening' sans rien faire editer a l'user.
    """
    if _port_bindable(preferred):
        return preferred
    for p in _LISTEN_PORT_FALLBACKS:
        if p != preferred and _port_bindable(p):
            return p
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", 0))   # 0 -> l'OS attribue un port libre (forcement bindable la)
        return s.getsockname()[1]
    finally:
        s.close()


def _configured_listen_port(config_path, default: int = DEFAULT_LISTEN_PORT) -> int:
    """Lit `listen-port = N` du config sldl (respecte un port choisi a la main)."""
    try:
        txt = Path(config_path).read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"(?m)^\s*listen-port\s*=\s*(\d+)", txt)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return default


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
    # Port d'ecoute : auto-pick d'un port bindable (anti 50300 reserve par l'OS) que
    # l'on impose via --listen-port (override le listen-port du config).
    preferred_port = _configured_listen_port(config_path)
    listen_port = pick_listen_port(preferred_port)
    args += ["--listen-port", str(listen_port)]
    if listen_port != preferred_port:
        logger.info("listen-port %s indisponible (occupe ou reserve par l'OS) -> bascule sur %s",
                    preferred_port, listen_port)
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
        fatal_context: List[str] = []   # la cascade autour de l'echec (vraie raison dedans)
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
            # Une fois l'echec repere, on collecte les lignes de raison qui suivent (sldl
            # ecrit la cause reelle APRES la ligne generique), en sautant la stack .NET.
            if fatal_line is not None and len(fatal_context) < 8:
                stripped = line.lstrip()
                if stripped and not stripped.startswith("at ") and "--- End of inner" not in line:
                    fatal_context.append(line)
        proc.wait()
        if fatal_line is not None:
            raise SoulseekError(_fatal_message(fatal_line, fatal_context))
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
