#!/usr/bin/env python3
"""LiveTranslate — offline live speech translation server for events (Apple Silicon).

Audio in -> VAD -> Whisper (MLX) -> Argos or local LLM (Ollama) -> network outputs:
  /control  operator console (PIN)      /overlay  vMix / OBS browser-source captions
  /         audience phones (QR)        /card     printable QR card
"""
import argparse, asyncio, collections, difflib, hashlib, hmac, html, json, os, queue, re, secrets, signal, socket, subprocess, sys, threading, time
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
from aiohttp import web, WSMsgType

SR, FRAME = 16000, 480  # 30 ms frames
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.environ.get("LT_DATA") or HERE  # the .app sets ~/Library/Application Support/LiveTranslate
SESS_DIR, TRANS_DIR = os.path.join(DATA, "sessions"), os.path.join(DATA, "transcripts")
CONF_PATH = os.path.join(DATA, "config.json")
PRESETS_PATH = os.path.join(DATA, "caption_presets.json")
NDI_PATH = os.path.join(DATA, "ndi_outputs.json")
OLLAMA = os.environ.get("LT_OLLAMA", "http://127.0.0.1:11434")  # the installed app runs its own on :11439
LOCAL = ("127.0.0.1", "::1")
DEFAULT_PORT = 8180  # not 8000: Bitfocus Companion's port at most events (vMix 8088, Resolume 8080, OBS 4455 also avoided)
RESTART_CODE = 75  # exit code that tells the menu-bar/tray app "start me again" (new port saved in Control)
IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform == "win32"
NO_WINDOW = {"creationflags": 0x08000000} if IS_WIN else {}  # child processes: no console window flashing up

LANGS = {  # code: (native name, English name)
    "en": ("English", "English"), "fr": ("Français", "French"), "es": ("Español", "Spanish"),
    "de": ("Deutsch", "German"), "it": ("Italiano", "Italian"), "pt": ("Português", "Portuguese"),
    "nl": ("Nederlands", "Dutch"), "ro": ("Română", "Romanian"), "pl": ("Polski", "Polish"),
    "uk": ("Українська", "Ukrainian"), "ru": ("Русский", "Russian"), "ar": ("العربية", "Arabic"),
    "zh": ("中文", "Chinese"), "ja": ("日本語", "Japanese"), "ko": ("한국어", "Korean"),
}
WHISPER = {"turbo": "mlx-community/whisper-large-v3-turbo",
           "large": "mlx-community/whisper-large-v3-mlx",
           "small": "mlx-community/whisper-small-mlx"}
FAST_WHISPER = "mlx-community/whisper-base-mlx"  # provisional captions: ~0.07 s vs ~1 s for turbo on M2 Pro
SPEC_FRAMES = 5  # 150 ms of silence: start transcribing the final speculatively
HALLU = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye", "so",
         "okay", "merci", "sous-titres réalisés par la communauté d'amara.org", "gracias",
         "merci d'avoir regardé", "sous-titrage st' 501", "sous-titrage société radio-canada",
         "subtítulos realizados por la comunidad de amara.org", "gracias por ver el video", "suscríbete"}
DEFAULT = {"name": "New session", "event_type": "conference", "context": "", "names": "", "glossary": [],
           "source": "en", "targets": ["fr", "es"], "engine": "argos", "whisper": "turbo",
           "device": "", "channel": 1, "silence_ms": 600, "max_s": 7, "partial_s": 0.6, "gate_db": -60, "gain_db": 0, "phone_pace": "smooth", "voice_gender": "female", "bleep": True, "bleep_extra": "",
           "vmix": {"host": "", "input": "", "fields": {}}, "wifi_ssid": "", "wifi_pass": ""}
STYLE = {  # caption overlay defaults — every key is editable in Control > Captions or via URL ?key=value
    "lang": "fr", "lang2": "", "mode": "rollup", "rows": 2, "clear": 6, "fade": 0.25,
    "font": "Helvetica Neue, Arial, sans-serif", "gfont": "", "size": 48, "size2": 0.8, "weight": 700,
    "italic": False, "upper": False, "lineHeight": 1.3, "letterSpacing": 0,
    "color": "#ffffff", "color2": "#ffd479", "outline": 0, "outlineColor": "#000000",
    "shadow": 3, "shadowColor": "#000000",
    "box": "line", "boxColor": "#000000", "boxOpacity": 0.65, "boxPadX": 14, "boxPadY": 4, "boxRadius": 6,
    "pos": "bottom", "offsetX": 0, "offsetY": 70, "width": 80, "align": "center",
    "bg": "transparent", "width_px": 1920, "height_px": 1080,
    "roll": 220, "partialOpacity": 0.85,
}
BUILTIN_PRESETS = {  # ready-made looks; editable copies live in caption_presets.json
    "subtitles": {**STYLE, "box": "none", "size": 46, "weight": 600, "outline": 1.5, "shadow": 6,
                  "shadowColor": "#000000", "rows": 2, "width": 72, "offsetY": 64, "clear": 5, "partialOpacity": 1},
    "subtitles-box": {**STYLE, "box": "line", "boxOpacity": 0.75, "boxPadX": 12, "boxPadY": 3, "boxRadius": 0,
                      "size": 44, "weight": 600, "shadow": 0, "rows": 2, "width": 72, "offsetY": 60, "clear": 5,
                      "partialOpacity": 1},
    "lower-third-2lang": {**STYLE, "lang2": "en", "box": "block", "boxOpacity": 0.7, "boxRadius": 0, "size": 40,
                          "size2": 0.75, "rows": 2, "width": 70, "offsetY": 90, "align": "left", "clear": 8},
}
# Profanity, per language, case-insensitive. "word*" = stem (fuck* -> fucking, fucker); plain words match whole
# words only (+s), so "cock" never hits "cocktail". Extra words: Control > Session > Bleep.
BAD = {
    "en": "fuck* shit* bitch* bastard* asshole* arsehole* dickhead* cunt* motherfuck* bullshit* wank* twat* prick "
          "slut* whore* piss pissed cock nigg* faggot* retard*",
    "fr": "putain* merde* connard* connasse* salope* enculé* encule* bâtard* batard* pute* niqu* nique* bordel "
          "couille* chier foutre pédé* enfoiré*",
    "es": "mierda* puta* puto* joder* jodido* coño* cabrón cabrones gilipollas pendejo* chinga* culero* hostia* "
          "maricón* cojones",
    "de": "scheiße* scheisse* scheiß* scheiss* fick* arschloch* hure* fotze* wichser* schlampe*",
    "it": "cazz* merda* stronz* puttan* vaffanculo* fanculo* minchia* troia* coglion*",
    "pt": "porra* caralho* merda* foda* fode* puta* buceta* cacete* viado*",
    "nl": "kut* godverdomme* klootzak* lul tering* kanker* hoer*",
}


# English swearing that also turns up untranslated in other languages — only words that are never ordinary words
# elsewhere (the full English list once bleeped French "en retard" = late, and "con"/"pute" style clashes exist too)
BAD_ANYWHERE = "fuck* motherfuck* shit* bullshit* bitch* asshole* cunt*"


def bleep_re(lang, extra=""):
    words = set((BAD.get(lang, "") + " " + (BAD["en"] if lang == "en" else BAD_ANYWHERE) + " "
                 + extra.replace(",", " ")).lower().split())
    if not words:
        return None
    pat = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) + r"(?:e?s)?(?!\w)"
           for w in sorted(words, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(?:" + "|".join(pat) + ")", re.I)


def bleep(text, rx):
    """Broadcast-style masking: first letter kept so the sentence still reads, rest starred."""
    return rx.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text) if rx and text else text


DEMO = ["Welcome everyone, and thank you for being here today.",
        "Before we begin, please make sure your phones are on silent.",
        "Our first speaker has travelled a long way to be with us.",
        "Let's give a warm welcome to our guests from Spain.",
        "We'll take a short break in about twenty minutes."]


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "session"


def read_json(path, default):
    """Saved state must never stop the server from booting: a missing or corrupt file gives `default`."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log(f"[files] ignoring unreadable {path} ({e})")
        return default


def write_json(path, obj):
    """Atomic: write a temp file, then rename over the old one — a crash or power cut mid-write leaves the
    previous version intact instead of a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_session(s):
    write_json(os.path.join(SESS_DIR, slug(s["name"]) + ".json"), s)


# ======================================================================= translation
def session_langs(s):
    """Every language of the session. 'auto' source: the targets are the set people may SPEAK and READ."""
    if s.get("source") == "auto":
        return list(dict.fromkeys(s.get("targets") or ["en"]))
    return list(dict.fromkeys([s["source"]] + list(s.get("targets", []))))


ART = r"(?:l'|l’|le |la |les |el |los |las |un |une |una |del |al |du )"


def gloss_sub(m, target):
    """Replace [article] + Argos' default word with the glossary term. Written WITH its article in the glossary
    ('la hostia'), the article is taken from there, so gender agrees ('el anfitrión' -> 'la hostia',
    'del banco' -> 'de la orilla'); without one, only the noun is swapped and the original article kept."""
    art = (m.group(1) or "")
    t_art = re.match(ART, target, flags=re.I)
    if t_art and art:
        a = art.lower().strip()
        pre = {"del": "de ", "al": "a ", "du": "de "}.get(a, "")
        return match_case(m.group(0), pre + target)
    noun = target[t_art.end():] if t_art else target
    return art + (noun if art else match_case(m.group(0), noun))


def match_case(found, repl):
    return repl[0].upper() + repl[1:] if found[:1].isupper() and repl else repl


def gloss_term(g, lang, s):
    """Glossary row -> term in `lang`. Rows have a column per language; 'src' = the fixed source column."""
    return g.get(lang) or (g.get("src") if lang == s.get("source") else None)


# Local AI on a Mac runs on MLX when its weights are installed (same model, ~40 % faster per line than Ollama:
# llama.cpp can't reuse a cached prompt for Gemma 3's sliding-window attention). Control still names them by
# their Ollama tags; Windows/Linux keep using Ollama (CUDA).
import models  # noqa: E402  (AI tiers + what this machine can run: shared with setup)
MLX_MODELS = {"translategemma:4b": "mlx-community/translategemma-4b-it-4bit",
              **{t["model"]: t["mlx"][0] for t in models.TIERS}}
_FIT = None


def machine_fit():
    """models.report() once per run (nvidia-smi / sysctl): Control shows it on the quality cards."""
    global _FIT
    if _FIT is None:
        try:
            _FIT = models.report()
        except Exception as e:
            log(f"[models] hardware check failed ({e!r})")
            _FIT = {"hardware": {}, "tiers": [], "recommend": "none"}
    return _FIT
STT_BUSY = threading.Event()  # set while Whisper transcribes a sentence: AI translation waits for it


def yield_to_stt(limit=2.5):
    """Speech recognition first. On a Mac, Whisper and the local AI share one GPU: when the AI held it, Whisper fell
    behind (backlog, 5–9 s lines during a song). The AI version is optional — the fast translation is already on
    screen — so it waits (≤2.5 s) for the sentence being transcribed instead of competing."""
    t = time.time()
    while STT_BUSY.is_set() and time.time() - t < limit:
        time.sleep(0.03)


MLX_PORT = int(os.environ.get("LT_MLX_PORT", "0"))  # 0 = a free port per LiveTranslate instance (two instances
# sharing one port once left a runtime that never bound its port: every AI request refused, silently Argos)


def mlx_repo(name):
    """The MLX weights for an Ollama-named model, if this Mac has them downloaded (never downloads)."""
    repo = MLX_MODELS.get(name)
    if not IS_MAC or not repo or os.environ.get("LT_NO_MLX"):
        return None
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo, local_files_only=True)
        return repo
    except Exception:
        return None


