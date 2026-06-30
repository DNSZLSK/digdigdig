"""Garde-fou : choix auto d'un port d'ecoute bindable pour sldl.

Regression visee : `listen-port = 50300` etait code en dur. Sur un Windows qui RESERVE
ce port (Hyper-V/WSL/Docker reservent des blocs dans 49152-65535), sldl plantait avec
'Failed to start listening' et le seul recours etait d'editer le .conf a la main. On
bind-teste et on retombe sur un port libre, passe via --listen-port.
"""

import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import soulseek


def test_returned_port_is_always_bindable():
    p = soulseek.pick_listen_port()
    assert soulseek._port_bindable(p), f"le port rendu doit etre bindable : {p}"


def test_falls_back_when_preferred_unbindable():
    # On TIENT un port (bind+listen) -> il devient non-bindable, comme un 50300 reserve.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("0.0.0.0", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]
    try:
        assert not soulseek._port_bindable(taken), "un port tenu ne doit pas etre bindable"
        chosen = soulseek.pick_listen_port(taken)
        assert chosen != taken, "doit eviter le port indisponible"
        assert soulseek._port_bindable(chosen), "le repli doit etre bindable"
    finally:
        holder.close()


def test_configured_listen_port_read_and_default():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "sldl.conf"
        cfg.write_text("# x\nlisten-port = 31337\n", encoding="utf-8")
        assert soulseek._configured_listen_port(str(cfg)) == 31337
        empty = Path(d) / "empty.conf"
        empty.write_text("# rien ici\n", encoding="utf-8")
        assert soulseek._configured_listen_port(str(empty)) == soulseek.DEFAULT_LISTEN_PORT


def main():
    test_returned_port_is_always_bindable()
    test_falls_back_when_preferred_unbindable()
    test_configured_listen_port_read_and_default()
    print("OK - port d'ecoute auto : repli si indisponible, lecture config, port rendu bindable")


if __name__ == "__main__":
    main()
