#!/usr/bin/env python3
"""Pipeline throughput: a slow translator (e.g. a loaded LLM) must not stall speech recognition.

    .venv/bin/python tests/test_pipeline.py

Stubs STT (0.1 s), a fast translation (0.05 s) and an AI translation (2 s per language), feeds 6 sentences 0.3 s
apart and checks "show first, refine after": (1) every sentence is published in order, immediately, with the fast
translation, (2) the STT worker never builds a backlog, (3) the AI's version arrives later as an 'update' for lines.
"""
import os, sys, time, threading
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server as S

STT_S, TR_S, N = 0.1, 2.0, 6


class FakeSTT:
    def __init__(self):
        self.n = 0
    def __call__(self, audio, lang, fast=False):
        time.sleep(STT_S)
        return f"sentence {int(audio[0])}"


class FakeTR:
    recent = []
    def __call__(self, text, to, frm, fast=False):
        time.sleep(0.05 if fast else TR_S)
        return f"(fast) {text}" if fast else f"[AI {to}] {text}"
    def remember(self, *a):
        pass


def main():
    hub = S.Hub()
    hub.s = {**S.DEFAULT, "source": "en", "targets": ["fr", "es"], "engine": "ollama:fake", "bleep": False}
    hub.stt, hub.tr, hub.running, hub.t0 = FakeSTT(), FakeTR(), True, time.time()
    out, upd, qmax, t0 = [], [], [0], time.time()
    def emit(m, kinds=None):
        if m.get("type") == "line":
            out.append((time.time() - t0, m["src"], m["tr"]["fr"]))
        elif m.get("type") == "update":
            upd.append((time.time() - t0, m["id"], m["tr"]["fr"]))
    hub.emit = emit
    stop = threading.Event()
    threading.Thread(target=lambda: [qmax.__setitem__(0, max(qmax[0], hub.utt.qsize())) or time.sleep(0.02)
                                     for _ in iter(stop.is_set, True)], daemon=True).start()
    for i in range(N):
        hub.utt.put(("audio", np.full(16000, i, np.float32), time.time(), None))
        time.sleep(0.3)
    deadline = time.time() + 40
    while len(out) < N and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(TR_S * 3)  # let the AI catch up
    stop.set()
    order_ok = [s for _, s, _ in out] == [f"sentence {i}" for i in range(N)]
    fast_first = all(t.startswith("(fast)") for _, _, t in out)
    ai_later = len(upd) >= 2 and all(t.startswith("[AI fr]") for _, _, t in upd)
    print(f"published {len(out)}/{N} · in order: {order_ok} · fast translation first: {fast_first} · "
          f"STT queue max {qmax[0]} · last caption at {out[-1][0]:.1f}s" if out else "nothing published")
    print(f"AI refinements: {len(upd)} lines updated, first at {upd[0][0]:.1f}s" if upd else "no AI refinements")
    ok = len(out) == N and order_ok and fast_first and ai_later and qmax[0] <= 1 and out[-1][0] < 2.6
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
