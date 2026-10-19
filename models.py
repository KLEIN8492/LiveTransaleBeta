"""Local AI translation tiers and "can this machine run it?" — shared by setup (installer.py) and Control (server.py).
Standard library only: setup imports it before any package is installed.

Translation speed of a local AI is set by memory bandwidth (every generated word reads the whole model once), and
whether it runs at all by memory. On a Mac the model shares RAM with Whisper, Dante, the browser and macOS — the
16 GB rehearsal Mac swapped and dropped AI lines with a 3 GB model + 4.4 GB LiveTranslate + apps (2026-10-11).

    python models.py      # prints what this machine can run
"""
import os, platform, re, shutil, subprocess, sys

IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform == "win32"
NO_WINDOW = {"creationflags": 0x08000000} if IS_WIN else {}

# Ordered weakest → strongest. Accuracy = tests/eval_context.py with the event description (16 tricky sentences).
# mem_gb: resident while translating (weights + caches). mlx: Apple Silicon weights (repo, download GB).
TIERS = [
    {"key": "balanced", "name": "Balanced", "model": "gemma3:4b", "accuracy": "11/16",
     "mlx": ("mlx-community/gemma-3-4b-it-qat-4bit", 3.03), "ollama_gb": 3.4, "mem_gb": 3.2,
     "desc": "Understands the event description. Right for live subtitles."},
    {"key": "accurate", "name": "Most accurate", "model": "translategemma:12b", "accuracy": "13/16",
     "mlx": ("mlx-community/translategemma-12b-it-4bit", 6.66), "ollama_gb": 8.1, "mem_gb": 7.8,
     "desc": "Google’s translation-tuned Gemma: best wording and jargon."},
    {"key": "maximum", "name": "Maximum", "model": "translategemma:27b", "accuracy": "not yet measured",
     "mlx": ("mlx-community/translategemma-27b-it-4bit", 15.23), "ollama_gb": 17.4, "mem_gb": 17.0,
     "desc": "The largest translation model: for a dedicated machine with lots of memory."},
]
TIER = {t["key"]: t for t in TIERS}
BY_MODEL = {t["model"]: t for t in TIERS}

RESERVE_GB = 13  # 16 GB Mac + gemma3:4b leaves 12.8 → "tight", as the 2026-10-11 rehearsal showed. Mac: memory the rest of the show needs (LiveTranslate+Whisper ~4.5, macOS ~3, Dante/browser/vMix)
LIVE_S, SLOW_S = 1.5, 3.0  # AI wording later than this after the fast version: "slow" / "too slow for live"


# ------------------------------------------------------------------ hardware
def ram_bytes():
    if IS_MAC:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0)
    if IS_WIN:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys
    with open("/proc/meminfo") as f:
        return int(f.readline().split()[1]) * 1024


def nvidia_gpu():
    """{'name', 'vram_gb'} of the first NVIDIA card (the driver's nvidia-smi), or None."""
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if IS_WIN else None)
    if not exe or not os.path.exists(exe):
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, **NO_WINDOW).stdout.strip().splitlines()
        name, mb = out[0].rsplit(",", 1)
        return {"name": name.strip(), "vram_gb": round(int(mb) / 1024)}
    except Exception:
        return None


def apple_chip():
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                              text=True).stdout.strip()
    except Exception:
        return ""


def apple_bandwidth(chip):
    """GB/s of an Apple Silicon chip — Apple's published figures; unknown chips by family (Ultra/Max/Pro/base)."""
    known = {"M1": 68, "M1 Pro": 200, "M1 Max": 400, "M1 Ultra": 800, "M2": 100, "M2 Pro": 200, "M2 Max": 400,
             "M2 Ultra": 800, "M3": 100, "M3 Pro": 150, "M3 Max": 300, "M3 Ultra": 800, "M4": 120, "M4 Pro": 273,
             "M4 Max": 410}
    m = re.search(r"\b(M\d+(?: (?:Pro|Max|Ultra))?)\b", chip)
    if m and m.group(1) in known:
        return known[m.group(1)]
    return 800 if "Ultra" in chip else 400 if "Max" in chip else 200 if "Pro" in chip else 120


