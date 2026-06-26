"""Verification de mise a jour : compare la version locale a la derniere Release GitHub.

NOTIFICATION seulement, pas d'auto-update : le .exe est package one-folder (un dossier
verrouille pendant qu'il tourne, on ne se remplace pas soi-meme proprement sous Windows).
Tout echoue en SILENCE : hors-ligne, rate-limit, JSON casse -> on ne montre rien, jamais
de blocage ni d'exception au lancement.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

REPO = "DNSZLSK/digdigdig"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def parse_version(s: str) -> Tuple[int, ...]:
    """'v0.2.10' / '0.2.10-beta' -> (0, 2, 10).

    Garde les segments numeriques de tete, s'arrete au 1er non-numerique (pre-release
    suffixe ignore). () si rien d'exploitable -> compare alors comme la plus petite."""
    if not s:
        return ()
    s = s.strip().lstrip("vV")
    out = []
    for part in s.split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        out.append(int(num))
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    """True si `latest` est STRICTEMENT plus recent que `current`.

    Comparaison NUMERIQUE par tuples (0.2.10 > 0.2.9), pas lexicale ('0.2.10' < '0.2.9')."""
    lv, cv = parse_version(latest), parse_version(current)
    if not lv:
        return False
    return lv > cv


def latest_tag(timeout: float = 4.0) -> Optional[str]:
    """Tag de la derniere Release GitHub ('v0.2.9'), ou None si indisponible (silencieux).

    GitHub exige un User-Agent ; appel anonyme (60 req/h/IP, large pour 1 check/lancement).
    `timeout` borne l'appel : sur reseau mort, on rend la main vite (pas de thread zombie)."""
    try:
        import requests
        r = requests.get(
            API_LATEST, timeout=timeout,
            headers={"User-Agent": "ddd-update-check",
                     "Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        tag = (r.json() or {}).get("tag_name")
        return tag or None
    except Exception as e:  # noqa: BLE001
        logger.debug("update check failed: %r", e)
        return None


def check_for_update(current_version: str, timeout: float = 4.0) -> Optional[str]:
    """Tag de la nouvelle version si une Release plus recente existe, sinon None.

    Fail-silent de bout en bout : ne leve jamais, ne bloque jamais."""
    tag = latest_tag(timeout=timeout)
    if tag and is_newer(tag, current_version):
        return tag
    return None
