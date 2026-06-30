# DDD - Soulseek support playbook (login / connection)

When a tester says "I can't connect / download", follow this playbook instead of
guessing. The golden rule: **NEVER trust the message shown in the window alone,
read the log.** That's what tells you the real cause.

---

## 30-second triage

1. Get the **real error line**: have them open the log and copy the last ~15
   lines (paths below).
2. Look for the line `---> Soulseek.XxxException: <reason>`. That's the decider:
   - `Soulseek.ListenException` / "start listening" / "port may be in use" -> **Case A: port 50300 in use**
   - `Soulseek.LoginRejectedException` / "rejected the login" / "username or password" -> **Case B: wrong credentials**
   - anything else -> read the raw reason, use common sense
3. Apply the case's fix. Send them the matching EN block.

---

## Where the log is

The tester is on the `.exe`, so:

| OS | Path |
|----|--------|
| Windows | `%APPDATA%\ddd\logs\ddd_upgrade.log` (or `ddd_acquire.log` for a scrape/acquire) |
| macOS | `~/Library/Application Support/ddd/logs/ddd_upgrade.log` |
| Linux | `~/.local/share/ddd/logs/ddd_upgrade.log` |

(In dev from the repo: `./logs/`.) On Windows, pasting the path into Explorer's
address bar opens the folder directly.

---

## The historical trap: the word "slskd"

Before the fix, DDD **always** showed `invalid credentials, or another session
(slskd) is already connected` as soon as sldl wrote "Login failed" - even when the
real cause was port 50300. The word "slskd" was **hard-coded text** in
`ddd/core/soulseek.py`, **not** a response from the Soulseek server. Several days
were lost chasing an slskd that didn't exist.

Since the fix, the window shows the real reason in brackets, e.g.
`... [sldl: Failed to start listening on 0.0.0.0:50300 ...]`. Read what's in the
brackets, and when in doubt, the log.

---

## Case A - Port 50300 in use (most common)

**Sign**: `Soulseek.ListenException` / "Failed to start listening on 0.0.0.0:50300".

**Cause**: another process is holding listen port 50300: a zombie `sldl.exe` from a
previous run, an `slskd`, or two DDD actions launched in parallel. DDD already kills
`slskd.exe` and `sldl.exe` before each run, but **not** SoulseekQt or an slskd
installed as a service.

**Fix**:
- Close any other Soulseek app. SoulseekQt: right-click its **system tray** icon ->
  Quit (closing the window isn't enough, it stays in the tray).
- Task Manager (Ctrl+Shift+Esc) -> kill any leftover `sldl.exe` / `slskd.exe`.
- Don't run two DDD actions at the same time.
- If it persists right after a run: wait 30s (socket in TIME_WAIT) or reboot once.

**To paste to the tester (EN)**:
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

## Case B - Login refused (wrong credentials)

**Sign**: `Soulseek.LoginRejectedException` / "rejected the login" / "username or password".

**Soulseek facts to know**:
- The **P2P network** Soulseek account is created **inside a client** (SoulseekQt,
  Nicotine+) on the very first login. There is **no** web signup for the network.
- `slsknet.org` = **forum** account, a separate system. Signing up there does **not**
  create a usable network account for DDD.
- A username already taken by someone else -> login refused (the password doesn't
  match). Common with a short handle.

**Fix**:
- Check user/pass in Settings: no extra space, no stray capital, no exotic character
  pasted wrong.
- Test **the same** creds directly in SoulseekQt:
  - SoulseekQt also refuses -> the creds are the problem. Pick a **new** simple
    username (letters + numbers), log in once in SoulseekQt to register it, then put
    those creds in DDD.
  - SoulseekQt connects fine -> then SoulseekQt was holding the connection (one login
    per account): **fully quit it**, then relaunch DDD.

**To paste to the tester (EN)**:
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

## Case C - "another session is already connected"

Protocol detail: on Soulseek, a 2nd login **kicks** the 1st (it isn't refused). A
real collision therefore shows up as disconnects / instability rather than a
**permanent** login refusal. If the failure persists even after a reboot, it's
probably not a collision: go back to **Case A** (port) or **Case B** (creds), log in
hand.

---

## False alarms (don't debug)

- **"wrong match"**: normal and intended. The audit rejected a downloaded file that
  didn't match (different artist / title / version). It's the anti-bad-replacement
  safety net. The original file isn't touched. Nothing to do.
- **Slow run or one that looks frozen**: that's sldl's anti-ban throttle (~34
  searches per 220s), not a bug. Only reducing the volume speeds it up. Don't touch
  `search-timeout` or shrink the chunks.

---

## For the maintainer

- The error message comes from `ddd/core/soulseek.py`: `_fatal_message()` +
  `_extract_sldl_reason()` read the sldl cascade and surface the real reason.
- Non-regression locked by `tests/test_soulseek_fatal.py` (port vs creds cascade)
  and `tests/test_soulseek_nowindow.py`.
- Reference sldl cascade: see `logs/ddd_upgrade.log` (a full port failure is in there).
