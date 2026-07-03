"""Identification par empreinte acoustique : retrouve 'Artiste - Titre' d'un fichier
au nom perdu (YH1, YH2, track01...) via son EMPREINTE, pas son spectre.

Le detecteur qualite (quality.py / detect.py) juge la QUALITE (coupure HF, aliasing) ;
il ne dira jamais QUEL morceau c'est. Pour ca il faut une empreinte perceptuelle
(principe Shazam) comparee a une base qui, elle, porte deja le titre : ici
**Chromaprint** (binaire `fpcalc`, empreinte locale <100 ms) + **AcoustID** (service
gratuit adosse a MusicBrainz). Chaque match renvoie aussi le MBID MusicBrainz, donc le
resultat s'enchaine sur la plomberie MB existante (`genre.py`) et sur `acquire`
(via `to_want_rows`) : un YHx pourri redevient une piste nommee ET un vrai lossless.

Limite honnete : la couverture depend de ce qui est reference dans AcoustID/MusicBrainz.
Catalogue commercial -> bon taux ; bootlegs/edits/white-labels jamais soumis ailleurs ->
introuvables, ce n'est pas la technique qui lache mais l'absence de reference. On ne
renomme JAMAIS a l'aveugle : dry-run par defaut, `--apply` ecrit, et seuls les matchs
au-dessus du seuil de confiance sont appliques (le reste est montre pour revue manuelle).

Empreinte mise en cache disque (1 JSON par empreinte, miss inclus) -> un re-run ne
retape pas AcoustID. Une erreur reseau n'est jamais cachee en negatif (cf genre.py).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import requests

from .. import paths
from . import config
from . import naming
from .rename import _sanitize, _unique_dest
from .scan import iter_audio_files
from .tokenize import (core_title_tokens, get_tokens, loose_title_tokens,
                       remove_diacritics, version_key)

logger = logging.getLogger(__name__)

# --- AcoustID / Chromaprint --------------------------------------------------
ACOUSTID_API = "https://api.acoustid.org/v2/lookup"
ACOUSTID_META = "recordings"     # -> chaque recording porte title + artists (+ mbid)
ACOUSTID_PAUSE_S = 0.34          # ~3 req/s : respect du rate-limit AcoustID en batch
FP_LENGTH_S = 120                # duree analysee par fpcalc (defaut AcoustID) -> empreinte stable
CACHE_VERSION = 1                # bump si la requete/parsing change -> invalide l'ancien cache

# Cle d'application AcoustID (gratuite, acoustid.org/new-application). Bakee ici -> `ddd
# identify` marche out-of-the-box pour tout le monde, sans reglage. Une cle *applicative*
# AcoustID est faite pour etre embarquee dans un client (comme Picard/beets) : ce n'est PAS
# un secret user (la cle user, pour les soumissions, est distincte). Surcharge possible par
# env ACOUSTID_API_KEY ou `ddd config set acoustid_api_key`.
DEFAULT_ACOUSTID_KEY = "NEClaspw7R"

# Seuil de confiance d'auto-application (score AcoustID 0..1). >= seuil -> MATCH (renomme sur
# --apply) ; en dessous mais avec un candidat -> LOW_CONFIDENCE (montre pour revue, jamais
# applique). 0.9 (conservateur) : sur un test reel un faux positif (piste acid -> chapitre
# d'audiobook) scorait 0.85 la ou un vrai match scorait 0.99 -> 0.9 recale le faux en
# LOW_CONFIDENCE sans perdre le vrai. Rename = destructif -> mieux vaut rater que renommer faux.
MIN_SCORE = 0.9

# Statuts d'identification
MATCH = "MATCH"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NO_MATCH = "NO_MATCH"
ERROR = "ERROR"


class IdentifyError(RuntimeError):
    """Erreur d'identification (base)."""


class FpcalcError(IdentifyError):
    """Echec du calcul d'empreinte (binaire fpcalc manquant, fichier illisible)."""


class AcoustidError(IdentifyError):
    """Echec cote service AcoustID (reponse d'erreur, reseau)."""


class AcoustidAuthError(AcoustidError):
    """Cle API AcoustID absente/invalide : fatal, on stoppe tout le run."""


def _no_window_kwargs() -> dict:
    """kwargs subprocess pour ne pas faire clignoter de console noire sous Windows."""
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


