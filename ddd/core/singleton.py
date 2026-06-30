"""Garde-fou single-instance : empeche un 2e lancement de DDD d'ouvrir une 2e fenetre.

Pourquoi : au cold-start, le .exe deplie Python + numpy/onnxruntime + le client Flet, et
rien ne s'affiche pendant plusieurs secondes. L'utilisateur re-double-clique par reflexe,
une 2e instance demarre, et son sldl ne peut pas binder le port 50300 -> ListenException
(cf. `soulseek._fatal_message`), en plus de la 2e fenetre. Un verrou pose DES le demarrage
rend ce 2e clic inoffensif. Le splash (pyi_splash) reduit la tentation ; ce verrou est le
filet de securite.

Windows : mutex nomme (gere par le kernel, relache automatiquement a la mort du process).
POSIX   : lock file exclusif via `flock` (relache automatiquement aussi).
Fail-open : si poser le verrou echoue (API absente, droits), on renvoie True - on ne bloque
JAMAIS un lancement legitime a cause du garde-fou.
"""

from __future__ import annotations

import os
import platform

# Garde le handle (Windows) / fd (POSIX) vivant tant que le process tourne : si on le
# laissait etre garbage-collecte, le verrou tomberait et le garde-fou ne servirait a rien.
_held = None


def acquire(name: str = "DDD") -> bool:
    """True si on est la 1ere instance (verrou pose), False si une autre tourne deja.

    Idempotent dans un meme process : un 2e appel (ex. entry.py puis gui.run()) renvoie
    True sans re-verrouiller, donc l'instance legitime ne se prend jamais pour un doublon.
    """
    global _held
    if _held is not None:
        return True
    if platform.system() == "Windows":
        return _acquire_windows(name)
    return _acquire_posix(name)


def _acquire_windows(name: str) -> bool:
    global _held
    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        # 'Local\\' = namespace de la session de login -> une instance par utilisateur connecte.
        handle = kernel32.CreateMutexW(None, 0, f"Local\\{name}_singleton")
        err = ctypes.get_last_error()
        if not handle:
            return True                       # echec de creation -> on ne bloque pas
        if err == ERROR_ALREADY_EXISTS:
            return False                      # une autre instance tient deja le mutex
        _held = handle
        return True
    except Exception:  # noqa: BLE001
        return True


def _acquire_posix(name: str) -> bool:
    global _held
    try:
        import fcntl
        from .. import paths
        lock_dir = paths.data_base()
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_dir / f"{name}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False                      # verrou deja tenu par une autre instance
        _held = fd
        return True
    except Exception:  # noqa: BLE001
        return True


def focus_existing(title: str = "DDD - DigDigDig") -> None:
    """Best-effort (Windows) : remet la fenetre DDD deja ouverte au premier plan.

    Peut echouer si la 1ere instance est encore en train de demarrer (fenetre pas encore
    creee) -> on ignore en silence. Le but premier reste de ne PAS lancer un 2e exemplaire ;
    ramener l'existant au premier plan n'est qu'un bonus.
    """
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)        # SW_RESTORE (sort d'un etat minimise)
            user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
