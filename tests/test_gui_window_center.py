"""Garde-fou : la fenetre se centre au demarrage (comme le splash), pas en haut-gauche.

Le splash PyInstaller est centre ; la fenetre Flet, elle, s'ouvrait a la position OS
par defaut (haut-gauche) -> saut visuel. `_center_window` doit appeler le centrage
quand l'API existe, et ne jamais lever quand elle est absente.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd import gui


class _RecordingWindow:
    def __init__(self):
        self.centered = 0

    def center(self):
        self.centered += 1


class _PageWithWindow:
    def __init__(self):
        self.window = _RecordingWindow()


class _PageNoCenterApi:
    """Ni page.window ni page.window_center : aucune API de centrage."""


def test_center_window_calls_window_center():
    page = _PageWithWindow()
    gui._center_window(page)
    assert page.window.centered == 1, "window.center() doit etre appele une fois"


def test_center_window_is_graceful_without_api():
    gui._center_window(_PageNoCenterApi())   # ne doit pas lever


def main():
    test_center_window_calls_window_center()
    test_center_window_is_graceful_without_api()
    print("OK - fenetre centree au demarrage (window.center() appele), degrade sans API")


if __name__ == "__main__":
    main()
