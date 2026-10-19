#!/usr/bin/env python3
"""NDI output on Windows/Linux: the caption overlay page rendered by a hidden Edge/Chrome → NDI with alpha.

    python ndi_cdp.py --url "http://127.0.0.1:8180/overlay?preset=subtitles&lang=fr" --name "LiveTranslate FR"

Same page as the vMix/OBS browser source, so the look is identical and style edits apply live. The browser runs
headless with a transparent background; overlay.html reports every visual change through a DevTools binding
(ltDirty), and only then — and only while someone receives the source — is a frame captured. Edge ships with
every Windows 10/11, so nothing extra is installed there. Exits when its parent (the server) dies.
"""
import argparse, asyncio, base64, io, json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request

import numpy as np

from ndi_lib import Sender, load_ndi


def find_browser():
    if os.environ.get("LT_BROWSER"):
        return os.environ["LT_BROWSER"]
    if sys.platform == "win32":
        bases = [os.environ.get(v, "") for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        cands = [os.path.join(b, *p) for b in bases if b for p in (("Microsoft", "Edge", "Application", "msedge.exe"),
                                                                     ("Google", "Chrome", "Application", "chrome.exe"))]
    elif sys.platform == "darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands = [shutil.which(n) or "" for n in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                                                  "microsoft-edge")]
    return next((c for c in cands if c and os.path.exists(c)), None)


class Renderer:
    def __init__(self, url, w, h, fps, sender):
        self.url, self.w, self.h, self.fps, self.sender = url, w, h, fps, sender
        self.dirty_until = time.time() + 3  # first seconds: page loading
        self.loop = None

    def dirty(self, ms):
        """Page changed; keep capturing while its animation (fade/roll-up) runs, then stop."""
        self.dirty_until = max(self.dirty_until, time.time() + ms / 1000 + 0.1)

    def start_browser(self):
        exe = find_browser()
        if not exe:
            sys.exit("No Edge/Chrome/Chromium found for NDI rendering (Linux: install chromium)")
        self.profile = tempfile.mkdtemp(prefix="LiveTranslate-ndi-")  # name matched by the Windows installer when it stops a running copy
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             f"--window-size={self.w},{self.h}", "--hide-scrollbars", "--mute-audio", "--no-first-run",
             "--no-default-browser-check", "--no-proxy-server", "--disable-background-timer-throttling",
             "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
             "--default-background-color=00000000", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.profile, "browser.log"), "w"))  # a PIPE nobody reads would fill and stall the browser
        port_file, port = os.path.join(self.profile, "DevToolsActivePort"), None
        for _ in range(150):  # the browser writes its DevTools port here once it listens
            try:
                with open(port_file) as f:
                    port = f.read().split()[0]
                break
            except (OSError, IndexError):
                pass
            if self.proc.poll() is not None:
                sys.exit("browser exited: " + open(os.path.join(self.profile, "browser.log")).read()[-300:])
            time.sleep(0.1)
        if not port:
            sys.exit("browser did not start")
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                page = next(p for p in pages if p.get("type") == "page")
                return page["webSocketDebuggerUrl"]
            except Exception:
                time.sleep(0.1)
        sys.exit("no page target in the browser")

    async def run(self):
        import aiohttp
        from PIL import Image
        ws_url = self.start_browser()
        self.loop = asyncio.get_running_loop()
        async with aiohttp.ClientSession() as http, http.ws_connect(ws_url, max_msg_size=0) as ws:
            seq, waiting = [0], {}

            async def call(method, **params):
                seq[0] += 1
                fut = self.loop.create_future()
                waiting[seq[0]] = fut
                await ws.send_str(json.dumps({"id": seq[0], "method": method, "params": params}))
                return await asyncio.wait_for(fut, 10)

            async def reader():
                async for m in ws:
                    d = json.loads(m.data)
                    if "id" in d and d["id"] in waiting:
                        waiting.pop(d["id"]).set_result(d.get("result", {}))
                    elif d.get("method") == "Runtime.bindingCalled" and d["params"]["name"] == "ltDirty":
                        try:
                            self.dirty(float(d["params"]["payload"] or 0))
                        except ValueError:
                            self.dirty(300)
                sys.exit("browser connection lost")

            asyncio.ensure_future(reader())
            await call("Emulation.setDeviceMetricsOverride", width=self.w, height=self.h, deviceScaleFactor=1, mobile=False)
            await call("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
            await call("Runtime.enable")
            await call("Runtime.addBinding", name="ltDirty")
            await call("Page.enable")
            await call("Page.navigate", url=self.url)
            frame_t = 1.0 / self.fps
            while True:
                t = time.time()
                if self.sender.watched() and t <= self.dirty_until:
                    r = await call("Page.captureScreenshot", format="png", fromSurface=True, optimizeForSpeed=True,
                                   clip={"x": 0, "y": 0, "width": self.w, "height": self.h, "scale": 1})
                    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGBA")
                    if img.size != (self.w, self.h):
                        img = img.resize((self.w, self.h))
                    rgba = np.asarray(img)
                    self.sender.update(np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]))  # PNG is straight alpha: RGBA → BGRA
                await asyncio.sleep(max(0.0, frame_t - (time.time() - t)))

    def stop(self):
        try:
            self.proc.terminate()
        except Exception:
            pass
        shutil.rmtree(getattr(self, "profile", ""), ignore_errors=True)


def watch_parent(renderer):
    parent = os.getppid()  # the server that started this sender
    def alive():
        if sys.platform == "win32":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, parent)
            if not h:
                return False
            code = ctypes.c_ulong(); k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
            return code.value == 259
        return os.getppid() == parent
    while alive():
        time.sleep(1)
    renderer.stop()
    os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", default="LiveTranslate")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    lib, path = load_ndi()
    sender = Sender(lib, a.name, w, h, a.fps)
    sender.start()
    r = sender.renderer = Renderer(a.url, w, h, a.fps, sender)
    threading.Thread(target=watch_parent, args=(r,), daemon=True).start()
    print(f"[ndi] '{a.name}' {w}x{h}@{a.fps} from {a.url} (runtime {path}, renderer {find_browser()})", flush=True)
    try:
        asyncio.run(r.run())
    finally:
        r.stop()


if __name__ == "__main__":
    main()