class MlxRuntime:
    """mlx_srv.py child process, started on first use, shared by every session; dies with the server."""
    lock, proc, port = threading.Lock(), None, 0

    @classmethod
    def url(cls):
        with cls.lock:
            if cls.proc is None or cls.proc.poll() is not None:
                cls.port = MLX_PORT or cls._free_port()
                cls.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "mlx_srv.py"), "--port", str(cls.port)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(150):
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/v1/models", timeout=1)
                        log(f"[llm] MLX runtime on :{cls.port}")
                        break
                    except Exception:
                        if cls.proc.poll() is not None:
                            break
                        time.sleep(0.2)
                else:
                    log(f"[llm] MLX runtime did not answer on :{cls.port} — restarting it on the next line")
                    cls.restart()
        return f"http://127.0.0.1:{cls.port}"

    @staticmethod
    def _free_port():
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            return so.getsockname()[1]

    @classmethod
    def restart(cls):
        """Called when the runtime refuses connections: alive but deaf is worse than dead (never restarted)."""
        if cls.proc and cls.proc.poll() is None:
            cls.proc.kill()

    @classmethod
    def stop(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()


class Translator:
    def __init__(self, s):
        self.s, self.recent = s, []
        eng = s.get("engine", "")
        self.mlx = mlx_repo(eng[7:]) if eng.startswith("ollama:") else None
        self.llm_down_until = 0.0
        self._dcache = {}
        try:
            import argos_lite as at  # same models and output, without Argos' torch/spaCy paragraph splitter (~1 GB)
            self.at = at
        except ImportError:
            self.at = None

    def remember(self, text, frm, tr):
        """Earlier lines for the prompt. The list GROWS (2 → 6 lines) and then drops back to the last 2, instead of
        sliding by one every line: a sliding window changes the prompt right after the system text, so the AI had
        to re-read everything; growing keeps the start identical, so only the new sentence is read."""
        self.recent.append((text, frm, tr))
        if len(self.recent) > 6:
            del self.recent[:-2]

    def _system(self, frm, to):
        """Faithfulness first: a subtitle must say what the speaker said, in the words this audience uses.
        (An earlier 'translate naturally and concisely' wording invited paraphrase and dropped meaning.)"""
        s, F, T = self.s, LANGS[frm][1], LANGS[to][1]
        p = [f"You are the live subtitle translator at this event, translating {F} speech into {T}."]
        if s.get("context"):
            p.append(f"THE EVENT: {s['context'].strip()}")
            p.append(f"Use this to understand every sentence: when a word has several meanings, always choose the "
                     f"meaning that fits this event, and use the vocabulary and jargon that {T}-speaking people "
                     f"at such an event really use.")
        p.append("Be faithful: keep all of the meaning, the speaker's tone and register, and the same person "
                 "(I/we/you). Do not add, explain, summarise, soften or leave anything out.")
        p.append("The text comes from live speech recognition and may contain small recognition errors: "
                 "translate what the speaker most likely said.")
        if s.get("names"):
            p.append(f"Proper names and special words — keep exactly as written, never translate: {s['names']}")
        gl = [f'"{gloss_term(g, frm, s)}" → "{gloss_term(g, to, s)}"' for g in s.get("glossary", [])
              if gloss_term(g, frm, s) and gloss_term(g, to, s)]
        if gl:
            p.append("Mandatory terminology, always use exactly: " + "; ".join(gl))
        p.append(f"Answer with the {T} translation only — no quotes, notes or alternatives.")
        return "\n".join(p)

    def _messages(self, text, frm, to, model=""):
        """Earlier lines as real chat turns (sentence -> its translation): the model keeps names and tone
        consistent, and — unlike a 'context only' note — never re-translates them into the new line."""
        if "hy-mt" in model.lower():  # Tencent HY-MT: trained on one fixed instruction, not on a chat
            return [{"role": "user", "content": self._hymt(text, frm, to)}]
        m = [{"role": "system", "content": self._system(frm, to)}]
        for src, f, tr in self.recent:
            if f == frm and tr.get(to):
                m += [{"role": "user", "content": src}, {"role": "assistant", "content": tr[to]}]
        return m + [{"role": "user", "content": text}]

    def _hymt(self, text, frm, to):
        """HY-MT 1.5's documented prompts: contextual translation + terminology intervention, in English."""
        s, T = self.s, LANGS[to][1]
        ctx = [s["context"].strip()] if s.get("context") else []
        ctx += [src for src, f, _ in list(self.recent)[-2:] if f == frm]
        gl = [f"{gloss_term(g, frm, s)} is translated as {gloss_term(g, to, s)}" for g in s.get("glossary", [])
              if gloss_term(g, frm, s) and gloss_term(g, to, s)]
        gl += [f"{n.strip()} is translated as {n.strip()}" for n in s.get("names", "").splitlines() if n.strip()]
        p = ""
        if gl:
            p += "Refer to the following translation:\n" + "\n".join(gl) + "\n\n"
        if ctx:
            p += "\n".join(ctx) + f"\nRefer to the context above and translate the following text into {T}, " \
                 "do not translate the context, without additional explanation:\n" + text
        else:
            p += f"Translate the following segment into {T}, without additional explanation.\n\n{text}"
        return p

    def term(self, term, frm, to):
        """Suggest the translation of ONE glossary term (Control fills empty glossary cells with it, marked
        'suggested'). With a local AI: the word people at THIS event use (context-aware); else Argos."""
        F, T = LANGS[frm][1], LANGS[to][1]
        eng = self.s.get("engine", "")
        if eng.startswith("ollama:"):
            ctx = self.s.get("context", "").strip()
            msgs = [{"role": "system", "content":
                     (f"THE EVENT: {ctx}\n" if ctx else "") +
                     f"You are the terminologist preparing the subtitle glossary for this event. Give the {T} term that "
                     f"{T}-speaking people at such an event really use for the {F} term. Keep the same form as the "
                     f"source (singular/plural, with an article only if it has one). Answer with the {T} term only — "
                     f"no quotes, no explanation, no alternatives."},
                    {"role": "user", "content": term}]
            try:
                if self.mlx:
                    body = json.dumps({"model": self.mlx, "temperature": 0, "max_tokens": 40, "messages": msgs}).encode()
                    req = urllib.request.Request(MlxRuntime.url() + "/v1/chat/completions", body,
                                                 {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        out = json.load(r)["choices"][0]["message"]["content"]
                else:
                    body = json.dumps({"model": eng[7:], "stream": False, "keep_alive": "60m", "think": False,
                                       "options": {"temperature": 0}, "messages": msgs}).encode()
                    with urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/chat", body), timeout=60) as r:
                        out = json.load(r)["message"]["content"]
                out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().splitlines()[0].strip('"“”«» .')
                if out:
                    return out
            except Exception as e:
                log(f"[glossary] AI suggestion failed ({e!r}) — using Argos")
        return self._argos(term, frm, to).strip().rstrip(".")

    def _mlx(self, text, frm, to, timeout=6):
        yield_to_stt()
        body = json.dumps({"model": self.mlx, "temperature": 0, "max_tokens": 300,
                           "messages": self._messages(text, frm, to, self.s.get("engine", ""))}).encode()
        req = urllib.request.Request(MlxRuntime.url() + "/v1/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)["choices"][0]["message"]["content"]
        return out.strip().strip('"“”«» ')

    def _ollama(self, model, text, frm, to, timeout=6):
        body = json.dumps({"model": model, "stream": False, "keep_alive": "60m", "think": False,
                           "options": {"temperature": 0},
                           "messages": self._messages(text, frm, to, model)}).encode()
        req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # live: a late caption is worse than an Argos one
            out = json.load(r)["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"“”«» ')

    def pairs(self):
        langs = session_langs(self.s)
        froms = langs if self.s.get("source") == "auto" else [self.s["source"]]
        return [(f, t) for f in froms for t in langs if t != f]

    def warm(self):
        """Load models before the first live line: Argos tokenisers (~1 s each on first use) and the LLM
        (cold load ~7 s, then one real call per language pair to cache each system prompt)."""
        for frm, to in self.pairs():
            try:
                self._argos("Hello.", frm, to)
            except Exception as e:
                log(f"[argos] {frm}->{to} unavailable ({e}) — run setup with these languages")
        eng = self.s.get("engine", "argos")
        if not eng.startswith("ollama:"):
            return
        try:
            if not self.mlx:
                body = json.dumps({"model": eng[7:], "keep_alive": "60m", "prompt": "", "stream": False}).encode()
                urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate", body), timeout=90).read()
            t = time.time()
            for frm, to in self.pairs():
                if self.mlx:
                    self._mlx("Hello and welcome.", frm, to, timeout=120)
                else:
                    self._ollama(eng[7:], "Hello and welcome.", frm, to, timeout=60)
            log(f"[llm] {eng[7:]}{' (MLX)' if self.mlx else ''} loaded, {len(self.pairs())} prompts cached in {time.time() - t:.1f}s")
        except Exception as e:
            log(f"[llm] could not load {eng[7:]} ({e}) — Argos will be used")

    def _argos(self, text, frm, to):
        out = self.at.translate(text, frm, to)  # no direct package (e.g. fr->es): Argos pivots via English
        low = text.lower()
        for g in self.s.get("glossary", []):
            a, b = gloss_term(g, frm, self.s), gloss_term(g, to, self.s)
            if not (a and b) or a.lower() not in low:
                continue
            # Argos can't take instructions, so enforce the term on its output: replace whatever Argos
            # normally renders the source term as (e.g. host -> "hôte") with the mandatory term ("hostie").
            for d in self._defaults(a, frm, to):
                out = re.sub(rf"(?<!\w)({ART})?{re.escape(d)}(?!\w)", lambda m: gloss_sub(m, b), out, flags=re.I)
            out = re.sub(rf"(?<!\w)({ART})?{re.escape(a)}(?!\w)", lambda m: gloss_sub(m, b), out, flags=re.I)  # untranslated
        return out

    def _defaults(self, term, frm, to):
        """How Argos renders `term` on its own (cached): bare, and with an article (keeps l'/le/la variants)."""
        key = (term.lower(), frm, to)
        if key not in self._dcache:
            outs = set()
            probes = [term] + ([f"the {term}", f"our {term}", f"a {term}", f"the {term} is here."] if frm == "en" else [])
            for probe in probes:
                try:
                    t = self.at.translate(probe, frm, to).strip(" .!?")
                    t = re.sub(r"\s+(est|está|es|ist)\s+(ici|aquí|hier)$", "", t, flags=re.I)  # "… is here." probe
                    t = re.sub(r"^(notre |nuestro |nuestra |unser |unsere )", "", t, flags=re.I)
                    outs.add(re.sub(r"^(l'|l’|le |la |les |el |los |las |un |une |una |der |die |das )", "", t, flags=re.I))
                except Exception:
                    pass
            self._dcache[key] = sorted((o for o in outs if o and o.lower() != term.lower()), key=len, reverse=True)
        return self._dcache[key]

    def __call__(self, text, to, frm, fast=False):
        """fast=True (partials): always Argos — an LLM round-trip per second per language is too slow."""
        if to == frm:
            return text
        eng = self.s.get("engine", "argos")
        if eng.startswith("ollama:") and not fast and time.time() > self.llm_down_until:
            try:
                if self.mlx:
                    return self._mlx(text, frm, to)
                return self._ollama(eng[7:], text, frm, to)
            except Exception as e:
                # circuit breaker: don't make every following line wait for a dead/hung LLM
                self.llm_down_until = time.time() + 20
                if self.mlx and "refused" in repr(e).lower():
                    MlxRuntime.restart()
                log(f"[llm] {e!r} -> Argos for the next 20 s")
        try:
            return self._argos(text, frm, to)
        except Exception as e:
            log(f"[argos] {frm}->{to} unavailable ({e})")
            return text


# ======================================================================= speech-to-text
def make_stt(s):
    """Apple Silicon: MLX (Metal GPU). Windows/Linux: faster-whisper — CUDA on an NVIDIA card, else CPU."""
    if IS_MAC and os.environ.get("LT_STT") != "ct2":  # LT_STT=ct2: test the Windows/Linux engine on a Mac
        return STT(s)
    return CT2STT(s)


def cuda_libs():
    """faster-whisper needs cuBLAS 12 + cuDNN 9. Setup installs them as pip wheels (nvidia-*-cu12) when the PC has an
    NVIDIA card; make the loader find them — before CTranslate2 first touches CUDA: DLL directories + PATH on Windows,
    preloaded with global symbols on Linux. Returns False when there is no usable NVIDIA GPU (CPU mode)."""
    try:
        import nvidia
        roots = [os.path.join(p, sub) for p in nvidia.__path__ for sub in os.listdir(p)]
    except Exception:
        roots = []
    for r in roots:
        if IS_WIN and os.path.isdir(os.path.join(r, "bin")):
            os.add_dll_directory(os.path.join(r, "bin"))
            os.environ["PATH"] = os.path.join(r, "bin") + os.pathsep + os.environ.get("PATH", "")
        elif not IS_WIN and os.path.isdir(os.path.join(r, "lib")):
            import ctypes, glob
            for so in sorted(glob.glob(os.path.join(r, "lib", "lib*.so.*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class CT2STT:
    """Same interface as STT (below) on faster-whisper / CTranslate2. On a GPU: Whisper turbo for finals + base
    for provisional captions, like the Mac. Without one, turbo takes ~10 s per sentence — far too slow live — so
    finals drop to `small` (~1–2 s on a recent 8-core CPU) and the operator is told why."""
    MODELS = {"turbo": "turbo", "large": "large-v3", "small": "small", "base": "base"}

    def __init__(self, s):
        from faster_whisper import WhisperModel
        self.gpu = cuda_libs()
        dev, ct = ("cuda", "float16") if self.gpu else ("cpu", "int8")
        want = s.get("whisper", "turbo")
        name = self.MODELS.get(want, want)
        self.note = ""
        if not self.gpu and name in ("turbo", "large-v3"):
            name = "small"
            self.note = "No NVIDIA graphics card found: speech recognition runs on the CPU with the smaller model."
        threads = 0 if self.gpu else max(4, (os.cpu_count() or 8) - 2)
        self.lang = None if s["source"] == "auto" else s["source"]
        self.model = WhisperModel(name, device=dev, compute_type=ct, cpu_threads=threads)
        try:
            self.fast_model = WhisperModel("base", device=dev, compute_type=ct, cpu_threads=threads)
        except Exception as e:
            log(f"[stt] fast model unavailable ({e}); partials use {name}")
            self.fast_model = self.model
        log(f"[stt] faster-whisper {name} + base on {dev} ({ct})")
        self.set_prompt(s)
        z = np.zeros(SR, np.float32)
        self(z, self.lang or "en")
        self(z, self.lang or "en", fast=True)

    def set_prompt(self, s):
        p = " ".join(x for x in [s.get("names", "").replace("\n", ", "), s.get("context", "")[:200]] if x)
        self.prompt = p.strip() or None

    def detect(self, audio, allowed):
        _, _, probs = self.fast_model.detect_language(audio)
        p = {l: 0.0 for l in allowed}
        for l, v in probs:
            if l in p:
                p[l] = float(v)
        tot = sum(p.values()) or 1.0
        return {l: v / tot for l, v in p.items()}

    def __call__(self, audio, lang, fast=False):
        m = self.fast_model if fast else self.model
        segs, _ = m.transcribe(audio, language=lang, beam_size=1, temperature=0.0, initial_prompt=self.prompt,
                               condition_on_previous_text=False, without_timestamps=True, vad_filter=False)
        segs = [x for x in segs if x.no_speech_prob < 0.6 and x.avg_logprob > -1.0 and x.compression_ratio < 2.4]
        return " ".join(x.text.strip() for x in segs).strip()


class STT:
    """Two Whisper models kept resident: the main one for finals, a tiny one for provisional captions.

    mlx_whisper.transcribe() caches only ONE model (ModelHolder) and reloads from disk whenever the
    path changes — alternating models would reload on almost every call. We hold both and point the
    holder at the right one before each call (safe: only the hub worker thread transcribes)."""

    def __init__(self, s):
        import mlx.core as mx
        import sys
        import mlx_whisper
        from mlx_whisper.load_models import load_model
        tmod = sys.modules["mlx_whisper.transcribe"]  # the package re-exports a *function* named transcribe
        self.m, self.holder = mlx_whisper, tmod.ModelHolder
        self.lang = None if s["source"] == "auto" else s["source"]
        self.model = WHISPER.get(s["whisper"], s["whisper"])
        self.note = ""
        self.loaded = {self.model: load_model(self.model, dtype=mx.float16)}
        try:
            self.loaded[FAST_WHISPER] = load_model(FAST_WHISPER, dtype=mx.float16)
            self.fast = FAST_WHISPER
        except Exception as e:  # not downloaded (offline): provisional captions use the main model
            log(f"[stt] fast model unavailable ({e}); partials use {s['whisper']}")
            self.fast = self.model
        self.set_prompt(s)
        for path in self.loaded:  # warm: first call compiles kernels
            self._run(np.zeros(SR, np.float32), path, self.lang or "en")
        if self.lang is None:
            self.detect(np.zeros(SR, np.float32), ["en"])

    def detect(self, audio, allowed):
        """Spoken-language probabilities restricted to `allowed`, from the tiny model (~0.03 s).
        Restricting matters: free choice among 99 languages mislabels short or accented lines."""
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
        model = self.loaded[self.fast]
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = model.detect_language(mel)
        p = {l: float(probs.get(l, 0.0)) for l in allowed}
        tot = sum(p.values()) or 1.0
        return {l: v / tot for l, v in p.items()}

    def _run(self, audio, path, lang):
        self.holder.model, self.holder.model_path = self.loaded[path], path
        return self.m.transcribe(audio, path_or_hf_repo=path, language=lang,
                                 initial_prompt=self.prompt, condition_on_previous_text=False,
                                 temperature=0.0)

    def set_prompt(self, s):
        p = " ".join(x for x in [s.get("names", "").replace("\n", ", "), s.get("context", "")[:200]] if x)
        self.prompt = p.strip() or None

    def __call__(self, audio, lang, fast=False):
        r = self._run(audio, self.fast if fast else self.model, lang)
        segs = [x for x in r.get("segments", [])
                if x.get("no_speech_prob", 0) < 0.6 and x.get("avg_logprob", 0) > -1.0
                and x.get("compression_ratio", 0) < 2.4]  # >2.4 = Whisper stuck repeating itself
        return " ".join(x["text"].strip() for x in segs).strip()


# ======================================================================= audio + VAD
class Capture(threading.Thread):
    def __init__(self, hub, s):
        super().__init__(daemon=True)
        self.hub, self.stop = hub, threading.Event()
        self.device = str(s.get("device", "")).strip() or None  # device NAME (indices shift when interfaces come and go)
        self.channel = max(1, int(s.get("channel", 1)))
        self.sil = int(s.get("silence_ms", 600)) // 30
        self.maxf = int(float(s.get("max_s", 7)) * SR / FRAME)
        self.part = int(float(s.get("partial_s", 1.0)) * SR / FRAME)  # 0 = no partial captions
        self.gate = 10 ** (float(s.get("gate_db", -60)) / 20)  # below this RMS a frame is silence, full stop
        self.set_gain(s.get("gain_db", 0))

    def set_gain(self, db):
        """Input trim, applied first: meter, silence gate, VAD and Whisper all see the trimmed signal.
        Changed live like a fader — no stream restart (that would drop audio)."""
        db = max(-20.0, min(30.0, float(db or 0)))
        self.gain = 10 ** (db / 20)

    def _open(self, sd):
        """Open the input stream; returns None (after reporting) if the device is missing.

        Blocking reads, no Python callback: a callback needs the GIL, and when translation/tokenising
        holds it too long CoreAudio overruns and PortAudio silently DROPS input (lost words, merged
        sentences). Here PortAudio's 500 ms ring buffer absorbs any stall; normal reads add no delay."""
        t = time.time()
        stream = None
        try:
            # rescan + open + count under ONE lock hold: otherwise Control's device list (another thread) can
            # re-initialise PortAudio between our rescan and our open, invalidating the stream being created
            with PA["lock"]:
                if PA["open"] == 0:
                    sd._terminate(); sd._initialize()  # so a re-plugged interface is found
                dev = resolve_device(sd, self.device)
                if dev is not None and sd.query_devices(dev)["max_input_channels"] < self.channel:
                    raise RuntimeError(f"{self.device} has fewer than {self.channel} inputs")
                stream = sd.InputStream(samplerate=SR, blocksize=FRAME, channels=self.channel,
                                        dtype="float32", device=dev, latency=0.5,
                                        extra_settings=stream_extra(sd, dev))
                stream.start()
                PA["open"] += 1
            log(f"[audio] device={self.device!r} channel={self.channel} (opened in {time.time() - t:.2f}s)")
            self.hub.audio_ok()
            return stream
        except Exception as e:
            if stream is not None:  # created but start() failed: don't leak an open PortAudio stream
                try:
                    stream.close()
                except Exception:
                    pass
            self.hub.audio_lost(f"Audio input: {e}")
            return None

    def run(self):
        import sounddevice as sd, webrtcvad
        vad = webrtcvad.Vad(2)
        stream, last_frame, overflows, last_ovf_log = None, time.time(), 0, 0.0
        pre = collections.deque(maxlen=10)
        buf, speech, voiced, silence, peak, last, last_part = [], False, 0, 0, 0.0, 0.0, 0
        seq = next(SEQ)  # global utterance counter: (seq, voiced length) identifies the exact audio of a final;
        # global so a new Capture (input switch, restart) can't reuse a key and match a stale speculative result
        dbg = os.environ.get("LT_DEBUG_AUDIO")
        n_read, t_dbg, max_gap = 0, time.time(), 0
        while not self.stop.is_set():
            if stream is None:  # (re)connect — interface unplugged, renamed, or not yet powered
                stream = self._open(sd)
                if stream is None:
                    self.stop.wait(2)
                    continue
                last_frame = time.time()
            try:
                ready = stream.read_available >= FRAME
                if ready:
                    data, ovf = stream.read(FRAME)
            except Exception:
                ready, ovf = False, False
                last_frame = 0  # stream error: treat as lost
            if not ready:
                try:
                    alive = stream.active and time.time() - last_frame < 2
                except Exception:
                    alive = False
                if not alive:  # no audio for 2 s = device unplugged / clock lost
                    self.hub.audio_lost("Audio input stopped — reconnecting…")
                    self._close(stream)
                    stream = None
                    buf, speech, voiced = [], False, 0
                else:
                    time.sleep(0.005)
                continue
            if ovf:
                overflows += 1
                if time.time() - last_ovf_log > 10:
                    log(f"[audio] input overflow ×{overflows} — audio was dropped (system overloaded?)")
                    last_ovf_log, overflows = time.time(), 0
            f = data[:, self.channel - 1].copy()
            if self.gain != 1.0:
                np.clip(f * self.gain, -1.0, 1.0, out=f)  # trim, never wrap around on clipping
            last_frame = time.time()
            if dbg:
                n_read += 1
                if last_frame - t_dbg >= 10:
                    log(f"[audio-dbg] {n_read} frames in {last_frame - t_dbg:.1f}s (expect {(last_frame - t_dbg) * SR / FRAME:.0f})"
                        f" · avail {stream.read_available} · longest uncut gap {max_gap * 30} ms")
                    n_read, t_dbg, max_gap = 0, last_frame, 0
            if len(f) != FRAME:
                continue
            peak = max(peak, float(np.abs(f).max()))
            now = time.time()
            if now - last > 0.15:
                self.hub.level(20 * np.log10(peak + 1e-9))
                peak, last = 0.0, now
            if self.hub.paused:
                buf, speech, voiced = [], False, 0
                pre.clear()
                continue
            # Energy gate BEFORE the VAD: webrtcvad's adaptive state drifts on digital silence (gated or muted
            # console channels send exact zeros) and after ~4 min it labels up to 840 ms of a pause as speech,
            # merging sentences. Silent frames never reach it. Also ends sentences ~90 ms sooner (no hangover).
            is_s = (float(np.sqrt(np.mean(f * f))) > self.gate
                    and vad.is_speech((np.clip(f, -1, 1) * 32767).astype(np.int16).tobytes(), SR))
            if not speech:
                pre.append(f)
                voiced = voiced + 1 if is_s else 0
                if voiced >= 3:
                    speech, buf, silence, last_part = True, list(pre), 0, len(pre)
                    seq = next(SEQ)
                continue
            buf.append(f)
            if dbg and is_s and silence >= 5:
                max_gap = max(max_gap, silence)  # a pause that did NOT end the sentence
            silence = 0 if is_s else silence + 1
            n = len(buf) - silence  # frames up to the last voiced one
            if silence >= self.sil:
                if n > 15:
                    spoke_until = now - silence * FRAME / SR  # latency/SRT measured from end of speech
                    self.hub.utt.put(("audio", np.concatenate(buf[:n + 5]), spoke_until, (seq, n)))
                buf, speech, voiced = [], False, 0
                pre.clear()
            elif silence == SPEC_FRAMES and n > 15:
                # probably the end of a sentence: transcribe now, while we wait to be sure. If the speaker
                # stays quiet until the cut, that result IS the final and the silence wait costs nothing.
                self.hub.utt.put(("spec", np.concatenate(buf[:n + 5]), now, (seq, n)))
            elif len(buf) >= self.maxf:
                # forced split: cut in the quietest 30 ms of the last second (a gap between words),
                # never mid-word — otherwise Whisper hears the split word on both sides
                tail = min(33, len(buf) - 1)
                cut = len(buf) - tail + int(np.argmin([np.sqrt(np.mean(x * x)) for x in buf[-tail:]]))
                self.hub.utt.put(("audio", np.concatenate(buf[:cut]), now, None))
                buf, last_part = buf[cut:], 0
                seq = next(SEQ)  # the remainder is new audio: never match a spec key from before the split
            elif self.part and len(buf) - last_part >= self.part and silence < 5:
                last_part = len(buf)  # speaker still talking: ask for a provisional caption
                self.hub.utt.put(("partial", np.concatenate(buf), now, None))
        if stream is not None:
            self._close(stream)

    def _close(self, stream):
        try:
            stream.close()
        except Exception:
            pass
        with PA["lock"]:
            PA["open"] -= 1


PA = {"lock": threading.Lock(), "open": 0}  # PortAudio re-scan invalidates every open stream
SEQ = __import__("itertools").count(1)


def pa_rescan(sd):
    """Refresh the device list (finds interfaces plugged in after launch) — only when no stream is open."""
    with PA["lock"]:
        if PA["open"] == 0:
            sd._terminate(); sd._initialize()


def input_devices(sd):
    """(index, info) of usable inputs. Windows lists every device once per audio API (MME, DirectSound, WASAPI,
    WDM-KS): keep only WASAPI — full channel counts (Dante Virtual Soundcard, multichannel interfaces), untruncated
    names, and Windows resamples to 16 kHz for us (auto_convert)."""
    devs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    if IS_WIN:
        apis = sd.query_hostapis()
        wasapi = [(i, d) for i, d in devs if "WASAPI" in apis[d["hostapi"]]["name"]]
        devs = wasapi or devs
    return devs


def stream_extra(sd, dev):
    if IS_WIN and dev is not None and "WASAPI" in sd.query_hostapis(sd.query_devices(dev)["hostapi"])["name"]:
        return sd.WasapiSettings(auto_convert=True)
    return None


def resolve_device(sd, name):
    """Saved device name -> current PortAudio index. Legacy numeric values still accepted."""
    if not name:
        return None
    if name.isdigit():
        return int(name)
    devs = [(i, d["name"]) for i, d in input_devices(sd)]
    for i, n in devs:
        if n == name:
            return i
    for i, n in devs:  # tolerate "(2)" suffixes / renamed aggregates
        if name.lower() in n.lower():
            return i
    raise RuntimeError(f"'{name}' not connected")


class WinAwake(threading.Thread):
    """Windows: no sleep / display-off during a session. SetThreadExecutionState is per thread and lapses when that
    thread ends, so a dedicated thread holds it until terminate() — same shape as the caffeinate process."""
    def __init__(self):
        super().__init__(daemon=True)
        self.done = threading.Event()
        self.start()

    def run(self):
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)  # CONTINUOUS | SYSTEM | DISPLAY
        self.done.wait()
        k.SetThreadExecutionState(0x80000000)

    def terminate(self):
        self.done.set()


def parent_alive(pid):
    """Cross-platform 'is the tray/menu-bar app still running'. (os.kill(pid, 0) would KILL it on Windows.)"""
    if IS_WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    return os.getppid() == pid


# ======================================================================= vMix title push
class VmixPusher(threading.Thread):
    """Writes text into vMix GT title fields (vMix Web API, SetText).

    Latest-value-wins per field: if vMix is slow, intermediate texts are dropped — never sent out of
    order, so an older line can't overwrite a newer one on air. One keep-alive connection, own thread."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cv, self.pending, self.conn, self.host, self.ok = threading.Condition(), {}, None, None, None
        self.start()

    def set(self, host, inp, field, text):
        host, inp = host.strip(), str(inp).strip()
        if not host or not inp:
            return
        if ":" not in host:
            host += ":8088"
        with self.cv:
            self.pending[(host, inp, field)] = text
            self.cv.notify()

    def run(self):
        import http.client
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                (host, inp, field), text = self.pending.popitem()
            q = urllib.parse.urlencode({"Function": "SetText", "Input": inp, "SelectedName": field, "Value": text})
            for attempt in (1, 2):  # second try on a fresh connection (vMix closed the idle one)
                try:
                    if self.conn is None or self.host != host:
                        self.conn, self.host = http.client.HTTPConnection(host, timeout=1.5), host
                    self.conn.request("GET", "/api/?" + q)
                    r = self.conn.getresponse(); r.read()
                    if self.ok is not True:
                        log(f"[vmix] connected to {host}")
                    self.ok = True
                    break
                except Exception as e:
                    self.conn = None
                    if attempt == 2 and self.ok is not False:  # log state changes, not every line
                        log(f"[vmix] {host} unreachable ({e}) — captions still go to the browser overlay")
                        self.ok = False


# ======================================================================= NDI outputs
class NdiOutputs:
    """One ndi_out.py process per configured NDI source (overlay page rendered off-screen → NDI with alpha).
    Saved in ndi_outputs.json, started with the server, independent of sessions (no session = transparent)."""

    def __init__(self, port):
        self.port, self.procs = port, {}
        self.lock, self.stopping = threading.Lock(), False
        self.outputs = [o for o in read_json(NDI_PATH, []) if isinstance(o, dict) and o.get("name")]
        from ndi_lib import find_runtime
        self.runtime = find_runtime()
        self.renderer = "ndi_out.py" if IS_MAC else "ndi_cdp.py"  # WebKit on macOS; hidden Edge/Chrome elsewhere

    def key(self, o):
        return (o["name"], o["preset"], o["lang"])

    def save(self, outputs):
        self.outputs = [{"name": o["name"].strip()[:60] or "LiveTranslate", "preset": o.get("preset", "subtitles"),
                         "lang": o.get("lang", "fr"), "on": bool(o.get("on", True))} for o in outputs
                        if str(o.get("name", "")).strip()]
        write_json(NDI_PATH, self.outputs)
        self.reconcile()

    def reconcile(self):
        """Make running processes match the configuration; restarts any that died.
        Called from the supervisor thread AND the web thread: serialised, and inert once stopping."""
        with self.lock:
            if not self.stopping:
                self._reconcile()

    def _reconcile(self):
        want = {self.key(o): o for o in self.outputs if o["on"]} if self.runtime else {}
        for k in list(self.procs):
            if k not in want or self.procs[k].poll() is not None:
                if self.procs[k].poll() is None:
                    self.procs[k].terminate()
                elif k in want:
                    log(f"[ndi] '{k[0]}' exited ({self.procs[k].returncode}) — restarting")
                del self.procs[k]
        for k, o in want.items():
            if k not in self.procs:
                q = urllib.parse.urlencode({"preset": o["preset"], "lang": o["lang"]})
                self.procs[k] = subprocess.Popen(
                    [sys.executable, os.path.join(HERE, self.renderer), "--name", o["name"],
                     "--url", f"http://127.0.0.1:{self.port}/overlay?{q}"], **NO_WINDOW)
                log(f"[ndi] sending '{o['name']}' ({o['preset']}, {o['lang']})")

    def status(self):
        return {"available": bool(self.runtime), "outputs": [
            {**o, "running": self.key(o) in self.procs and self.procs[self.key(o)].poll() is None} for o in self.outputs]}

    def stop_all(self):
        with self.lock:
            self.stopping = True  # the supervisor must not respawn senders during shutdown
            for p in self.procs.values():
                p.terminate()


# ======================================================================= hub
class Hub:
    def __init__(self, demo=False):
        self.demo = demo
        self.s = dict(DEFAULT)
        self.running = self.loading = self.paused = False
        self.error = ""
        self.clients = {"guest": set(), "overlay": set(), "op": set()}
        self.outq = {}  # ws -> asyncio.Queue
        self.history = collections.deque(maxlen=80)
        self.next_id = 1
        self.utt = queue.Queue()
        self.stt = self.tr = self.cap = None
        self.cur_lang = "en"
        self.notice, self.mismatch = "", (None, 0)
        self.transcript = None
        self.loop = None
        self.pool = ThreadPoolExecutor(6)
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.presets = {"default": dict(STYLE), **{k: dict(v) for k, v in BUILTIN_PRESETS.items()}}
        for k, v in read_json(PRESETS_PATH, {}).items():  # older saved presets get new keys (roll…) filled in
            if isinstance(v, dict):
                self.presets[k] = {**STYLE, **v}
        self.all_lines = []
        self.vmix = VmixPusher()
        self.lock_vmix, self._vmix_timer = threading.Lock(), None
        self.awake = self.activity = None
        self.ndi = None
        self.echo_note, self.echoes = "", []
        import tts
        self.voice = tts.Voice(DATA, log)
        self.pubq, self.refineq, self.refining, self._vmix_last = queue.Queue(), queue.Queue(), [], None
        self.worker_t = threading.Thread(target=self._worker, daemon=True)
        self.worker_t.start()
        self.pub_t = threading.Thread(target=self._publisher, daemon=True)
        self.pub_t.start()
        self.ref_t = threading.Thread(target=self._refiner, daemon=True)
        self.ref_t.start()
        threading.Thread(target=self._supervise, daemon=True).start()
        if demo:
            threading.Thread(target=self._demo, daemon=True).start()

    def save_preset(self, name, style):
        self.presets[name] = {**STYLE, **style}
        self._write_presets()
        self.emit({"type": "presets", "presets": self.presets}, ("overlay", "op"))

    def delete_preset(self, name):
        if name != "default":
            self.presets.pop(name, None)
            self._write_presets()
            self.emit({"type": "presets", "presets": self.presets}, ("overlay", "op"))

    def _write_presets(self):
        write_json(PRESETS_PATH, self.presets)

    # ---- broadcasting
    async def _send(self, kinds, msg):
        """Fan out without awaiting anyone: each client has its own queue + sender task, so one phone on
        bad Wi-Fi can never delay the vMix overlay. Overlays/operators are enqueued first."""
        data = json.dumps(msg, ensure_ascii=False)
        for k in sorted(kinds, key=("overlay", "op", "guest").index):
            for ws in list(self.clients[k]):
                q = self.outq.get(ws)
                if q is None:
                    continue
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:  # >200 messages behind: that client is gone in practice
                    self.clients[k].discard(ws)
                    asyncio.ensure_future(ws.close())

    async def sender(self, ws):
        q = self.outq[ws]
        try:
            while True:
                data = await q.get()
                await asyncio.wait_for(ws.send_str(data), 5)
        except Exception:
            await ws.close()

    def emit(self, msg, kinds=("guest", "overlay", "op")):
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._send(kinds, msg)))
            except RuntimeError:  # event loop already closed: the server is shutting down
                pass

    def public(self):
        return {"type": "session", "name": self.s["name"], "source": self.s["source"],
                "langs": session_langs(self.s), "running": self.running,
                "phone_pace": self.s.get("phone_pace", "smooth"),  # default reading style; each viewer can change it
                "voices": self.voice.langs()}  # languages with the natural voice; others use the phone's own

    def state(self):
        return {"type": "state", "running": self.running, "loading": self.loading,
                "monitoring": bool(self.cap) and not self.running, "notice": self.notice,
                "paused": self.paused, "error": self.error, "session": self.s, "demo": self.demo,
                "stt_note": getattr(self.stt, "note", "") if self.running else "",
                "port_note": getattr(self, "port_note", ""), "echo_note": self.echo_note,
                "counts": {k: len(v) for k, v in self.clients.items()}}

    def push_state(self):
        self.emit(self.state(), ("op",))
        self.emit(self.public(), ("guest", "overlay"))

    def level(self, db):
        if self.clients["op"]:
            self.emit({"type": "level", "db": round(db, 1)}, ("op",))

    def fail(self, msg):
        log("[error]", msg)
        self.error, self.running, self.loading = msg, False, False
        self._stay_awake(False)  # a failed restart over a live session must not keep the Mac awake forever
        self.push_state()

    def audio_lost(self, msg):  # session stays live; Capture keeps retrying
        if self.error != msg:
            log("[audio]", msg)
            self.error = msg
            self.push_state()

    def audio_ok(self):
        if self.error:
            self.error = ""
            self.push_state()

    # ---- session lifecycle
    def start(self, s):
        with self.lock:
            self._stop_capture()
            self.s = {**DEFAULT, **s}
            self.running, self.loading, self.error, self.paused = False, True, "", False
            self.push_state()
            try:
                save_session(self.s)
                self.cur_lang = session_langs(self.s)[0]
                self.notice, self.mismatch = "", (None, 0)
                self.tr = Translator(self.s)
                self.tr.warm()
                if not self.demo:
                    log(f"[stt] loading {self.s['whisper']}…")
                    self.stt = make_stt(self.s)
                os.makedirs(TRANS_DIR, exist_ok=True)
                self.transcript = os.path.join(
                    TRANS_DIR, f"{datetime.now():%Y-%m-%d_%H%M}_{slug(self.s['name'])}.txt")
                self.history.clear()
                self.all_lines = []
                self.t0 = time.time()
                if not self.demo:
                    self.cap = Capture(self, self.s)
                    self.cap.start()
                self.emit({"type": "clear"})
                self.running, self.loading = True, False
                self._stay_awake(True)
                self.voice.warm()
                log(f"[session] started: {self.s['name']}")
            except Exception as e:
                return self.fail(f"Could not start: {e}")
            self.push_state()

    def _supervise(self):
        """Last line of defence mid-show: a dead thread is restarted within 2 s instead of captions just stopping."""
        while True:
            time.sleep(2)
            try:
                if not self.ref_t.is_alive():
                    log("[supervisor] refiner thread died — restarting")
                    self.ref_t = threading.Thread(target=self._refiner, daemon=True)
                    self.ref_t.start()
                if not self.pub_t.is_alive():
                    log("[supervisor] publisher thread died — restarting")
                    self.pub_t = threading.Thread(target=self._publisher, daemon=True)
                    self.pub_t.start()
                if not self.worker_t.is_alive():
                    log("[supervisor] worker thread died — restarting")
                    self.worker_t = threading.Thread(target=self._worker, daemon=True)
                    self.worker_t.start()
                if self.ndi:
                    self.ndi.reconcile()
                cap = self.cap
                if self.running and not self.demo and cap and not cap.stop.is_set() and not cap.is_alive():
                    log("[supervisor] audio capture thread died — restarting")
                    with self.lock:
                        if self.cap is cap:
                            self.cap = Capture(self, self.s)
                            self.cap.start()
            except Exception as e:
                log("[supervisor]", repr(e))

    def _stay_awake(self, on):
        """During a session: no idle/display sleep (caffeinate) and no App Nap timer throttling."""
        if on and self.awake is None:
            try:
                if IS_MAC:
                    self.awake = subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])
                elif IS_WIN:
                    self.awake = WinAwake()
                else:  # Linux desktop: hold an idle/sleep inhibitor for as long as the session runs
                    self.awake = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=LiveTranslate",
                                                   "--why=Live session", "sleep", "infinity"],
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                log("[power] can't prevent sleep:", e)
            try:
                if not IS_MAC:
                    raise ImportError
                from Foundation import NSProcessInfo
                self.activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                    0x00FFFFFF | 0xFF00000000, "LiveTranslate live session")  # UserInitiated | LatencyCritical
            except Exception:
                pass
        elif not on:
            if self.awake:
                self.awake.terminate()
                self.awake = None
            if self.activity is not None and IS_MAC:
                try:
                    from Foundation import NSProcessInfo
                    NSProcessInfo.processInfo().endActivity_(self.activity)
                except Exception:
                    pass
                self.activity = None

    def set_gain(self, db):
        self.s["gain_db"] = max(-20.0, min(30.0, float(db or 0)))
        if self.cap:
            self.cap.set_gain(self.s["gain_db"])
        if self.running:
            save_session(self.s)

    def monitor(self, s):
        """Input check before going live: open the chosen input so the meter moves; nothing is transcribed
        (the worker ignores audio while no session runs). Replaced by the real capture on Start."""
        if self.running or self.loading or self.demo:
            return
        with self.lock:
            self._stop_capture()
            self.error = ""
            self.cap = Capture(self, {**self.s, **s})
            self.cap.start()
        self.push_state()

    def _stop_capture(self, drain=True):
        if self.cap:
            self.cap.stop.set()
            self.cap.join(timeout=3)  # release the interface before reopening it
            self.cap = None
        if not drain:  # live input switch: lines already heard/translating must still go out
            return
        while not self.utt.empty():
            self.utt.get_nowait()
        while not self.pubq.empty():
            self.pubq.get_nowait()
        while not self.refineq.empty():
            self.refineq.get_nowait()

    def stop(self):
        with self.lock:
            self._stop_capture()
            self.running = self.loading = False
            self._stay_awake(False)
            self._vmix_clear()
            log("[session] stopped")
            self.push_state()

    def update_live(self, s):
        """Apply context / names / glossary / outputs without restarting audio."""
        audio_keys = ("device", "channel", "silence_ms", "max_s", "partial_s", "gate_db")
        audio_changed = any(k in s and s[k] != self.s.get(k) for k in audio_keys)
        engine_changed = s.get("engine", self.s.get("engine")) != self.s.get("engine")
        langs_changed = session_langs({**self.s, **{k: s[k] for k in ("source", "targets") if k in s}}) != session_langs(self.s)
        if "source" in s and s["source"] != self.s.get("source"):
            self.notice, self.mismatch = "", (None, 0)
        for k in ("name", "context", "names", "glossary", "engine", "vmix", "wifi_ssid", "wifi_pass", "targets", "bleep", "bleep_extra", "source", "gain_db", "phone_pace", "voice_gender") + audio_keys:
            if k in s:
                self.s[k] = s[k]
        if self.stt:
            self.stt.set_prompt(self.s)
        if self.cap and "gain_db" in s:
            self.cap.set_gain(self.s["gain_db"])
        if (engine_changed or langs_changed) and self.tr:
            self.pool.submit(self.tr.warm)  # new language pairs: load them now, not on the first live line
        if audio_changed and self.running and not self.demo:
            with self.lock:  # swap the input only; Whisper stays loaded
                self._stop_capture(drain=False)
                self.cap = Capture(self, self.s)
                self.cap.start()
            log(f"[audio] switched to {self.s['device'] or 'system default'} ch {self.s['channel']}")
        if self.running:  # persist: a crash-resume (--autostart) must come back with the live edits (input!)
            try:
                save_session(self.s)
            except Exception as e:
                log("[session] could not save live changes:", e)
        self.push_state()

    # ---- pipeline
    def others(self, lang):
        return [l for l in session_langs(self.s) if l != lang]

    def choose_lang(self, audio, kind):
        """Which language is being spoken? Fixed-source sessions: always the source.
        'auto': tiny-model detection among the session languages, with hysteresis — provisional captions
        only switch language when confident, so a short 'OK' or a name can't flip the whole room."""
        if self.s.get("source") != "auto":
            return self.s["source"]
        if len(audio) < 0.5 * SR or not self.stt:
            return self.cur_lang
        p = self.stt.detect(audio, session_langs(self.s))
        best = max(p, key=p.get)
        if kind == "partial":
            if best != self.cur_lang and p[best] >= 0.8:
                self.cur_lang = best
            return self.cur_lang
        lang = best if p[best] >= 0.5 else self.cur_lang  # a whole sentence: trust it unless it's a coin flip
        if lang != self.cur_lang:
            log(f"[lang] {self.cur_lang} -> {lang} ({p[best]:.0%})")
        self.cur_lang = lang
        return lang

    def check_language(self, audio):
        """Single-language sessions: notice when people are clearly speaking another language (a common setup
        mistake that otherwise fails silently — English labelled French 'translates' to itself)."""
        src = self.s.get("source")
        if src == "auto" or not self.stt or len(audio) < 1.5 * SR:
            return
        try:
            cands = list(dict.fromkeys([src] + session_langs(self.s) + ["en", "fr", "es", "de"]))
            p = self.stt.detect(audio, [c for c in cands if c in LANGS])
        except Exception:
            return
        best = max(p, key=p.get)
        if best != src and p[best] >= 0.8:
            self.mismatch = (best, self.mismatch[1] + 1 if self.mismatch[0] == best else 1)
        elif best == src:
            self.mismatch = (None, 0)
        notice = ""
        if self.mismatch[1] >= 3:
            notice = (f"Speakers seem to be using {LANGS[best][1]}, but Setup says {LANGS[src][1]}. "
                      f"Choose “Several languages” in Setup, or change the spoken language, then Apply changes.")
        if notice != self.notice:
            self.notice = notice
            if notice:
                log("[lang]", notice)
            self.push_state()

    def publish_partial(self, src, lang):
        """Provisional caption while the speaker is mid-sentence. Replaced by the next final line."""
        if not self.tr:
            return
        targets = self.others(lang)
        tr = {}
        if self.partial_policy(src):
            tr = dict(zip(targets, self.pool.map(lambda l: self.tr(src, l, lang, fast=True), targets)))
        src, tr = self.clean(src, lang, tr)
        msg = {"type": "partial", "src": src, "lang": lang, "tr": tr}
        self.emit(msg)
        if tr or not targets:
            self._vmix(msg)

    def clean(self, src, lang, tr):
        """Bleep every output in its own language (plus English, which leaks into all of them)."""
        if not self.s.get("bleep"):
            return src, tr
        key = self.s.get("bleep_extra", "")
        if getattr(self, "_rx_key", None) != key:
            self._rx, self._rx_key = {}, key
        rx = lambda l: self._rx.setdefault(l, bleep_re(l, key))
        return bleep(src, rx(lang)), {l: bleep(t, rx(l)) for l, t in tr.items()}

    def partial_policy(self, src):
        """Should this provisional caption be translated and shown to the audience, or only the operator?

        Called ~once per second while someone speaks. Translating partials cuts perceived latency
        but translations of half-sentences can be wrong and flicker on screen.
        """
        # TODO(user): decide when a partial is worth translating. Ideas: minimum word count,
        # only with the Argos engine (fast) vs Ollama, only if src ends on a natural break (",", "and"…).
        return True

    def publish(self, src, lang, t_end, dur=2.0, pre=None):
        """Final line. SHOW FIRST, REFINE AFTER: the line goes out as soon as the fast offline translation is ready
        (~0.1 s); with a local-AI engine the AI's version then replaces it in place ('update' message) on every
        output. Nothing ever waits for the AI, so a slow model can no longer delay captions or make them queue.
        Never blocks: the speech worker calling this keeps transcribing.
        t_end = wall-clock time the speech ended. pre = AI translations already started speculatively."""
        if not self.tr:
            return
        targets = self.others(lang)
        pre = pre or {}
        llm = self.s.get("engine", "").startswith("ollama:") and targets
        fast = {l: self.pool.submit(self.tr, src, l, lang, True) for l in targets} if llm else \
               {l: pre.get(l) or self.pool.submit(self.tr, src, l, lang) for l in targets}
        refine = None
        if llm:
            busy = sum(1 for fs in self.refining if not all(f.done() for f in fs.values()))
            self.refining = [fs for fs in self.refining if not all(f.done() for f in fs.values())]
            if busy < 2 or pre:  # the AI is at most 2 lines behind; beyond that, keep the fast version
                refine = {l: pre.get(l) or self.pool.submit(self.tr, src, l, lang) for l in targets}
                self.refining.append(refine)
            elif not getattr(self, "_skip_logged", False):
                log("[translate] local AI busy — keeping the fast translation for some lines")
            self._skip_logged = refine is None
        self.pubq.put((src, lang, t_end, dur, fast, refine))

    def _publisher(self):
        while True:
            src, lang, t_end, dur, futs, refine = self.pubq.get()
            try:
                msg = self._emit_line(src, lang, t_end, dur, futs, refine)
                if refine and msg:
                    self.refineq.put((msg, src, lang, refine))
            except Exception as e:
                log("[publisher]", repr(e))

    def _refiner(self):
        """Replaces fast translations with the AI's, in order, as each one finishes."""
        while True:
            msg, raw, lang, refine = self.refineq.get()
            try:
                tr = {}
                for l, f in refine.items():
                    try:
                        tr[l] = f.result(timeout=30)
                    except Exception as e:
                        log(f"[translate] AI {l}: {e!r} — keeping the fast version")
                if not tr:
                    self._write_transcript(msg)
                    continue
                self.tr.remember(raw, lang, {**msg["tr"], **tr})
                _, clean = self.clean(raw, lang, tr)
                msg["tr"] = {**msg["tr"], **clean}
                msg.pop("refining", None)
                msg["refined"] = round(time.time() - msg["t"], 1)
                self.emit({"type": "update", "id": msg["id"], "tr": msg["tr"]})
                self._write_transcript(msg)
                log(f"[refined +{msg['refined']}s] " + " | ".join(f"{k}: {v}" for k, v in msg["tr"].items()))
                if msg["id"] == self._vmix_last:  # only if it is still the line on air
                    self._vmix(msg)
            except Exception as e:
                log("[refiner]", repr(e))

    def _write_transcript(self, msg):
        if self.transcript:
            with open(self.transcript, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.fromtimestamp(msg['t']):%H:%M:%S}] ({msg['lang']}) {msg['src']}\n" +
                        "".join(f"    {k}: {v}\n" for k, v in msg["tr"].items()))

    def _emit_line(self, src, lang, t_end, dur, futs, refine=None):
        tr = {}
        for l, f in futs.items():
            try:
                tr[l] = f.result(timeout=10)
            except Exception as e:
                log(f"[translate] {l}: {e!r}")
                tr[l] = src
        raw = src
        if not refine:
            self.tr.remember(raw, lang, tr)  # translator context keeps the raw text; every output below is clean
        src, tr = self.clean(src, lang, tr)
        msg = {"type": "line", "id": self.next_id, "src": src, "lang": lang, "tr": tr,
               "lat": round(time.time() - t_end, 1), "t": time.time(),
               "ts": [round(max(0, t_end - dur - self.t0), 2), round(t_end - self.t0, 2)]}
        if refine:
            msg["refining"] = True
        self.next_id += 1
        self.history.append(msg)
        self.all_lines.append(msg)
        self.emit(msg)
        log(f"[{msg['lat']}s] ({lang}) {src}  ->  " + " | ".join(f"{k}: {v}" for k, v in tr.items()))
        if not refine:
            self._write_transcript(msg)  # AI lines are written once refined, with the final wording
        self._vmix_last = msg["id"]
        self._vmix(msg)
        return msg

    def _vmix(self, msg):
        """Queue the line's text for every mapped GT title field (provisional or final)."""
        v = self.s.get("vmix") or {}
        for lang, field in (v.get("fields") or {}).items():
            text = msg["src"] if lang == msg["lang"] else msg["tr"].get(lang)
            if field and text:
                self.vmix.set(v.get("host", ""), v.get("input", ""), field, text)
        # like the browser overlay's auto-hide: empty the title once nobody has spoken for `clear` seconds,
        # otherwise the last sentence stays on air in vMix forever
        try:
            secs = float(v.get("clear", 6) or 0)
        except (TypeError, ValueError):
            secs = 6.0
        with self.lock_vmix:
            if self._vmix_timer:
                self._vmix_timer.cancel()
            self._vmix_timer = None
            if secs > 0 and v.get("fields"):
                self._vmix_timer = threading.Timer(secs, self._vmix_clear)
                self._vmix_timer.daemon = True
                self._vmix_timer.start()

    def _vmix_clear(self):
        """Empty every mapped vMix title field (retracted text, Clear screens, Stop, idle)."""
        with self.lock_vmix:
            if self._vmix_timer:
                self._vmix_timer.cancel()
                self._vmix_timer = None
        v = self.s.get("vmix") or {}
        for field in (v.get("fields") or {}).values():
            if field:
                self.vmix.set(v.get("host", ""), v.get("input", ""), field, "")

    def _worker(self):
        """Single consumer: finals, partials and typed lines are handled strictly in order."""
        spec = (None, "", None, {})  # (key, text, lang, {lang: Future}) of the last speculative final
        dbg = os.environ.get("LT_DEBUG_AUDIO")
        st = collections.defaultdict(list)
        t_dbg = time.time()
        while True:
            item = self.utt.get()
            if dbg:
                st["q"].append(self.utt.qsize())
                if time.time() - t_dbg >= 10:
                    log("[worker-dbg] " + " · ".join(f"{k} n={len(v)} avg={np.mean(v) * (1 if k == 'q' else 1000):.0f}"
                                                    f"{'' if k == 'q' else 'ms'} max={max(v) * (1 if k == 'q' else 1000):.0f}"
                                                    for k, v in sorted(st.items()) if v))
                    st.clear(); t_dbg = time.time()
            t_item = time.time()
            if not self.running or len(item) != 4:
                continue
            kind, data, t_end, key = item
            try:
                if kind == "text":  # typed by the operator: in the language currently being spoken
                    self.publish(data, self.cur_lang, t_end, 2.0)
                    continue
                if dbg:
                    st["wait_" + kind].append(t_item - t_end)  # how long it sat in the queue
                if not self.stt:
                    continue
                if kind == "partial":
                    if self.utt.qsize():  # behind: never spend time on a caption that is already stale
                        continue
                    lang = self.choose_lang(data, "partial")
                    src = self.stt(data, lang, fast=True)
                    if src and src.lower().strip(" .!?,…") not in HALLU:
                        self.publish_partial(src, lang)
                    continue
                if kind == "spec":
                    lang = self.choose_lang(data, "final")
                    STT_BUSY.set()
                    try:
                        src = self.stt(data, lang)
                    finally:
                        STT_BUSY.clear()
                    futs = {}
                    if src and src.lower().strip(" .!?,…") not in HALLU:
                        if self.s.get("engine", "").startswith("ollama:"):  # slow engine: start it now
                            futs = {l: self.pool.submit(self.tr, src, l, lang) for l in self.others(lang)}
                        self.publish_partial(src, lang)  # best-quality provisional text, the moment they pause
                    spec = (key, src, lang, futs)
                    continue
                waiting = sum(1 for it in list(self.utt.queue) if it and it[0] == "audio")
                if waiting >= 2:
                    log(f"[worker] backlog: {waiting} sentences waiting for transcription — speech recognition is behind")
                hit = key is not None and spec[0] == key  # speaker stayed quiet: speculative result is final
                if hit:
                    src, lang = spec[1], spec[2]
                else:
                    lang = self.choose_lang(data, "final")
                    STT_BUSY.set()
                    try:
                        src = self.stt(data, lang)
                    finally:
                        STT_BUSY.clear()
                if self.is_echo(src):  # before the language check: an echoed English voice must not count as "speakers use English"
                    self.emit({"type": "partial", "src": "", "lang": lang, "tr": {}})
                    continue
                self.check_language(data)
                if src.lower().strip(" .!?,…") in HALLU:
                    self.emit({"type": "partial", "src": "", "lang": lang, "tr": {}})  # retract provisional text
                    self._vmix_clear()  # …in the vMix title too (it may be showing that provisional text)
                    continue
                t_pub = time.time()
                self.publish(src, lang, t_end, len(data) / SR, pre=spec[3] if hit else None)
                if dbg:
                    st["final_stt"].append(t_pub - t_item); st["final_publish"].append(time.time() - t_pub)
            except Exception as e:
                log("[worker]", repr(e))

    ECHO_S, ECHO_RATIO = 20, 0.8  # echoes measured 5–12 s after the original

    def is_echo(self, src):
        """True if this 'new' sentence is something we published in the last 20 s — in any language. That's the
        input hearing our own output: a phone playing Listen out loud near the mic, or the translation on the PA.
        Without this guard every line comes back 6–12 s later (often re-worded, since Whisper turns the English
        voice back into the session's language) and can loop forever."""
        norm = lambda t: re.sub(r"\W+", " ", t.lower()).strip()
        n = norm(src)
        if len(n) < 8:  # "ok", "oui oui": too short to tell an echo from someone repeating themselves
            return False
        now, nums = time.time(), re.findall(r"\d+", n)
        for m in reversed(self.history):
            if now - m["t"] > self.ECHO_S:
                break
            if now - m["t"] < 3:  # an echo needs ≥3 s (voice generated, played, heard, sentence ends)
                continue
            for t in [m["src"], *m["tr"].values()]:
                t = norm(t)
                if re.findall(r"\d+", t) != nums:  # "table 12" vs "table 13": different content, not an echo
                    continue
                if difflib.SequenceMatcher(None, n, t).ratio() >= self.ECHO_RATIO:
                    log(f"[echo] dropped {src!r} — matches line {m['id']} from {now - m['t']:.0f}s ago")
                    self.echoes = [x for x in getattr(self, "echoes", []) if now - x < 120] + [now]
                    if len(self.echoes) >= 2 and not self.echo_note:
                        self.echo_note = ("LiveTranslate is hearing its own translation (Listen playing near the microphone, "
                                          "or the translation on the PA). Repeated lines are being dropped — use earphones "
                                          "for Listen, or feed the console instead of a room mic.")
                        self.push_state()
                    return True
        if self.echo_note and now - max(getattr(self, "echoes", [0])) > 120:
            self.echo_note = ""
            self.push_state()
        return False

    def say(self, text):
        self.utt.put(("text", text, time.time(), None))

    def _demo(self):
        i = 0
        while True:
            time.sleep(4)
            if self.running and not self.paused:
                words = DEMO[i % len(DEMO)].split()
                for n in range(3, len(words), 3):  # simulate provisional captions while "speaking"
                    self.publish_partial(" ".join(words[:n]), self.cur_lang)
                    time.sleep(0.6)
                self.say(DEMO[i % len(DEMO)])
                i += 1


# ======================================================================= web
def load_conf(args):
    conf = {}
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH) as f:
            conf = json.load(f)
    conf.setdefault("secret", secrets.token_hex(32))
    return conf


def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not IS_WIN:  # same reuse rule aiohttp applies, so a port we just left counts as free
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port(want):
    """The wanted port, or — if another program holds it — the next free one (never fail to start at a show)."""
    for p in [want] + list(range(want + 1, want + 20)):
        if port_free(p):
            return p
    return want


def save_conf(conf):
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(conf, f, indent=2)
    os.chmod(tmp, 0o600)  # holds the password hash and the session secret
    os.replace(tmp, CONF_PATH)


class Auth:
    """Who may use Control and the API. The machine running LiveTranslate is trusted (localhost); every other device
    signs in with the admin password. Password: PBKDF2-SHA256, salted, never stored. Sessions: signed cookies valid
    30 days; changing the password or 'sign out all devices' rotates the secret and ends every session.
    Brute force: 5 wrong attempts from a device locks it 30 s, doubling up to 15 min."""
    ITER, DAYS, COOKIE, MIN_LEN = 200_000, 30, "lt_admin", 8

    def __init__(self, conf):
        self.conf, self.fails = conf, {}
        if conf.get("pin") and not conf.get("admin"):  # older versions: a 4-digit PIN keeps working until replaced
            salt = secrets.token_hex(16)
            conf["admin"] = {"salt": salt, "hash": self._hash(str(conf["pin"]), salt), "weak": True}
        conf.pop("pin", None)
        save_conf(conf)

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), Auth.ITER).hex()

    def status(self):
        a = self.conf.get("admin") or {}
        return {"has_password": bool(a), "weak": bool(a.get("weak")), "changed": a.get("changed", "")}

    def check(self, pw):
        a = self.conf.get("admin")
        return bool(a) and hmac.compare_digest(self._hash(pw or "", a["salt"]), a["hash"])

    def set_password(self, pw):
        salt = secrets.token_hex(16)
        self.conf["admin"] = {"salt": salt, "hash": self._hash(pw, salt), "changed": time.strftime("%Y-%m-%d %H:%M")}
        self.rotate()

    def rotate(self):  # every existing session becomes invalid
        self.conf["secret"] = secrets.token_hex(32)
        save_conf(self.conf)

    def token(self):
        ts = str(int(time.time()))
        return ts + "." + hmac.new(self.conf["secret"].encode(), ts.encode(), "sha256").hexdigest()

    def valid(self, tok):
        ts, _, sig = (tok or "").partition(".")
        if not ts.isdigit() or time.time() - int(ts) > self.DAYS * 86400:
            return False
        return hmac.compare_digest(sig, hmac.new(self.conf["secret"].encode(), ts.encode(), "sha256").hexdigest())

    def local(self, req):
        return req.remote in LOCAL

    def authed(self, req):
        return self.local(req) or self.valid(req.cookies.get(self.COOKIE))

    def set_cookie(self, resp):
        resp.set_cookie(self.COOKIE, self.token(), max_age=self.DAYS * 86400, httponly=True, samesite="Strict", path="/")

    def locked_for(self, ip):
        n, until = self.fails.get(ip, (0, 0))
        return max(0, int(until - time.time()) + 1) if until > time.time() else 0

    def failed(self, ip):
        n = self.fails.get(ip, (0, 0))[0] + 1
        wait = 0 if n < 5 else min(900, 30 * 2 ** (n - 5))
        self.fails[ip] = (n, time.time() + wait)
        log(f"[auth] wrong password from {ip} ({n})" + (f" — locked {wait} s" if wait else ""))
        return wait


def make_app(hub, args, conf):
    base = f"http://{args.ip}:{args.port}"
    auth = Auth(conf)
    if args.password:
        auth.set_password(args.password)
    authed = auth.authed

    def need_auth(req, api=False):
        if not authed(req):
            if api:
                raise web.HTTPUnauthorized(text="sign in at /login")
            raise web.HTTPFound("/login")

    def page(name):
        return web.FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-store"})

    async def guest(req): return page("index.html")
    async def overlay(req): return page("overlay.html")

    async def control(req):
        need_auth(req)
        return page("control.html")

    async def login(req):
        ip, err, wait = req.remote, "", auth.locked_for(req.remote)
        if req.method == "POST" and not wait:
            data = await req.post()
            if auth.check(data.get("password", "")):
                auth.fails.pop(ip, None)
                log(f"[auth] signed in from {ip}")
                r = web.HTTPFound("/control")
                auth.set_cookie(r)
                raise r
            wait = auth.failed(ip)
            err = "Wrong password."
        if wait:
            err = f"Too many attempts. Try again in {wait} s."
        if not auth.status()["has_password"]:
            err, form = "", False
        else:
            form = True
        with open(os.path.join(STATIC, "login.html"), encoding="utf-8") as f:
            page_ = f.read()
        page_ = page_.replace("{{ERROR}}", html.escape(err)).replace("{{FORM}}", "" if form else "hidden") \
                     .replace("{{NOPW}}", "hidden" if form else "")
        return web.Response(content_type="text/html", text=page_, headers={"Cache-Control": "no-store"})

    async def logout(req):
        r = web.HTTPFound("/login")
        r.del_cookie(auth.COOKIE, path="/")
        raise r

    def qr_svg(data):
        import qrcode, qrcode.image.svg
        return qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2).to_string().decode()

    async def qr(req):
        s = hub.s
        # Wi-Fi QR syntax: \ ; , : " must be backslash-escaped or phones join the wrong network / fail
        wq = lambda v: re.sub(r'([\\;,:"])', r"\\\1", str(v or ""))
        pw = s.get("wifi_pass", "")
        data = ((f"WIFI:T:WPA;S:{wq(s['wifi_ssid'])};P:{wq(pw)};;" if pw else f"WIFI:T:nopass;S:{wq(s['wifi_ssid'])};;")
                if req.match_info["kind"] == "wifi" and s.get("wifi_ssid") else base + "/")
        return web.Response(text=qr_svg(data), content_type="image/svg+xml")

    async def speech(req):
        """MP3 of one line in one language for phones that tapped Listen (public, like the audience page).
        Waits briefly for the AI's refined wording — listeners hear each sentence once, so make it the better one."""
        lang, key = req.match_info["lang"], req.match_info["id"]
        gender = hub.s.get("voice_gender", "female")
        if key == "hello":
            import tts
            text = tts.HELLO.get(lang, "")
        else:
            try:
                lid = int(key)
            except ValueError:
                raise web.HTTPNotFound()
            msg = next((m for m in reversed(hub.history) if m["id"] == lid), None)
            if msg is None:
                raise web.HTTPNotFound()
            for _ in range(20):  # ≤ 2 s
                if not msg.get("refining"):
                    break
                await asyncio.sleep(0.1)
            text = msg["src"] if lang == msg["lang"] else msg["tr"].get(lang, "")
            text = re.sub(r"\w\*+", "", text)  # bleeped words are left out, not spelled "f star star"
        fut = hub.voice.get(key, text, lang, gender)
        try:
            mp3 = await asyncio.wait_for(asyncio.wrap_future(fut), 15)
        except Exception as e:
            log(f"[voice] {lang}: {e!r}")
            mp3 = None
        if not mp3:
            raise web.HTTPNotFound()
        return web.Response(body=mp3, content_type="audio/mpeg", headers={"Cache-Control": "max-age=600"})

    async def card(req):
        with open(os.path.join(STATIC, "card.html"), encoding="utf-8") as f:
            page_html = f.read()
        s = hub.s
        langs = " · ".join(LANGS[l][0] for l in session_langs(s) if l in LANGS)  # every language a phone can pick
        rep = {"{{NAME}}": s["name"], "{{LANGS}}": langs, "{{URL}}": base + "/",
               "{{SSID}}": s.get("wifi_ssid", ""), "{{PASS}}": s.get("wifi_pass", ""),
               "{{HASWIFI}}": "wifi" if s.get("wifi_ssid") else "nowifi"}
        for k, v in rep.items():
            page_html = page_html.replace(k, html.escape(str(v)))  # a "<" in a session name must not break the card
        return web.Response(text=page_html, content_type="text/html")

    # ---- JSON API (operator only)
    async def api(req):
        need_auth(req, api=True)
        what = req.match_info["what"]
        if what == "state":  # polled by the menu-bar app
            return web.json_response({**hub.state(), **auth.status(), "base": base})
        if what == "info":
            try:  # offline (Argos) language pairs installed on this machine — Control warns about gaps
                import argostranslate.package as ap
                pairs = sorted(f"{x.from_code}-{x.to_code}" for x in ap.get_installed_packages())
            except Exception:
                pairs = []
            return web.json_response({"base": base, "langs": LANGS, "whisper": list(WHISPER), "style": STYLE,
                                      "default": DEFAULT, "auth": {**auth.status(), "local": auth.local(req)},
                                      "argos_pairs": pairs, "voices": hub.voice.langs(), "platform": sys.platform,
                                      "fit": machine_fit()})
        if what == "devices":
            try:
                import sounddevice as sd
                pa_rescan(sd)
                devs = [{"name": d["name"], "ch": d["max_input_channels"]} for _, d in input_devices(sd)]
            except Exception as e:
                devs = [{"name": f"(audio unavailable: {e})", "ch": 0}]
            return web.json_response(devs)
        if what == "ollama":  # local AI models: Ollama's + those installed only as MLX weights (Mac)
            try:
                with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=1.5) as r:
                    names = [m["name"] for m in json.load(r)["models"]]
            except Exception:
                names = []
            names += [n for n in MLX_MODELS if n not in names and mlx_repo(n)]
            return web.json_response(names)
        if what == "sessions":
            os.makedirs(SESS_DIR, exist_ok=True)
            out = []
            for fn in sorted(os.listdir(SESS_DIR)):
                if fn.endswith(".json"):  # one corrupt file must not hide every saved setup
                    s = read_json(os.path.join(SESS_DIR, fn), None)
                    if isinstance(s, dict) and s.get("name"):
                        out.append(s)
            return web.json_response(out)
        if what == "srt":
            lang = req.query.get("lang", "")
            if lang not in LANGS:  # also keeps the query out of the Content-Disposition header
                lang = session_langs(hub.s)[0]
            def tc(x):
                ms = int(round(x * 1000))
                h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
                return f"{h:02}:{m:02}:{sec:02},{ms:03}"
            rows = [(l["ts"], l["src"] if lang == l.get("lang", hub.s["source"]) else l["tr"].get(lang, ""))
                    for l in list(hub.all_lines)]
            rows = [(ts, t) for ts, t in rows if t.strip()]  # an empty cue is invalid SRT for many players
            out = []
            for n, ((a, b), text) in enumerate(rows, 1):
                end = max(b, a + 1)  # at least 1 s on screen…
                if n < len(rows):
                    end = max(b, min(end, rows[n][0][0]))  # …but never overlapping the next cue
                out.append(f"{n}\n{tc(a)} --> {tc(end)}\n{text}\n")
            return web.Response(text="\n".join(out), content_type="text/plain", charset="utf-8", headers={
                "Content-Disposition": f'attachment; filename="{slug(hub.s["name"])}_{lang}.srt"'})
        if what == "gloss_suggest" and req.method == "POST":  # Control: fill a glossary row's empty languages
            b = await req.json()
            term, frm = str(b.get("term", "")).strip()[:120], b.get("from")
            tos = [l for l in b.get("to", []) if l in LANGS and l != frm]
            if not term or frm not in LANGS or not tos:
                return web.json_response({})
            eng = b.get("engine") or hub.s.get("engine", "argos")
            if not eng.startswith("ollama:"):  # Fastest is for live speed; a suggestion can afford the best model
                try:
                    with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=1.5) as r:
                        names = [m["name"] for m in json.load(r)["models"]]
                except Exception:
                    names = []
                names += [n for n in MLX_MODELS if n not in names and mlx_repo(n)]
                if names:
                    eng = "ollama:" + names[0]
            tr = Translator({**hub.s, "engine": eng, "context": b.get("context", hub.s.get("context", ""))})
            loop = asyncio.get_running_loop()
            outs = await asyncio.gather(*[loop.run_in_executor(hub.pool, tr.term, term, frm, l) for l in tos],
                                        return_exceptions=True)
            case = lambda o: (o[0].lower() + o[1:]) if term[:1].islower() and o[:2] != o[:2].upper() else o  # "court métrage" -> "short film", but keep "BBC"
            return web.json_response({l: case(o) for l, o in zip(tos, outs) if isinstance(o, str) and o})
        if what == "ndi":
            if req.method == "POST":
                hub.ndi.save((await req.json()).get("outputs", []))
            return web.json_response(hub.ndi.status())
        if what == "presets" and req.method == "GET":
            return web.json_response(hub.presets)
        if what == "transcript":
            if not hub.transcript or not os.path.exists(hub.transcript):
                raise web.HTTPNotFound(text="No transcript yet")
            return web.FileResponse(hub.transcript, headers={
                "Content-Disposition": f'attachment; filename="{os.path.basename(hub.transcript)}"'})
        if req.method == "POST" and what in ("password", "signout_all"):
            body = await req.json() if req.can_read_body else {}
            if what == "password":
                new = body.get("new", "")
                if len(new) < Auth.MIN_LEN:
                    return web.json_response({"ok": False, "error": f"Use at least {Auth.MIN_LEN} characters."})
                # from another device, changing it needs the current one (a stolen session can't lock the owner out)
                if auth.status()["has_password"] and not auth.local(req) and not auth.check(body.get("current", "")):
                    auth.failed(req.remote)
                    return web.json_response({"ok": False, "error": "The current password is wrong."})
                auth.set_password(new)
                log(f"[auth] admin password changed from {req.remote} — all other devices signed out")
            else:
                auth.rotate()
                log(f"[auth] all devices signed out from {req.remote}")
            r = web.json_response({"ok": True, **auth.status()})
            auth.set_cookie(r)  # the device that made the change stays signed in
            return r
        if req.method == "POST":
            body = await req.json() if req.can_read_body else {}
            if what == "start":
                hub.running, hub.loading = False, True  # immediately: no stale LIVE while models load
                hub.push_state()
                threading.Thread(target=hub.start, args=(body,), daemon=True).start()
            elif what == "stop":
                hub.stop()
            elif what == "update":
                hub.update_live(body)
            elif what == "monitor":
                hub.monitor(body)
            elif what == "phone_pace":  # Control → Access: default reading style on phones, applies at once
                if body.get("pace") in ("live", "smooth", "relaxed"):
                    hub.s["phone_pace"] = body["pace"]
                    hub.push_state()
            elif what == "port":  # Control → Access → Network: move to another port (saved), live session resumes
                try:
                    port = int(body.get("port"))
                except (TypeError, ValueError):
                    return web.json_response({"error": "Enter a number between 1024 and 65535."}, status=400)
                if not 1024 <= port <= 65535:
                    return web.json_response({"error": "Use a port between 1024 and 65535."}, status=400)
                if port != args.port and not port_free(port):
                    return web.json_response({"error": f"Port {port} is already used by another program on this machine."}, status=409)
                conf["port"] = port
                save_conf(conf)
                if port == args.port:
                    return web.json_response({"ok": True, "port": port, "restart": False})
                log(f"[net] moving to port {port} (restarting)")
                if not os.environ.get("LT_PARENT"):  # run by hand: nobody would restart us
                    return web.json_response({"ok": True, "port": port, "restart": False,
                                              "note": "Saved — restart the server to use it."})
                async def bye():  # no hub.stop(): the menu-bar/tray app restarts us with --autostart (session resumes)
                    await asyncio.sleep(0.6)
                    if hub.ndi:
                        hub.ndi.stop_all()
                    os._exit(RESTART_CODE)
                asyncio.ensure_future(bye())
                return web.json_response({"ok": True, "port": port, "restart": True})
            elif what == "voice":  # Control → Access: natural voice female/male, applies to the next sentence
                if body.get("gender") in ("female", "male"):
                    hub.s["voice_gender"] = body["gender"]
                    hub.push_state()
            elif what == "gain":  # the Setup fader: applies at once, live session or input check
                hub.set_gain(body.get("gain_db", 0))
            elif what == "quit":  # Control's "Quit LiveTranslate": works even when the menu-bar icon is hidden
                log("[app] quit requested from Control")
                hub.stop()
                if hub.ndi:  # os._exit skips on_shutdown: stop the NDI senders here or they outlive us
                    hub.ndi.stop_all()
                if os.environ.get("LT_PARENT") and IS_MAC:
                    os.kill(os.getppid(), signal.SIGTERM)  # menu-bar app: stops the AI runtime, then exits
                # Windows/Linux tray: it sees this exit code 0 (crashes are non-zero) and quits too —
                # os.kill(SIGTERM) on Windows would TerminateProcess the tray and orphan the AI runtime
                threading.Timer(0.5, lambda: os._exit(0)).start()
            elif what == "presets":
                if body.get("delete"):
                    hub.delete_preset(body["name"])
                else:
                    hub.save_preset(slug(body["name"]), body.get("style", {}))
            return web.json_response({"ok": True})
        raise web.HTTPNotFound()

    async def ws_handler(req):
        kind = req.query.get("kind", "guest")
        if kind == "op" and not authed(req):  # signed out (password changed / signed out elsewhere): say so, don't downgrade
            ws = web.WebSocketResponse()
            await ws.prepare(req)
            await ws.send_str(json.dumps({"type": "auth", "ok": False}))
            await ws.close()
            return ws
        if kind not in hub.clients:
            kind = "guest"
        ws = web.WebSocketResponse(heartbeat=10)  # dead overlays/phones detected within ~10 s
        await ws.prepare(req)
        hub.outq[ws] = q = asyncio.Queue(maxsize=200)
        q.put_nowait(json.dumps(hub.state() if kind == "op" else hub.public(), ensure_ascii=False))
        if kind in ("overlay", "op"):
            q.put_nowait(json.dumps({"type": "presets", "presets": hub.presets}, ensure_ascii=False))
        q.put_nowait(json.dumps({"type": "history", "lines": list(hub.history)}, ensure_ascii=False))
        hub.clients[kind].add(ws)
        sender = asyncio.ensure_future(hub.sender(ws))
        hub.push_state()
        try:
            async for m in ws:
                if kind != "op" or m.type != WSMsgType.TEXT:
                    continue
                try:  # one malformed message must not drop the operator's connection
                    c = json.loads(m.data)
                    cmd = c.get("cmd") if isinstance(c, dict) else None
                    if cmd in ("pause", "resume"):
                        hub.paused = cmd == "pause"
                    elif cmd == "delete" and "id" in c:
                        # in place (the publisher thread appends to these concurrently), and from the subtitle
                        # files too: a deleted line is a correction, it must not come back in the SRT
                        for coll in (hub.history, hub.all_lines):
                            for x in [x for x in coll if x["id"] == c["id"]]:
                                coll.remove(x)
                        hub.emit({"type": "delete", "id": c["id"]})
                    elif cmd == "clear":
                        hub.history.clear()
                        hub.emit({"type": "clear"})
                        hub._vmix_clear()
                    elif cmd == "say" and str(c.get("text", "")).strip() and hub.tr:
                        hub.say(str(c["text"]).strip())
                    hub.push_state()
                except Exception as e:
                    log("[ws] bad operator message:", repr(e))
        finally:
            hub.clients[kind].discard(ws)
            hub.outq.pop(ws, None)
            sender.cancel()
            hub.push_state()
        return ws

    async def on_start(app):
        hub.loop = asyncio.get_running_loop()
        hub.ndi = NdiOutputs(args.port)
        hub.ndi.reconcile()
        if args.autostart:
            last = sorted((os.path.join(SESS_DIR, f) for f in os.listdir(SESS_DIR) if f.endswith(".json")),
                          key=os.path.getmtime) if os.path.isdir(SESS_DIR) else []
            for path in reversed(last):  # newest readable session (a corrupt file must not block the resume)
                s = read_json(path, None)
                if isinstance(s, dict) and s.get("name"):
                    threading.Thread(target=hub.start, args=(s,), daemon=True).start()
                    break

    async def on_stop(app):
        if hub.ndi:
            hub.ndi.stop_all()
        MlxRuntime.stop()

    app = web.Application()
    app.on_startup.append(on_start)
    app.on_shutdown.append(on_stop)
    r = app.router
    r.add_get("/", guest)
    r.add_get("/overlay", overlay)
    r.add_get("/control", control)
    r.add_route("*", "/login", login)
    r.add_get("/logout", logout)
    r.add_get("/card", card)
    r.add_get("/tts/{id}/{lang}.mp3", speech)
    r.add_get("/qr/{kind}.svg", qr)
    r.add_route("*", "/api/{what}", api)
    r.add_get("/ws", ws_handler)
    r.add_static("/fonts", os.path.join(STATIC, "fonts"), append_version=False)
    return app


