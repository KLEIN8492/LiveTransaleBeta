#!/usr/bin/env python3
"""LiveTranslate setup — installs everything the app needs so it then runs 100 % offline.

Standard library only: it runs on a bare Python (fetched by the bundled `uv`) before any package exists.
Serves a setup page on 127.0.0.1, checks the machine, then installs into the support folder:
  venv/ (Python packages) · hf/ (speech models) · xdg/data/argos-translate/ (offline translation) · ollama/ (private AI runtime + model)
Every step is idempotent: re-running setup only adds what is missing (new languages, a bigger AI model).

    python installer.py --sup <support dir> --app <bundle app dir> --uv <uv binary> --bundle <LiveTranslate.app or .exe>

Same file on every platform: macOS (MLX speech on Apple Silicon), Windows and Linux (faster-whisper — on an NVIDIA
card when there is one, otherwise the CPU with smaller models).
"""
import argparse, hashlib, json, os, secrets, platform, shutil, subprocess, sys, tarfile, threading, time, urllib.request, webbrowser, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LANGS = {"en": "English", "fr": "Français", "es": "Español", "de": "Deutsch", "it": "Italiano", "pt": "Português",
         "nl": "Nederlands", "ro": "Română", "pl": "Polski", "uk": "Українська", "ru": "Русский", "ar": "العربية",
         "zh": "中文", "ja": "日本語", "ko": "한국어"}
IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform == "win32"
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.dirname(_here)]  # models.py sits next to this file in builds, one level up in the repo
from models import TIERS, ram_bytes, nvidia_gpu, report as hw_report  # noqa: E402
# Mac: the local AI runs on MLX (server.py MLX_MODELS) — ~2.6x faster per line than Ollama for Gemma 3, and no
# Ollama runtime to install. Keys = the Ollama names Control shows; values = (MLX repo, GB).
MLX_AI = {t["model"]: t["mlx"] for t in TIERS}
NO_WINDOW = {"creationflags": 0x08000000} if IS_WIN else {}
WHISPER_MLX = ["mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-base-mlx"]
WHISPER_GPU = ["mobiuslabsgmbh/faster-whisper-large-v3-turbo", "Systran/faster-whisper-base"]  # ~1.7 GB
WHISPER_CPU = ["Systran/faster-whisper-small", "Systran/faster-whisper-base"]  # ~0.6 GB; turbo is too slow on a CPU
CUDA_WHEELS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]  # what faster-whisper needs on an NVIDIA card
VOICE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
VOICE_FILES = {"kokoro-v1.0.int8.onnx": 92361271, "voices-v1.0.bin": 28214398}
AI = {t["key"]: (t["model"], t["ollama_gb"]) for t in TIERS}  # balanced / accurate / maximum (models.py)
if os.environ.get("LT_TEST_AI_MODEL"):  # test hook: exercise the AI step with a tiny model
    AI["balanced"] = (os.environ["LT_TEST_AI_MODEL"], 0.3)
OLLAMA_VERSION = "v0.40.2"
OLLAMA_URL = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/" + (
    "ollama-darwin.tgz" if IS_MAC else "ollama-windows-amd64.zip" if IS_WIN else "ollama-linux-amd64.tar.zst")
OLLAMA_MB = 170 if IS_MAC else 1470 if IS_WIN else 1440  # Windows/Linux builds carry the GPU libraries
OLLAMA_PORT = 11439  # private instance: never clashes with an Ollama the user runs on 11434
GB = 1e9
PLIST = os.path.expanduser("~/Library/LaunchAgents/local.tools.livetranslate.plist")


def env_for(sup):
    """Same environment the app runs with: every model path inside the support folder."""
    return {**os.environ, "LT_DATA": sup, "HF_HOME": f"{sup}/hf",
            "XDG_DATA_HOME": f"{sup}/xdg/data", "XDG_CACHE_HOME": f"{sup}/xdg/cache", "XDG_CONFIG_HOME": f"{sup}/xdg/config",
            "UV_PYTHON_INSTALL_DIR": f"{sup}/python", "UV_CACHE_DIR": f"{sup}/uv-cache", "UV_PYTHON_PREFERENCE": "only-managed",
            "OLLAMA_MODELS": f"{sup}/ollama/models", "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}",
            "LT_OLLAMA": f"http://127.0.0.1:{OLLAMA_PORT}", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
            "LT_SUPPORT": sup}


def du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


