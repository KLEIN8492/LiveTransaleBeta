#!/usr/bin/env python3
"""LiveTranslate menu-bar app: runs server.py as a child process and gives quick access to its pages.

The server is a separate process on purpose: Quit (or a crash here) always tears it down,
so the audio interface is released and the port is free for the next launch.
"""
import json, os, shutil, signal, subprocess, sys, threading, time, urllib.request, webbrowser

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSPasteboard, NSPasteboardTypeString

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.expanduser("~/Library/Logs/LiveTranslate.log")
SUP = os.environ.get("LT_SUPPORT", os.path.expanduser("~/Library/Application Support/LiveTranslate"))
LAUNCHER = os.path.normpath(os.path.join(HERE, "..", "..", "MacOS", "LiveTranslate"))
BUNDLE = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
PLIST = os.path.expanduser("~/Library/LaunchAgents/local.tools.livetranslate.plist")
OLLAMA_EXE, OLLAMA_PORT = os.path.join(SUP, "ollama", "bin", "ollama"), 11439



def wanted_port():
    """LT_PORT, else the port saved in Control → Access → Network (config.json), else 8180 (8000 = Companion)."""
    if os.environ.get("LT_PORT"):
        return int(os.environ["LT_PORT"])
    try:
        with open(os.path.join(SUP, "config.json")) as f:
            return int(json.load(f).get("port") or 8180)
    except Exception:
        return 8180


def local():
    """Where the server really listens: it moves to the next free port if another program holds ours."""
    try:
        with open(os.path.join(SUP, "running-port")) as f:
            return f"http://127.0.0.1:{int(f.read())}"
    except Exception:
        return f"http://127.0.0.1:{wanted_port()}"

