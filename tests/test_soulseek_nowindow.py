"""Verifie qu'aucune console noire ne surgit quand sldl tourne (regression UX).

- `_no_window_kwargs()` renvoie CREATE_NO_WINDOW sous Windows, rien ailleurs.
- `run_sldl` applique bien ce flag au Popen ET remet le handle via on_proc
  (necessaire au bouton Annuler de la GUI). On monkeypatch Popen : pas de reseau.
"""

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import soulseek


class _FakeProc:
    def __init__(self):
        self.stdout = iter([])   # pas de sortie -> boucle de lecture vide
        self.returncode = 0

    def wait(self):
        return 0


def main():
    # 1. le helper
    kw = soulseek._no_window_kwargs()
    if platform.system() == "Windows":
        assert kw == {"creationflags": subprocess.CREATE_NO_WINDOW}, kw
    else:
        assert kw == {}, kw

    # 2. run_sldl : Popen recoit le flag + on_proc recoit le process
    tmp = ROOT / "staging" / "_test_nowindow"
    tmp.mkdir(parents=True, exist_ok=True)
    fake_exe = tmp / "sldl.exe"
    fake_cfg = tmp / "sldl.conf"
    in_csv = tmp / "in.csv"
    for p in (fake_exe, fake_cfg, in_csv):
        p.write_bytes(b"")

    captured = {}
    orig_popen = soulseek.subprocess.Popen

    def fake_popen(args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    seen = []
    soulseek.subprocess.Popen = fake_popen
    try:
        code = soulseek.run_sldl(
            in_csv, tmp, root=ROOT, creds={"user": "u", "pass": "p"},
            sldl_exe=fake_exe, config_path=fake_cfg,
            on_proc=lambda proc: seen.append(proc),
        )
    finally:
        soulseek.subprocess.Popen = orig_popen

    assert code == 0
    assert seen, "on_proc doit recevoir le handle du process (bouton Annuler)"
    if platform.system() == "Windows":
        assert captured["kwargs"].get("creationflags") == subprocess.CREATE_NO_WINDOW, \
            "Popen doit recevoir CREATE_NO_WINDOW (pas de console noire)"

    # cleanup
    for p in (fake_exe, fake_cfg, in_csv):
        p.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass

    print("OK - sldl tourne sans console + on_proc cable")


if __name__ == "__main__":
    main()