class Setup:
    def __init__(self, a):
        self.a, self.sup = a, a.sup
        self.venv_py = f"{self.sup}/venv/Scripts/python.exe" if IS_WIN else f"{self.sup}/venv/bin/python"
        self.env = env_for(self.sup)
        self.steps, self.error, self.done, self.running, self.log = [], "", False, False, []
        self.installed = self.load_installed()
        self.gpu = None if IS_MAC else nvidia_gpu()

    # ---------------------------------------------------------------- machine check
    def load_installed(self):
        try:
            with open(f"{self.sup}/installed.json") as f:
                return json.load(f)
        except Exception:
            return {}

    def whisper(self):
        return WHISPER_MLX if IS_MAC else WHISPER_GPU if self.gpu else WHISPER_CPU

    def check(self):
        ram = ram_bytes() / GB
        os.makedirs(self.sup, exist_ok=True)
        free = shutil.disk_usage(self.sup).free / GB
        try:
            urllib.request.urlopen("https://huggingface.co", timeout=4)
            online = True
        except Exception:
            online = False
        fit = hw_report()  # per tier: good / slow / tight / no on THIS machine, and the strongest that runs well
        rec = fit["recommend"]
        machine = platform.machine().lower()
        supported = machine == "arm64" if IS_MAC else machine in ("amd64", "x86_64")
        sys.path.insert(0, self.a.app)
        try:
            from ndi_lib import find_runtime
            ndi = bool(find_runtime())
        except Exception:
            ndi = False
        return {"platform": "mac" if IS_MAC else "windows" if IS_WIN else "linux", "supported": supported,
                "arm64": supported, "os": os_name(), "macos": os_name(), "ram_gb": round(ram), "gpu": self.gpu,
                "free_gb": round(free, 1), "online": online, "ndi": ndi,
                "langs": LANGS, "ai": {k: {"model": m, "gb": MLX_AI[m][1] if IS_MAC else g} for k, (m, g) in AI.items()},
                "recommend_ai": rec, "fit": {t["key"]: {k: t[k] for k in ("level", "why", "secs", "accuracy")}
                                             for t in fit["tiers"]}, "chip": fit["hardware"]["chip"],
                "installed": self.installed, "login": self.login_on(), "has_password": self._has_password(),
                "voice_gb": round(sum(VOICE_FILES.values()) / GB, 2)}

    def _has_password(self):
        try:
            with open(f"{self.sup}/config.json") as f:
                return bool(json.load(f).get("admin"))
        except Exception:
            return False

    def needed_gb(self, langs, ai, voice=True):
        have = self.installed
        gb = 0.0 if os.path.exists(self.venv_py) else (2.2 if IS_MAC else 1.4)
        if self.gpu and not have.get("cuda"):
            gb += 1.3  # cuBLAS + cuDNN
        gb += 0.0 if have.get("speech") else (1.9 if IS_MAC or self.gpu else 0.65)
        gb += 0.12 * 2 * len(set(langs) - set(have.get("langs", [])) - {"en"})
        if voice and not have.get("voice"):
            gb += sum(VOICE_FILES.values()) / GB
        if ai != "none" and IS_MAC:
            gb += 0 if have.get("ai") == ai else MLX_AI[AI[ai][0]][1]
        elif ai != "none":
            gb += (0.0 if self.ollama_exe() else OLLAMA_MB / 1000) + (0 if have.get("ai") == ai else AI[ai][1])
        return round(gb, 1)

    def ollama_exe(self):
        for p in (f"{self.sup}/ollama/ollama.exe", f"{self.sup}/ollama/bin/ollama"):
            if os.path.exists(p):
                return p
        return None

    # ---------------------------------------------------------------- running steps
    def step(self, sid, label):
        st = {"id": sid, "label": label, "status": "waiting", "detail": "", "pct": None}
        self.steps.append(st)
        return st

    def run_cmd(self, st, cmd, env=None, parse=None):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env or self.env,
                             bufsize=1, encoding="utf-8", errors="replace", **NO_WINDOW)
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            self.log = (self.log + [line])[-200:]
            if parse:
                parse(line)
            else:
                st["detail"] = line[-110:]
        if p.wait() != 0:
            raise RuntimeError(f"{st['label']} failed — see the log below")

    def py(self, st, code, parse=None):
        self.run_cmd(st, [self.venv_py, "-c", code], parse=parse)

    def download(self, url, dest, st, lo, hi, label):
        """Stream a file to disk with progress mapped onto pct lo..hi; resumes nothing, writes atomically."""
        with urllib.request.urlopen(url, timeout=60) as r, open(dest + ".part", "wb") as f:
            total, got = int(r.headers.get("Content-Length", 0)), 0
            while chunk := r.read(1 << 20):
                f.write(chunk); got += len(chunk)
                st["pct"] = lo + int(got / total * (hi - lo)) if total else None
                st["detail"] = f"{label} {got / 1e6:.0f} of {total / 1e6:.0f} MB"
        os.replace(dest + ".part", dest)

    def set_admin_password(self, pw):
        """Same format as server.py's Auth (PBKDF2-SHA256, 200 000 rounds, salted) — the password itself is never stored."""
        path, conf = f"{self.sup}/config.json", {}
        if os.path.exists(path):
            with open(path) as f:
                conf = json.load(f)
        salt = secrets.token_hex(16)
        conf["admin"] = {"salt": salt, "changed": time.strftime("%Y-%m-%d %H:%M"),
                         "hash": hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()}
        conf.pop("pin", None)
        conf["secret"] = secrets.token_hex(32)
        with open(path + ".tmp", "w") as f:
            json.dump(conf, f, indent=2)
        os.chmod(path + ".tmp", 0o600)
        os.replace(path + ".tmp", path)

    def install(self, langs, ai, login, password="", voice=True):
        langs = sorted(set(langs) | {"en"})
        self.steps, self.error, self.done, self.running = [], "", False, True
        s_env = self.step("env", "Python and libraries" + (" · NVIDIA GPU support" if self.gpu else ""))
        s_sp = self.step("speech", "Speech recognition models")
        s_tr = self.step("translate", f"Offline translation · {', '.join(LANGS[l] for l in langs)}")
        s_vo = self.step("voice", "Natural voice for listeners") if voice else None
        s_ai = self.step("ai", f"Local AI · {AI[ai][0]}") if ai != "none" else None
        s_ok = self.step("verify", "Offline self-test")
        s_fin = self.step("finish", "Finish")
        ollama = None
        whisper = self.whisper()
        try:
            # 1. Python + packages (uv: its own Python 3.11, nothing touches the system)
            s_env["status"] = "active"
            if not os.path.islink(f"{self.sup}/venv"):  # a developer machine links venv to the project's own: re-creating
                # it here would re-base that venv onto this folder's Python (packages are still added below)
                self.run_cmd(s_env, [self.a.uv, "venv", "--python", "3.11", "--allow-existing", f"{self.sup}/venv"])
            if not IS_MAC and not IS_WIN:  # Linux: PyPI's torch (for Argos' sentence splitter) is a 2.5 GB CUDA build
                self.run_cmd(s_env, [self.a.uv, "pip", "install", "--python", self.venv_py, "torch",
                                     "--index-url", "https://download.pytorch.org/whl/cpu"])
            self.run_cmd(s_env, [self.a.uv, "pip", "install", "--python", self.venv_py, "-r", f"{self.a.app}/requirements.txt"])
            if self.gpu:
                self.run_cmd(s_env, [self.a.uv, "pip", "install", "--python", self.venv_py] + CUDA_WHEELS)
            s_env.update(status="done", detail="Installed")

            # 2. speech models (progress = bytes on disk vs expected size)
            s_sp["status"] = "active"
            have = subprocess.run([self.venv_py, "-c", "from huggingface_hub import snapshot_download as d\n"
                                   + "".join(f"d('{r}', local_files_only=True)\n" for r in whisper)],
                                  env=self.env, capture_output=True, **NO_WINDOW).returncode == 0
            if not have:
                stop = threading.Event()
                expect = (1.76 if IS_MAC or self.gpu else 0.62) * GB
                base0 = du(f"{self.sup}/hf")
                def watch():
                    while not stop.wait(0.5):
                        got = du(f"{self.sup}/hf") - base0
                        s_sp["pct"] = min(99, int(got / expect * 100)); s_sp["detail"] = f"{got / GB:.2f} of ~{expect / GB:.2f} GB"
                threading.Thread(target=watch, daemon=True).start()
                try:
                    self.py(s_sp, "from huggingface_hub import snapshot_download as d\n" + "".join(f"d('{r}')\n" for r in whisper),
                            parse=lambda l: None)
                finally:
                    stop.set()
            s_sp.update(status="done", pct=100, detail="Whisper turbo + base" if IS_MAC or self.gpu else
                        "Whisper small + base (CPU — an NVIDIA card allows the larger, more accurate model)")

            # 3. offline translation packages: English ↔ each language (other pairs pivot through English)
            s_tr["status"] = "active"
            pairs = [p for l in langs if l != "en" for p in ((f"en", l), (l, "en"))]
            code = f"""
import argostranslate.package as p
pairs = {pairs!r}
have = {{(x.from_code, x.to_code) for x in p.get_installed_packages()}}
todo = [x for x in pairs if x not in have]
print('TODO', len(todo), flush=True)
if todo:
    p.update_package_index()
    avail = {{(x.from_code, x.to_code): x for x in p.get_available_packages()}}
    for i, x in enumerate(todo, 1):
        if x not in avail:
            print('MISSING', x[0], x[1], flush=True); continue
        print('GET', i, len(todo), x[0], x[1], flush=True)
        p.install_from_path(avail[x].download())
print('OK', flush=True)
"""
            def tr_parse(line):
                w = line.split()
                if w[0] == "GET":
                    s_tr["pct"] = int((int(w[1]) - 1) / int(w[2]) * 100); s_tr["detail"] = f"{LANGS.get(w[3], w[3])} → {LANGS.get(w[4], w[4])}"
                elif w[0] == "MISSING":
                    s_tr["detail"] = f"No offline package for {w[1]} → {w[2]} (the local AI will handle it)"
            self.py(s_tr, code, parse=tr_parse)
            s_tr.update(status="done", pct=100, detail=f"{len(langs)} languages")

            # 4. natural voice (Kokoro, ~120 MB): what phones hear when they tap Listen
            if s_vo:
                s_vo["status"] = "active"
                d = f"{self.sup}/models/kokoro"
                os.makedirs(d, exist_ok=True)
                for i, (name, size) in enumerate(VOICE_FILES.items()):
                    if not (os.path.exists(f"{d}/{name}") and os.path.getsize(f"{d}/{name}") == size):
                        self.download(VOICE_URL + name, f"{d}/{name}", s_vo, i * 80, 80 + i * 20, "Voice model")
                s_vo.update(status="done", pct=100, detail="English · Français · Español · Italiano · Português")

            # 5a. Mac: the AI model as MLX weights (no runtime to install; server.py starts mlx_srv.py)
            if s_ai and IS_MAC:
                s_ai["status"] = "active"
                repo, gb = MLX_AI[AI[ai][0]]
                stop, base0 = threading.Event(), du(f"{self.sup}/hf")
                def watch_ai():
                    while not stop.wait(0.5):
                        got = du(f"{self.sup}/hf") - base0
                        s_ai["pct"] = min(99, int(got / (gb * GB) * 100)); s_ai["detail"] = f"{AI[ai][0]} · {got / GB:.2f} of ~{gb} GB"
                threading.Thread(target=watch_ai, daemon=True).start()
                try:
                    self.py(s_ai, f"from huggingface_hub import snapshot_download as d\nd({repo!r})", parse=lambda l: None)
                finally:
                    stop.set()
                s_ai.update(status="done", pct=100, detail=f"{AI[ai][0]} (MLX)")

            # 5b. Windows/Linux: private Ollama runtime + model
            if s_ai and not IS_MAC:
                s_ai["status"] = "active"
                exe = self.ollama_exe()
                if not exe:
                    root = f"{self.sup}/ollama"
                    os.makedirs(root, exist_ok=True)
                    arc = f"{root}/{OLLAMA_URL.rsplit('/', 1)[1]}"
                    self.download(OLLAMA_URL, arc, s_ai, 0, 20, "AI runtime")
                    s_ai["detail"] = "Unpacking the AI runtime…"
                    if IS_WIN:
                        with zipfile.ZipFile(arc) as z:
                            z.extractall(root)
                    elif IS_MAC:
                        os.makedirs(f"{root}/bin", exist_ok=True)
                        with tarfile.open(arc) as t:
                            t.extractall(f"{root}/bin", filter="data")
                        found = [os.path.join(r, f) for r, _, fs in os.walk(f"{root}/bin") for f in fs if f == "ollama"]
                        if found and found[0] != f"{root}/bin/ollama":
                            os.symlink(found[0], f"{root}/bin/ollama")
                    else:  # Linux: .tar.zst (bin/ollama + lib/ollama/…); zstandard comes with the venv
                        self.py(s_ai, f"""
import tarfile, zstandard
with open({arc!r}, 'rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as z, tarfile.open(fileobj=z, mode='r|') as t:
    t.extractall({root!r}, filter='data')
""")
                    os.remove(arc)
                    exe = self.ollama_exe()
                    if not exe:
                        raise RuntimeError("AI runtime archive did not contain ollama")
                    if not IS_WIN:
                        os.chmod(exe, 0o755)
                ollama = subprocess.Popen([exe, "serve"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          **NO_WINDOW)
                for _ in range(60):
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags", timeout=1); break
                    except Exception:
                        time.sleep(0.5)
                model = AI[ai][0]
                req = urllib.request.Request(f"http://127.0.0.1:{OLLAMA_PORT}/api/pull", json.dumps({"model": model}).encode(),
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=3600) as r:
                    for line in r:
                        d = json.loads(line)
                        if d.get("error"):
                            raise RuntimeError(d["error"])
                        if d.get("total"):
                            s_ai["pct"] = 20 + int(d.get("completed", 0) / d["total"] * 80)
                            s_ai["detail"] = f"{model} · {d.get('completed', 0) / GB:.2f} of {d['total'] / GB:.2f} GB"
                        else:
                            s_ai["detail"] = d.get("status", "")
                s_ai.update(status="done", pct=100, detail=model)

            # 6. offline self-test: everything must load and work with the network switched off for libraries
            s_ok["status"] = "active"
            env = {**self.env, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
            if IS_MAC:
                speech = f"""
import mlx_whisper
for r in {whisper!r}:
    mlx_whisper.transcribe(np.zeros(16000, np.float32), path_or_hf_repo=r)
"""
            else:
                speech = f"""
sys.path.insert(0, {self.a.app!r})
import server
gpu = server.cuda_libs()
from faster_whisper import WhisperModel
for r in {whisper!r}:
    list(WhisperModel(r, device='cuda' if gpu else 'cpu', compute_type='float16' if gpu else 'int8').transcribe(np.zeros(16000, np.float32))[0])
print('speech on', 'GPU' if gpu else 'CPU', flush=True)
"""
            code = f"""
import numpy as np, json, sys, urllib.request
{speech}
print('speech ok', flush=True)
import argostranslate.translate as t
for l in {[l for l in langs if l != 'en']!r}:
    print('argos', l, t.translate('Good evening and welcome.', 'en', l), flush=True)
if {bool(s_vo)!r}:
    sys.path.insert(0, {self.a.app!r})
    import tts
    mp3 = tts.Voice({self.sup!r}).get(0, 'Good evening.', 'en').result(120)
    print('voice', len(mp3), 'bytes', flush=True)
model = {AI[ai][0] if s_ai else None!r}
if model and {IS_MAC!r}:
    from mlx_lm import load, generate
    m, tok = load({MLX_AI.get(AI[ai][0], ("",))[0] if s_ai and IS_MAC else ""!r})
    msgs = [{{"role": "user", "content": "Translate into French, answer only the translation: Good evening."}}]
    print('ai', generate(m, tok, tok.apply_chat_template(msgs, add_generation_prompt=True), max_tokens=20).strip(), flush=True)
elif model:
    body = json.dumps({{"model": model, "stream": False, "messages": [{{"role": "user", "content": "Translate into French, answer only the translation: Good evening."}}]}}).encode()
    out = json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:{OLLAMA_PORT}/api/chat', body), timeout=300))
    print('ai', out['message']['content'].strip(), flush=True)
print('ALL OK', flush=True)
"""
            lines = []
            self.run_cmd(s_ok, [self.venv_py, "-c", code], env=env, parse=lambda l: (lines.append(l), s_ok.update(detail=l[-110:])))
            if "ALL OK" not in lines:
                raise RuntimeError("self-test did not complete")
            s_ok.update(status="done", detail=" · ".join(l for l in lines if l.startswith(("speech on", "argos", "voice", "ai")))[:160])

            # 7. finish: login item + record what is installed
            s_fin["status"] = "active"
            self.set_login(login)
            if password:
                self.set_admin_password(password)
            self.installed = {"version": time.strftime("%Y.%m.%d"), "speech": True, "cuda": bool(self.gpu),
                              "voice": bool(s_vo) or self.installed.get("voice", False),
                              "ai": ai if s_ai else self.installed.get("ai", "none"),
                              "langs": sorted(set(langs) | set(self.installed.get("langs", []))), "date": time.strftime("%Y-%m-%d %H:%M")}
            with open(f"{self.sup}/installed.json", "w") as f:
                json.dump(self.installed, f, indent=2)
            subprocess.run([self.a.uv, "cache", "clean"], env=self.env, capture_output=True, **NO_WINDOW)
            s_fin.update(status="done", detail="Ready — runs offline from now on")
            self.done = True
        except Exception as e:
            for st in self.steps:
                if st["status"] == "active":
                    st["status"] = "failed"
            self.error = str(e)
        finally:
            if ollama:
                ollama.terminate()
            self.running = False

    def login_on(self):
        if IS_MAC:
            return os.path.exists(PLIST)
        sys.path.insert(0, self.a.app)
        from tray import App
        return App(self.a.bundle).login_on()

    def set_login(self, on):
        if not IS_MAC:
            sys.path.insert(0, self.a.app)
            from tray import set_login
            return set_login(on, self.a.bundle)
        if on:
            os.makedirs(os.path.dirname(PLIST), exist_ok=True)
            with open(PLIST, "w") as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.tools.livetranslate</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>{self.a.bundle}</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""")
        elif os.path.exists(PLIST):
            os.remove(PLIST)


def os_name():
    if IS_MAC:
        return "macOS " + platform.mac_ver()[0]
    if IS_WIN:
        build = int(platform.version().split(".")[-1] or 0)
        return f"Windows {'11' if build >= 22000 else platform.release()}"
    try:
        with open("/etc/os-release") as f:
            return next(l.split("=", 1)[1].strip().strip('"') for l in f if l.startswith("PRETTY_NAME="))
    except Exception:
        return "Linux"


# -------------------------------------------------------------------- web UI
def serve(setup, a):
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installer.html")
    fonts = os.path.join(a.app, "static", "fonts")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            if self.path == "/":
                return self.send(200, open(page, "rb").read(), "text/html; charset=utf-8")
            if self.path.startswith("/fonts/"):
                f = os.path.join(fonts, os.path.basename(self.path))
                if os.path.exists(f):
                    return self.send(200, open(f, "rb").read(), "text/css" if f.endswith(".css") else "font/woff2")
            if self.path == "/api/check":
                return self.send(200, setup.check())
            if self.path == "/api/progress":
                return self.send(200, {"steps": setup.steps, "done": setup.done, "error": setup.error,
                                       "running": setup.running, "log": setup.log[-12:]})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/estimate":
                return self.send(200, {"gb": setup.needed_gb(body.get("langs", []), body.get("ai", "none"),
                                                             body.get("voice", True))})
            if self.path == "/api/install":
                if not setup.running:
                    threading.Thread(target=setup.install, args=(body.get("langs", ["en"]), body.get("ai", "none"),
                                                                  bool(body.get("login")), body.get("password", ""),
                                                                  body.get("voice", True) is not False),
                                     daemon=True).start()
                return self.send(200, {"ok": True})
            if self.path == "/api/launch":
                self.send(200, {"ok": True})
                if IS_MAC:
                    subprocess.Popen(["/usr/bin/open", "-n", a.bundle])
                else:  # the launcher (LiveTranslate.exe / the Linux script) now finds installed.json and starts the app
                    subprocess.Popen([a.bundle], **NO_WINDOW)
                threading.Timer(1.0, lambda: os._exit(0)).start()
                return
            self.send(404, {"error": "not found"})

    srv = ThreadingHTTPServer((a.host, a.port), H)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    with open(os.path.join(a.sup, "setup-port"), "w") as f:
        f.write(str(srv.server_address[1]))
    print("setup page:", url, flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    srv.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sup", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--uv", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = reachable from another computer (Linux box without a screen)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.sup, exist_ok=True)
    if sys.stdout is None:  # Windows pythonw (no console): keep a log like the Mac launcher does
        os.makedirs(os.path.join(a.sup, "logs"), exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(a.sup, "logs", "setup.log"), "a", buffering=1, encoding="utf-8")
    lock = os.path.join(a.sup, "setup-port")
    try:  # setup already open (double-clicked twice): bring its page back instead of starting a second one
        port = int(open(lock).read())
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/progress", timeout=1)
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return
    except Exception:
        pass
    serve(Setup(a), a)


if __name__ == "__main__":
    main()
