# 1600 Creator HQ: TikTok views tracker

Tracks per-video views and likes across the 1600.tech creator accounts and renders a dashboard. Live snapshot: https://1600-views.vercel.app

## How it works

- `fetch.py`: opens each handle in `data/accounts.json` in a real Chrome window (Playwright, persistent profile in `data/chrome-profile/`, gitignored) and intercepts TikTok's `item_list` responses, so counts are exact with no login. Writes `data/data.json` and appends daily totals to `data/history.json`.
- `server.py`: local dashboard server for `index.html`.
- `publish.py`: copies a snapshot into `site/` for the public Vercel deploy (`npx vercel` from `site/`).
- `refresh.sh`: fetch -> publish -> `vercel --prod` -> git push. Nothing runs it on a schedule (the launchd job is disabled, plist parked at `com.colepryor.1600-views-refresh.plist.disabled`). Refreshing is manual: start `server.py`, open http://localhost:1616 and click "Refresh & publish", or run `./refresh.sh` in a terminal. Logs go to `refresh.log`. The public page has no Refresh button, so it only changes when you run this. Note: `fetch.py` opens a visible Chrome window each run.

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

## Owners and splits

The **Accounts** button (top right of the local dashboard) opens one sheet that does all of it: add an account, assign it, split it between people, rename a person, remove an account. Clicking a handle anywhere on the page opens the same sheet on that account.

An account can be shared. Give each person a percentage and their views, likes, followers and "views today" divide that way everywhere: the creator table, the creator race, the filter chips and the hero numbers. Percentages are relative, so 3 and 1 is the same as 75 and 25; they are scaled to 100 on save. Videos stay whole (a clip is a clip), and avg views per video uses the weighted count so a 50% owner still sees the account's real average.

`data/owners.json` is the store and stays hand-editable: `"handle": "Name"` for a sole owner, `"handle": {"A": 60, "B": 40}` for a split. Names become filter chips at the top of the dashboard.

Removing an account takes two clicks (the button arms, then confirms). Adding one pulls just that handle and does not publish; the live site only changes when you press **Refresh & publish**.
