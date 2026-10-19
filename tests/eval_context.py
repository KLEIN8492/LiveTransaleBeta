#!/usr/bin/env python3
"""Does the session context make translations more faithful? Measures it instead of assuming it.

    .venv/bin/python tests/eval_context.py [--engines argos,ollama:gemma3:4b] [--show]

Each case is a sentence that is ambiguous WITHOUT context. A translation passes when it contains one of the
`want` words and none of the `avoid` words (case/accent-insensitive). Every engine runs twice: with the
context/names/glossary of the case, and with them blank — the difference is what context buys you.
"""
import argparse, os, sys, time, unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server as S  # noqa: E402

CASES = [
    dict(ctx="Catholic Sunday mass. The priest is speaking during the liturgy.",
         src="Please rise for the host.",
         want={"fr": ["hostie"], "es": ["hostia", "sagrada forma"]}, avoid={"fr": ["hôte", "hote"], "es": ["anfitri"]}),
    dict(ctx="Protestant church Sunday worship.",
         src="The service starts again after the break.",
         want={"fr": ["culte"], "es": ["culto"]}, avoid={"fr": ["le service"], "es": ["el servicio"]}),
    dict(ctx="Rowing regatta on the river Seine, commentary from the riverside.",
         src="The crews are waiting near the bank.",
         want={"fr": ["rive", "berge", "quai", "bord"], "es": ["orilla", "ribera"]},
         avoid={"fr": ["banque"], "es": ["banco"]}),
    dict(ctx="Rock concert, sound check with the band on stage.",
         src="Can I get more vocals in my monitor?",
         want={"fr": ["retour"], "es": ["monitor", "retorno"]}, avoid={"fr": ["moniteur", "écran"], "es": ["pantalla"]}),
    dict(ctx="Fashion week runway show.",
         src="The models are coming out now.",
         want={"fr": ["mannequin"], "es": ["modelo"]}, avoid={"fr": ["modèles", "modeles"], "es": []}),
    dict(ctx="Football match commentary, Premier League.",
         src="He has been booked for that tackle.",
         want={"fr": ["averti", "carton jaune"], "es": ["amonestado", "tarjeta amarilla", "amonestaci"]},
         avoid={"fr": ["réserv", "reserv"], "es": ["reservad"]}),
    dict(ctx="Conference on energy. Speaker: Pastor-turned-engineer Daniel Klein.", names="Daniel Klein",
         src="Thank you, Daniel Klein, for your talk.",
         want={"fr": ["daniel klein"], "es": ["daniel klein"]}, avoid={"fr": ["petit"], "es": ["pequeño"]}),
    dict(ctx="Business summit.", glossary=[{"src": "keynote speaker", "fr": "invité d'honneur", "es": "invitado de honor"}],
         src="Our keynote speaker arrives in five minutes.",
         want={"fr": ["invité d'honneur", "invité d’honneur"], "es": ["invitado de honor"]},
         avoid={"fr": ["conférencier"], "es": ["orador"]}),
]
LANGS = ["fr", "es"]


def norm(t):
    return unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode().replace("’", "'")


def judge(out, case, lang):
    o = norm(out)
    ok = any(norm(w) in o for w in case["want"][lang]) and not any(norm(w) in o for w in case["avoid"].get(lang, []))
    return ok


def run(engine, with_ctx, show):
    score, n, times = 0, 0, []
    for c in CASES:
        s = {**S.DEFAULT, "source": "en", "targets": LANGS, "engine": engine,
             "context": c["ctx"] if with_ctx else "", "names": c.get("names", "") if with_ctx else "",
             "glossary": c.get("glossary", []) if with_ctx else []}
        tr = S.Translator(s)
        for l in LANGS:
            t = time.time(); out = tr(c["src"], l, "en"); times.append(time.time() - t)
            ok = judge(out, c, l); score += ok; n += 1
            if show:
                print(f"    {'✓' if ok else '✗'} {l}  {out}")
    return score, n, sorted(times)[len(times) // 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="argos,ollama:gemma3:4b")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    for e in a.engines.split(","):
        if e.startswith("ollama:"):
            S.Translator({**S.DEFAULT, "engine": e, "targets": LANGS}).warm()
        for ctx in (False, True):
            if a.show:
                print(f"  {e} · {'WITH' if ctx else 'without'} context")
            sc, n, med = run(e, ctx, a.show)
            print(f"{e:22} {'with context   ' if ctx else 'without context'}  {sc:2}/{n} correct  · median {med * 1000:.0f} ms/translation")


if __name__ == "__main__":
    main()
