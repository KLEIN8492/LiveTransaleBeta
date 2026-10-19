#!/usr/bin/env python3
"""LiveTranslate on Windows/Linux: the system-tray twin of menubar.py (macOS).

Runs server.py as a child process (so Quit — or a crash here — always frees the audio interface and the port),
restarts it if it crashes (resuming a live session), runs the private AI runtime, and offers the same menu as the
Mac menu bar. No tray available (a Linux box without a desktop): runs headless — same server, same recovery.

    pythonw tray.py [--launcher <LiveTranslate.exe>]       # started by the Windows launcher / Linux script
"""
import argparse, json, os, shutil, subprocess, sys, threading, time, urllib.request, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = sys.platform == "win32"
SUP = os.environ.get("LT_SUPPORT") or (os.path.join(os.environ.get("LOCALAPPDATA", ""), "LiveTranslate") if IS_WIN
                                       else os.path.expanduser("~/.local/share/LiveTranslate"))
LOG = os.path.join(SUP, "logs", "LiveTranslate.log")
OLLAMA_PORT = 11439
NO_WINDOW = {"creationflags": 0x08000000} if IS_WIN else {}
AUTOSTART = os.path.expanduser("~/.config/autostart/livetranslate.desktop")



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

def ollama_exe():
    for p in (os.path.join(SUP, "ollama", "ollama.exe"), os.path.join(SUP, "ollama", "bin", "ollama")):
        if os.path.exists(p):
            return p
    return None


def server_up():
    try:
        with urllib.request.urlopen(local() + "/api/state", timeout=1) as r:
            return json.load(r)
    except Exception:
        return None


