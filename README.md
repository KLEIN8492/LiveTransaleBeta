# LiveTranslate — live speech translation & captions (offline · macOS, Windows, Linux)

Audio in → VAD → Whisper (MLX) → Argos or local LLM (Ollama) → Control, vMix/OBS overlay, audience phones, SRT.

## Installing on a machine (hand-out installer)
Build: `bash macapp/make_app.sh` → `dist/LiveTranslate Installer <date>.pkg` (≈20 MB; also a .dmg).

On the target Mac (Apple Silicon, macOS 13+):
1. Double-click the installer → LiveTranslate goes to /Applications and opens by itself.
2. A **setup page** opens in the browser: it checks the Mac (chip, memory, disk, NDI), you pick **languages** and
   **local AI** (Offline only · Balanced · Most accurate · Maximum — see below; each shows whether *this* machine runs it), and
   whether it **opens at login**. Install downloads 2–12 GB once, shows progress, and ends with an **offline self-test**.
3. Done: LiveTranslate runs the server **locally** from the menu bar (LT). Control: `http://<that-mac>:8180/control`.
   No internet needed any more. Menu bar → "Add languages or AI models…" re-runs setup (only fetches what's missing).

Everything lives in `~/Library/Application Support/LiveTranslate` (own Python, speech models in hf/, translation packages in xdg/data/argos-translate/,
private AI runtime on port 11439 + models, sessions, presets). **Uninstall:** quit, delete the app and that folder
(and `~/Library/LaunchAgents/local.tools.livetranslate.plist` if "open at login" was on). Logs:
`~/Library/Logs/LiveTranslate.log` (+ `LiveTranslate-AI.log`).

**Unsigned build:** without an Apple Developer ID, a downloaded installer is blocked the first time — System Settings →
Privacy & Security → "Open Anyway". Copying it from a USB drive avoids this. To sign and notarize (no warning at all):
`DEV_ID_APP="Developer ID Application: …" DEV_ID_INSTALLER="Developer ID Installer: …" NOTARY_PROFILE=… bash macapp/make_app.sh`.

## Local AI tiers — and what each machine can run (`models.py`)
| Tier | Model | Accuracy* | Download (Mac / PC) | Runs well on |
|---|---|---|---|---|
| Balanced | gemma3:4b | 11/16 | 3.0 / 3.4 GB | 16 GB Mac (tight: close other apps), 6 GB+ graphics card |
| Most accurate | translategemma:12b | 13/16 | 6.7 / 8.1 GB | 24 GB+ Mac, 10 GB+ graphics card |
| Maximum | translategemma:27b | not yet measured | 15.2 / 17.4 GB | 32 GB+ Mac with Max/Ultra chip, 24 GB graphics card |

\*`tests/eval_context.py` with the event description, 16 tricky sentences. Setup and Control both show, per tier,
**runs well / slower / tight on memory / too big for this machine**, and mark the strongest one that runs well. The rule
(`models.fit`): memory = model + 13 GB for the rest of the show on a Mac (Whisper, macOS, Dante, browser), or the
model + 1.5 GB on the graphics card on a PC; speed ≈ 54 × model GB ÷ memory bandwidth (calibrated on an M2 Pro:
gemma3:4b 0.81 s per line), "slow" above 1.5 s, too slow above 3 s. Check any machine: `python3 models.py`.

## Windows (testers) and Linux (dedicated box)
Same app, same pages; the speech engine is **faster-whisper** instead of MLX. Build both on this Mac:
`bash winapp/make_win.sh` (needs `brew install makensis`) → `dist/LiveTranslate Setup <date>.exe` ·
`bash linux/make_linux.sh` → `dist/LiveTranslate-linux-x86_64-<date>.tar.gz`.
- **Windows 10/11 64-bit:** run the Setup .exe (no admin rights) → Start menu/desktop "LiveTranslate" → the same setup
  page. Lives in the **tray** (bottom-right, maybe under ^); Control is the same URL. Unsigned: SmartScreen shows
  "Windows protected your PC" → More info → Run anyway. When Windows asks whether *pythonw* may use the network, allow
  **Private networks** (phones, vMix, OBS connect over the LAN). Uninstall: Settings → Apps → LiveTranslate (asks
  whether to keep the downloaded models in `%LOCALAPPDATA%\LiveTranslate`). Logs: `%LOCALAPPDATA%\LiveTranslate\logs`.
- **Linux (Ubuntu 22.04/24.04, x86_64):** `tar xzf …; cd LiveTranslate; ./install.sh` (needs `sudo apt install
  libportaudio2`, + `chromium-browser` for NDI). No screen: `livetranslate --setup --lan` (setup page on :8090 from
  another computer), then `livetranslate --service` (systemd user service, starts at boot, restarts on failure).
- **Speech:** with an **NVIDIA card** (driver installed; RTX 3060 12 GB or better) setup adds CUDA and uses Whisper turbo
  + base like the Mac. Without one it runs on the CPU with Whisper small (~2 s per sentence on a fast 8-core; Control
  says so). Audio inputs on Windows are listed once (WASAPI, full channel count, Windows resamples).
- **NDI** renders the overlay in a hidden **Edge** (built into Windows) / Chromium → NDI runtime (NDI Tools / NDI SDK).
- Test the Windows engine on a Mac: `LT_STT=ct2 .venv/bin/python server.py` (needs `faster-whisper` in the venv).

## Natural voice (audience "Listen")
Phones that tap Listen hear a neural voice (Kokoro, offline, ~0.4 s per sentence on the CPU, ~15 KB MP3 per sentence)
for **English, French, Spanish, Italian, Portuguese (Brazilian)**; other languages fall back to the phone's own voice.
Each sentence is synthesised once per language and shared by every phone; the voice waits up to 2 s for the AI's
refined wording, skips ahead if it falls behind, and leaves bleeped words out. Female/male: Control → Access →
Audience → Listening voice. Installed by setup ("Natural voice for listeners", 120 MB, `models/kokoro`).

## Dev (terminal)
    bash setup.sh en,fr,es           # once, with internet
    bash run.sh                      # prints the Control URL   (--demo, --autostart, --password …, --port)
Optional better translations: `brew install ollama && ollama pull gemma3:4b` (12b if you have the disk/RAM),
then pick "Local AI" as engine. Falls back to Argos automatically if the LLM is slow (>6 s) or down.

## Control (any device on the LAN, PIN-protected; localhost needs none)
Run it on a dedicated machine and operate from a laptop or iPad at the desk.
- **Setup** — the signal-flow strip (input → speech → audience → quality → outputs) shows the whole session and turns
  into a red tally when live. Below it: **event type** (conference, worship, show, sport, multilingual panel, custom —
  sets timing defaults), event description and names, languages, **audio input with a level check before going
  live**, **translation quality** (Fastest = Argos · Balanced / Most accurate = the smallest / largest local AI model
  installed), and **Fine-tuning** (timing, glossary, exact models, profanity, vMix title push, audience Wi-Fi).
  "Apply changes" updates a live session without stopping it. Saved setups reload everything.
- **Live** — every language side by side, original tagged, remove lines, type lines, pause (space bar), transcript and
  subtitle files per language.
- **Captions & outputs** — caption looks with live preview, NDI outputs, browser-overlay address.
- **Audience** — QR for phones and a printable card.

## Admin access
Control and its API are for admins. **The machine running LiveTranslate opens Control without a password**; every other
device (laptop, iPad, phone) must sign in with the **admin password** (set during setup, or in Control → Access →
Admins; at least 8 characters). Sessions last 30 days per browser. Changing the password or "Sign out all other
devices" ends every other session; changing it from another device requires the current password. Five wrong attempts
lock that device for 30 s, doubling up to 15 min. The password is stored only as a salted PBKDF2 hash (config.json,
readable by the owner only). Until a password exists, Control is reachable on the machine itself only. The audience page
(`/`), the overlay (`/overlay`) and the QR card stay public — screens and phones need them. Test: `tests/test_auth.py`.

## Multilingual events (several spoken languages)
Session → Spoken language → **Auto**, then tick the languages people may speak *and* read (e.g. EN · FR · ES).
Every sentence is detected as one of them (restricted to that list, so short lines aren't mislabelled) and translated
into all the others: someone who picked French sees French speakers' original words and translations of everyone else.
Provisional captions only switch language at ≥80 % confidence, so a name or an "OK" can't flip the room.
Glossary in Auto mode has one column per language. Needs Argos packages for each pair (`bash setup.sh en,fr,es`;
pairs without a direct package, e.g. fr→es, pivot through English automatically).
Test: `.venv/bin/python tests/bench_live.py --multi`.

## Live-use behaviour
- Device chosen by **name**, so Dante/aggregate devices appearing don't shift it. Unplugged interface → header shows
  the error, capture retries every 2 s and resumes on its own.
- Provisional captions every 0.6 s while someone talks (tiny Whisper + Argos, never the LLM); replaced by the final line.
- Long sentences are split at the quietest point of the last second (never mid-word).
- Silence gate (default −60 dBFS) in front of the voice detector: gated/muted console channels send digital
  silence, which made the detector drift after ~4 min and merge sentences. Raise it in a noisy room.
- **Echo guard:** a "new" sentence that matches a line published 3–20 s earlier (any language) is dropped — that is
  the input hearing LiveTranslate's own output (Listen played out loud near a room mic, translation on the PA), which
  otherwise comes back 5–12 s later, re-worded, and loops. Control warns after two. Test: `tests/test_echo.py`.
- Latency shown per line = end of speech → captions out. Backlog warnings go to the log.

## vMix (subtitles on the programme)
**Option A — browser overlay (recommended: smooth roll-up, styling, provisional text).**
1. Mac and vMix PC on the same wired LAN; give the Mac a fixed IP.
2. Control → Captions/vMix → pick a preset: `subtitles` (white, outline + shadow, 2 lines), `subtitles-box`
   (broadcast black box per line) or `lower-third-2lang` (translation + original stacked). Copy the URL.
3. vMix: Add Input → **Web Browser** → paste `http://<mac-ip>:8180/overlay?preset=subtitles&lang=fr`,
   resolution **1920×1080**. Put the input on an **Overlay** channel (1–4) over the programme; background stays transparent.
4. One input per language: same URL with `&lang=es`, etc. Any style key can be forced in the URL (`&size=52&pos=top`).
5. Style changes in Control update vMix live — no need to touch the input. Start/stop the session freely: the input
   reconnects by itself (also after a Mac/server restart).
Tips: Roll-up slide 150–250 ms looks like broadcast captions; 0 = hard cut. Mode "Pop-on" = one balanced sentence
at a time (film style). Auto-hide clears the screen after N s of silence.

**Option B — title push** (text into a GT title's fields via the vMix Web API, e.g. to use your own title design).
Session → vMix title push: host `<vmix-ip>:8088` (vMix Settings → Web Controller enabled), title input name/number,
field per language (e.g. `Headline.Text`). Provisional + final text are pushed, latest-wins (never out of order).
Test without vMix: `.venv/bin/python tests/mock_vmix.py` and host `127.0.0.1:8088`.

## NDI output
Control → Captions/vMix → **NDI outputs** → + Add (name, look, language) → Save & apply. Each output is a 1920×1080
NDI source **with alpha**, rendered from the same overlay page (identical look, style edits apply live), started
with the app and restarted automatically if it ever stops. Needs the NDI runtime (NDI Tools, ndi.video/tools).
~30 % of one core while someone receives it, ~2 % when nobody does. Check from the command line:
`.venv/bin/python tests/ndi_probe.py --name "LiveTranslate FR" --png frame.png`.
- **vMix:** Add Input → NDI → pick "<Mac name> (LiveTranslate FR)" → overlay channel.
- **OBS:** Sources → + → **NDI Source** (DistroAV plugin) → pick the name. Or without NDI: Sources → Browser →
  `http://<mac-ip>:8180/overlay?preset=subtitles&lang=fr`, 1920×1080 (after an update: Properties → Refresh cache).

## Context, names, glossary — what each one really does (measured: `tests/eval_context.py`)
16 sentences that are wrong without context (host → hostie at a mass, bank → rive at a regatta, monitor → retour…):
| engine | no context | with context | per translation |
|---|---|---|---|
| Argos | 4/16 | 4/16 — **Argos cannot use context**; glossary only | 0.1 s |
| gemma3:4b | 4/16 | 10/16 | ~0.6 s |
| gemma3:12b | 7/16 | 12/16 | ~2.5 s (too slow for live subtitles with 2+ languages) |
| gemma4:e2b | 4/16 | 8/16 | ~0.5 s, but 7.5 GB in memory |
| gemma4:e4b | 6/16 | 10/16 | ~0.9 s, 9.9 GB in memory — live lines up to 5.9 s on a 16 GB Mac |
| translategemma:4b | 4/16 | 9/16 | ~0.8 s, 2.8 GB (ties gemma3:4b) |
| **translategemma:12b** | 7/16 | **13/16** | ~2.3 s, 7.8 GB — "Most accurate" since 2026-10-11; needs a 32 GB machine |
- **Context** → used by the Local AI engine to pick the right meaning and jargon, and by speech recognition.
- **Names** → fixes recognition of hard names for every engine (tested: "Nidouid here / Nickstera" → "Ní Dhuibhir / Nyxtera").
- **Glossary** → type a term in any language, Tab: the other languages are suggested (local AI + event description, else Argos), shown dimmed until checked — a wrong glossary term is enforced on every line. Enforced by every engine (Argos best effort). Only terms with ONE meaning at the event; write the
  article for gendered languages (`la hostia`) so grammar stays right. Auto-suggesting terms was tried and dropped:
  even the 12B model proposed wrong jargon (monitor → moniteur), which a glossary would then enforce.

## Latency (M2 Pro, measured end-of-speech → on screen, `tests/bench_live.py`)
| engine | first text on screen | final line | 7-min soak (120 lines) |
|---|---|---|---|
| Argos (offline) | 0.5–0.9 s | 0.9–1.2 s | 0 missed, 0 merged, no drift |
| Argos, Auto (EN/FR/ES) | ~0.8 s | ~1.9 s | 6/6 languages detected |
| Local AI gemma3:4b (Ollama) | ~0.5 s (Argos provisional) | ~2.6 s | — |
| **Local AI gemma3:4b (MLX, 2026-10-11)** | ~1.1 s | ~1.7 s, AI wording **+0.3–0.7 s** | — |

**Local AI speed (2026-10-11, `tests/bench_models.py`, FR+ES per line, M2 Pro 16 GB):** gemma3:4b took 2.1 s per
line on Ollama: llama.cpp cannot reuse a cached prompt for Gemma 3 (sliding-window attention), and the sliding
history changed the prompt every line, so the event description was re-read for every line and language. Fixes:
the history now grows and resets (prompt start stays identical) → 1.13 s on Ollama; on a Mac the same model runs
on **MLX** (`mlx_srv.py`, one prompt cache per language, both languages decoded together) → **0.81 s**, 11/16 on
the context test. Smaller/faster models were all less accurate: llama3.2:3b 8/16 (broken French), granite4.1:3b
7/16, HY-MT 1.5 1.8B 7/16 (translates the context into the subtitle), gemma3:1b 5/16, Gemma 4 E2B 8/16 (7.5 GB).
Speculative decoding (1B draft) was slower — it disables batching. `LT_NO_MLX=1` forces Ollama.

The floor on an M2 Pro is Whisper turbo itself (~0.75 s per sentence); a faster Mac lowers every number.
Provisional captions run on a tiny Whisper model every 0.6 s; at 150 ms of silence the final is transcribed (and,
with the LLM, translated) speculatively, so the 600 ms end-of-sentence wait overlaps with the work.

## Reliability (built for live shows)
- Server crash → menu-bar app restarts it and **resumes the live session** (~7 s); overlays/phones reconnect alone.
- Audio interface unplugged → header/menu bar show it, capture retries every 2 s and resumes.
- Ollama down/hung → circuit breaker: Argos for 20 s, then retries (no 6 s stalls per line).
- Dead capture/worker thread → supervisor restarts it within 2 s.
- Each phone/overlay has its own send queue: a phone on bad Wi-Fi can't delay the vMix overlay.
- During a session the Mac won't idle/display-sleep and App Nap is off. Log rotates at 10 MB.
- `tests/bench_live.py --loops 30` = 11-minute soak test (misses, worst latency, memory).

## Network
Port **8180** (8000 is Bitfocus Companion's). Change it in Control → Access → Network (restarts in a few seconds,
a live session carries on; vMix/OBS inputs and QR cards then need the new address). If another program already holds
the port at launch, LiveTranslate takes the next free one and says so in Control. `LT_PORT` / `--port` override it.
Mac and vMix PC on the same LAN; give the Mac a fixed IP. For audience phones use a dedicated Wi-Fi router.

## Testing without a mic
Pick **BlackHole 16ch** as input, then play speech into it, e.g.
`say -o /tmp/t.wav --data-format=LEI16@48000 "…"` and play the file to BlackHole ch 1.
