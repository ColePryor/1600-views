#!/usr/bin/env python3
"""Local server for the 1600 Creator HQ dashboard.

  cd ~/1600-views && ./venv/bin/python server.py
  open http://localhost:1616

Endpoints:
  GET  /            dashboard
  GET  /api/data    accounts + latest stats + daily history
  POST /api/accounts {"handle": "...", "name": "..."}   add a TikTok account (name optional)
  POST /api/owner    {"handle": "...", "name": "..."}    sole owner ("" clears), or
                     {"handle": "...", "shares": {"A": 60, "B": 40}}  split it
  POST /api/owner/rename {"from": "...", "to": "..."}    rename a person everywhere
  POST /api/accounts/remove {"handle": "..."}   drop an account (list, cached data, owner; history kept)
  POST /api/refresh  re-scrape all accounts, rebuild site/, deploy to Vercel
                     (runs refresh.sh, ~10-60s/account plus the deploy)
                     {"publish": false, "handle": "..."}  scrape only, one account
"""
import json
import ssl
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
PORT = 1616
PY = ROOT / "venv" / "bin" / "python"

# Site user counts come straight from sixteen's Neon Postgres (same numbers
# as the admin portal), read-only, cached so a page reload doesn't hammer it.
SIXTEEN_ENV = Path.home() / "sixteen" / ".env.local"
USERS_TTL = 300
users_cache = {"at": 0.0, "data": {"error": "not loaded yet"}}
users_lock = threading.Lock()


def fetch_site_users():
    import pg8000.native

    url = None
    for line in SIXTEEN_ENV.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not url:
        return {"error": "no DATABASE_URL in ~/sixteen/.env.local"}

    u = urlparse(url)
    con = pg8000.native.Connection(
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        host=u.hostname,
        port=u.port or 5432,
        database=(u.path or "/").lstrip("/"),
        ssl_context=ssl.create_default_context(),
        timeout=15,
    )
    try:
        # Day buckets in Eastern time, matching sixteen's dayKey().
        day = ("to_char(to_timestamp(%s.created_at/1000.0) AT TIME ZONE "
               "'America/New_York', 'YYYY-MM-DD')")
        signups = con.run(
            f"SELECT {day % 'users'} AS d, COUNT(*)::int FROM users "
            "WHERE is_bot = 0 GROUP BY 1")
        active = con.run(
            f"SELECT {day % 'a'} AS d, COUNT(DISTINCT a.user_id)::int "
            "FROM attempts a JOIN users u ON u.id = a.user_id AND u.is_bot = 0 "
            "WHERE a.created_at >= (extract(epoch from now()) - 15*86400) * 1000 "
            "GROUP BY 1")
        total = con.run("SELECT COUNT(*)::int FROM users WHERE is_bot = 0")[0][0]
    finally:
        con.close()
    return {"total": total,
            "signups": {d: n for d, n in signups},
            "active": {d: n for d, n in active}}


def site_users():
    with users_lock:
        if time.time() - users_cache["at"] > USERS_TTL:
            try:
                users_cache["data"] = fetch_site_users()
            except Exception as e:
                users_cache["data"] = {"error": str(e)[:200]}
            users_cache["at"] = time.time()
        return users_cache["data"]

refresh_lock = threading.Lock()
refresh_state = {"running": False, "lastError": ""}


def load_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def norm_shares(value):
    """Normalize one owners.json entry to {name: percent} summing to 100.

    An entry is either a plain name (that person runs the account outright) or
    a {name: weight} split. Weights are relative, so {"A": 1, "B": 3} and
    {"A": 25, "B": 75} mean the same thing; both come back as percents.
    """
    if isinstance(value, str):
        value = {value: 1} if value.strip() else {}
    if not isinstance(value, dict):
        return {}
    pairs = []
    for name, w in value.items():
        name = str(name).strip()[:40]
        try:
            w = float(w)
        except (TypeError, ValueError):
            w = 0.0
        if name and w > 0:
            pairs.append((name, w))
    total = sum(w for _, w in pairs)
    if not total:
        return {}
    # Largest-remainder rounding so the percents always add up to exactly 100.
    exact = [(n, w / total * 100) for n, w in pairs]
    out = {n: int(v) for n, v in exact}
    left = 100 - sum(out.values())
    for n, v in sorted(exact, key=lambda x: -(x[1] - int(x[1]))):
        if left <= 0:
            break
        out[n] += 1
        left -= 1
    return {n: v for n, v in out.items() if v > 0}


def load_owners():
    """handle -> {person: percent}, percents summing to 100."""
    raw = load_json(DATA / "owners.json", {})
    return {h: sh for h, v in raw.items() if (sh := norm_shares(v))}


