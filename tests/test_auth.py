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
    ok = bool(ok); results.append(ok); print(("PASS " if ok else "FAIL ") + name)


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
# --- the venue Wi-Fi password is write-only: stored here, printed on the (admin-only) QR card, never sent back
WIFI = "s3cret-wifi-pw"
req(LOCAL + "/api/update", {"wifi_ssid": "Venue", "wifi_pass": WIFI})
st, body, _, _ = req(LOCAL + "/api/state")
check("the API never returns the Wi-Fi password", WIFI not in body and json.loads(body)["session"]["wifi_pass_set"])
req(LOCAL + "/api/update", {"wifi_ssid": "Venue", "context": "edited"})  # Control sends no wifi_pass unless retyped
check("applying other changes keeps the stored Wi-Fi password", WIFI in req(LOCAL + "/card")[1])
check("the QR card (shows the Wi-Fi password) needs admin access", req(REMOTE + "/card")[0] == 302)
check("the Wi-Fi QR code needs admin access", req(REMOTE + "/qr/wifi.svg")[0] == 302)
check("the audience QR code stays public", req(REMOTE + "/qr/guest.svg")[0] == 200)
req(LOCAL + "/api/update", {"wifi_pass": ""})
check("sending an empty Wi-Fi password removes it", not json.loads(req(LOCAL + "/api/state")[1])["session"]["wifi_pass_set"])
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
# --- venue NAT: the operator's laptop and a guest's phone reach us from ONE address (here: both are REMOTE)
def sign_in(pw, cookie=None, extra=""):
    """→ (status, body, all Set-Cookie pairs as one Cookie header)"""
    h = {"Content-Type": "application/x-www-form-urlencoded", **({"Cookie": cookie} if cookie else {})}
    r = urllib.request.Request(REMOTE + "/login", f"password={pw}{extra}".encode(), h)
    try:
        resp = urllib.request.build_opener(NoRedirect).open(r, timeout=10)
    except urllib.error.HTTPError as e:
        resp = e
    jar = "; ".join(c.split(";")[0] for c in resp.headers.get_all("Set-Cookie") or [])
    return resp.status, resp.read().decode(errors="replace"), jar


st, _, operator = sign_in(PW2)
dev = "; ".join(c for c in operator.split("; ") if c.startswith("lt_dev="))
check("signing in remembers the device", st == 302 and dev)
for i in range(5):
    sign_in("wrong")  # a guest on the same Wi-Fi/NAT, no cookies
st, body, _ = sign_in(PW2)
check("5 wrong attempts lock the device (even the right password waits)", st == 200 and "Too many attempts" in body)
check("…while the operator's signed-in session from the same address keeps working",
      req(REMOTE + "/api/state", cookie=operator)[0] == 200)
st, _, _ = sign_in(PW2, cookie=dev)
check("…and the operator's known device can still sign in from that address", st == 302)
for i in range(5):
    sign_in("wrong", cookie=dev)
st, body, _ = sign_in(PW2, cookie=dev)
check("a known device brute-forcing is locked too (its own counter)", st == 200 and "Too many attempts" in body)
code = json.loads(req(LOCAL + "/api/state")[1]).get("unlock", "")
check("unlock code is shown on this machine only",
      code and "unlock" not in json.loads(req(REMOTE + "/api/state", cookie=operator)[1]))
st, _, _ = sign_in(PW2, extra="&unlock=1111-1111")
check("wrong unlock code does not unlock", st == 200)
st, _, _ = sign_in(PW2, extra=f"&unlock={code}")
check("a new device locked by someone else gets in with the unlock code", st == 302)
check("the unlock code is single-use", json.loads(req(LOCAL + "/api/state")[1]).get("unlock") != code)
for i in range(7):
    req(LOCAL + "/api/password", {"current": "wrong", "new": "whatever-123"})
check("this machine is never locked out", json.loads(req(LOCAL + "/api/password", {"current": PW2, "new": PW2})[1])["ok"])
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
