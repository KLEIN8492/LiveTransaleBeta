#!/usr/bin/env python3
"""What LiveTranslate keeps on disk: owner-only permissions, saved sessions, crash litter. No server needed —
imports server.py against a throwaway data folder.

    .venv/bin/python tests/test_storage.py
"""
import os, stat, sys, tempfile

DATA = tempfile.mkdtemp(prefix="lt-storage-")
os.environ["LT_DATA"] = DATA
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server as S  # noqa: E402

S.log = lambda *a: None
results = []


def check(name, ok):
    ok = bool(ok); results.append(ok); print(("PASS " if ok else "FAIL ") + name)


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


# --- Issue 3: files holding secrets are owner-only
S.save_conf({"secret": "x"})
check("config.json is created owner-only (0600)", mode(S.CONF_PATH) == 0o600)
S.save_session({**S.DEFAULT, "name": "Gala", "wifi_pass": "pw"})
sess = [os.path.join(S.SESS_DIR, f) for f in os.listdir(S.SESS_DIR)]
check("saved sessions (hold the Wi-Fi password) are owner-only", sess and all(mode(p) == 0o600 for p in sess))
os.chmod(S.CONF_PATH, 0o644); os.chmod(sess[0], 0o644)
S.tighten_permissions()
check("startup tightens files an older version left world-readable",
      mode(S.CONF_PATH) == 0o600 and mode(sess[0]) == 0o600 and mode(S.SESS_DIR) == 0o700)
r = S.redact({"name": "Gala", "wifi_pass": "pw"})
check("redact() drops the Wi-Fi password, keeps whether it is set", "wifi_pass" not in r and r["wifi_pass_set"])
check("keep_secret(): absent → keep the stored one", S.keep_secret({"name": "Gala"}, {"wifi_pass": "pw"})["wifi_pass"] == "pw")
check("keep_secret(): '' → removed", S.keep_secret({"wifi_pass": ""}, {"wifi_pass": "pw"})["wifi_pass"] == "")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