def save_owners(owners):
    """Write owners.json back, keeping the compact form for sole owners."""
    out = {}
    for h, shares in owners.items():
        if not shares:
            continue
        out[h] = next(iter(shares)) if len(shares) == 1 else shares
    (DATA / "owners.json").write_text(json.dumps(out, indent=1))
    return owners


def set_owner(handle, value):
    """Set who runs an account: a name, a {name: weight} split, or "" to clear."""
    owners = load_owners()
    shares = norm_shares(value)
    if shares:
        owners[handle] = shares
    else:
        owners.pop(handle, None)
    return save_owners(owners)


def rename_owner(old, new):
    """Rename a person everywhere, keeping each account's split intact."""
    old, new = (old or "").strip(), (new or "").strip()[:40]
    owners = load_owners()
    if not old or not new or old == new:
        return owners
    for h, shares in owners.items():
        if old in shares:
            merged = dict(shares)
            merged[new] = merged.pop(old) + merged.get(new, 0)
            owners[h] = norm_shares(merged)
    return save_owners(owners)


def run_fetch(publish=True, handle=None):
    """Manual refresh: scrape TikTok, and by default rebuild site/, deploy, push.

    Nothing runs on a schedule any more (the launchd job is disabled), so the
    dashboard's button is the only thing that updates the public page.
    publish=False just scrapes (used when a newly added account is pulled in).
    """
    cmd = (["/bin/zsh", str(ROOT / "refresh.sh")] if publish
           else [str(PY), str(ROOT / "fetch.py")] + ([handle] if handle else []))
    refresh_state["running"] = True
    refresh_state["lastError"] = ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            refresh_state["lastError"] = (r.stderr or r.stdout)[-400:]
    except Exception as e:
        refresh_state["lastError"] = str(e)
    finally:
        refresh_state["running"] = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/data"):
            self.send_json({
                "accounts": load_json(DATA / "accounts.json", []),
                "owners": load_owners(),
                "data": load_json(DATA / "data.json", {"accounts": {}}),
                "history": load_json(DATA / "history.json", {}),
                "users": site_users(),
                "refreshing": refresh_state["running"],
                "lastError": refresh_state["lastError"],
            })
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}

        if self.path == "/api/accounts":
            h = (body.get("handle") or "").strip().lstrip("@")
            if not h:
                self.send_json({"error": "no handle"}, 400)
                return
            accounts = load_json(DATA / "accounts.json", [])
            if h not in accounts:
                accounts.append(h)
                (DATA / "accounts.json").write_text(json.dumps(accounts))
            name = (body.get("name") or "").strip()
            if name:
                set_owner(h, name)
            self.send_json({"ok": True, "accounts": accounts,
                            "owners": load_owners()})
            return

        if self.path == "/api/accounts/remove":
            h = (body.get("handle") or "").strip().lstrip("@")
            if not h:
                self.send_json({"error": "no handle"}, 400)
                return
            accounts = [a for a in load_json(DATA / "accounts.json", []) if a != h]
            (DATA / "accounts.json").write_text(json.dumps(accounts))
            data = load_json(DATA / "data.json", {"accounts": {}})
            if h in data.get("accounts", {}):
                del data["accounts"][h]
                (DATA / "data.json").write_text(json.dumps(data, indent=1))
            owners = set_owner(h, "")
            self.send_json({"ok": True, "accounts": accounts, "owners": owners})
            return

        if self.path == "/api/owner":
            h = (body.get("handle") or "").strip().lstrip("@")
            if not h:
                self.send_json({"error": "no handle"}, 400)
                return
            value = body["shares"] if isinstance(body.get("shares"), dict) \
                else (body.get("name") or "").strip()[:40]
            owners = set_owner(h, value)
            self.send_json({"ok": True, "owners": owners})
            return

        if self.path == "/api/owner/rename":
            old_name = (body.get("from") or "").strip()
            new_name = (body.get("to") or "").strip()[:40]
            if not old_name or not new_name:
                self.send_json({"error": "need from and to"}, 400)
                return
            self.send_json({"ok": True, "owners": rename_owner(old_name, new_name)})
            return

        if self.path == "/api/refresh":
            publish = body.get("publish", True) is not False
            handle = (body.get("handle") or "").strip().lstrip("@") or None
            if refresh_lock.acquire(blocking=False):
                def go():
                    try:
                        run_fetch(publish, handle)
                    finally:
                        refresh_lock.release()
                threading.Thread(target=go, daemon=True).start()
            self.send_json({"ok": True, "running": True})
            return

        self.send_json({"error": "unknown endpoint"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"1600 Creator HQ on http://localhost:{PORT}")
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
