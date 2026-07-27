"""Lecture audio a deux backends : libsndfile d'abord, PyAV en repli.

libsndfile (embarque via soundfile) couvre WAV/FLAC/AIFF/MP3/OGG et s'arrete la. Un
`.mp4`, un `.m4a` (AAC ou ALAC) ou un `.wma` n'est meme pas OUVRABLE : `sf.info` leve.
Resultat cote scan, avant ce module : ces fichiers etaient soit comptes illisibles, soit
- pour les extensions absentes de la liste, comme `.mp4` - jamais parcourus du tout, et
un dossier n'en contenant que ca avait l'air VIDE.

PyAV (wheel pip qui embarque ses propres libs ffmpeg -> toujours AUCUN ffmpeg systeme a
installer) sert de repli generique : un seul backend couvre AAC, ALAC, WMA v1/v2/Pro,
WMA-lossless, AC3/DTS, APE, TTA, WavPack, Opus... On ne rajoute donc plus un decodeur par
format, seulement l'extension a la liste que le scan parcourt (cf quality.LOSSY_EXTS).

Contrat identique a soundfile pour ce dont quality.py a besoin : les infos du fichier et
la lecture d'une FENETRE arbitraire, au sample rate NATIF. Aucun resample de frequence :
il deplacerait le cutoff spectral, c'est-a-dire exactement la mesure sur laquelle repose
tout le verdict de qualite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Codecs sans perte : le conteneur ne suffit pas a le dire (un .m4a porte de l'AAC lossy
# OU de l'ALAC lossless, un .wma de la wmav2 lossy OU de la wmalossless). Le codec reel,
# lui, tranche -> c'est lui qui decide si le fichier "pretend etre lossless" et doit donc
# passer au controle spectral anti-upscale.
LOSSLESS_CODECS = {
    "alac", "flac", "wmalossless", "ape", "tta", "wavpack", "mlp", "truehd", "als",
}


@dataclass
class AudioInfo:
    """Infos minimales pour l'analyse spectrale, quel que soit le backend."""
    samplerate: int
    channels: int
    duration: float
    frames: int
    codec: str = ""            # '' quand soundfile a suffi (l'extension tranche deja)
    stream_bitrate: int = 0    # kbps de la PISTE audio ; 0 = inconnu
    backend: str = "soundfile"


def _av():
    """Import paresseux de PyAV -> le module, ou None s'il n'est pas installe.

    Paresseux pour deux raisons : ne pas charger ~65 Mo de DLL ffmpeg tant qu'aucun
    fichier exotique n'est croise, et laisser une install source sans `av` fonctionner
    exactement comme avant (les formats non-libsndfile restent simplement illisibles).
    """
    try:
        import av  # noqa: PLC0415
        return av
    except ImportError:
        return None


def _channels(codec_ctx) -> int:
    layout = getattr(codec_ctx, "layout", None)
    return int(getattr(layout, "nb_channels", 0) or getattr(codec_ctx, "channels", 0) or 1)


def _av_info(av, path: str) -> AudioInfo:
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("aucune piste audio dans le conteneur")
        ctx = stream.codec_context
        sr = int(ctx.sample_rate or 0)
        if sr <= 0:
            raise ValueError("sample rate inconnu")
        if stream.duration and stream.time_base:
            dur = float(stream.duration * stream.time_base)
        else:
            dur = float(container.duration or 0) / 1_000_000.0
        # Debit de la PISTE, pas du conteneur : sur un .mp4 video, taille*8/duree compte
        # la video et donnerait un debit audio fantaisiste (donc un verdict fantaisiste).
        bitrate = int((stream.bit_rate or 0) / 1000)
        return AudioInfo(sr, _channels(ctx), dur, int(dur * sr),
                         codec=(ctx.name or ""), stream_bitrate=bitrate, backend="av")


def open_info(path) -> AudioInfo:
    """Infos du fichier : soundfile d'abord, PyAV en repli.

    soundfile passe en premier parce qu'il couvre l'immense majorite d'une biblio
    (FLAC/WAV/AIFF/MP3) et qu'il est plus leger ; on ne paie PyAV que sur ce qu'il refuse.
    Leve si aucun backend n'y arrive (l'appelant en fait un verdict ERROR lisible).
    """
    path = str(path)
    try:
        i = sf.info(path)
        return AudioInfo(int(i.samplerate), int(i.channels), float(i.duration), int(i.frames))
    except Exception as e_sf:  # noqa: BLE001
        av = _av()
        if av is None:
            raise
        try:
            return _av_info(av, path)
        except Exception as e_av:  # noqa: BLE001
            # Les deux backends ont echoue : on remonte les DEUX raisons, sinon on ne sait
            # pas si le fichier est casse ou juste dans un format non couvert.
            raise RuntimeError(f"libsndfile: {e_sf} | PyAV: {e_av}") from e_av


def _av_window(path: str, info: AudioInfo, start_frame: int, frames: int) -> Optional[np.ndarray]:
    av = _av()
    if av is None:
        return None
    sr = info.samplerate
    start_s = start_frame / sr if sr else 0.0
    chunks = []
    got = 0
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return None
        if start_s > 0 and stream.time_base:
            container.seek(int(start_s / stream.time_base), stream=stream)
        # format fltp = on normalise seulement la REPRESENTATION (planar float) ; `rate=sr`
        # rappelle qu'on garde la frequence native - resampler fausserait le cutoff.
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout=stream.layout, rate=sr)
        for frame in container.decode(stream):
            # Un seek atterrit sur un paquet ANTERIEUR a la cible : on jette tout ce qui
            # se termine avant la fenetre, sinon on analyserait le mauvais bout du morceau.
            if frame.time is not None and frame.time + frame.samples / sr <= start_s:
                continue
            for out in resampler.resample(frame):
                arr = out.to_ndarray()           # (channels, samples), planar
                chunks.append(arr)
                got += arr.shape[1]
            if got >= frames:
                break
    if not chunks:
        return None
    data = np.concatenate(chunks, axis=1)[:, :frames]
    return data.T.astype(np.float64)             # (samples, channels), comme soundfile


def read_window(path, info: AudioInfo, start_frame: int, frames: int) -> Optional[np.ndarray]:
    """Lit `frames` echantillons a partir de `start_frame` -> (samples, canaux) float64.

    Meme forme de retour que `sf.read(..., always_2d=True)`. None si la fenetre n'a rien
    donne (fin de fichier, paquet casse) : l'appelant passe simplement a la suivante.
    """
    path = str(path)
    if info.backend == "soundfile":
        data, _ = sf.read(path, start=start_frame, frames=frames,
                          dtype="float64", always_2d=True)
        return data
    return _av_window(path, info, start_frame, frames)
