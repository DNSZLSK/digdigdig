"""Garde-fou : la checkbox de selection d'une track est cochable pour TOUTE ligne audio.

Regression : le modele de qualite club (presets) verrouillait la checkbox des qu'une
track passait la barre du preset (`disabled=not _is_upgradable`). Avec le defaut
dj_club (>= 18 kHz), la quasi-totalite d'une lib normale devenait non-cochable -> un
testeur ne pouvait plus choisir track par track. On dissocie desormais :

  - `_is_audio_row`  -> pilote le `disabled` : seuls SKIPPED/ERROR sont verrouilles.
  - `_is_upgradable` -> reste le "sous la barre" (selection par defaut + compteur + filtre).

Une bonne track doit donc etre COCHABLE (audio_row) mais PAS auto-selectionnee (upgradable).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd import gui
from ddd.core import quality


def _qr(verdict=quality.LOSSLESS, ext=".flac", cutoff_hz=22000.0,
        container_bitrate=1000):
    """Construit un QualityResult minimal mais valide pour les helpers de selection."""
    return quality.QualityResult(
        path="lib/track" + ext, filename="track" + ext, ext=ext,
        format_class=quality._format_class(ext),
        sample_rate=44100, channels=2, duration_s=300.0,
        cutoff_hz=cutoff_hz, cutoff_std_hz=0.0, hf_energy_ratio=0.0,
        est_source_bitrate=0, container_bitrate=container_bitrate,
        verdict=verdict, confidence="", reason="")


def test_good_track_is_tickable_but_not_auto_selected():
    """Le coeur du fix : une track deja bonne reste cochable a la main."""
    flac = _qr(verdict=quality.LOSSLESS, ext=".flac", cutoff_hz=22000.0)
    mp3_320 = _qr(verdict=quality.HQ, ext=".mp3", cutoff_hz=19000.0, container_bitrate=320)
    for good in (flac, mp3_320):
        assert gui._is_audio_row(good) is True, "une bonne track doit etre cochable"
        # disabled = not _is_audio_row -> False -> la checkbox repond au clic
        assert (not gui._is_audio_row(good)) is False
        # mais elle n'est PAS dans la selection par defaut (rien a upgrader)
        assert gui._is_upgradable(good, "dj_club") is False


def test_below_bar_track_is_audio_and_upgradable():
    douteux = _qr(verdict=quality.DOUTEUX, ext=".mp3", cutoff_hz=17000.0, container_bitrate=320)
    assert gui._is_audio_row(douteux) is True
    assert gui._is_upgradable(douteux, "dj_club") is True


def test_non_audio_rows_stay_locked():
    """SKIPPED/ERROR ne sont pas de l'audio analysable -> checkbox verrouillee."""
    for verdict in (quality.SKIPPED, quality.ERROR):
        row = _qr(verdict=verdict, ext=".txt")
        assert gui._is_audio_row(row) is False, f"{verdict} doit rester verrouille"
        assert gui._is_upgradable(row, "dj_club") is False


def main():
    test_good_track_is_tickable_but_not_auto_selected()
    test_below_bar_track_is_audio_and_upgradable()
    test_non_audio_rows_stay_locked()
    print("OK - checkbox cochable pour toute ligne audio (bonnes tracks incluses), "
          "SKIPPED/ERROR verrouilles, defaut d'upgrade inchange")


if __name__ == "__main__":
    main()
