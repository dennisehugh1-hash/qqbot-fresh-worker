#!/usr/bin/env python3
"""Send a file via NapCat OneBot API.
Usage: send_file.py --user <QQ number> --file /path/to/file.pptx [--name file.pptx]
       send_file.py --group <group number> --file /path/to/file.pptx
Env: NAPCAT_API (default http://127.0.0.1:3000), NAPCAT_TOKEN (source napcat-api.env first).
Tries upload_private_file / upload_group_file first; if that fails, falls back to
a [CQ:file] message segment. Prints API JSON; exit 1 on failure.
"""
import argparse, json, os, sys, urllib.request
ap = argparse.ArgumentParser()
ap.add_argument("--user"); ap.add_argument("--group"); ap.add_argument("--file", required=True); ap.add_argument("--name")
a = ap.parse_args()
api = os.environ.get("NAPCAT_API", "http://127.0.0.1:3000").rstrip("/")
name = a.name or os.path.basename(a.file)
headers = {"Content-Type": "application/json"}
if os.environ.get("NAPCAT_TOKEN"):
    headers["Authorization"] = f"Bearer {os.environ['NAPCAT_TOKEN']}"
def post(path, payload):
    req = urllib.request.Request(api + path, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
try:
    if a.group:
        res = post("/upload_group_file", {"group_id": int(a.group), "file": a.file, "name": name})
    else:
        res = post("/upload_private_file", {"user_id": int(a.user), "file": a.file, "name": name})
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("retcode") == 0 else 1)
except Exception as e:
    print(f"upload_file failed: {e}, falling back to CQ file segment")
    try:
        msg = f"[CQ:file,file=file://{a.file},name={name}]"
        if a.group:
            res = post("/send_group_msg", {"group_id": int(a.group), "message": msg})
        else:
            res = post("/send_private_msg", {"user_id": int(a.user), "message": msg})
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0 if res.get("retcode") == 0 else 1)
    except Exception as e2:
        print(f"send_file failed: {e2}"); sys.exit(1)
