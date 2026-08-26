#!/bin/zsh
# Scheduled refresh: scrape TikTok, rebuild the static site, deploy to Vercel.
# Run hourly by launchd (com.colepryor.1600-views-refresh); logs to refresh.log.
set -e
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd /Users/colepryor/1600-views

echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh start"
./venv/bin/python fetch.py
./venv/bin/python publish.py
(cd site && npx vercel --prod --yes)

# git is best-effort; the deploy already happened
git add -A || true
if ! git diff --cached --quiet; then
  git commit -qm "Scheduled refresh" && git push -q || echo "git push skipped"
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh done"
