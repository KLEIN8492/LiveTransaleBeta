#!/usr/bin/env python3
"""End-to-end latency bench: plays speech into BlackHole and measures what an overlay actually sees.

    .venv/bin/python tests/bench_live.py [--port 8766] [--engine argos|ollama:gemma3:4b] [--loops 1]

Starts a session on BlackHole 16ch ch1 (localhost needs no PIN), plays N sentences separated by
0.9 s pauses, and for each sentence reports, measured from the moment the speaker stopped:
  text   — first provisional caption showing the sentence's last word (what viewers see first)
  final  — final translated line
Soak test: --loops 30 (≈ 10 min). Also prints server RSS growth and the worst final latency.
"""
import argparse, asyncio, json, os, subprocess, tempfile, time, urllib.request, wave

import numpy as np
import aiohttp
import sounddevice as sd

SENTENCES = [
    "Good evening and welcome to the conference.",
    "Please take your seats, we are about to begin.",
    "Our keynote speaker has worked on renewable energy for twenty years.",
    "She will talk about battery storage and the grid.",
    "After the break we will have a panel with three guests.",
    "We will take questions from the audience at the end.",
]
MULTI = [  # (voice, language, sentence): speakers switching language, for --multi (source = auto)
    ("Samantha", "en", "Good evening and welcome to the conference."),
    ("Thomas", "fr", "Merci beaucoup, je suis très heureux d'être ici ce soir."),
    ("Mónica", "es", "Gracias, yo también estoy muy contenta de participar."),
    ("Samantha", "en", "Let's start with the first question for our panel."),
    ("Thomas", "fr", "Le stockage d'énergie est le grand défi des dix prochaines années."),
    ("Mónica", "es", "Estoy de acuerdo, sobre todo para las redes eléctricas."),
]
GAP = 0.9
SR = 48000


def synth(text, path, voice="Samantha"):
    subprocess.run(["say", "-v", voice, "-o", path, "--data-format=LEI16@48000", text], check=True)
    w = wave.open(path)
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    nz = np.nonzero(np.abs(a) > 0.01)[0]  # trim say's own leading/trailing silence: true speech end
    return a[nz[0]:nz[-1] + 1]


def build(loops, items):
    tmp = tempfile.mkdtemp()
    clips = [synth(t, os.path.join(tmp, f"{i}.wav"), v) for i, (v, _, t) in enumerate(items)]
    audio, marks, pos = [], [], 0.5
    audio.append(np.zeros(int(SR * 0.5), np.float32))
    for _ in range(loops):
        for (_, lang, t), c in zip(items, clips):
            audio.append(c); pos += len(c) / SR
            marks.append((t, lang, pos))  # seconds from playback start where speech ends
            audio.append(np.zeros(int(SR * GAP), np.float32)); pos += GAP
    audio.append(np.zeros(SR * 4, np.float32))
    return np.concatenate(audio), marks


def last_word(t):
    return t.rstrip(".,!? ").split()[-1].lower().strip(".,")


def rss(port):
    pid = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
    if not pid:
        return 0
    out = subprocess.run(["ps", "-o", "rss=", "-p", pid[0]], capture_output=True, text=True).stdout.strip()
    return int(out or 0) // 1024


def no_app_nap():
    """A background player gets throttled by macOS after a few minutes -> stuttering playback, dropped
    samples, merged sentences that look like server bugs. Same opt-out the server uses during a session."""
    try:
        from Foundation import NSProcessInfo
        return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(0x00FFFFFF | 0xFF00000000, "bench")
    except Exception:
        return None


def wire_gaps(rec, marks, t_play):
    """Measure what actually went over BlackHole: shortest silence between sentences as received."""
    env = np.abs(rec[:, 0]) > 0.01
    idx = np.nonzero(env)[0]
    if not len(idx):
        return None
    gaps = np.diff(idx) / SR
    return float(gaps[gaps > 0.3].min()) if (gaps > 0.3).any() else 0.0


