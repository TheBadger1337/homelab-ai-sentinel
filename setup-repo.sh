#!/usr/bin/env bash
# Run this once to initialize the git repo and push to Forgejo.
# Usage: bash setup-repo.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
FORGEJO_URL="http://matthew-cassada@forgejo.home.internal/matthew-cassada/homelab-ai-sentinel.git"

cd "$REPO_DIR"

git init
git remote add origin "$FORGEJO_URL"
git add .
git commit -m "Initial MVP: Flask webhook server with Claude AI + Discord alerts

- POST /webhook accepts generic JSON and Uptime Kuma alert formats
- Normalizes alerts into a common NormalizedAlert struct
- Calls claude-sonnet-4-6 to generate AI Insight + Suggested Actions
- Posts a formatted Discord embed with color-coded severity
- GET /health endpoint for container healthcheck
- Dockerized with gunicorn; config via ANTHROPIC_API_KEY + DISCORD_WEBHOOK_URL env vars"

git push -u origin main

echo "Done! Repo initialized and pushed to Forgejo."
