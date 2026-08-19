#!/usr/bin/env python3
"""Serve refresh requests from the public dashboard.

The live site (1600-views.vercel.app) cannot scrape TikTok itself, so its
Refresh button drops a request into Vercel Blob (site/api/refresh.js). This
worker polls the store from the Mac, and for each request runs
fetch.py -> publish.py -> `vercel --prod` -> git commit/push, writing a status
marker back so the page can reload when the new snapshot is live.

Run it in the background (tmux):
  cd ~/1600-views && ./venv/bin/python refresh_worker.py

Needs BLOB_READ_WRITE_TOKEN, read from site/.env.local (vercel env pull).
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
PY = sys.executable
POLL_SECS = 15
API = "https://blob.vercel-storage.com"


def load_token():
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if tok:
        return tok
    for line in (SITE / ".env.local").read_text().splitlines():
        if line.startswith("BLOB_READ_WRITE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("BLOB_READ_WRITE_TOKEN missing: run `cd site && npx vercel env pull .env.local`")


TOKEN = load_token()


def blob(method, path="", body=None, headers=None, query=None):
    url = API + "/" + path.lstrip("/")
    if query:
        url += "?" + urllib.parse.urlencode(query)
    h = {"authorization": "Bearer " + TOKEN, "x-api-version": "12"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def list_blobs(prefix):
    out = blob("GET", "", query={"prefix": prefix, "limit": "1000"})
    return out.get("blobs", [])


def put_blob(pathname, text):
    return blob("PUT", "", query={"pathname": pathname}, body=text.encode(), headers={
        "x-vercel-blob-access": "private",
        "x-add-random-suffix": "0",
        "x-content-type": "text/plain",
    })


def del_blobs(urls):
    if urls:
        blob("POST", "delete", body=json.dumps({"urls": urls}).encode(),
             headers={"content-type": "application/json"})


def set_status(state, text=""):
    old = [b["url"] for b in list_blobs("refresh-status/")]
    put_blob(f"refresh-status/{state}-{int(time.time() * 1000)}.json", text or "{}")
    del_blobs(old)


def log(msg):
    print(datetime.now().strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, cwd, timeout):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


def do_refresh():
    set_status("running")
    log("fetch.py")
    run([PY, str(ROOT / "fetch.py")], ROOT, 1800)
    log("publish.py")
    run([PY, str(ROOT / "publish.py")], ROOT, 300)
    log("vercel --prod")
    run(["npx", "vercel", "--prod", "--yes"], SITE, 600)
    try:
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True, capture_output=True)
        chg = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if chg.returncode != 0:
            subprocess.run(["git", "commit", "-qm", "Refresh data (from live site)"], cwd=ROOT, check=True, capture_output=True)
            subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True, capture_output=True, timeout=120)
    except Exception as e:  # git is best-effort, the deploy already happened
        log(f"git skipped: {e}")
    set_status("done")
    log("done")


def main():
    log(f"refresh worker up, polling every {POLL_SECS}s")
    while True:
        try:
            reqs = list_blobs("refresh-request/")
            if reqs:
                log(f"{len(reqs)} request(s)")
                del_blobs([b["url"] for b in reqs])
                try:
                    do_refresh()
                except Exception as e:
                    log(f"error: {e}")
                    set_status("error", str(e)[:400])
        except Exception as e:
            log(f"poll error: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