async def main():
    _act = no_app_nap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--engine", default="argos")
    ap.add_argument("--loops", type=int, default=1)
    ap.add_argument("--multi", action="store_true", help="EN/FR/ES speakers, source=auto")
    ap.add_argument("--vmix", default="", help="host:port of vMix (or tests/mock_vmix.py) to test title push")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    items = MULTI if a.multi else [("Samantha", "en", t) for t in SENTENCES]
    audio, marks = build(a.loops, items)
    sess = {"name": "Bench", "source": "auto" if a.multi else "en",
            "targets": ["en", "fr", "es"] if a.multi else ["fr", "es"], "engine": a.engine,
            "device": "BlackHole 16ch", "channel": 1, "bleep": True,
            "vmix": {"host": a.vmix, "input": "Captions", "fields": {"fr": "Headline.Text", "es": "Description.Text"}}}
    req = urllib.request.Request(base + "/api/start", json.dumps(sess).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
    while True:
        st = json.load(urllib.request.urlopen(base + "/api/state"))
        if st["running"] and not st["loading"]:
            break
        await asyncio.sleep(0.5)
    rss0 = rss(a.port)
    events = []
    async with aiohttp.ClientSession() as cs, cs.ws_connect(base.replace("http", "ws") + "/ws?kind=overlay") as ws:
        async def listen():
            async for m in ws:
                d = json.loads(m.data)
                if d.get("type") in ("partial", "line"):
                    events.append((time.time(), d))
        task = asyncio.create_task(listen())
        await asyncio.sleep(0.5)
        t0 = time.time()
        rec = sd.playrec(audio, SR, device="BlackHole 16ch", input_mapping=[1], output_mapping=[1],
                         latency="high")
        await asyncio.sleep(len(audio) / SR + 2)
        task.cancel()
    rows, finals = [], []
    for text, lang, end in marks:
        t_end = t0 + end
        w = last_word(text)
        seen = next((t for t, d in events if t >= t_end - 0.3 and w in d["src"].lower()), None)
        hit = next(((t, d) for t, d in events if d["type"] == "line" and t >= t_end - 0.3 and w in d["src"].lower()), None)
        fin = hit and hit[0]
        got = hit and hit[1].get("lang", "en")
        rows.append((text, seen and seen - t_end, fin and fin - t_end, lang, got, hit and hit[1]))
        if fin:
            finals.append(fin - t_end)
    f = lambda x: f"{x:5.2f}s" if x is not None else "  miss"
    print(f"\nengine={a.engine}  sentences={len(marks)}")
    print("  text   final  lang  sentence")
    for text, s, fn, lang, got, d in rows[: len(items)] if a.loops > 3 else rows:
        flag = lang if got == lang else f"{got}≠{lang}" if got else "  "
        print(f" {f(s)} {f(fn)}  {flag:5} {text[:50]}")
        if a.multi and d:
            print("                        " + " | ".join(f"{k}: {v[:38]}" for k, v in d["tr"].items()))
    wrong = [r for r in rows if r[4] and r[4] != r[3]]
    print(f"\n  language detected correctly: {len(rows) - len(wrong) - sum(1 for r in rows if not r[4])}/{len(rows)}")
    ok = [r for r in rows if r[2] is not None]
    if ok:
        print(f"\n  median text {np.median([r[1] for r in ok if r[1] is not None]):.2f}s · median final "
              f"{np.median(finals):.2f}s · worst final {max(finals):.2f}s · missed {len(rows) - len(ok)}/{len(rows)}")
    print(f"  server RSS {rss0} MB -> {rss(a.port)} MB")
    sd.wait()
    g = wire_gaps(rec, marks, t0)
    print(f"  shortest pause between sentences as received on BlackHole: {g:.2f}s (sent {GAP:.2f}s)"
          + ("  <-- playback was disturbed: results not trustworthy" if g is not None and g < GAP - 0.1 else ""))


if __name__ == "__main__":
    asyncio.run(main())
