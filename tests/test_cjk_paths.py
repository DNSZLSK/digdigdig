# -*- coding: utf-8 -*-
"""Chemins japonais (CJK) : la chaine d'I/O fichier de DDD doit les encaisser.

Maillon a risque sous Windows = soundfile/libsndfile sur des chemins non-ASCII (pathlib /
mutagen / CSV sont nativement Unicode). On ecrit un WAV dans un dossier + fichier a noms
japonais, on le relit, on l'analyse (quality), et on verifie que le parseur de nom decoupe
bien 'Artiste - Titre' en CJK + parentheses pleine-chasse. Garde-fou pour le testeur Win JP.
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf

from ddd.core import naming, quality

ARTIST = "中田ヤスタカ"
TITLE = "東京テスト（オリジナル・ミックス）"


def main():
    base = Path(tempfile.mkdtemp(prefix="ddd_cjk_"))
    try:
        folder = base / "日本語フォルダ" / "テクノ"
        folder.mkdir(parents=True, exist_ok=True)
        fpath = folder / f"{ARTIST} - {TITLE}.wav"

        # 1. ecriture libsndfile sur un chemin japonais (le point qui casse historiquement).
        sr = 44100
        t = np.linspace(0, 2, sr * 2, endpoint=False)
        sig = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(str(fpath), np.column_stack([sig, sig]), sr, subtype="PCM_16")
        assert fpath.exists() and fpath.stat().st_size > 0, "WAV non ecrit sur le chemin CJK"

        # 2. relecture libsndfile depuis le chemin japonais.
        info = sf.info(str(fpath))
        assert info.samplerate == sr and info.channels == 2, info
        data, rate = sf.read(str(fpath))
        assert rate == sr and data.shape[0] == sr * 2, (data.shape, rate)
        print("OK - soundfile write/read sur dossier + fichier japonais")

        # 3. coeur detecteur : analyse de bout en bout, chemin/nom CJK preserves.
        q = quality.analyze_file(str(fpath))
        assert q is not None, "quality.analyze_file a renvoye None"
        assert q.filename == fpath.name, (q.filename, fpath.name)
        assert "東京テスト" in q.path, q.path
        assert q.verdict == "LOSSLESS", f"WAV plein spectre attendu LOSSLESS, recu {q.verdict}"
        print("OK - quality.analyze_file (verdict + nom/chemin japonais intacts)")

        # 4. parseur de nom : decoupe 'Artiste - Titre' en CJK (+ parentheses pleine-chasse).
        parsed = naming.parse_filename(fpath)
        assert parsed.artist == ARTIST, (parsed.artist, ARTIST)
        assert "東京テスト" in parsed.title, parsed.title
        assert parsed.parseable, "le nom japonais 'Artiste - Titre' doit etre parseable"
        print("OK - parse_filename decoupe le nom japonais (artiste / titre)")

        print("\nOK - DDD encaisse les chemins/noms japonais (I/O + analyse + parse)")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
