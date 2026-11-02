#!/usr/bin/env python3
"""Admin access: this machine is trusted, every other device needs the admin password.

    .venv/bin/python tests/test_auth.py [--port 8766]

'Another device' = requests to this Mac's LAN address (not 127.0.0.1). Changes the server's admin password —
run it against a dev server, not a production one.
"""
import argparse, asyncio, json, socket, sys, time, urllib.error, urllib.request
import aiohttp

ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8766); a = ap.parse_args()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); LAN = s.getsockname()[0]; s.close()
LOCAL, REMOTE = f"http://127.0.0.1:{a.port}", f"http://{LAN}:{a.port}"
results = []


def check(name, ok):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *x):
        return None


def req(url, data=None, cookie=None, form=False, method=None, headers=None):
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})}
    if cookie:
        h["Cookie"] = cookie
    body = (data.encode() if form else json.dumps(data).encode()) if data is not None else None
    r = urllib.request.Request(url, body, h, method=method)
    try:
        resp = urllib.request.build_opener(NoRedirect).open(r, timeout=10)
    except urllib.error.HTTPError as e:
        resp = e
    c = resp.headers.get("Set-Cookie", "")
    return resp.status, resp.read().decode(errors="replace"), (c.split(";")[0] if c else None), resp.headers.get("Location")


async def ws_kind(cookie, base=None, origin=None):
    async with aiohttp.ClientSession(headers={"Cookie": cookie} if cookie else {}) as cs:
        async with cs.ws_connect((base or REMOTE).replace("http", "ws") + "/ws?kind=op", origin=origin) as ws:
            m = await ws.receive(timeout=5)
            return json.loads(m.data).get("type") if m.type == aiohttp.WSMsgType.TEXT else None


PW, PW2 = "test-password-1", "test-password-2"
check("this Mac opens Control without a password", req(LOCAL + "/control")[0] == 200)
st, _, _, loc = req(REMOTE + "/control")
check("another device is sent to sign in", st == 302 and loc == "/login")
check("another device gets 401 from the API", req(REMOTE + "/api/state")[0] == 401)
# --- another website open in the operator's browser (Control is passwordless on this machine)
EVIL = {"Origin": "http://evil.example"}
check("cross-site page can't set the first password", req(LOCAL + "/api/password", {"new": PW}, headers=EVIL)[0] == 403)
check("cross-site page can't start a session", req(LOCAL + "/api/stop", {}, headers=EVIL)[0] == 403)
check("Sec-Fetch-Site: cross-site is refused", req(LOCAL + "/api/stop", {}, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403)
check("Origin: null (sandboxed frame) is refused", req(LOCAL + "/api/stop", {}, headers={"Origin": "null"})[0] == 403)
check("Control's own page (same origin) is accepted",
      req(LOCAL + "/api/stop", {}, headers={"Origin": LOCAL, "Sec-Fetch-Site": "same-origin"})[0] == 200)
check("DNS rebinding (foreign Host name) is refused, even for reads",
      req(LOCAL + "/api/state", headers={"Host": f"evil.example:{a.port}"})[0] == 421)
try:
    asyncio.run(ws_kind(None, LOCAL, origin="http://evil.example"))
    ws_refused = False
except aiohttp.WSServerHandshakeError as e:
    ws_refused = e.status == 403
check("cross-site page can't open the operator WebSocket", ws_refused)
check("this Mac's own Control page gets the operator WebSocket", asyncio.run(ws_kind(None, LOCAL, origin=LOCAL)) == "state")
# --- bootstrap: the FIRST password is set freely from this machine (a fresh install); after that it takes the current one
check("set password from this Mac", json.loads(req(LOCAL + "/api/password", {"new": PW})[1])["ok"])
check("replacing it from this Mac without the current password is refused",
      not json.loads(req(LOCAL + "/api/password", {"new": PW2})[1])["ok"])
check("replacing it from this Mac with the current password works",
      json.loads(req(LOCAL + "/api/password", {"current": PW, "new": PW})[1])["ok"])
check("too-short password refused", not json.loads(req(LOCAL + "/api/password", {"new": "short"})[1])["ok"])
st, _, cookie, loc = req(REMOTE + "/login", f"password={PW}", form=True)
check("correct password signs in", st == 302 and loc == "/control" and cookie and cookie.startswith("lt_admin="))
check("signed-in device can use Control", req(REMOTE + "/control", cookie=cookie)[0] == 200 and req(REMOTE + "/api/state", cookie=cookie)[0] == 200)
check("signed-in device gets the live connection", asyncio.run(ws_kind(cookie)) == "state")
check("signed-out device is refused the live connection", asyncio.run(ws_kind(None)) == "auth")
r = json.loads(req(REMOTE + "/api/password", {"new": PW2}, cookie=cookie)[1])
check("changing it from another device needs the current password", not r["ok"])
st, body, cookie2, _ = req(REMOTE + "/api/password", {"current": PW, "new": PW2}, cookie=cookie)
check("change with the current password works and keeps that device signed in",
      json.loads(body)["ok"] and req(REMOTE + "/api/state", cookie=cookie2)[0] == 200)
check("old sessions end after a password change", req(REMOTE + "/api/state", cookie=cookie)[0] == 401)
req(LOCAL + "/api/signout_all", {})
check("'sign out all devices' ends remote sessions", req(REMOTE + "/api/state", cookie=cookie2)[0] == 401)
for i in range(5):
    req(REMOTE + "/login", "password=wrong", form=True)
st, body, _, _ = req(REMOTE + "/login", f"password={PW2}", form=True)
check("5 wrong attempts lock the device (even the right password waits)", st == 200 and "Too many attempts" in body)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
