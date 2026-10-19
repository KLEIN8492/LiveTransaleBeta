#!/usr/bin/env python3
"""Which local AI model / runtime gives the best translation per second of waiting?

For each model: (1) accuracy with event context (tests/eval_context.py cases, FR+ES), (2) what a live line costs:
French AND Spanish requested at the same time, after a few earlier lines (real prompt with history), median of
8 lines. Point --ollama at a differently configured server to compare runtimes (e.g. NUM_PARALLEL=2).

    .venv/bin/python tests/bench_models.py --models gemma3:4b,gemma3:1b [--ollama http://127.0.0.1:11440]
    LT_NO_MLX=1 … forces Ollama on a Mac (compare runtimes)
"""
import argparse, os, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server as S  # noqa: E402
import eval_context as E  # noqa: E402

CTX = ("Annual energy-sector conference. Keynotes and a panel on battery storage. Audience: engineers and investors. "
       "Speakers: Daniel Klein (CEO, Nyxtera), Aoife Ní Dhuibhir.")
LINES = ["Good evening and welcome to the energy summit.",
         "Our first speaker has worked on grid-scale battery storage for ten years.",
         "She will talk about how storage changes the economics of renewables.",
         "After the break we will have a panel with three guests.",
         "Please take your seats, we are about to begin with the keynote.",
         "The question everyone asks is how cheap batteries can really get.",
         "Last year prices fell by almost twenty percent.",
         "That changes everything for solar farms in the south of Europe.",
         "Daniel, the stage is yours.",
         "Thank you, it is a real pleasure to be here tonight.",
         "Let me start with a short story about a wind farm in Ireland.",
         "We had more power than the grid could take, so we built storage."]


def line_cost(model):
    s = {**S.DEFAULT, "source": "en", "targets": ["fr", "es"], "engine": "ollama:" + model, "context": CTX,
         "names": "Daniel Klein\nAoife Ní Dhuibhir\nNyxtera"}
    tr = S.Translator(s)  # same routing as live: MLX on a Mac when its weights are installed, else Ollama
    tr.warm()
    call = lambda text, l: tr._mlx(text, "en", l, timeout=60) if tr.mlx else tr._ollama(model, text, "en", l, timeout=60)
    walls, sample = [], ""
    with ThreadPoolExecutor(2) as ex:
        for i, text in enumerate(LINES):
            t = time.time()
            fr, es = ex.map(lambda l: call(text, l), ("fr", "es"))
            if i >= 4:  # first lines build the history; measure with a realistic prompt
                walls.append(time.time() - t)
            tr.remember(text, "en", {"fr": fr, "es": es})
            sample = sample or (text, fr)
    return statistics.median(walls), max(walls), sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--ollama", default=S.OLLAMA)
    ap.add_argument("--no-accuracy", action="store_true")
    a = ap.parse_args()
    S.OLLAMA = a.ollama
    S.log = lambda *x: None
    print(f"runtime {a.ollama}")
    for m in a.models.split(","):
        med, worst, (src, fr) = line_cost(m)
        acc = ""
        if not a.no_accuracy:
            sc, n, _ = E.run("ollama:" + m, True, False)
            acc = f"{sc:2}/{n} correct with context · "
        print(f"{m + (' [MLX]' if S.mlx_repo(m) else ' [Ollama]'):34} {acc}line (FR+ES) median {med:.2f}s worst {worst:.2f}s   e.g. «{fr[:60]}»", flush=True)


if __name__ == "__main__":
    main()
