# 1600 Creator HQ: TikTok views tracker

Tracks per-video views and likes across the 1600.tech creator accounts and renders a dashboard. Live snapshot: https://1600-views.vercel.app

## How it works

- `fetch.py`: opens each handle in `data/accounts.json` in a real Chrome window (Playwright, persistent profile in `data/chrome-profile/`, gitignored) and intercepts TikTok's `item_list` responses, so counts are exact with no login. Writes `data/data.json` and appends daily totals to `data/history.json`.
- `server.py`: local dashboard server for `index.html`.
- `publish.py`: copies a snapshot into `site/` for the public Vercel deploy (`npx vercel` from `site/`).
- `refresh_worker.py`: makes the Refresh button on the live site work. The page drops a request into Vercel Blob via `site/api/refresh.js`; this worker (run on the Mac, tmux `1600-views-worker`) picks it up, runs fetch -> publish -> deploy, and reports status back so the page reloads when the new snapshot is live. Needs `BLOB_READ_WRITE_TOKEN` in `site/.env.local` (`cd site && npx vercel env pull .env.local`).

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install playwright
./venv/bin/playwright install chromium
./venv/bin/python fetch.py            # all accounts
./venv/bin/python fetch.py handle     # one account, no @
./venv/bin/python server.py           # local dashboard
```

Add or remove creators by editing `data/accounts.json`.

Each handle can be assigned to the person who runs it: click a handle in the "Views by creator" table (or use `+ name` / `rename`), or edit `data/owners.json` (`{"handle": "Name"}`). Names become filter chips at the top and the table rolls views, likes, followers and videos up per person.