class App(rumps.App):
    def __init__(self):
        super().__init__("LiveTranslate", title="LT ○", quit_button=None)
        self.status = rumps.MenuItem("Starting…")
        self.login_item = rumps.MenuItem("Open at login", callback=self.toggle_login)
        self.login_item.state = os.path.exists(PLIST)
        self.pin = rumps.MenuItem("Access: …")
        self.menu = [self.status, self.pin, None,
                     rumps.MenuItem("Open Control", callback=self.open_control, key="o"),
                     rumps.MenuItem("Open audience page", callback=lambda _: self.open("/")),
                     rumps.MenuItem("Copy overlay URL (vMix / OBS)", callback=self.copy_overlay),
                     rumps.MenuItem("Copy Control URL (other devices)", callback=lambda _: self.copy("/control")),
                     None,
                     rumps.MenuItem("Add languages or AI models…", callback=self.setup),
                     self.login_item,
                     None,
                     rumps.MenuItem("Show log", callback=lambda _: subprocess.run(["open", "-a", "Console", LOG])),
                     rumps.MenuItem("Restart server", callback=self.restart),
                     rumps.MenuItem("Quit LiveTranslate", callback=self.quit, key="q")]
        self.base, self.proc, self.opened = local(), None, False
        self.crashes, self.was_live = [], False
        self.ollama = None
        self.start_ollama()
        self.start_server()
        rumps.Timer(self.poll, 1.5).start()

    # ---- private AI runtime (installed by setup into the support folder; never clashes with another Ollama)
    def start_ollama(self):
        """Private AI runtime on :11439 with 2 parallel requests, so each line's languages translate at the same
        time instead of one after the other. Installed app: the bundled runtime + its own models. Development Mac
        without one: a private instance of the system `ollama` binary, sharing the models already downloaded."""
        exe = OLLAMA_EXE if os.path.exists(OLLAMA_EXE) else shutil.which("ollama") or (
            "/opt/homebrew/bin/ollama" if os.path.exists("/opt/homebrew/bin/ollama") else None)
        try:  # an Ollama already runs here (e.g. Homebrew's): use it — a second server would load every model twice
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1)
            system_running = True
        except Exception:
            system_running = False
        if not exe or (exe != OLLAMA_EXE and system_running):
            os.environ.setdefault("LT_OLLAMA", "http://127.0.0.1:11434")
            return
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}", "OLLAMA_NUM_PARALLEL": "2",
               "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0", "OLLAMA_KEEP_ALIVE": "60m"}
        if exe == OLLAMA_EXE:
            env["OLLAMA_MODELS"] = os.path.join(SUP, "ollama", "models")
        else:
            env.pop("OLLAMA_MODELS", None)  # the system install's own models (~/.ollama/models)
        ai_log = open(os.path.expanduser("~/Library/Logs/LiveTranslate-AI.log"), "a")  # chatty: keep out of the main log
        self.ollama = subprocess.Popen([exe, "serve"], env=env, stdout=ai_log, stderr=subprocess.STDOUT)
        os.environ["LT_OLLAMA"] = f"http://127.0.0.1:{OLLAMA_PORT}"

    def setup(self, _):
        subprocess.Popen([LAUNCHER, "--setup"])

    def toggle_login(self, item):
        if item.state:
            if os.path.exists(PLIST):  # may already be gone (setup unticked it, or removed by hand)
                os.remove(PLIST)
        else:
            os.makedirs(os.path.dirname(PLIST), exist_ok=True)
            with open(PLIST, "w") as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>local.tools.livetranslate</string>
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>{BUNDLE}</string></array>
<key>RunAtLoad</key><true/></dict></plist>
""")
        item.state = not item.state

    # ---- server process
    def start_server(self, resume=False):
        if os.path.exists(LOG) and os.path.getsize(LOG) > 10e6:  # keep one previous log, 10 MB each
            os.replace(LOG, LOG + ".1")
        log = open(LOG, "a", buffering=1)
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} launch\n")
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUNBUFFERED": "1", "LT_PARENT": "1"}
        args = ([sys.executable, os.path.join(HERE, "server.py")]  # port: the server reads config.json itself
                + (["--port", os.environ["LT_PORT"]] if os.environ.get("LT_PORT") else []) + (["--autostart"] if resume else []))
        try:
            os.remove(os.path.join(SUP, "running-port"))  # until the new server writes where it listens
        except OSError:
            pass
        self.proc = subprocess.Popen(args,
                                     stdout=log, stderr=subprocess.STDOUT, env=env, cwd=HERE)

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)  # aiohttp shuts down cleanly on SIGINT
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self, _):
        self.stop_server()
        self.start_server()

    def quit(self, _):
        self.stop_server()
        if self.ollama and self.ollama.poll() is None:
            self.ollama.terminate()
        rumps.quit_application()

    # ---- status
    def poll(self, _):
        if self.proc.poll() == 75:  # moved to another port from Control: start again there, session resumes
            self.start_server(resume=self.was_live)
            return
        if self.proc.poll() is not None:  # crashed: restart, resuming the session if one was live
            now = time.time()
            self.crashes = [t for t in self.crashes if now - t < 300] + [now]
            if len(self.crashes) > 5:
                self.title, self.status.title = "LT ✕", f"Server keeps crashing (exit {self.proc.returncode}) — see log"
                return
            rumps.notification("LiveTranslate", "Server restarted",
                               "Resuming the live session…" if self.was_live else "Restarted after an error.")
            self.start_server(resume=self.was_live)
            return
        try:
            with urllib.request.urlopen(local() + "/api/state", timeout=1) as r:
                st = json.load(r)
        except Exception:
            self.status.title = "Starting server…"
            return
        self.base = st["base"]
        self.pin.title = ("Other devices: admin password" if st.get("has_password") and not st.get("weak")
                          else "Other devices: set an admin password in Control → Access")
        self.was_live = st["running"] or st["loading"]
        if st["error"]:  # audio lost etc. — session may still be live, capture is retrying
            self.title, text = "LT !", st["error"][:60]
        elif st["loading"]:
            self.title, text = "LT …", "Loading models…"
        elif st["running"]:
            self.title = "LT ◼" if st["paused"] else "LT ●"
            c = st["counts"]
            text = f"{'PAUSED' if st['paused'] else 'LIVE'} · {st['session']['name']} · {c['guest']} phones · {c['overlay']} overlays"
        else:
            self.title, text = "LT ○", "Ready — no session running"
        self.status.title = text
        if not self.opened:  # first launch of the day: go straight to Control
            self.opened = True
            self.open_control(None)

    # ---- actions
    def open(self, path):
        webbrowser.open(local() + path)

    def open_control(self, _):
        self.open("/control")

    def copy(self, path):
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(self.base + path, NSPasteboardTypeString)
        rumps.notification("LiveTranslate", "Copied", self.base + path)

    def copy_overlay(self, _):
        self.copy("/overlay?preset=default")


if __name__ == "__main__":
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    app = App()
    def _term(*_):  # Quit from Control, or the installer updating the app
        if app.ollama and app.ollama.poll() is None:
            app.ollama.terminate()
        os._exit(0)
    signal.signal(signal.SIGTERM, _term)
    app.run()
