"""Liens d'achat pour les tracks introuvables sur Soulseek.

Construit des URLs de recherche **Discogs** (marketplace vinyle - le plus susceptible
d'avoir les vieux house obscurs) et **Bandcamp** (digital), et ecrit une page HTML
cliquable (1 bloc par track, boutons Discogs/Bandcamp) + un CSV. Liens de recherche
purs : zero auth, zero reseau. (L'enrichissement Discogs prix/dispo via le token reste
une evolution facile, non codee ici.)
"""

from __future__ import annotations

import csv
from html import escape as _esc
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus

from .naming import search_title

try:                                  # logo DDD embarque (data-URI WebP), degrade si absent
    from ._logo import LOGO_DATA_URI as _LOGO
except Exception:  # noqa: BLE001
    _LOGO = ""

DISCOGS = "discogs"
BANDCAMP = "bandcamp"
STORES = (DISCOGS, BANDCAMP)
_LABEL = {DISCOGS: "Discogs", BANDCAMP: "Bandcamp"}

Track = Tuple[str, str]   # (artist, title)


def _query(artist: str, title: str) -> str:
    """Requete de recherche : titre nettoye de ses [label, annee] (search_title)."""
    return " ".join(p for p in (artist.strip(), search_title(title)) if p).strip()


def search_url(store: str, artist: str, title: str) -> str:
    q = quote_plus(_query(artist, title))
    if store == DISCOGS:
        return f"https://www.discogs.com/search/?q={q}&type=release"
    if store == BANDCAMP:
        return f"https://bandcamp.com/search?q={q}"
    raise ValueError(f"unknown store: {store}")


def links_for(artist: str, title: str) -> Dict[str, str]:
    return {s: search_url(s, artist, title) for s in STORES}


def _dedup(tracks: Sequence[Track]) -> List[Track]:
    """Dedup sur lower(artist) - lower(title), ordre preserve."""
    seen, out = set(), []
    for artist, title in tracks:
        if not title:
            continue
        key = (artist.lower().strip(), title.lower().strip())
        if key in seen:
            continue
        seen.add(key)
        out.append((artist, title))
    return out


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem; background: #1E1E1E; color: #D0D0D0;
       font: 15px/1.4 -apple-system, Segoe UI, Roboto, sans-serif; }
header { display: flex; align-items: center; gap: 1rem; margin-bottom: .3rem; }
header img { height: 50px; width: auto; display: block; }
h1 { margin: 0; font-size: 1.35rem; font-weight: 600; }
.sub { margin: .2rem 0 1.5rem; color: #9A9A9A; }
.list { display: flex; flex-direction: column; gap: .5rem; max-width: 820px; }
.card { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
        padding: .7rem 1rem; background: #252525; border: 1px solid #3A3A3A; border-radius: 10px; }
.meta { min-width: 0; }
.artist { font-weight: 600; color: #D0D0D0; }
.title { color: #9A9A9A; }
.artist:after { content: " - "; color: #6A6A6A; }
.btns { display: flex; gap: .5rem; flex: none; }
.btn { text-decoration: none; padding: .4rem .85rem; border-radius: 7px; font-weight: 600;
       font-size: .85rem; white-space: nowrap; }
.btn.discogs { background: #5C6B7A; color: #1E1E1E; }
.btn.bandcamp { background: transparent; color: #D0D0D0; border: 1px solid #5C6B7A; }
.btn:hover { filter: brightness(1.15); }
"""


def _card(artist: str, title: str, links: Dict[str, str]) -> str:
    a, t = _esc(artist or "?"), _esc(title or "")
    btns = "".join(
        f'<a class="btn {s}" href="{_esc(links[s])}" target="_blank" rel="noopener">{_LABEL[s]}</a>'
        for s in STORES
    )
    return (f'  <div class="card"><div class="meta">'
            f'<span class="artist">{a}</span><span class="title">{t}</span></div>'
            f'<div class="btns">{btns}</div></div>')


def write_buy_page(tracks: Sequence[Track], out_html, out_csv,
                   heading: str = "Not found - buy") -> Tuple[Path, Path]:
    """Ecrit la page HTML cliquable + le CSV. Retourne (html, csv). Dedup automatique."""
    items = _dedup(tracks)
    out_html, out_csv = Path(out_html), Path(out_csv)
    out_html.parent.mkdir(parents=True, exist_ok=True)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["artist", "title", "discogs", "bandcamp"])
        for artist, title in items:
            li = links_for(artist, title)
            w.writerow([artist, title, li[DISCOGS], li[BANDCAMP]])

    cards = "\n".join(_card(a, t, links_for(a, t)) for a, t in items)
    sub = f"{len(items)} track(s) to buy - Discogs (vinyl/marketplace) + Bandcamp (digital)"
    logo = f'<img src="{_LOGO}" alt="DDD - DigDigDig">' if _LOGO else ""
    html = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_esc(heading)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<header>{logo}<h1>{_esc(heading)}</h1></header>\n"
        f"<p class=\"sub\">{_esc(sub)}</p>\n"
        f"<div class=\"list\">\n{cards}\n</div>\n</body>\n</html>\n"
    )
    out_html.write_text(html, encoding="utf-8")
    return out_html, out_csv


def write_unfindable(outcomes, outputs_dir, name: str) -> Optional[Path]:
    """Depuis des UpgradeOutcome, ecrit la page des introuvables (action == NOT_FOUND).

    Helper UNIQUE appele par tous les points d'entree (CLI upgrade/acquire, workers GUI)
    pour ne pas dupliquer le filtre + l'ecriture. Renvoie le chemin HTML, ou None si aucun
    introuvable. Import lazy de la constante pour eviter un cycle stores <-> upgrade.
    """
    from .upgrade import ACT_NOT_FOUND
    tracks = [(o.artist, o.title) for o in outcomes
              if getattr(o, "action", "") == ACT_NOT_FOUND and getattr(o, "title", "")]
    if not tracks:
        return None
    out_dir = Path(outputs_dir)
    html, _ = write_buy_page(tracks, out_dir / f"unfindable_{name}.html",
                             out_dir / f"unfindable_{name}.csv",
                             heading=f"Not found - {name}")
    return html
