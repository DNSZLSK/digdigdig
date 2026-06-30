"""Garde-fou single-instance : 1ere instance OK, doublon refuse, idempotent.

Le cas qui compte (Windows) est teste pour de vrai : on tient le verrou dans CE process
et on lance un SOUS-process qui tente le meme verrou -> il doit se voir refuser. C'est
exactement le scenario "2e double-clic pendant le demarrage".
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import singleton


def main():
    name = f"DDD-test-{os.getpid()}"   # unique a ce run -> pas de collision avec un vrai DDD

    # 1. 1ere instance : le verrou est pose.
    assert singleton.acquire(name) is True, "la 1ere instance doit obtenir le verrou"

    # 2. Idempotent dans le meme process (entry.py puis gui.run() appellent acquire).
    assert singleton.acquire(name) is True, "re-acquire dans le meme process doit rester True"

    # 3. focus_existing ne doit jamais lever (best-effort).
    singleton.focus_existing("DDD - DigDigDig (fenetre inexistante)")

    # 4. Windows : une VRAIE 2e instance (sous-process) doit etre refusee tant qu'on tient le verrou.
    if os.name == "nt":
        code = (
            "import sys; sys.path.insert(0, r'{root}');"
            "from ddd.core import singleton;"
            "print(singleton.acquire('{name}'))"
        ).format(root=str(ROOT), name=name)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.stdout.strip() == "False", (
            f"une 2e instance doit etre refusee, sortie={out.stdout!r} err={out.stderr!r}"
        )
        print("OK - 2e instance (sous-process) refusee par le mutex nomme")

    print("OK - single-instance : 1ere instance OK, idempotent, focus best-effort sans crash")


if __name__ == "__main__":
    main()
