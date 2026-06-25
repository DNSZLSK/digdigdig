"""Classif genre par l'AUDIO (local, hors-ligne) via le modele Discogs-EffNet en ONNX.

Source TERMINALE du tri (organize.py) : quand le tag ID3 + Discogs ne donnent aucun
dossier (la longue traine ~28%), on analyse le SPECTRE du fichier -> top styles Discogs
(la meme taxonomie que map_styles_to_folder route deja). Marche sur n'importe quel nom de
fichier, meme "Track_01.flac".

100% numpy / scipy / soundfile + onnxruntime : PAS de TensorFlow, pas de reseau. Le modele
`discogs-effnet-bsdynamic-1.onnx` (MTG/UPF, CC BY-NC) sort directement les 400 styles
Discogs (sortie 'activations', Sigmoid) depuis un mel-spectrogram [128 frames, 96 mels] a
16 kHz mono.

Degradation gracieuse : si onnxruntime n'est pas dispo ou le modele/labels absents,
`classify()` renvoie None et le tri continue sans audio-ML (aucune dependance dure).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .. import paths

logger = logging.getLogger(__name__)

# Parametres du modele (fixes par l'entrainement Essentia/MusiCNN ; NE PAS changer a la legere).
SR, N_FFT, HOP, N_MELS, PATCH = 16000, 512, 256, 96, 128
MAX_SECONDS = 120            # on ne decode que ~2 min (le milieu) : assez de patches, 2x plus rapide
MIN_PROB = 0.08             # en dessous, on ignore le style (bruit)
TOP_N = 6


def _slaney_mel_fb() -> np.ndarray:
    """Banc de filtres mel facon librosa(htk=False, norm='slaney') = l'entree EffNet."""
    def h2m(f):
        f = np.asarray(f, float); m = 3.0 * f / 200.0
        return np.where(f >= 1000.0, 15.0 + np.log(np.maximum(f, 1e-9) / 1000.0) / (np.log(6.4) / 27.0), m)

    def m2h(m):
        m = np.asarray(m, float); f = 200.0 * m / 3.0
        return np.where(m >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), f)

    fftf = np.linspace(0, SR / 2, N_FFT // 2 + 1)
    mpts = m2h(np.linspace(h2m(0.0), h2m(SR / 2), N_MELS + 2))
    fdiff = np.diff(mpts); ramps = mpts.reshape(-1, 1) - fftf.reshape(1, -1)
    fb = np.zeros((N_MELS, N_FFT // 2 + 1))
    for i in range(N_MELS):
        fb[i] = np.maximum(0, np.minimum(-ramps[i] / fdiff[i], ramps[i + 2] / fdiff[i + 1]))
    fb *= (2.0 / (mpts[2:N_MELS + 2] - mpts[:N_MELS])).reshape(-1, 1)
    return fb.astype(np.float32)


_FB = _slaney_mel_fb()
_WIN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)   # hann periodique (sym=False)

# --- Etat paresseux : session ONNX + labels, charges au 1er appel, None si indispo --------
_SESSION = None        # onnxruntime.InferenceSession | False (indispo)
_STYLES: Optional[List[str]] = None   # style apres "---" pour chaque classe
_MEMO: dict = {}       # cache en-session : (path, size, mtime) -> [(style, prob), ...]


def available() -> bool:
    """True si l'audio-ML est utilisable (onnxruntime installe + modele present)."""
    return _ensure_session() is not None


def _ensure_session():
    global _SESSION, _STYLES
    if _SESSION is not None:
        return _SESSION or None
    model, labels = paths.effnet_model(), paths.effnet_labels()
    if not model.exists() or not labels.exists():
        logger.info("audio-ML desactive : modele/labels absents (%s)", model)
        _SESSION = False
        return None
    try:
        import onnxruntime as ort
        _SESSION = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        classes = json.load(open(labels, encoding="utf-8"))["classes"]
        _STYLES = [c.split("---")[-1] for c in classes]   # "Electronic---Deep House" -> "Deep House"
    except Exception as e:  # noqa: BLE001  (onnxruntime absent, modele corrompu, etc.)
        logger.warning("audio-ML desactive : %r", e)
        _SESSION = False
        return None
    return _SESSION


def _load_audio(path) -> Optional[np.ndarray]:
    """Decode ~MAX_SECONDS au milieu du fichier -> 16 kHz mono float32. None si illisible."""
    import soundfile as sf
    try:
        info = sf.info(str(path))
        want = int(MAX_SECONDS * info.samplerate)
        start = max(0, (info.frames - want) // 2) if info.frames > want else 0
        y, sr = sf.read(str(path), start=start, frames=want, dtype="float32", always_2d=True)
    except Exception as e:  # noqa: BLE001  (format non decodable : m4a/opus sans ffmpeg, fichier casse)
        logger.debug("audio-ML: decode echoue %s: %r", path, e)
        return None
    y = y.mean(axis=1)
    if sr != SR:
        from scipy.signal import resample_poly
        g = math.gcd(int(sr), SR)
        y = resample_poly(y, SR // g, sr // g).astype(np.float32)
    return y


def _melspec(y: np.ndarray) -> np.ndarray:
    if len(y) < N_FFT:
        return np.zeros((0, N_MELS), np.float32)
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * _WIN, axis=1)) ** 2     # power spectrum
    return np.log10(1.0 + 10000.0 * (spec @ _FB.T)).astype(np.float32)


def classify(path) -> Optional[List[Tuple[str, float]]]:
    """Top styles Discogs (style, proba) tries par proba decroissante, ou None si indispo.

    Renvoie [] si le fichier ne se decode pas / trop court. Memoise en-session par
    (chemin, taille, mtime) pour ne pas re-inferer entre le dry-run et l'apply.
    """
    sess = _ensure_session()
    if sess is None:
        return None
    p = Path(path)
    try:
        st = p.stat(); key = (str(p), st.st_size, int(st.st_mtime))
    except OSError:
        key = (str(p), 0, 0)
    if key in _MEMO:
        return _MEMO[key]

    y = _load_audio(p)
    mel = _melspec(y) if y is not None else np.zeros((0, N_MELS), np.float32)
    npatch = len(mel) // PATCH
    if npatch == 0:
        _MEMO[key] = []
        return []
    patches = mel[:npatch * PATCH].reshape(npatch, PATCH, N_MELS)
    probs = sess.run(["activations"], {"melspectrogram": patches})[0].mean(axis=0)   # [400]
    order = np.argsort(probs)[::-1][:TOP_N]
    out = [(_STYLES[i], float(probs[i])) for i in order if probs[i] >= MIN_PROB]
    _MEMO[key] = out
    return out
