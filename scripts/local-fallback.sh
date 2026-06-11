#!/usr/bin/env bash
# Local fallback for the Letterboxd sync.
#
# GitHub-hosted runners sometimes land on Cloudflare-flagged IPs and the
# scheduled sync fails with a 403 (see scrape.py). This script is meant to run
# daily from a residential IP (e.g. via launchd). If the most recent GitHub
# Actions sync run on main failed, it performs the sync locally in a dedicated
# clone and pushes the result. "[skip ci]" keeps that push from triggering the
# workflow again.
#
# Requires: gh (authenticated), git configured with the gh credential helper.

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

REPO="CoandaEffect/letterboxd-sync"
CLONE_DIR="${HOME}/.cache/letterboxd-sync-fallback"

conclusion=$(gh run list --repo "$REPO" --workflow sync.yml --branch main --limit 1 \
  --json conclusion --jq '.[0].conclusion')
if [[ "$conclusion" != "failure" ]]; then
  echo "$(date '+%F %T') Latest sync run concluded '${conclusion:-in progress}'; nothing to do."
  exit 0
fi

echo "$(date '+%F %T') Latest sync run failed; running local fallback sync..."

if [[ ! -d "$CLONE_DIR/.git" ]]; then
  git clone "https://github.com/${REPO}.git" "$CLONE_DIR"
fi
cd "$CLONE_DIR"
git checkout -q main
git fetch -q origin main
git reset -q --hard origin/main

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python scrape.py

git add letterboxd-watched.csv
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git -c user.name="letterboxd-sync fallback" \
      -c user.email="andrew.wilterson@gmail.com" \
      commit -m "[skip ci] Update watched films $(date -u +%Y-%m-%d) (local fallback)"
  GIT_TERMINAL_PROMPT=0 git push origin main
fi
