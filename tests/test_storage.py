#!/usr/bin/env python3
"""What LiveTranslate keeps on disk: owner-only permissions, saved sessions, crash litter. No server needed —
imports server.py against a throwaway data folder.

    .venv/bin/python tests/test_storage.py
"""
import atexit, os, shutil, stat, sys, tempfile

DATA = tempfile.mkdtemp(prefix="lt-storage-")
atexit.register(shutil.rmtree, DATA, True)  # leave nothing behind (it holds a test config and sessions)
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
S.list_sessions()  # the real order: Control lists saved setups before the first session is ever saved
check("the sessions folder is created owner-only (0700), whoever creates it first", mode(S.SESS_DIR) == 0o700)
S.save_session({**S.DEFAULT, "name": "Gala", "wifi_pass": "pw"})
check("listing saved setups never returns the Wi-Fi password",
      all("wifi_pass" not in x and x["wifi_pass_set"] for x in S.list_sessions()))
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

# --- Issue 4: saved sessions keyed by a stable id
for f in S._session_files():
    os.remove(f)
a = S.ensure_id({**S.DEFAULT, "name": "Conférence!", "glossary": [{"src": "court métrage", "en": "short film"}]})
b = S.ensure_id({**S.DEFAULT, "name": "conference"})
S.save_session(a); S.save_session(b)
check("'Conférence!' and 'conference' (same slug) are two saved setups",
      a["id"] != b["id"] and len(S._session_files()) == 2)
S.save_session({**a, "name": "Gala du festival"})
mine = S._session_files(a["id"])
kept = S.read_json(mine[0], {}) if mine else {}
check("renaming keeps ONE file for that setup, with its content",
      len(mine) == 1 and kept.get("name") == "Gala du festival" and kept.get("glossary") == a["glossary"]
      and len(S._session_files()) == 2)
check("a client without an id (curl, tests) reuses the setup of that name",
      S.ensure_id({"name": "Gala du festival"})["id"] == a["id"])
check("'save as a new setup' gets a new id", S.ensure_id({**a, "new_setup": True})["id"] != a["id"])
check("ids can't escape the sessions folder", S.ensure_id({"name": "x", "id": "../../etc/passwd"})["id"].isalnum())
S.write_json(os.path.join(S.SESS_DIR, "old-show.json"), {**S.DEFAULT, "name": "Old show"})
S.migrate_sessions()
old = [S.read_json(p, {}) for p in S._session_files() if "old-show" in p]
check("pre-id files are migrated to <id>-<slug>.json once",
      not os.path.exists(os.path.join(S.SESS_DIR, "old-show.json")) and len(old) == 1 and old[0].get("id"))

# --- crash litter and launch behaviour
mine = os.path.join(S.SESS_DIR, f"x.json.{os.getpid() + 999999}.1.tmp")  # a dead process's half-written file
live = os.path.join(S.DATA, f"caption_presets.json.{os.getppid()}.1.tmp")  # another LIVE process still writing
for p in (mine, live):
    open(p, "w").close()
S.sweep_tmp()
check("startup sweeps .tmp files left by a crashed write", not os.path.exists(mine))
check("…but not those of a LiveTranslate still running", os.path.exists(live))
os.remove(live)
A = S.Auth({"secret": "a" * 64})
tok = A.token()
A.on_launch(resumed=True)
check("admin cookies survive a supervised restart (crash recovery / port change)", A.valid(tok))
A.on_launch(resumed=False)
check("admin cookies end when LiveTranslate is started afresh", not A.valid(tok))

# --- Issue 5: MlxRuntime restart vs url() (fake child process, no model loaded)
import threading  # noqa: E402


class FakeProc:
    alive = []

    def __init__(self, *a, **k):
        self.dead = False
        FakeProc.alive.append(self)

    def poll(self):
        return 0 if self.dead else None

    def kill(self):
        self.dead = True

    terminate = kill

    def wait(self, t=None):
        return 0


R, real_popen, real_open, real_sleep = S.MlxRuntime, S.subprocess.Popen, S.urllib.request.urlopen, S.time.sleep
S.subprocess.Popen, S.urllib.request.urlopen = FakeProc, (lambda *a, **k: None)  # runtime answers at once
try:
    u1 = R.url()
    R.restart(u1)  # FR thread: refused by runtime 1
    u2 = R.url()   # a third thread starts runtime 2
    p2 = R.proc
    R.restart(u1)  # ES thread: ALSO refused by runtime 1, reported late
    check("a late failure from the old runtime doesn't kill the new one", not p2.dead and R.url() == u2)
    R.restart(u2)
    check("restart clears the runtime so the next url() starts a fresh one", R.proc is None and R.port == 0 and p2.dead)

    def deaf(*a, **k):
        raise OSError("connection refused")
    S.urllib.request.urlopen, S.time.sleep = deaf, (lambda x: None)  # starts but never answers
    box = {}

    def call_url():
        try:
            R.url()
        except RuntimeError as e:
            box["err"] = e
    t = threading.Thread(target=call_url, daemon=True)
    t.start(); t.join(5)
    check("a runtime that never answers raises instead of deadlocking url()", not t.is_alive() and box.get("err"))
    check("…and it is killed and forgotten", R.proc is None and all(p.dead for p in FakeProc.alive))
    check("the lock is free afterwards", R.lock.acquire(timeout=1) and (R.lock.release() or True))
finally:
    S.subprocess.Popen, S.urllib.request.urlopen, S.time.sleep = real_popen, real_open, real_sleep

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
