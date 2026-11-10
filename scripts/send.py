#!/usr/bin/env python3
"""Send a QQ message via NapCat's OneBot HTTP API.
Usage: send.py --user <QQ number> --text "hello"   (private)
       send.py --group <group number> --text "hello" (group)
Env: NAPCAT_API (default http://127.0.0.1:3000), NAPCAT_TOKEN.
Prints the API JSON response; exit 1 on failure.
"""
import argparse, json, os, random, sys, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--user"); ap.add_argument("--group"); ap.add_argument("--text", required=True)
a = ap.parse_args()
api = os.environ.get("NAPCAT_API", "http://127.0.0.1:3000").rstrip("/")
if a.group:
    path, payload = "/send_group_msg", {"group_id": int(a.group), "message": a.text}
elif a.user:
    path, payload = "/send_private_msg", {"user_id": int(a.user), "message": a.text}
else:
    print("need --user or --group"); sys.exit(2)
# Anti-kick jitter is a one-line switch: set JITTER_ENABLED=1 in env to
# enable a short random delay before every send. It is off by default.
import os as _os
if _os.environ.get("JITTER_ENABLED") == "1":
    _delay = min(3.0, max(0.0, random.gauss(1.5, 0.75)))
    time.sleep(_delay)
req = urllib.request.Request(api + path, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json",
                                      **({"Authorization": f"Bearer {os.environ['NAPCAT_TOKEN']}"} if os.environ.get("NAPCAT_TOKEN") else {})})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.read().decode())
except Exception as e:
    print(f"send failed: {e}"); sys.exit(1)
