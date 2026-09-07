#!/bin/zsh
# Manual refresh: scrape TikTok, rebuild the static site, deploy to Vercel.
# Nothing schedules this. Run it from the local dashboard's Refresh button
# (http://localhost:1616) or by hand: ./refresh.sh. Logs to refresh.log.
set -e
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd /Users/colepryor/1600-views

echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh start (manual)"
./venv/bin/python fetch.py
./venv/bin/python publish.py
(cd site && npx vercel --prod --yes)

# git is best-effort; the deploy already happened
git add -A || true
if ! git diff --cached --quiet; then
  git commit -qm "Scheduled refresh" && git push -q || echo "git push skipped"
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh done"