def hardware():
    ram = ram_bytes() / 2 ** 30
    if IS_MAC:
        chip = apple_chip()
        return {"kind": "mac", "chip": chip, "ram_gb": round(ram), "bandwidth": apple_bandwidth(chip), "gpu": None}
    gpu = nvidia_gpu()
    return {"kind": "pc", "chip": platform.processor() or platform.machine(), "ram_gb": round(ram),
            "gpu": gpu, "bandwidth": 400 if gpu else 50}  # typical RTX card / dual-channel DDR on the CPU


# ------------------------------------------------------------------ verdict
def seconds_per_line(tier, hw, on_gpu=True):
    """Estimated delay of the AI wording for one line (two languages decoded together, ~20 words each).
    Calibrated on the M2 Pro (200 GB/s): gemma3:4b MLX measured 0.81 s → 54 × model GB / bandwidth."""
    gb = tier["mlx"][1] if hw["kind"] == "mac" else tier["ollama_gb"]
    return round(54 * gb / (hw["bandwidth"] if on_gpu else 50), 1)


def fit(tier, hw):
    """→ {'level': 'good'|'slow'|'tight'|'no', 'secs': float, 'why': str} — what Control and setup show."""
    if hw["kind"] == "mac":
        spare = hw["ram_gb"] - tier["mem_gb"]
        secs = seconds_per_line(tier, hw)
        if spare < RESERVE_GB - 3:
            return {"level": "no", "secs": secs, "why": f"needs about {tier['mem_gb'] + RESERVE_GB:.0f} GB of memory "
                    f"(this Mac has {hw['ram_gb']} GB): speech recognition and audio would starve"}
        mem_tight = spare < RESERVE_GB
    else:
        vram = (hw["gpu"] or {}).get("vram_gb", 0)
        on_gpu = vram >= tier["mem_gb"] + 1.5
        secs = seconds_per_line(tier, hw, on_gpu)
        if not on_gpu and hw["ram_gb"] < tier["mem_gb"] + 8:
            return {"level": "no", "secs": secs, "why": f"needs a graphics card with {tier['mem_gb'] + 1.5:.0f} GB "
                    f"or {tier['mem_gb'] + 8:.0f} GB of memory"}
        mem_tight = False
    if secs > SLOW_S:
        return {"level": "no", "secs": secs, "why": f"about {secs} s per line on this machine — too slow for live"}
    if mem_tight:
        return {"level": "tight", "secs": secs,
                "why": f"fits, but leaves little memory: close other apps (Dante, browser) during the show"}
    if secs > LIVE_S:
        return {"level": "slow", "secs": secs, "why": f"about {secs} s per line: fine for phones and slow speech"}
    return {"level": "good", "secs": secs, "why": f"about {secs} s per line"}


def report(hw=None):
    """Every tier with its verdict, plus the strongest one this machine runs well (the recommendation)."""
    hw = hw or hardware()
    tiers = [{**{k: v for k, v in t.items() if k != "mlx"}, "download_gb": t["mlx"][1] if hw["kind"] == "mac"
              else t["ollama_gb"], **fit(t, hw)} for t in TIERS]
    lv = lambda *ls: [t["key"] for t in tiers if t["level"] in ls]
    # live subtitles want speed first: strongest that is fast; else strongest slow one; else smallest tight one
    rec = (lv("good") or lv("slow") or lv("tight")[:1] or ["none"])[-1]
    return {"hardware": hw, "tiers": tiers, "recommend": rec}


if __name__ == "__main__":
    r = report()
    h = r["hardware"]
    print(f"{h['chip']} · {h['ram_gb']} GB" + (f" · {h['gpu']['name']} {h['gpu']['vram_gb']} GB" if h["gpu"] else ""))
    for t in r["tiers"]:
        print(f"  {t['name']:14} {t['model']:20} {t['level']:6} {t['why']}")
    print("recommended:", r["recommend"])
