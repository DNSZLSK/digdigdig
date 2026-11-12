"""Le message de statut est rattache a son onglet : celui de Sort ne doit plus
s'afficher sur Identify/Library, et il revient quand on retourne sur Sort."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

flet = pytest.importorskip("flet")

from test_gui_build import StubPage  # noqa: E402


def _walk(c):
    yield c
    for attr in ("controls", "content"):
        v = getattr(c, attr, None)
        if isinstance(v, list):
            for x in v:
                yield from _walk(x)
        elif v is not None and hasattr(v, "_get_control_name"):
            yield from _walk(v)


def _tab(page, name):
    for root in page.controls:
        for c in _walk(root):
            if isinstance(c, flet.Container) and getattr(getattr(c, "content", None), "value", None) == name:
                return c
    raise LookupError(name)


def test_status_message_follows_its_tab():
    from ddd import gui

    page = StubPage()
    gui.main(page)
    status = page.controls[2]          # page.add(main_view, progress, status, ...)
    assert isinstance(status, flet.Text)

    _tab(page, "Sort by genre").on_click(None)
    status.value = "Preview: 16 would be filed"

    _tab(page, "Identify").on_click(None)
    assert status.value == ""

    _tab(page, "Sort by genre").on_click(None)
    assert status.value == "Preview: 16 would be filed"
