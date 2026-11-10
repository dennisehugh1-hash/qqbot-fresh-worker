#!/usr/bin/env python3
"""Catch-up for messages missed while NapCat/bridge was down (e.g. host replacement).

Why this exists: bridge only receives live NapCat events. A message sent
while bridge/QQ is down never enters bridge/inbox.jsonl, and it is NOT
replayed after recovery. This script recovers it from QQ server history:
  1. NapCat get_group_msg_history for each known group (trigger messages only:
     @bot or bot QQ mentioned)
  2. NapCat get_friend_msg_history for the owner (all incoming private
     messages; private chats are always answered)

Dedupe (both must pass, or the message is already handled):
  - message_id against bridge/inbox.jsonl
  - (user_id, time, text) against BOTH personas' history files.
    Both personas' history is split: private -> history-private.jsonl,
    group -> history-group.jsonl, under each persona's folder.
    Dedupe checks all four files: a message already logged by EITHER
    persona is answered, not missed, so switching personas can never
    make catchup re-inject a message the other persona already handled.
    --inject still writes the 'in' entry to the ACTIVE persona's file
    matching the message's message_type.
    History entries carry NO message_id, so message_id dedupe alone
    would re-inject messages that were already answered by hand but
    never reached the inbox. That re-injection would make the bot
    answer the same message a second time.

Modes:
  - Dry-run (default): only PRINTS missed messages. Never sends, never
    writes inbox.jsonl or history files, never advances offsets.
  - --inject: appends each missed message to inbox.jsonl as a bridge
    event (same shape bridge.py writes for a live message, so the inbox
    hook wakes the worker exactly as a live message would) AND appends
    the matching 'in' entry to the active persona's matching split
    history file, so a later run dedupes it. Missed messages are injected oldest-first.

Trigger (wired into the keepalive check, Case A): run --inject once when
a check confirms the bot is back online after an outage episode.

Limits, known and accepted:
  - Group replies to the bot without an @ are NOT detected (a reply
    segment carries only the replied message id, not its sender).
  - Only the last `count` messages per source are fetched (default 50);
    how far back message_seq pagination reaches is untested.
  - Only messages from the last --max-age-secs (default 6h) are
    considered. The window is anchored at now, NOT at the last inbox
    _recv_at: live traffic after an outage advances _recv_at past the
    outage, so anchoring on it would miss exactly the messages lost
    mid-outage. Dedupe (inbox + history) is what prevents re-processing,
    not the window.
  - Images in missed messages are not pre-downloaded; the hook's
    normal image handling applies when the worker processes them.

Usage: python3 bridge/catchup.py [--inject]
       [--group <GROUP_ID>] [--friend <OWNER_QQ>] [--count 50]
       [--max-age-secs 21600]
       [--inbox PATH] [--history PATH] [--history-private PATH]
       [--history-group PATH]   (the last ones are for testing;
       --history, if given, is used for both types; they replace only
       the ACTIVE persona's files, the other persona's real files are
       still read for dedupe)
Env: reads bridge/napcat-api.env for NAPCAT_API, NAPCAT_TOKEN.
Also reads BOT_QQ (required), OWNER_QQ, and CATCHUP_GROUPS
(comma-separated group numbers) from the environment.
Never prints credential values.
"""
import argparse, json, os, sys, time, urllib.request
from pathlib import Path

HOME = Path.home()
QQBOT = HOME / "workspace" / "qqbot"
BRIDGE = QQBOT / "bridge"
INBOX = BRIDGE / "inbox.jsonl"
BOT_QQ = os.environ.get("BOT_QQ", "")
OWNER_QQ = int(os.environ.get("OWNER_QQ", "0") or 0)
GROUPS = [int(g) for g in os.environ.get("CATCHUP_GROUPS", "").split(",") if g.strip()]
DEFAULT_FRIENDS = [OWNER_QQ] if OWNER_QQ else []
MAX_AGE_SECS_DEFAULT = 21600  # 6h: episodes last minutes; catchup runs
# on recovery, so 6h is generous while bounding stale re-injections.

