#!/usr/bin/env python3
"""Stand-in for the vMix Web API (port 8088): logs every SetText call as JSON lines.
    .venv/bin/python tests/mock_vmix.py [--port 8088] [--delay 0.05] > calls.jsonl"""
import argparse, json, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8088); ap.add_argument("--delay", type=float, default=0.05)
a = ap.parse_args()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like vMix

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        time.sleep(a.delay)  # vMix answers in tens of ms
        print(json.dumps({"t": time.time(), **q}, ensure_ascii=False), flush=True)
        body = b"Function completed successfully."
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *x):
        pass


ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