class App:
    def __init__(self, launcher):
        self.launcher = launcher
        self.proc = self.ollama = self.icon = None
        self.crashes, self.was_live, self.opened, self.quitting = [], False, False, False
        self.base, self.status = local(), "Starting…"
        self.access = ""

    # ---- private AI runtime (same as the Mac: own port, own models, 2 languages translated in parallel)
    def start_ollama(self):
        exe = ollama_exe()
        if not exe:
            os.environ.setdefault("LT_OLLAMA", "http://127.0.0.1:11434")  # a system Ollama, if there is one
            return
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}", "OLLAMA_NUM_PARALLEL": "2",
               "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0", "OLLAMA_KEEP_ALIVE": "60m",
               "OLLAMA_MODELS": os.path.join(SUP, "ollama", "models")}
        ai_log = open(os.path.join(SUP, "logs", "LiveTranslate-AI.log"), "a")
        self.ollama = subprocess.Popen([exe, "serve"], env=env, stdout=ai_log, stderr=subprocess.STDOUT, **NO_WINDOW)
        os.environ["LT_OLLAMA"] = f"http://127.0.0.1:{OLLAMA_PORT}"

    # ---- server process
    def start_server(self, resume=False):
        if os.path.exists(LOG) and os.path.getsize(LOG) > 10e6:
            os.replace(LOG, LOG + ".1")
        log = open(LOG, "a", buffering=1, encoding="utf-8")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} launch\n")
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUNBUFFERED": "1",
               "PYTHONIOENCODING": "utf-8", "LT_PARENT": "1", "LT_PARENT_PID": str(os.getpid())}
        args = ([sys.executable, os.path.join(HERE, "server.py")]  # port: the server reads config.json itself
                + (["--port", os.environ["LT_PORT"]] if os.environ.get("LT_PORT") else []) + (["--autostart"] if resume else []))
        try:
            os.remove(os.path.join(SUP, "running-port"))  # until the new server writes where it listens
        except OSError:
            pass
        self.proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=HERE, **NO_WINDOW)

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # lines are written as they happen; NDI senders follow their parent out
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def quit(self, *_):
        self.quitting = True
        self.stop_server()
        if self.ollama and self.ollama.poll() is None:
            self.ollama.terminate()
        if self.icon:
            self.icon.stop()
        os._exit(0)

    def restart(self, *_):
        self.stop_server()
        self.start_server()

    def watch(self):
        while not self.quitting:
            time.sleep(1.5)
            if self.proc.poll() is not None:
                if self.proc.returncode == 0:  # "Quit LiveTranslate" in Control
                    return self.quit()
                if self.proc.returncode == 75:  # moved to another port from Control: start again there
                    self.start_server(resume=self.was_live)
                    continue
                now = time.time()
                self.crashes = [t for t in self.crashes if now - t < 300] + [now]
                if len(self.crashes) > 5:
                    self.set_status(f"Server keeps crashing (exit {self.proc.returncode}) — see the log")
                    continue
                self.notify("Server restarted — " + ("resuming the live session…" if self.was_live else "after an error."))
                self.start_server(resume=self.was_live)
                continue
            st = server_up()
            if not st:
                self.set_status("Starting server…")
                continue
            self.base = st["base"]
            self.access = ("Other devices: admin password" if st.get("has_password") and not st.get("weak")
                           else "Other devices: set an admin password in Control → Access")
            self.was_live = st["running"] or st["loading"]
            if st["error"]:
                text = st["error"][:60]
            elif st["loading"]:
                text = "Loading models…"
            elif st["running"]:
                c = st["counts"]
                text = (f"{'PAUSED' if st['paused'] else 'LIVE'} · {st['session']['name']} · "
                        f"{c['guest']} phones · {c['overlay']} overlays")
            else:
                text = "Ready — no session running"
            self.set_status(text)
            if not self.opened:  # first launch: straight to Control
                self.opened = True
                self.open("/control")

    # ---- tray
    def set_status(self, text):
        if text != self.status:
            self.status = text
            if self.icon:
                self.icon.title = "LiveTranslate — " + text[:100]
                self.icon.update_menu()

    def notify(self, text):
        try:
            if self.icon and self.icon.HAS_NOTIFICATION:
                self.icon.notify(text, "LiveTranslate")
        except Exception:
            pass

    def open(self, path):
        webbrowser.open(local() + path)

    def copy(self, path):
        text = self.base + path
        try:
            if IS_WIN:
                subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, **NO_WINDOW)
            else:
                tool = next((t for t in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"])
                             if shutil.which(t[0])), None)
                subprocess.run(tool, input=text.encode(), check=True)
            self.notify("Copied " + text)
        except Exception:
            self.notify(text)

    def login_on(self):
        if IS_WIN:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                    winreg.QueryValueEx(k, "LiveTranslate")
                    return True
            except OSError:
                return False
        return os.path.exists(AUTOSTART)

    def toggle_login(self, *_):
        set_login(not self.login_on(), self.launcher)

    def setup(self, *_):
        if self.launcher:
            subprocess.Popen([self.launcher, "--setup"], **NO_WINDOW)

    def show_log(self, *_):
        if IS_WIN:
            os.startfile(LOG)
        else:
            subprocess.Popen(["xdg-open", LOG])

    def run(self, tray=True):
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        self.start_ollama()
        self.start_server()
        if not tray:
            self.opened = True  # service: nobody to open a browser for
        threading.Thread(target=self.watch, daemon=True).start()
        try:
            if not tray:
                raise RuntimeError("headless")
            import pystray
            from PIL import Image
            from pystray import Menu, MenuItem as Item
            img = Image.open(os.path.join(HERE, "static", "icon.png"))
            self.icon = pystray.Icon("LiveTranslate", img, "LiveTranslate", Menu(
                Item(lambda _: self.status, None, enabled=False),
                Item(lambda _: self.access or "Access: …", None, enabled=False),
                Menu.SEPARATOR,
                Item("Open Control", lambda *_: self.open("/control"), default=True),
                Item("Open audience page", lambda *_: self.open("/")),
                Item("Copy overlay URL (vMix / OBS)", lambda *_: self.copy("/overlay?preset=default")),
                Item("Copy Control URL (other devices)", lambda *_: self.copy("/control")),
                Menu.SEPARATOR,
                Item("Add languages or AI models…", self.setup, visible=bool(self.launcher)),
                Item("Open at login", self.toggle_login, checked=lambda _: self.login_on(), visible=bool(self.launcher)),
                Menu.SEPARATOR,
                Item("Show log", self.show_log),
                Item("Restart server", self.restart),
                Item("Quit LiveTranslate", self.quit)))
            self.icon.run()  # blocks; double-click (Windows) = Open Control
        except Exception as e:  # no desktop / no tray: keep serving headless
            print(f"[tray] no system tray ({e}) — running headless; Control at {local()}/control", flush=True)
            while True:
                time.sleep(3600)


def set_login(on, launcher):
    """Start LiveTranslate when the user logs in (shared with the setup page)."""
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, "LiveTranslate", 0, winreg.REG_SZ, f'"{launcher}"')
            else:
                try:
                    winreg.DeleteValue(k, "LiveTranslate")
                except OSError:
                    pass
    elif on:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=LiveTranslate\nExec={launcher}\nX-GNOME-Autostart-enabled=true\n")
    elif os.path.exists(AUTOSTART):
        os.remove(AUTOSTART)


def main():
    if sys.stdout is None:  # pythonw: no console — messages go to the log
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        sys.stdout = sys.stderr = open(LOG, "a", buffering=1, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--launcher", default="")
    ap.add_argument("--headless", action="store_true", help="no tray icon (Linux service)")
    a = ap.parse_args()
    if server_up():  # already running (tray icon hidden in the overflow): just bring Control up
        webbrowser.open(local() + "/control")
        return
    App(a.launcher).run(tray=not a.headless)


if __name__ == "__main__":
    main()