@dataclass
class Candidate:
    score: float
    artist: str
    title: str
    recording_mbid: str = ""
    duration: Optional[int] = None

    def as_dict(self) -> dict:
        return {"score": self.score, "artist": self.artist, "title": self.title,
                "recording_mbid": self.recording_mbid, "duration": self.duration}

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(score=float(d.get("score") or 0.0), artist=d.get("artist", ""),
                   title=d.get("title", ""), recording_mbid=d.get("recording_mbid", ""),
                   duration=d.get("duration"))


@dataclass
class IdentifyResult:
    path: str
    status: str
    best: Optional[Candidate] = None
    candidates: List[Candidate] = field(default_factory=list)
    proposed_name: str = ""      # 'Artiste - Titre.ext' propose (dry-run ou avant apply)
    new_path: str = ""           # chemin apres renommage (apply reussi)
    applied: bool = False
    note: str = ""

    @property
    def filename(self) -> str:
        return Path(self.path).name


# --- Empreinte (fpcalc) ------------------------------------------------------

def fingerprint_file(path, *, length: int = FP_LENGTH_S, fpcalc=None):
    """Calcule (duration_s, fingerprint) d'un fichier via Chromaprint `fpcalc -json`.

    `fpcalc` = binaire pre-resolu (evite de re-resoudre par fichier). Leve FpcalcError
    si le binaire manque ou si le fichier est illisible (que l'appelant traite en ERROR).
    """
    exe = Path(fpcalc) if fpcalc else paths.fpcalc_exe()
    if not exe.exists():
        raise FpcalcError(
            f"fpcalc not found: {exe}. The Chromaprint fingerprint binary is missing "
            "from this build. Install fpcalc (acoustid.org/chromaprint) or rebuild DDD "
            "with it bundled under bin/fpcalc/.")
    args = [str(exe), "-json", "-length", str(length), str(path)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, **_no_window_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        raise FpcalcError(f"fpcalc failed on {Path(path).name}: {e}") from e
    if proc.returncode != 0:
        raise FpcalcError(f"fpcalc error on {Path(path).name}: "
                          f"{(proc.stderr or '').strip()[:200]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise FpcalcError(f"fpcalc gave no JSON for {Path(path).name}: {e}") from e
    fp = data.get("fingerprint") or ""
    dur = int(round(float(data.get("duration") or 0)))
    if not fp or dur <= 0:
        raise FpcalcError(f"fpcalc produced no usable fingerprint for {Path(path).name}")
    return dur, fp


# --- Requete AcoustID --------------------------------------------------------

def _parse_results(results: Sequence[dict]) -> List[Candidate]:
    """Aplati la reponse AcoustID en candidats (score du result porte sur chaque recording),
    tries par score decroissant. Ignore les results sans recording nomme."""
    cands: List[Candidate] = []
    for res in results:
        score = float(res.get("score") or 0.0)
        for rec in (res.get("recordings") or []):
            title = (rec.get("title") or "").strip()
            if not title:
                continue
            names = [a.get("name", "").strip() for a in (rec.get("artists") or [])]
            artist = ", ".join(n for n in names if n).strip()
            dur = rec.get("duration")
            cands.append(Candidate(
                score=score, artist=artist, title=title,
                recording_mbid=rec.get("id", "") or "",
                duration=int(dur) if dur else None))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def lookup(fingerprint: str, duration: int, *, api_key: str,
           meta: str = ACOUSTID_META, timeout: int = 30) -> List[Candidate]:
    """Interroge AcoustID (POST : les empreintes sont longues) -> liste de candidats.

    Leve AcoustidAuthError si la cle est invalide/absente (fatal), AcoustidError pour
    les autres erreurs du service, et laisse remonter requests.RequestException (reseau).
    """
    resp = requests.post(ACOUSTID_API, data={
        "client": api_key,
        "duration": str(int(duration)),
        "fingerprint": fingerprint,
        "meta": meta,
    }, timeout=timeout)
    # AcoustID renvoie un corps JSON d'erreur MEME en HTTP 400 (empreinte/cle invalide) ->
    # on parse le JSON AVANT raise_for_status. Sinon le HTTPError masque le code d'erreur
    # AcoustID (4 = cle invalide) et une mauvaise cle passerait pour une simple erreur reseau
    # (ERROR par fichier) au lieu d'abort le run avec un message clair (AcoustidAuthError).
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()          # pas de JSON -> remonte l'erreur HTTP brute
        raise AcoustidError(f"AcoustID: non-JSON response (HTTP {resp.status_code})")
    if data.get("status") != "ok":
        err = data.get("error") or {}
        msg = err.get("message", "unknown error")
        # code 4 = invalid API key (AcoustID). Message defensif au cas ou le code bouge.
        if err.get("code") == 4 or "api key" in msg.lower():
            raise AcoustidAuthError(f"AcoustID rejected the API key: {msg}")
        raise AcoustidError(f"AcoustID error: {msg}")
    return _parse_results(data.get("results") or [])


# --- Cache disque (1 fichier par empreinte, miss inclus) ---------------------

def _fp_key(fingerprint: str) -> str:
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _cache_file(cache_dir, key: str) -> Path:
    return Path(cache_dir) / f"{key}.json"


def _load_cache(cache_dir, key: str) -> Optional[List[Candidate]]:
    p = _cache_file(cache_dir, key)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if d.get("v") != CACHE_VERSION:
        return None
    return [Candidate.from_dict(c) for c in (d.get("candidates") or [])]


def _store_cache(cache_dir, key: str, cands: Sequence[Candidate]) -> None:
    p = _cache_file(cache_dir, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"v": CACHE_VERSION,
                                 "candidates": [c.as_dict() for c in cands]},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        logger.warning("acoustid cache write failed %s: %r", p, e)


# --- Orchestration -----------------------------------------------------------

def _classify(cands: Sequence[Candidate], min_score: float):
    """(status, best) depuis les candidats et le seuil (calcule a la LECTURE, pas cache :
    le seuil peut changer d'un run a l'autre)."""
    if not cands:
        return NO_MATCH, None
    best = cands[0]
    return (MATCH if best.score >= min_score else LOW_CONFIDENCE), best


def resolve_api_key(explicit: str = "") -> str:
    """Cle AcoustID : argument explicite -> env ACOUSTID_API_KEY -> config -> defaut bundle."""
    return (explicit or os.environ.get("ACOUSTID_API_KEY", "")
            or (config.get("acoustid_api_key", "") or "") or DEFAULT_ACOUSTID_KEY).strip()


def identify_file(path, *, api_key: str, cache_dir=None, min_score: float = MIN_SCORE,
                  length: int = FP_LENGTH_S, fpcalc=None, sleep: bool = True) -> IdentifyResult:
    """Identifie un fichier : empreinte -> cache -> AcoustID. Ne leve pas pour un fichier
    illisible (status ERROR) ; laisse remonter AcoustidAuthError (fatal pour le run)."""
    path = Path(path)
    try:
        duration, fp = fingerprint_file(path, length=length, fpcalc=fpcalc)
    except FpcalcError as e:
        return IdentifyResult(path=str(path), status=ERROR, note=str(e))

    key = _fp_key(fp)
    if cache_dir is not None:
        cached = _load_cache(cache_dir, key)
        if cached is not None:
            status, best = _classify(cached, min_score)
            return IdentifyResult(path=str(path), status=status, best=best, candidates=cached)

    try:
        cands = lookup(fp, duration, api_key=api_key)
    except AcoustidAuthError:
        raise                                     # cle invalide -> on stoppe tout le run
    except (requests.RequestException, AcoustidError) as e:
        # erreur reseau/service : NE PAS cacher (sinon un miss transitoire empoisonne le
        # cache et la piste n'est plus jamais retrouvee), status ERROR.
        return IdentifyResult(path=str(path), status=ERROR, note=f"lookup failed: {e}")

    if sleep:
        time.sleep(ACOUSTID_PAUSE_S)
    if cache_dir is not None:
        _store_cache(cache_dir, key, cands)
    status, best = _classify(cands, min_score)
    return IdentifyResult(path=str(path), status=status, best=best, candidates=cands)


def _apply_one(src: Path, dst: Path, cand: Candidate) -> bool:
    """Renomme src -> dst puis ecrit les tags artist/title (best-effort). True si renomme."""
    try:
        src.rename(dst)
    except OSError as e:
        logger.warning("identify rename failed %s -> %s: %r", src, dst, e)
        return False
    naming.write_tags(dst, artist=cand.artist, title=cand.title)   # best-effort
    return True


def identify_folder(folder, *, api_key: str, exclude_names: Sequence[str] = (),
                    cache_dir=None, min_score: float = MIN_SCORE, limit: int = 0,
                    apply: bool = False, outputs_dir=None, fpcalc=None,
                    cancel: Optional[Callable] = None,
                    progress: Optional[Callable] = None) -> List[IdentifyResult]:
    """Identifie tous les fichiers audio d'un dossier. Pour chaque MATCH sur du confiant,
    propose 'Artiste - Titre.ext' (anti-collision) et, si `apply`, renomme + tague. Les
    LOW_CONFIDENCE sont montres mais jamais appliques. Dry-run par defaut.

    Leve FpcalcError si le binaire d'empreinte manque (fatal, avant la boucle) et
    AcoustidAuthError si la cle est refusee (fatal). Retourne un resultat par fichier.
    """
    folder = Path(folder)
    exe = Path(fpcalc) if fpcalc else paths.fpcalc_exe()
    if not exe.exists():
        raise FpcalcError(
            f"fpcalc not found: {exe}. The Chromaprint fingerprint binary is missing "
            "from this build. Install fpcalc (acoustid.org/chromaprint) or rebuild DDD "
            "with it bundled under bin/fpcalc/.")

    files = list(iter_audio_files(folder, exclude_names))
    if limit > 0:
        files = files[:limit]

    results: List[IdentifyResult] = []
    reserved: set = set()
    for i, f in enumerate(files, 1):
        if cancel and cancel():
            break
        res = identify_file(f, api_key=api_key, cache_dir=cache_dir,
                            min_score=min_score, fpcalc=exe)
        if res.status == MATCH and res.best and res.best.artist and res.best.title:
            proposed = _sanitize(f"{res.best.artist} - {res.best.title}{f.suffix}")
            dst = f.with_name(proposed)
            if dst.name == f.name:                # deja bon nom -> juste (re)taguer
                res.proposed_name = f.name
                if apply:
                    naming.write_tags(f, artist=res.best.artist, title=res.best.title)
                    res.applied = True
            else:
                final = _unique_dest(dst, f, reserved)
                reserved.add(str(final).lower())
                res.proposed_name = final.name
                if apply:
                    res.applied = _apply_one(f, final, res.best)
                    if res.applied:
                        res.new_path = str(final)
        results.append(res)
        if progress:
            progress(i, len(files), f)

    if apply and outputs_dir is not None:
        _write_log(results, Path(outputs_dir) / f"identify_{folder.name}.csv")
    return results


def apply_selected(items, *, progress: Optional[Callable] = None):
    """Renomme + tague exactement la selection CONFIRMEE par l'user (GUI piste-par-piste).

    `items` = iterable de (path, artist, title). Anti-collision ENTRE les items (deux qui
    viseraient le meme nom -> suffixe ' (n)'). Retourne [(path, applied: bool, new_path), ...].
    C'est la voie sure : rien n'est renomme tant que l'user n'a pas coche et valide (la
    validation a montre qu'aucun seuil ne garantit la justesse -> la confirmation humaine
    est la surete).
    """
    items = list(items)
    reserved: set = set()
    out = []
    for i, (path, artist, title) in enumerate(items, 1):
        p = Path(path)
        cand = Candidate(0.0, artist, title)
        proposed = _sanitize(f"{artist} - {title}{p.suffix}")
        dst = p.with_name(proposed)
        if dst.name == p.name:                    # deja bon nom -> juste (re)taguer
            naming.write_tags(p, artist=artist, title=title)
            out.append((str(p), True, str(p)))
        else:
            final = _unique_dest(dst, p, reserved)
            reserved.add(str(final).lower())
            ok = _apply_one(p, final, cand)
            out.append((str(p), ok, str(final) if ok else str(p)))
        if progress:
            progress(i, len(items), path)
    return out


def to_want_rows(results: Sequence[IdentifyResult]) -> List[dict]:
    """Matchs confiants -> rows Artist/Title/Length pour `upgrade.acquire_rows` (recupere
    le vrai lossless de la piste enfin nommee). C'est la boucle : identifier puis acquerir."""
    rows: List[dict] = []
    for r in results:
        if r.status == MATCH and r.best and r.best.artist and r.best.title:
            rows.append({"Artist": r.best.artist, "Title": r.best.title,
                         "Length": r.best.duration or "", "Source": "identify"})
    return rows


# --- Calibration du seuil : la biblio deja nommee comme verite terrain --------
# Au lieu de deviner le seuil sur 2 exemples, on fait tourner identify (dry-run) sur des
# fichiers DEJA correctement nommes, on compare le candidat AcoustID au nom connu avec la
# MEME logique de tokens que `upgrade._reject_reason` (presence artiste + couverture noyau
# titre + version_key), et on mesure la precision REELLE par tranche de score. On etiquette
# en 3 classes, pas 2 : un rename est destructif, donc "bon morceau mauvaise version" (renommer
# un remix par le nom de l'original) est un risque distinct de "mauvais morceau".

LBL_CORRECT = "correct"          # artiste + titre + version concordent
LBL_DIFF_VERSION = "diff-version"  # meme artiste + titre, version differente (remix vs original)
LBL_WRONG = "wrong"              # mauvais morceau (artiste ou titre ne colle pas)
LBL_NO_MATCH = "no-match"        # AcoustID n'a rien rendu

# Tranches de score (haut -> bas) pour la precision par bande.
VALIDATE_BANDS = [(0.95, 1.01), (0.90, 0.95), (0.85, 0.90), (0.80, 0.85),
                  (0.75, 0.80), (0.70, 0.75), (0.0, 0.70)]
# Seuils testes en cumule ("j'accepte tout >= t") pour recommander --min-score.
VALIDATE_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


@dataclass
class ValidateRow:
    path: str
    known_artist: str
    known_title: str
    score: float
    cand_artist: str
    cand_title: str
    label: str


# Seuil de similarite floue (difflib) pour juger "meme morceau" entre le nom connu et le
# candidat AcoustID. Volontairement flou et PAS la logique stricte de l'upgrade : ici on
# ETIQUETTE la verite terrain (est-ce le bon morceau ?), donc il faut tolerer les vraies
# variantes d'orthographe (fichier "Uforic Undulance" vs MusicBrainz "Uforic Undulence" =
# MEME morceau, pas un faux). Un labeleur trop strict compterait des VRAIS matchs comme faux
# et fausserait la precision des tranches hautes -> seuil recommande trop haut, a l'envers du but.
_SAME_TRACK_RATIO = 0.85


def _norm(s: str) -> str:
    return " ".join(remove_diacritics(s or "").lower().split())


def _title_norm(title: str) -> str:
    """Titre reduit a son noyau (sans version/feat) pour la comparaison floue."""
    return " ".join(core_title_tokens(title) or loose_title_tokens(title))


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if (a and b) else 0.0


def _artist_ok(known_artist: str, cand_artist: str) -> bool:
    """Artiste : recouvrement de tokens (rapide) sinon similarite floue (typos, '&' vs 'and')."""
    kreq = get_tokens(known_artist)
    if not kreq:
        return True                          # rien de jugeable cote artiste -> ne bloque pas
    if any(t in set(get_tokens(cand_artist)) for t in kreq):
        return True
    return _ratio(_norm(known_artist), _norm(cand_artist)) >= _SAME_TRACK_RATIO


def _label_match(known_artist: str, known_title: str, cand: Optional[Candidate]) -> str:
    """Etiquette un candidat AcoustID face au nom connu (verite terrain). 3 classes + no-match.

    'Meme morceau' = artiste concordant ET titre-noyau flou-egal (tolere fautes/translitteration).
    Puis version_key departage correct (meme version) vs diff-version (remix vs original).
    """
    if cand is None:
        return LBL_NO_MATCH
    title_ok = _ratio(_title_norm(known_title), _title_norm(cand.title)) >= _SAME_TRACK_RATIO
    if not (_artist_ok(known_artist, cand.artist) and title_ok):
        return LBL_WRONG
    if version_key(known_title) == version_key(cand.title):
        return LBL_CORRECT
    return LBL_DIFF_VERSION


def validate_folder(folder, *, api_key: str, cache_dir=None, exclude_names: Sequence[str] = (),
                    sample: int = 0, fpcalc=None, outputs_dir=None,
                    progress: Optional[Callable] = None) -> List[ValidateRow]:
    """Calibre le seuil sur la verite terrain : ne prend que les fichiers dont le nom EXISTANT
    est fiable (`resolve_name` confident + artiste + titre), les identifie (cache-friendly),
    et etiquette chaque candidat AcoustID contre ce nom connu. Ne renomme RIEN. `sample` > 0 ->
    echantillon aleatoire (seed fixe -> re-run deterministe = gratuit via cache)."""
    folder = Path(folder)
    exe = Path(fpcalc) if fpcalc else paths.fpcalc_exe()
    if not exe.exists():
        raise FpcalcError(
            f"fpcalc not found: {exe}. Install it (acoustid.org/chromaprint) or bundle it "
            "under bin/fpcalc/.")

    truth = []
    for f in iter_audio_files(folder, exclude_names):
        r = naming.resolve_name(f)
        if r.confident and r.artist and r.title:      # verite terrain fiable seulement
            truth.append((f, r.artist, r.title))
    if sample and len(truth) > sample:
        import random
        truth = random.Random(0).sample(truth, sample)  # seed fixe -> echantillon stable

    rows: List[ValidateRow] = []
    for i, (f, ka, kt) in enumerate(truth, 1):
        res = identify_file(f, api_key=api_key, cache_dir=cache_dir, fpcalc=exe)
        best = res.best
        rows.append(ValidateRow(
            path=str(f), known_artist=ka, known_title=kt,
            score=best.score if best else 0.0,
            cand_artist=best.artist if best else "", cand_title=best.title if best else "",
            label=_label_match(ka, kt, best)))
        if progress:
            progress(i, len(truth), f)

    if outputs_dir is not None:
        _write_validate_csv(rows, Path(outputs_dir) / f"identify_validate_{folder.name}.csv")
    return rows


def summarize_validation(rows: Sequence[ValidateRow], *, target: float = 0.99, min_n: int = 20):
    """Precision par tranche + precision cumulee par seuil + seuil recommande.

    Precision (stricte, pour un rename destructif) = correct / (matchs de la tranche) : on NE
    compte PAS diff-version comme bon (renommer un remix par le nom de l'original = faux), mais
    on le remonte a part pour voir POURQUOI. Recommande le plus BAS seuil dont la precision
    cumulee (accepter tout >= seuil) >= `target` avec assez d'echantillons (`min_n`).
    """
    matched = [r for r in rows if r.label != LBL_NO_MATCH]
    n_total, n_match = len(rows), len(matched)

    def _stats(sub):
        n = len(sub)
        c = sum(1 for r in sub if r.label == LBL_CORRECT)
        d = sum(1 for r in sub if r.label == LBL_DIFF_VERSION)
        w = sum(1 for r in sub if r.label == LBL_WRONG)
        return {"n": n, "correct": c, "diff_version": d, "wrong": w,
                "precision": (c / n) if n else None}

    bands = []
    for lo, hi in VALIDATE_BANDS:
        s = _stats([r for r in matched if lo <= r.score < hi])
        s["lo"], s["hi"] = lo, hi
        bands.append(s)

    cumulative, recommended = [], None
    for t in VALIDATE_THRESHOLDS:
        s = _stats([r for r in matched if r.score >= t])
        s["threshold"] = t
        cumulative.append(s)
        if recommended is None and s["n"] >= min_n and s["precision"] is not None \
                and s["precision"] >= target:
            recommended = t                    # plus bas seuil sur/precis (buckets tries bas->haut)

    return {"n_total": n_total, "n_match": n_match, "n_no_match": n_total - n_match,
            "recall": (n_match / n_total) if n_total else 0.0,
            "bands": bands, "cumulative": cumulative,
            "recommended": recommended, "target": target, "min_n": min_n}


def _write_validate_csv(rows: Sequence[ValidateRow], path: Path) -> str:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["label", "score", "known_artist", "known_title",
                    "cand_artist", "cand_title", "version_known", "version_cand", "file"])
        for r in sorted(rows, key=lambda r: r.score, reverse=True):
            w.writerow([r.label, f"{r.score:.3f}", r.known_artist, r.known_title,
                        r.cand_artist, r.cand_title, version_key(r.known_title),
                        version_key(r.cand_title), r.path])
    return str(path)


def _write_log(results: Sequence[IdentifyResult], path: Path) -> str:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "file", "artist", "title", "score", "proposed_name",
                    "applied", "mbid", "note"])
        for r in results:
            b = r.best
            w.writerow([r.status, r.path, b.artist if b else "", b.title if b else "",
                        f"{b.score:.3f}" if b else "", r.proposed_name, int(r.applied),
                        b.recording_mbid if b else "", r.note])
    return str(path)
