# DDD - Playbook de support Soulseek (login / connexion)

Quand un testeur dit "je n'arrive pas a me connecter / a telecharger", suis ce
playbook au lieu de deviner. La regle d'or : **ne JAMAIS se fier au message affiche
dans la fenetre seul, lire le log.** C'est lui qui dit la vraie cause.

---

## Triage en 30 secondes

1. Recuperer la **vraie ligne d'erreur** : lui faire ouvrir le log et copier les ~15
   dernieres lignes (chemins plus bas).
2. Y chercher la ligne `---> Soulseek.XxxException: <raison>`. C'est elle qui tranche :
   - `Soulseek.ListenException` / "start listening" / "port may be in use" -> **Cas A : port 50300 occupe**
   - `Soulseek.LoginRejectedException` / "rejected the login" / "username or password" -> **Cas B : mauvais identifiants**
   - autre chose -> lire la raison brute, appliquer le bon sens
3. Appliquer le fix du cas. Lui renvoyer le bloc EN correspondant.

---

## Ou est le log

Le testeur est sur le `.exe`, donc :

| OS | Chemin |
|----|--------|
| Windows | `%APPDATA%\ddd\logs\ddd_upgrade.log` (ou `ddd_acquire.log` pour un scrape/acquire) |
| macOS | `~/Library/Application Support/ddd/logs/ddd_upgrade.log` |
| Linux | `~/.local/share/ddd/logs/ddd_upgrade.log` |

(En dev depuis le repo : `./logs/`.) Sur Windows, coller le chemin dans la barre
d'adresse de l'Explorateur ouvre directement le dossier.

---

## Le piege historique : le mot "slskd"

Avant le fix, DDD affichait **toujours** `invalid credentials, or another session
(slskd) is already connected` des que sldl ecrivait "Login failed" - meme quand la
vraie cause etait le port 50300. Le mot "slskd" etait du **texte code en dur** dans
`ddd/core/soulseek.py`, **pas** un retour du serveur Soulseek. Plusieurs jours ont ete
perdus a chasser un slskd qui n'existait pas.

Depuis le fix, la fenetre montre la vraie raison entre crochets, ex.
`... [sldl: Failed to start listening on 0.0.0.0:50300 ...]`. Lis ce qu'il y a dans
les crochets, et au moindre doute, le log.

---

## Cas A - Port 50300 occupe (le plus frequent)

**Signe** : `Soulseek.ListenException` / "Failed to start listening on 0.0.0.0:50300".

**Cause** : un autre process tient le port d'ecoute 50300 : un `sldl.exe` zombie d'un
run precedent, un `slskd`, ou deux actions DDD lancees en parallele. DDD tue deja
`slskd.exe` et `sldl.exe` avant chaque run, mais **pas** SoulseekQt ni un slskd
installe en service.

**Fix** :
- Fermer toute autre app Soulseek. SoulseekQt : clic droit sur l'icone du **system
  tray** -> Quit (fermer la fenetre ne suffit pas, il reste dans le tray).
- Task Manager (Ctrl+Shift+Echap) -> tuer les `sldl.exe` / `slskd.exe` restants.
- Ne pas lancer deux actions DDD en meme temps.
- Si ca persiste juste apres un run : attendre 30 s (socket en TIME_WAIT) ou rebooter.

**A coller au testeur (EN)** :
```
That's a port conflict, not a login problem. Something is still holding
Soulseek's port 50300:
1. Fully quit SoulseekQt - right-click its system tray icon -> Quit (closing
   the window isn't enough).
2. Open Task Manager and end any leftover sldl.exe or slskd.exe.
3. Don't run two DDD actions at once.
Then try again. If it still happens right after a run, wait 30s or reboot once.
```

---

## Cas B - Login refuse (mauvais identifiants)

**Signe** : `Soulseek.LoginRejectedException` / "rejected the login" / "username or password".

**Rappels Soulseek a connaitre** :
- Le compte Soulseek du **reseau P2P** se cree **dans un client** (SoulseekQt,
  Nicotine+) au tout premier login. Il n'y a **pas** d'inscription web pour le reseau.
- `slsknet.org` = compte de **forum**, systeme separe. S'inscrire la ne cree **pas**
  de compte reseau utilisable dans DDD.
- Un username deja pris par quelqu'un d'autre -> login refuse (le mot de passe ne
  correspond pas). Frequent avec un pseudo court.

**Fix** :
- Verifier user/pass dans Reglages : pas d'espace en trop, pas de majuscule parasite,
  pas de caractere exotique copie de travers.
- Tester **les memes** creds directement dans SoulseekQt :
  - SoulseekQt refuse aussi -> les creds sont en cause. Choisir un **nouveau** username
    simple (lettres + chiffres), se logger une fois dans SoulseekQt pour l'enregistrer,
    puis mettre ces creds dans DDD.
  - SoulseekQt se connecte bien -> alors SoulseekQt tenait la connexion (un seul login
    par compte) : le **quitter completement**, puis relancer DDD.

**A coller au testeur (EN)** :
```
Soulseek refused the login - it's a credentials issue, not slskd.
Two things:
1. Re-check the username/password in DDD Settings: no extra space, no stray
   capital, nothing odd pasted in.
2. Soulseek network accounts are created INSIDE a client (SoulseekQt), not on
   the slsknet.org website (that's just the forum). Open SoulseekQt and log in
   with the exact same user/pass:
   - If it also fails -> pick a brand new username with a simple password
     (letters + numbers), log in once in SoulseekQt to register it, then use
     those in DDD.
   - If it works -> SoulseekQt was holding the connection. Fully quit it
     (tray -> Quit) and run DDD. Never run both at once: one login per account.
```

---

## Cas C - "another session is already connected"

Detail protocole : sur Soulseek, un 2e login **kicke** le 1er (il ne se fait pas
refuser). Une vraie collision se traduit donc plutot par des deconnexions / de
l'instabilite que par un login refuse **permanent**. Si l'echec persiste meme apres
reboot, ce n'est probablement pas une collision : repartir sur le **Cas A** (port) ou
le **Cas B** (creds), log a l'appui.

---

## Faux problemes (ne pas debugger)

- **"wrong match"** : normal et voulu. L'audit a rejete un fichier telecharge qui ne
  correspondait pas (artiste / titre / version differente). C'est le filet de securite
  anti-mauvais-remplacement. Le fichier d'origine n'est pas touche. Rien a faire.
- **Run lent ou qui semble fige** : c'est le throttle anti-ban de sldl (~34 recherches
  par 220 s), pas un bug. Seule la reduction du volume accelere. Ne pas toucher au
  `search-timeout` ni reduire les chunks.

---

## Pour le mainteneur

- Le message d'erreur vient de `ddd/core/soulseek.py` : `_fatal_message()` +
  `_extract_sldl_reason()` lisent la cascade sldl et remontent la vraie raison.
- Non-regression verrouillee par `tests/test_soulseek_fatal.py` (cascade port vs creds)
  et `tests/test_soulseek_nowindow.py`.
- Cascade sldl de reference : voir `logs/ddd_upgrade.log` (un echec port complet y est).
