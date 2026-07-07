"""Smoke test GUI : construit tout l'arbre de controles sans lancer de fenetre.

On passe un faux Page (stub) qui enregistre add/overlay/update. Les workers ne sont
declenches que sur clic, donc la construction seule n'ouvre aucune connexion reseau.
Ce test attrape les erreurs d'API Flet (kwargs/enums invalides) a la construction.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class StubPage:
    def __init__(self):
        self.controls = []
        self.overlay = []
        self.title = None
        self.theme_mode = None
        self.padding = None
        self.window_width = None
        self.window_height = None

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def run_thread(self, fn):
        pass


def main():
    import flet  # noqa: F401  (verifie que flet est installe)
    from ddd import gui

    page = StubPage()
    gui.main(page)

    assert page.controls, "aucun controle ajoute a la page"
    assert page.title and "DDD" in page.title
    assert len(page.overlay) == 3, \
        "les 3 FilePicker (dossier + inbox + identify) doivent etre dans overlay"
    assert callable(gui.run)

    # _fmt_eta : formatage de l'ETA du feedback upgrade (status "batch i/N · ~ETA left")
    assert gui._fmt_eta(0) == "0s"
    assert gui._fmt_eta(45) == "45s"
    assert gui._fmt_eta(90) == "1m"
    assert gui._fmt_eta(3600) == "1h00"
    assert gui._fmt_eta(4920) == "1h22"
    assert gui._fmt_eta(-5) == "0s", "un ETA negatif est clampe a 0"

    # la legende couvre CHAQUE statut reellement rendu (pas de trou : verdict/phase/action)
    for v in (gui.quality.LOSSLESS, gui.quality.HQ, gui.quality.DOUTEUX, gui.quality.MAUVAIS):
        assert v in gui.VERDICT_HELP, f"verdict {v} sans explication dans la legende"
    for act in gui.ACTION_LABEL:
        assert act in gui.ACTION_HELP, f"action {act} sans explication dans la legende"

    print(f"OK - GUI construite : {len(page.controls)} controles racine, "
          f"overlay={len(page.overlay)}, titre={page.title!r}")


if __name__ == "__main__":
    main()
