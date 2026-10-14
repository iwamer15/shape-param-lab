#!/bin/bash
# shape-param-lab を起動（Mac / Linux）。初回はライブラリ導入と画面のビルドを自動で行う。
set -e
cd "$(dirname "$0")"
if [ ! -d frontend/dist ]; then
  (cd frontend && npm install && npm run build)
fi
cd backend
uv sync
( sleep 3 && open http://127.0.0.1:8200 2>/dev/null || xdg-open http://127.0.0.1:8200 2>/dev/null || true ) &
uv run uvicorn server:app --host 127.0.0.1 --port 8200
