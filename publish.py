#!/usr/bin/env python3
"""Build a shareable static snapshot of the dashboard into site/.

The published page is read-only: same UI, but the data is baked in at
publish time instead of served live, because the TikTok scraper and the
Neon read only run on this Mac. To update the public page: refresh the
local dashboard, run this script, then redeploy site/ to Vercel.
"""
import json
from pathlib import Path

import server

ROOT = Path(__file__).parent
SITE = ROOT / "site"
SITE.mkdir(exist_ok=True)

snapshot = {
    "accounts": server.load_json(server.DATA / "accounts.json", []),
    "owners": server.load_json(server.DATA / "owners.json", {}),
    "data": server.load_json(server.DATA / "data.json", {"accounts": {}}),
    "history": server.load_json(server.DATA / "history.json", {}),
    "users": server.site_users(),
    "refreshing": False,
    "lastError": "",
}
if snapshot["users"].get("error"):
    print(f"warning: user counts unavailable: {snapshot['users']['error']}")
(SITE / "snapshot.json").write_text(json.dumps(snapshot))

html = (ROOT / "index.html").read_text()
html = html.replace("fetch('/api/data')", "fetch('snapshot.json')")
html = html.replace("</style>", "  #addAcct, #refreshBtn, .edit { display: none; }\n</style>")
(SITE / "index.html").write_text(html)

n = len(snapshot["data"].get("accounts", {}))
print(f"site/ built: {n} account(s), snapshot {len(json.dumps(snapshot)):,} bytes")
