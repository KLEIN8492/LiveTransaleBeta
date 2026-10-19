#!/usr/bin/env python3
"""NDI output: renders the caption overlay page off-screen and sends it as an NDI video source with alpha.

    python ndi_out.py --url "http://127.0.0.1:8180/overlay?preset=subtitles&lang=fr" --name "LiveTranslate FR"

Same page as the vMix/OBS browser source (one renderer, identical look, presets update live). WebKit snapshots
→ BGRA → NDI via the installed NDI runtime (ndi_lib.py, ctypes — no SDK needed). Windows/Linux: ndi_cdp.py. Started and stopped
by server.py, one process per NDI source; exits when its parent dies.
"""
import argparse, os, sys, time

import numpy as np
import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyProhibited, NSBackingStoreBuffered, NSMakeRect,
                    NSWindow, NSWindowStyleMaskBorderless, NSTimer)
from Foundation import NSURL, NSURLRequest
import Quartz
import WebKit

from ndi_lib import Sender, load_ndi


# ----------------------------------------------------------------------------------------- renderer
class DirtyHandler(objc.lookUpClass("NSObject"), protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    """Receives window.webkit.messageHandlers.lt.postMessage(ms) from overlay.html on every visual change."""

    def userContentController_didReceiveScriptMessage_(self, ucc, msg):
        self.renderer.dirty(float(msg.body() or 0))


class Renderer:
    """Off-screen WKWebView → BGRA frames. Snapshots only while someone is receiving the NDI source."""

    def __init__(self, url, w, h, fps, sender):
        self.w, self.h, self.fps, self.sender = w, h, fps, sender
        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        self.handler = DirtyHandler.alloc().init()
        self.handler.renderer = self
        cfg.userContentController().addScriptMessageHandler_name_(self.handler, "lt")
        prefs = cfg.preferences()
        try:  # macOS 14+: keep rendering/animating although the window is never on screen
            prefs.setInactiveSchedulingPolicy_(2)  # WKInactiveSchedulingPolicyNone
        except Exception:
            pass
        self.view = WebKit.WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, w, h), cfg)
        self.view.setValue_forKey_(False, "drawsBackground")  # transparent page -> real alpha
        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(-20000, -20000, w, h), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.win.setOpaque_(False)
        self.win.setContentView_(self.view)
        self.win.orderBack_(None)
        self.view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
        self.busy, self.dirty_until = False, time.time() + 3  # first seconds: page loading
        self.cs = Quartz.CGColorSpaceCreateDeviceRGB()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / fps, True, lambda t: self.tick())

    def dirty(self, ms):
        """Page changed; keep snapshotting while its animation (fade/roll-up) runs, then stop."""
        self.dirty_until = max(self.dirty_until, time.time() + ms / 1000 + 0.1)

    def tick(self):
        # snapshot only while the picture changes; between changes the sender re-sends the last frame
        if self.busy or not self.sender.watched() or time.time() > self.dirty_until:
            return
        self.busy = True
        conf = WebKit.WKSnapshotConfiguration.alloc().init()
        conf.setRect_(NSMakeRect(0, 0, self.w, self.h))
        conf.setSnapshotWidth_(self.w / max(1.0, self.win.backingScaleFactor()))  # points -> exactly w pixels
        self.view.takeSnapshotWithConfiguration_completionHandler_(conf, self.got)

    def got(self, image, error):
        try:
            if image is None:
                return
            cg = image.CGImageForProposedRect_context_hints_(None, None, None)[0]
            buf = bytearray(self.w * self.h * 4)
            ctx = Quartz.CGBitmapContextCreate(buf, self.w, self.h, 8, self.w * 4, self.cs,
                                               Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little)
            Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, self.w, self.h), cg)
            px = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 4)
            # CoreGraphics gives premultiplied alpha; NDI BGRA is straight alpha -> un-premultiply edges
            a = px[..., 3:4].astype(np.float32)
            rgb = np.where(a > 0, np.minimum(px[..., :3] * (255.0 / np.maximum(a, 1)), 255), 0)
            out = np.empty_like(px)
            out[..., :3] = rgb.astype(np.uint8)
            out[..., 3] = px[..., 3]
            self.sender.update(np.ascontiguousarray(out))
        finally:
            self.busy = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", default="LiveTranslate")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    lib, path = load_ndi()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)  # no Dock icon, no menu bar
    sender = Sender(lib, a.name, w, h, a.fps)
    sender.start()
    sender.renderer = Renderer(a.url, w, h, a.fps, sender)
    parent = os.getppid()
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, True, lambda t: os.getppid() != parent and os._exit(0))
    print(f"[ndi] '{a.name}' {w}x{h}@{a.fps} from {a.url} (runtime {path})", flush=True)
    app.run()


if __name__ == "__main__":
    main()
