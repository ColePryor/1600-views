#!/bin/zsh
# Scheduled refresh: scrape TikTok, rebuild the static site, deploy to Vercel.
# Run 4x/day by launchd (com.colepryor.1600-views-refresh) at 11:30, 15:30,
# 19:30, 23:30, each just before a views_texter send; logs to refresh.log.
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