def download(langs):
    print("Downloading Whisper models (turbo)…")
    import mlx_whisper
    mlx_whisper.transcribe(np.zeros(SR, np.float32), path_or_hf_repo=WHISPER["turbo"])
    mlx_whisper.transcribe(np.zeros(SR, np.float32), path_or_hf_repo=FAST_WHISPER)
    import argostranslate.package as p
    p.update_package_index()
    have = {(x.from_code, x.to_code) for x in p.get_installed_packages()}
    for pkg in p.get_available_packages():
        pair = (pkg.from_code, pkg.to_code)
        if pkg.from_code in langs and pkg.to_code in langs and pair not in have:
            print(f"Installing Argos {pair[0]}->{pair[1]}…")
            p.install_from_path(pkg.download())
    print("Done — everything needed is stored locally.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=None,
                    help=f"default: the port saved in Control → Access (config.json), else {DEFAULT_PORT}")
    ap.add_argument("--ip", help="override detected LAN IP shown in QR codes")
    ap.add_argument("--password", "--pin", dest="password",
                    help="set the admin password for other devices (min 8 characters; saved as a hash)")
    ap.add_argument("--autostart", action="store_true", help="start the last used session on launch")
    ap.add_argument("--demo", action="store_true", help="no audio/Whisper: send sample sentences")
    ap.add_argument("--download", metavar="LANGS", nargs="?", const="en,fr,es",
                    help="download models (needs internet), e.g. --download en,fr,es,de")
    args = ap.parse_args()
    if args.download:
        return download(args.download.split(","))
    args.ip = args.ip or local_ip()
    os.makedirs(DATA, exist_ok=True)
    if args.password is not None and len(args.password) < Auth.MIN_LEN:
        sys.exit(f"--password: use at least {Auth.MIN_LEN} characters")
    conf = load_conf(args)
    want = args.port or int(os.environ.get("LT_PORT") or 0) or int(conf.get("port") or 0) or DEFAULT_PORT
    args.port = free_port(want)
    args.port_note = "" if args.port == want else (
        f"Port {want} is used by another program on this machine, so LiveTranslate runs on {args.port} this time — "
        f"addresses in vMix/OBS changed. Pick a free port in Control → Access → Network.")
    if args.port_note:
        log("[net]", args.port_note)
    with open(os.path.join(DATA, "running-port"), "w") as f:  # the menu-bar/tray app reads where we really are
        f.write(str(args.port))
    hub = Hub(demo=args.demo)
    hub.port_note = args.port_note
    if os.environ.get("LT_PARENT"):  # started by the menu-bar app: never outlive it (frees port + interface)
        parent = int(os.environ.get("LT_PARENT_PID") or os.getppid())
        def watch():
            while parent_alive(parent):
                time.sleep(1)
            log("[app] menu-bar app gone — exiting")
            os._exit(0)
        threading.Thread(target=watch, daemon=True).start()
    access = ("admin password required from other devices" if conf.get("admin")
              else "this machine only — set an admin password in Control → Access to allow other devices")
    print(f"\n  Control:  http://{args.ip}:{args.port}/control   ({access})"
          f"\n  Audience: http://{args.ip}:{args.port}/"
          f"\n  Overlay:  http://{args.ip}:{args.port}/overlay?lang=fr   (vMix: Add Input > Web Browser)\n")
    web.run_app(make_app(hub, args, conf), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
