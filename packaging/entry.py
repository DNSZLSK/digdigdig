"""Point d'entree du .exe empaquete : lance la fenetre native DDD.

Separe du package pour que PyInstaller ait un script racine clair. Au runtime
(gele), ddd/paths.py bascule les chemins vers les bons emplacements.
"""

from ddd.core import singleton   # stdlib only -> import tres leger (avant numpy/Flet)


def _close_splash() -> None:
    try:
        import pyi_splash   # present uniquement dans le .exe avec splash (Windows/Linux)
        pyi_splash.close()
    except Exception:       # noqa: BLE001
        pass


if __name__ == "__main__":
    # Garde-fou single-instance AVANT les imports lourds : un 2e double-clic (reflexe
    # frequent pendant le demarrage lent) ne doit pas ouvrir une 2e fenetre ni un 2e
    # sldl (collision port 50300). On pose le verrou des le depart.
    if not singleton.acquire("DDD"):
        singleton.focus_existing()
        _close_splash()
        raise SystemExit(0)
    from ddd.gui import run
    run()
