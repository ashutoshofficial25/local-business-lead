#!/bin/sh
# Start MongoDB (Docker), set up the venv, and run the web app:  ./run.sh
set -e
cd "$(dirname "$0")"
docker compose up -d 2>/dev/null || echo "Docker not available - using MongoDB at MONGO_URI from .env"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python app.py
