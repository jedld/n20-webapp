#!/bin/sh
# Start Natural20 webapp for remote play via ngrok.
# Usage: ./start_ngrok.sh [--edit] <campaign_dir>
# Example: ./start_ngrok.sh ../user_levels/wild_sheep_chase

WEBAPP_DIR="$(cd "$(dirname "$0")" && pwd)"

export N20_USE_MINIFIED_ASSETS=1
export FLASK_DEBUG=0
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5000,http://127.0.0.1:5000,http://localhost:5001,http://127.0.0.1:5001,https://*.ngrok.io,https://*.ngrok-free.app,https://*.ngrok-free.dev,http://*.ngrok-free.dev}"

exec "$WEBAPP_DIR/start_web.sh" "$@"
