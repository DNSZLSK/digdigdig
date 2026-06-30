"""La traduction des echecs fatals sldl montre la VRAIE raison (port vs creds).

Regression visee : avant, DDD affichait toujours 'invalid credentials, or another
session (slskd)...' des que sldl disait 'Login failed', meme quand la cause reelle
etait le port 50300 occupe. On lit desormais la cascade sldl (la ligne
'---> Soulseek.XxxException: <reason>') au lieu de deviner.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ddd.core import soulseek

# Cascade REELLE capturee dans logs/ddd_upgrade.log : la cause est le port, pas les creds.
PORT_CASCADE = [
    "Login failed definitively for DNSZLSK after 1 attempts.",
    "Failed to ensure Soulseek connection and login: Failed to start listening on "
    "0.0.0.0:50300; the IP and/or port may be in use or are otherwise unavailable",
    "Failed to initialize Soulseek client: Soulseek login failed: Failed to start "
    "listening on 0.0.0.0:50300; the IP and/or port may be in use or are otherwise unavailable",
    "Unhandled exception. System.InvalidOperationException: Soulseek login failed: Failed "
    "to start listening on 0.0.0.0:50300; the IP and/or port may be in use or are otherwise unavailable",
    " ---> Soulseek.ListenException: Failed to start listening on 0.0.0.0:50300; the IP "
    "and/or port may be in use or are otherwise unavailable",
]

# Cascade d'un VRAI refus de login (mauvais identifiants) : meme forme, autre exception.
CREDS_CASCADE = [
    "Login failed definitively for someuser after 1 attempts.",
    "Failed to ensure Soulseek connection and login: The server rejected the login "
    "attempt (invalid username or password)",
    "Unhandled exception. System.InvalidOperationException: Soulseek login failed: The "
    "server rejected the login attempt (invalid username or password)",
    " ---> Soulseek.LoginRejectedException: The server rejected the login attempt "
    "(invalid username or password)",
]


def main():
    # 1. Port occupe : message doit pointer le PORT 50300, pas les creds.
    msg = soulseek._fatal_message(PORT_CASCADE[0], PORT_CASCADE)
    assert "50300" in msg, msg   # la vraie raison sldl (le port qui a echoue) est remontee
    assert "listen port" in msg.lower(), msg
    assert "reserved by the os" in msg.lower(), msg   # n'affirme plus juste "occupe" : aussi reserve OS
    assert "wrong username" not in msg.lower(), f"un echec de port n'est PAS un probleme de creds : {msg}"
    print("OK - cascade port -> message pointe le port (occupe OU reserve par l'OS), pas les creds")

    # 2. Login refuse : message doit pointer les CREDS et NE PAS accuser slskd au pif.
    msg = soulseek._fatal_message(CREDS_CASCADE[0], CREDS_CASCADE)
    assert "wrong username or password" in msg.lower(), msg
    assert "slskd" not in msg.lower(), f"un refus de login ne doit plus inventer 'slskd' : {msg}"
    # la VRAIE raison sldl doit etre remontee, pas juste la ligne generique.
    assert "rejected the login" in msg.lower(), f"la vraie ligne sldl doit apparaitre : {msg}"
    print("OK - cascade creds -> message 'wrong username or password' + raison brute, sans 'slskd'")

    # 3. _extract_sldl_reason prend bien l'exception interne (la plus precise).
    assert soulseek._extract_sldl_reason(PORT_CASCADE).startswith("Failed to start listening")
    assert soulseek._extract_sldl_reason(CREDS_CASCADE).startswith("The server rejected")
    print("OK - extraction de la raison = ligne '---> Soulseek.XxxException:'")

    print("\nOK - traduction des echecs fatals sldl (port vs creds) verifiee")


if __name__ == "__main__":
    main()
