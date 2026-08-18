#!/usr/bin/env python3
"""Pull public TikTok stats for the accounts in data/accounts.json.

Opens each profile in a real Chrome window (parked offscreen) and intercepts
TikTok's own item_list API responses, so per-video views/likes come back
exact, with no login and no manual logging. Writes data/data.json and appends
today's totals to data/history.json.

Usage:
  ./venv/bin/python fetch.py            # all accounts in data/accounts.json
  ./venv/bin/python fetch.py handle     # one account (no @)
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
PROFILE = DATA / "chrome-profile"
MAX_SCROLLS = 60  # ~15-35 videos per API page


def load_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def fetch_account(ctx, handle):
    items = {}
    status = {"more": True}
    page = ctx.new_page()

    def on_response(resp):
        if "/api/post/item_list/" not in resp.url:
            return
        try:
            j = resp.json()
        except Exception:
            return
        for it in j.get("itemList") or []:
            s = it.get("stats") or {}
            items[it["id"]] = {
                "id": it["id"],
                "desc": (it.get("desc") or "")[:120],
                "createTime": it.get("createTime", 0),
                "views": s.get("playCount", 0),
                "likes": s.get("diggCount", 0),
                "comments": s.get("commentCount", 0),
                "shares": s.get("shareCount", 0),
            }
        status["more"] = bool(j.get("hasMore"))

    page.on("response", on_response)
    page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded",
              timeout=45000)
    page.wait_for_timeout(5000)

    acct = {"handle": handle, "followers": 0, "totalLikes": 0, "videoCount": 0}
    try:
        raw = page.locator('script#__UNIVERSAL_DATA_FOR_REHYDRATION__').text_content(timeout=8000)
        info = json.loads(raw)["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
        st = info.get("stats", {})
        acct.update(followers=st.get("followerCount", 0),
                    totalLikes=st.get("heartCount", 0),
                    videoCount=st.get("videoCount", 0),
                    nickname=info.get("user", {}).get("nickname", handle))
    except Exception as e:
        print(f"  ! no profile JSON for @{handle}: {e}", file=sys.stderr)

    last_count, stalls = -1, 0
    for _ in range(MAX_SCROLLS):
        if not status["more"] and items:
            break
        if acct["videoCount"] and len(items) >= acct["videoCount"]:
            break
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        if len(items) == last_count:
            stalls += 1
            if stalls >= 6:
                break
        else:
            stalls = 0
        last_count = len(items)

    page.close()
    vids = sorted(items.values(), key=lambda v: v["createTime"], reverse=True)
    acct["videos"] = vids
    acct["totalViews"] = sum(v["views"] for v in vids)
    acct["likesFromVideos"] = sum(v["likes"] for v in vids)
    return acct


def main():
    accounts = load_json(DATA / "accounts.json", [])
    if len(sys.argv) > 1:
        accounts = [sys.argv[1].lstrip("@")]
    if not accounts:
        print("No accounts yet. Add one in the dashboard or data/accounts.json.")
        return

    out = load_json(DATA / "data.json", {"accounts": {}})
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, channel="chrome",
            args=["--disable-blink-features=AutomationControlled",
                  "--window-position=-2400,-2400"],
            viewport={"width": 1280, "height": 900}, locale="en-US")
        try:
            for h in accounts:
                print(f"fetching @{h} ...")
                try:
                    acct = fetch_account(ctx, h)
                    out["accounts"][h] = acct
                    print(f"  {len(acct['videos'])}/{acct['videoCount']} videos, "
                          f"{acct['totalViews']:,} views")
                except Exception as e:
                    print(f"  ! failed @{h}: {e}", file=sys.stderr)
        finally:
            ctx.close()

    out["fetchedAt"] = int(time.time())
    (DATA / "data.json").write_text(json.dumps(out, indent=1))

    hist = load_json(DATA / "history.json", {})
    d = date.today().isoformat()
    hist.setdefault(d, {})
    for h, a in out["accounts"].items():
        hist[d][h] = {
            "views": a["totalViews"],
            "likes": a["likesFromVideos"] or a["totalLikes"],
            "videoCount": a["videoCount"] or len(a["videos"]),
            "followers": a["followers"],
        }
    (DATA / "history.json").write_text(json.dumps(hist, indent=1))
    print("saved data/data.json + data/history.json")


if __name__ == "__main__":
    main()
