#!/usr/bin/env python3
"""Local server for the 1600 Creator HQ dashboard.

  cd ~/1600-views && ./venv/bin/python server.py
  open http://localhost:1616

Endpoints:
  GET  /            dashboard
  GET  /api/data    accounts + latest stats + daily history
  POST /api/accounts {"handle": "..."}   add a TikTok account
  POST /api/refresh  re-scrape all accounts (runs fetch.py, ~10-60s/account)
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


def run_fetch():
    refresh_state["running"] = True
    refresh_state["lastError"] = ""
    try:
        r = subprocess.run([str(PY), str(ROOT / "fetch.py")], capture_output=True,
                           text=True, timeout=1800)
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
            self.send_json({"ok": True, "accounts": accounts})
            return

        if self.path == "/api/refresh":
            if refresh_lock.acquire(blocking=False):
                def go():
                    try:
                        run_fetch()
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
