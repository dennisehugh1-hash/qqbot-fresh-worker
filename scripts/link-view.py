#!/usr/bin/env python3
"""Link/card/video viewer helper for the QQ hook worker.

Usage:
  python3 link-view.py "<raw_message or URL>"
  python3 link-view.py --local-file /path/to/video.mp4

What it does, and only this:
- Extracts plain http(s) URLs, CQ:json/CQ:xml card meta (title/desc/jumpUrl/preview),
  and CQ:video segments (file/url/file_size) from a raw QQ message.
- For a http(s) link: curl -L (max 20s, max 5MB for the page) and extracts
  <title>, og:title, og:description, meta description, og:image.
  If an og:image/preview exists, downloads it (max 10MB) to media/inbox/.
- For a CQ:video url: downloads the file (max 50MB, max 60s) to media/inbox/,
  runs ffprobe for duration, and ffmpeg-extracts 3 frames (10%, 50%, 90%)
  to media/inbox/ as .jpg files for the worker to view.
- Prints ONE JSON object to stdout: kind, title, desc, url, preview_path,
  video_path, duration, frames[], error. It never logs in, never submits
  credentials, never follows mqqapi:// or other non-http schemes, and never
  claims to have watched a whole video: frames are samples only.

Config via env: QQBOT_HOME (project root, default ~/workspace/qqbot).
Media downloads go to <QQBOT_HOME>/media/inbox/.
"""
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse

BASE = os.environ.get("QQBOT_HOME", os.path.expanduser("~/workspace/qqbot"))
MEDIA = os.path.join(BASE, "media", "inbox")
os.makedirs(MEDIA, exist_ok=True)


def run(cmd, timeout=70):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), 1


def slug(s):
    import hashlib
    out = re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:60].strip("_")
    return out or hashlib.md5(s.encode("utf-8", "ignore")).hexdigest()[:10]


def download(url, dest, max_mb=50, timeout=60):
    cmd = ["curl", "-fsL", "--max-time", str(timeout), "--max-filesize", str(max_mb * 1024 * 1024),
           "-A", "Mozilla/5.0", "-o", dest, url]
    _, err, rc = run(cmd, timeout=timeout + 10)
    if rc != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0:
        return False, err or "download failed"
    return True, ""


def fetch_page(url):
    out = {"url": url}
    tmp = os.path.join(MEDIA, "page.html")
    ok, err = download(url, tmp, max_mb=5, timeout=20)
    if not ok:
        out["error"] = f"page fetch failed: {err}"
        return out
    try:
        text = open(tmp, encoding="utf-8", errors="replace").read()
    except Exception as e:
        out["error"] = str(e)
        return out
    def meta(prop):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)', text, re.I)
        if m:
            return html.unescape(m.group(1))
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\']', text, re.I)
        return html.unescape(m.group(1)) if m else ""
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    out["title"] = meta("og:title") or (html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else "")
    out["desc"] = meta("og:description") or meta("description") or ""
    img = meta("og:image") or meta("twitter:image") or ""
    out["preview_url"] = urllib.parse.urljoin(url, img) if img else ""
    if out["preview_url"]:
        dest = os.path.join(MEDIA, f"preview-{slug(out['title'] or 'link')}.jpg")
        ok2, _ = download(out["preview_url"], dest, max_mb=10, timeout=20)
        if ok2:
            out["preview_path"] = dest
    return out


def extract_video_frames(video_path, stem):
    dur_out, _, _ = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", video_path], timeout=30)
    try:
        duration = float(dur_out)
    except Exception:
        duration = 0
    frames = []
    for i, frac in enumerate([0.1, 0.5, 0.9]):
        t = max(0.5, duration * frac) if duration else (1 + i * 2)
        dest = os.path.join(MEDIA, f"{stem}-frame{i+1}.jpg")
        _, _, rc = run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", video_path,
                        "-frames:v", "1", "-q:v", "3", dest], timeout=60)
        if rc == 0 and os.path.exists(dest):
            frames.append(dest)
    return duration, frames


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--local-file":
        path = sys.argv[2]
        stem = slug(os.path.basename(path))
        duration, frames = extract_video_frames(path, stem)
        print(json.dumps({"kind": "video_file", "video_path": path, "duration": duration,
                          "frames": frames}, ensure_ascii=False))
        return
    raw = sys.argv[1] if len(sys.argv) >= 2 else ""
    raw_un = html.unescape(raw)

    # 1. CQ:video segment
    m = re.search(r"\[CQ:video[^\]]*file=([^,\]]+)[^\]]*url=([^,\]]+)", raw_un)
    if not m:
        m = re.search(r"\[CQ:video[^\]]*url=([^,\]]+)[^\]]*file=([^,\]]+)", raw_un)
        if m:
            m = type("M", (), {"group": lambda s, i: (m.group(2), m.group(1))[i-1]})()
    if m:
        try:
            vfile, vurl = m.group(1), m.group(2)
        except Exception:
            vfile, vurl = "", ""
        vurl = html.unescape(vurl).replace("&amp;", "&")
        if vurl.startswith("http"):
            dest = os.path.join(MEDIA, slug(vfile or "video") + ".mp4")
            ok, err = download(vurl, dest, max_mb=50, timeout=60)
            if not ok:
                print(json.dumps({"kind": "video", "url": vurl, "error": f"video download failed: {err}"}, ensure_ascii=False))
                return
            duration, frames = extract_video_frames(dest, slug(vfile or "video"))
            print(json.dumps({"kind": "video", "url": vurl, "video_path": dest,
                              "duration": duration, "frames": frames}, ensure_ascii=False))
            return
        print(json.dumps({"kind": "video", "error": "video url is not http(s), cannot download"}, ensure_ascii=False))
        return

    # 2. CQ:json / CQ:xml card: extract jumpUrl/title/desc/preview
    if "CQ:json" in raw_un or "CQ:xml" in raw_un:
        def find(pat):
            mm = re.search(pat, raw_un, re.S)
            return html.unescape(mm.group(1)).replace("&amp;", "&") if mm else ""
        jump = find(r'"jumpUrl"\s*:\s*"([^"]+)"')
        title = find(r'"title"\s*:\s*"([^"]+)"')
        desc = find(r'"desc"\s*:\s*"([^"]+)"')
        preview = find(r'"preview"\s*:\s*"([^"]+)"')
        result = {"kind": "card", "jumpUrl": jump, "title": title, "desc": desc, "preview_url": preview}
        if preview.startswith("http"):
            dest = os.path.join(MEDIA, f"card-preview-{slug(title or 'card')}.jpg")
            ok2, _ = download(preview, dest, max_mb=10, timeout=20)
            if ok2:
                result["preview_path"] = dest
        if jump.startswith("http"):
            page = fetch_page(jump)
            result.update({k: v for k, v in page.items() if k not in result or not result[k]})
            if page.get("preview_path"):
                result["preview_path"] = page["preview_path"]
        elif jump:
            result["error"] = "jumpUrl is not http(s) (QQ internal link), only card title/desc/preview are usable"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 3. Plain http(s) URL
    urls = re.findall(r"https?://[^\s\]\"'<>]+", raw_un)
    if urls:
        url = urls[0].rstrip(".,")
        page = fetch_page(url)
        page["kind"] = "link"
        print(json.dumps(page, ensure_ascii=False))
        return

    print(json.dumps({"kind": "none", "error": "no http link, card, or video segment found in input"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
