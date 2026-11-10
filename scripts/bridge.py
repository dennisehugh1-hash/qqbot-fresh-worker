#!/usr/bin/env python3
"""NapCat -> Muse bridge (stdlib only).

- Listens on 127.0.0.1:6198 for OneBot11 HTTP events posted by NapCat.
- Appends private/group message events to inbox.jsonl (consumed by the
  hook that wakes Muse). Non-message events go to events.jsonl.
- send.py (same folder) posts replies back through NapCat's HTTP API.

Config via env: BRIDGE_TOKEN (shared with NapCat http post), NAPCAT_API
(default http://127.0.0.1:3000), NAPCAT_TOKEN, BOT_QQ.
"""
import gzip, json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).parent
INBOX = BASE / "inbox.jsonl"
EVENTS = BASE / "events.jsonl"
BOT_QQ = os.environ.get("BOT_QQ", "")

def history_path(message_type=None):
    """History file for one event. Both personas split by message_type
    (private -> history-private.jsonl, group -> history-group.jsonl)
    so private and group contexts never share a file. Unknown/missing
    message_type falls back to the private file (it still carries
    message_type in the entry)."""
    try:
        import json as _json
        active = _json.load(open(Path(__file__).parent.parent / "personas" / "active.json")).get("active")
    except Exception:
        active = "character"
    if active == "steward":
        base = Path(__file__).parent.parent / "personas" / "steward"
    else:
        base = Path(__file__).parent.parent / "personas" / "character"
    if message_type == "group":
        return base / "history-group.jsonl"
    return base / "history-private.jsonl"

def append(path: Path, obj: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass
    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks = []
            while True:
                size_line = self.rfile.readline(65536).strip()
                try:
                    size = int(size_line.split(b";")[0], 16)
                except Exception:
                    break
                if size == 0:
                    # consume trailing headers until blank line
                    while True:
                        line = self.rfile.readline(65536)
                        if line in (b"\r\n", b"\n", b""):
                            break
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)  # CRLF
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b"{}"

    def do_POST(self):
        raw = self._read_body()
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        # one-line diagnostic for the next real post (headers + body size/head)
        try:
            (BASE / "debug-last-post.txt").write_text(
                f"headers={dict(self.headers)}\nlen={len(raw)}\nhead={raw[:300]!r}\n", encoding="utf-8")
        except Exception:
            pass
        try:
            ev = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            self.send_response(400); self.end_headers(); return
        ev["_recv_at"] = int(time.time())
        pt = ev.get("post_type")
        if pt in ("message", "message_sent"):
            hist = {"ts": ev.get("_recv_at"), "direction": "out" if pt == "message_sent" else "in",
                    "message_type": ev.get("message_type"), "user_id": ev.get("user_id"),
                    "group_id": ev.get("group_id"), "text": ev.get("raw_message", "")}
            append(history_path(ev.get("message_type")), hist)
            if pt == "message" and str(ev.get("user_id")) != str(ev.get("self_id")):
                append(INBOX, ev)
        else:
            append(EVENTS, ev)
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"bridge up")

if __name__ == "__main__":
    port = int(os.environ.get("BRIDGE_PORT", "6198"))
    HTTPServer(("127.0.0.1", port), H).serve_forever()
