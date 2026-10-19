#!/usr/bin/env python3
"""Echo guard: the input hearing our own output (Listen out loud near the mic) must not come back as new lines.
Cases are real ones from a session on 2026-10-10 (MacBook mic + a phone playing Listen).

    .venv/bin/python tests/test_echo.py
"""
import os, sys, tempfile, time
os.environ["LT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server

hub = server.Hub(demo=False)
ok = True
def line(src, en, age):
    hub.history.append({"id": len(hub.history) + 1, "src": src, "lang": "fr", "tr": {"en": en}, "t": time.time() - age})
def check(name, src, want):
    global ok
    got = hub.is_echo(src)
    ok &= got == want
    print(f"{'PASS' if got == want else 'FAIL'} {name}: {src!r} -> {'echo' if got else 'kept'}")

line("1, 2, 2, est-ce que ça va bien ?", "1, 2, 2, are you okay?", 6)
check("English voice heard back verbatim", "1, 2, 2, are you okay?", True)
line("Je trouve que c'est une super idée.", "I think it's a great idea.", 12)
check("voice heard back, re-worded into French", "Je trouve que c'est une très bonne idée.", True)
line("Bon c'est juste un petit test, pas très important mais on teste quoi.", "Well, it's just a little test, not very important, but what are we testing?", 8)
check("long line re-worded", "C'est juste un petit test, pas très important. Je vais faire un test.", True)
check("new sentence", "Voilà, je continue de parler et de dire bonjour.", False)
check("short reply said twice", "Oui, oui.", False)
hub.history.clear()
line("Je trouve que c'est une super idée.", "I think it's a great idea.", 40)
check("same words 40 s later (a real repeat)", "Je trouve que c'est une super idée.", False)
line("Nous passons à la table 12.", "We now move to table 12.", 8)
check("different number = different sentence", "Nous passons à la table 13.", False)
line("Bienvenue à toutes et à tous ce soir.", "Welcome, everyone, tonight.", 1)
check("too soon to be an echo (1 s)", "Welcome, everyone, tonight.", False)
print("echo notice:", repr(hub.echo_note[:60]))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
