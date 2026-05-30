"""Point d'entree du .exe empaquete : lance la fenetre native DDD.

Separe du package pour que PyInstaller ait un script racine clair. Au runtime
(gele), ddd/paths.py bascule les chemins vers les bons emplacements.
"""

from ddd.gui import run

if __name__ == "__main__":
    run()
