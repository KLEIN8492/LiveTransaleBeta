#!/usr/bin/env python3
"""Setup says one language, speakers use another → Control must say so (instead of failing silently).

    .venv/bin/python tests/test_language_notice.py

Real detection model, synthetic speech, no audio devices: 3 English sentences into a session set to French must
raise the notice; switching the session to French speech (or Auto) must clear it.
"""
import os, sys
import numpy as np
from scipy.signal import resample_poly
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bench_live as B
import server as S


def clips(voice, lang, texts):
    audio, marks = B.build(1, [(voice, lang, t) for t in texts])
    a = resample_poly(audio, 1, 3).astype(np.float32)
    return [a[int(max(0, e - 3) * 16000):int(e * 16000)] for _, _, e in marks]


def main():
    hub = S.Hub()
    hub.s = {**S.DEFAULT, "source": "fr", "targets": ["en", "es"]}
    hub.stt = S.STT(hub.s)
    hub.push_state = lambda: None
    for c in clips("Samantha", "en", B.SENTENCES[:3]):
        hub.check_language(c)
    raised = hub.notice
    print("after 3 English sentences:", raised or "(no notice)")
    for c in clips("Thomas", "fr", [t for _, l, t in B.MULTI if l == "fr"] * 2)[:3]:
        hub.check_language(c)
    print("after French speech:", repr(hub.notice))
    ok = "English" in raised and "French" in raised and hub.notice == ""
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
