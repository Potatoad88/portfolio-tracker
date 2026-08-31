#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "Missing .env. Copy .env.example and add your Tiger credentials."
  exit 1
fi

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "Missing .venv. Follow the setup steps in README.md first."
  exit 1
fi

(
  set -a
  . "$PROJECT_DIR/.env"
  set +a
  export SSL_CERT_FILE="$($PROJECT_DIR/.venv/bin/python -m certifi)"
  exec "$PROJECT_DIR/.venv/bin/uvicorn" main:app --app-dir "$PROJECT_DIR/backend" --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm --prefix "$PROJECT_DIR/frontend" run dev -- --host 127.0.0.1