def load_env():
    env = {}
    for line in (BRIDGE / "napcat-api.env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def active_history_paths():
    """Both personas split: {"private": history-private.jsonl,
    "group": history-group.jsonl} under the active persona's folder."""
    try:
        active = json.load(open(QQBOT / "personas" / "active.json")).get("active")
    except Exception:
        active = "character"
    if active == "steward":
        base = QQBOT / "personas" / "steward"
    else:
        base = QQBOT / "personas" / "character"
    return {"private": base / "history-private.jsonl",
            "group": base / "history-group.jsonl"}

def all_history_paths():
    """All four split files, both personas, for dedupe only.
    A message handled by EITHER persona is already answered, so it
    must never be re-injected when the other persona takes over.
    --inject still writes to the ACTIVE persona's files only."""
    return {
        "steward-private": QQBOT / "personas" / "steward" / "history-private.jsonl",
        "steward-group": QQBOT / "personas" / "steward" / "history-group.jsonl",
        "character-private": QQBOT / "personas" / "character" / "history-private.jsonl",
        "character-group": QQBOT / "personas" / "character" / "history-group.jsonl",
    }

def active_history_path():
    # Legacy single-file helper: steward -> private file, character -> its file.
    return active_history_paths()["private"]

def call(api_base, path, payload, token):
    req = urllib.request.Request(
        f"{api_base}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def history_messages(data):
    if isinstance(data, dict):
        return data.get("messages", []) or []
    return []

def to_bridge_event(rec, now):
    """Map a NapCat history record to the event shape bridge.py writes
    for a live message. History records already are OneBot-shaped; the
    differences are the message segment format (history may be 'string')
    and the bridge-added _recv_at."""
    ev = dict(rec)
    raw = str(rec.get("raw_message", ""))
    if rec.get("message_format") == "string" or isinstance(rec.get("message"), str):
        ev["message"] = [{"type": "text", "data": {"text": raw}}]
        ev["message_format"] = "array"
    ev["post_type"] = "message"
    ev["self_id"] = int(BOT_QQ) if BOT_QQ else rec.get("self_id")
    ev["_recv_at"] = now
    return ev

def history_entry(ev):
    """The 'in' entry bridge.py would have written for this event."""
    return {"ts": int(ev.get("time", 0) or 0), "direction": "in",
            "message_type": ev.get("message_type"),
            "user_id": ev.get("user_id"), "group_id": ev.get("group_id"),
            "text": ev.get("raw_message", "")}

def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def main():
    if not BOT_QQ:
        print("BOT_QQ is not set in the environment", file=sys.stderr)
        sys.exit(2)
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", type=int, action="append", default=list(GROUPS))
    ap.add_argument("--friend", type=int, action="append", default=list(DEFAULT_FRIENDS))
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--max-age-secs", type=int, default=MAX_AGE_SECS_DEFAULT,
                    help="only consider messages newer than now minus this (default 21600)")
    ap.add_argument("--inject", action="store_true",
                    help="append missed messages to inbox.jsonl + history (default: dry-run)")
    ap.add_argument("--inbox", type=str, default=str(INBOX))
    ap.add_argument("--history", type=str, default=None,
                    help="history path (default: active persona's); used for both types if given")
    ap.add_argument("--history-private", type=str, default=None,
                    help="private history path (default: active persona's private file)")
    ap.add_argument("--history-group", type=str, default=None,
                    help="group history path (default: active persona's group file)")
    args = ap.parse_args()

    inbox_path = Path(args.inbox)
    hist_paths = active_history_paths()
    if args.history:
        hist_paths = {"private": Path(args.history), "group": Path(args.history)}
    if args.history_private:
        hist_paths["private"] = Path(args.history_private)
    if args.history_group:
        hist_paths["group"] = Path(args.history_group)
    # Dedupe files: the active persona's files (possibly overridden
    # above for testing) PLUS the other persona's real files. The
    # other persona's files have no override flag, so they are always
    # read from their real paths.
    dedupe_files = [("private", hist_paths["private"]),
                    ("group", hist_paths["group"])]
    for _k, _p in all_history_paths().items():
        _kind = "private" if _k.endswith("private") else "group"
        if _p != hist_paths[_kind] and _p not in [q for _, q in dedupe_files]:
            dedupe_files.append((_kind, _p))
    now = int(time.time())

    seen_ids = set()
    last_recv = 0
    if inbox_path.exists():
        for line in inbox_path.read_text().splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("message_id"):
                seen_ids.add(str(d["message_id"]))
            last_recv = max(last_recv, int(d.get("_recv_at", 0) or 0))
    if last_recv == 0:
        last_recv = now - 3600  # fresh/empty inbox: diagnostic only
    cutoff = now - args.max_age_secs

    # (user_id, time, text) already answered/logged, per file, plus union.
    # Dedupe checks the union across BOTH personas and both types: a
    # message logged in any of the four files is never re-injected,
    # even if it was logged under the other type or by the other persona.
    handled_private = set()
    handled_group = set()
    for _kind, _path in dedupe_files:
        if not _path.exists():
            continue
        for line in _path.read_text().splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("direction") == "in":
                _entry = (str(d.get("user_id")), int(d.get("ts", 0) or 0),
                          str(d.get("text", "")))
                if _kind == "private":
                    handled_private.add(_entry)
                else:
                    handled_group.add(_entry)
    handled = handled_private | handled_group

    env = load_env()
    api_base = env.get("NAPCAT_API", "http://127.0.0.1:3000").rstrip("/")
    token = env.get("NAPCAT_TOKEN", "")

    missed = []  # list of bridge events, oldest first

    for gid in args.group:
        try:
            d = call(api_base, "get_group_msg_history",
                     {"group_id": gid, "message_seq": 0, "count": args.count}, token)
        except Exception as e:
            print(f"group {gid}: history fetch failed: {e}", file=sys.stderr)
            continue
        for m in history_messages(d.get("data")):
            mid = str(m.get("message_id", ""))
            t = int(m.get("time", 0) or 0)
            if not mid or mid in seen_ids or t <= cutoff:
                continue
            raw = str(m.get("raw_message", ""))
            if (str(m.get("user_id")), t, raw) in handled:
                continue
            # Only messages that would have triggered a reply: @bot / bot QQ mentioned.
            if f"[CQ:at,qq={BOT_QQ}]" not in raw and BOT_QQ not in raw:
                continue
            missed.append(to_bridge_event(m, now))

    for uid in args.friend:
        try:
            d = call(api_base, "get_friend_msg_history",
                     {"user_id": uid, "message_seq": 0, "count": args.count}, token)
        except Exception as e:
            print(f"friend {uid}: history fetch failed: {e}", file=sys.stderr)
            continue
        for m in history_messages(d.get("data")):
            # Incoming only: the bot's own sent messages come back as
            # post_type message_sent with user_id == self_id.
            if m.get("post_type") != "message":
                continue
            if str(m.get("user_id")) == BOT_QQ:
                continue
            mid = str(m.get("message_id", ""))
            t = int(m.get("time", 0) or 0)
            if not mid or mid in seen_ids or t <= cutoff:
                continue
            raw = str(m.get("raw_message", ""))
            if (str(m.get("user_id")), t, raw) in handled:
                continue
            missed.append(to_bridge_event(m, now))

    missed.sort(key=lambda e: int(e.get("time", 0) or 0))
    injected = 0
    if args.inject:
        for ev in missed:
            append_jsonl(inbox_path, ev)
            _mt = ev.get("message_type")
            _hp = hist_paths.get(_mt) if _mt in hist_paths else hist_paths["private"]
            append_jsonl(_hp, history_entry(ev))
            injected += 1

    print(json.dumps({
        "last_inbox_recv": last_recv, "cutoff": cutoff,
        "inbox_lines": len(seen_ids),
        "missed_trigger_messages": [
            {"source": ("group " + str(e.get("group_id")) if e.get("message_type") == "group"
                        else "private"), "message_id": e.get("message_id"),
             "time": e.get("time"), "user_id": e.get("user_id"),
             "raw_message": str(e.get("raw_message", ""))[:200]}
            for e in missed],
        "injected": injected, "dry_run": not args.inject,
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
