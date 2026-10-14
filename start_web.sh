#!/bin/sh
# Standalone webapp launcher (n20-webapp repository).
WEBAPP_DIR="$(cd "$(dirname "$0")/webapp" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$WEBAPP_DIR/.env" ]; then
    set -a
    . "$WEBAPP_DIR/.env"
    set +a
fi

EDIT_MODE=0
RESET_SAVES=0
NEW_CAMPAIGN=0
# Optional flags (accepted in any position):
#   --edit   DM sessions start in campaign edit mode after login.
#   --reset  wipe persisted save state at boot (saves, logs, notebook, NPC memories).
#   --new    create the campaign directory with starter files (index.json, game.yml,
#            one empty map, DM login dm/admin) when it does not exist yet.
# The first non-flag argument is the campaign directory (or slug).
CAMPAIGN_ARG=""
for arg in "$@"; do
    case "$arg" in
        --edit)  EDIT_MODE=1 ;;
        --reset) RESET_SAVES=1 ;;
        --new)   NEW_CAMPAIGN=1 ;;
        *)
            if [ -z "$CAMPAIGN_ARG" ]; then
                CAMPAIGN_ARG="$arg"
            fi
            ;;
    esac
done
if [ -n "$CAMPAIGN_ARG" ]; then
    TEMPLATE_DIR="$CAMPAIGN_ARG"
else
    TEMPLATE_DIR="${TEMPLATE_DIR:-../natural_20.py/templates}"
fi
export N20_EDIT_MODE="$EDIT_MODE"
export N20_RESET_SAVES="$RESET_SAVES"

PORT="${FLASK_RUN_PORT:-${PORT:-5001}}"
export FLASK_RUN_PORT="$PORT"
export FLASK_RUN_HOST="${FLASK_RUN_HOST:-0.0.0.0}"
export FLASK_DEBUG=0
export N20_BOOT_ID="${N20_BOOT_ID:-boot-$(date +%s)-$$}"

N20_PYTHON="${N20_PYTHON:-python}"
PARENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# When n20-webapp is a submodule inside the engine repo, add engine root for `import natural20`.
if [ -d "$PARENT_DIR/natural20" ]; then
  ENGINE_ROOT="$PARENT_DIR"
else
  ENGINE_ROOT=""
fi
if [ -n "$ENGINE_ROOT" ]; then
  export PYTHONPATH="$ENGINE_ROOT:$REPO_ROOT:$WEBAPP_DIR${PYTHONPATH:+:$PYTHONPATH}"
  export N20_CAMPAIGNS_DIR="${N20_CAMPAIGNS_DIR:-$ENGINE_ROOT/user_levels}"
else
  export PYTHONPATH="$REPO_ROOT:$WEBAPP_DIR${PYTHONPATH:+:$PYTHONPATH}"
fi

if [ "$NEW_CAMPAIGN" = "1" ]; then
  if [ -z "$CAMPAIGN_ARG" ]; then
    echo "--new needs a campaign directory or slug, e.g. ./start_web.sh --new ../user_levels/my_campaign" >&2
    exit 2
  fi
  case "$TEMPLATE_DIR" in
    */*) NEW_CAMPAIGN_PATH="$TEMPLATE_DIR" ;;
    *)   NEW_CAMPAIGN_PATH="${N20_CAMPAIGNS_DIR:-.}/$TEMPLATE_DIR" ;;
  esac
  "$N20_PYTHON" -m natural20.campaign_scaffold --if-missing "$NEW_CAMPAIGN_PATH" || exit 1
  TEMPLATE_DIR="$(cd "$NEW_CAMPAIGN_PATH" && pwd)"
fi

# Resolve TEMPLATE_DIR relative to engine root, campaigns dir, webapp repo, or slug.
case "$TEMPLATE_DIR" in
  /*) ;;
  *)
    # Strip trailing slashes for consistent matching.
    TEMPLATE_DIR="${TEMPLATE_DIR%/}"
    if [ -n "${N20_CAMPAIGNS_DIR:-}" ] && [ -f "$N20_CAMPAIGNS_DIR/$TEMPLATE_DIR/index.json" ]; then
      TEMPLATE_DIR="$(cd "$N20_CAMPAIGNS_DIR/$TEMPLATE_DIR" && pwd)"
    elif [ -n "$ENGINE_ROOT" ] && [ -f "$ENGINE_ROOT/$TEMPLATE_DIR/index.json" ]; then
      TEMPLATE_DIR="$(cd "$ENGINE_ROOT/$TEMPLATE_DIR" && pwd)"
    elif [ -n "${N20_CAMPAIGNS_DIR:-}" ] && [ -f "$N20_CAMPAIGNS_DIR/$(basename "$TEMPLATE_DIR")/index.json" ]; then
      TEMPLATE_DIR="$(cd "$N20_CAMPAIGNS_DIR/$(basename "$TEMPLATE_DIR")" && pwd)"
    elif [ -n "$ENGINE_ROOT" ] && [ -f "$ENGINE_ROOT/templates/index.json" ] && [ "$TEMPLATE_DIR" = "../natural_20.py/templates" ]; then
      TEMPLATE_DIR="$(cd "$ENGINE_ROOT/templates" && pwd)"
    elif [ -d "$REPO_ROOT/$TEMPLATE_DIR" ]; then
      TEMPLATE_DIR="$(cd "$REPO_ROOT/$TEMPLATE_DIR" && pwd)"
    elif [ -d "$WEBAPP_DIR/$TEMPLATE_DIR" ]; then
      TEMPLATE_DIR="$(cd "$WEBAPP_DIR/$TEMPLATE_DIR" && pwd)"
    fi
    ;;
esac
export TEMPLATE_DIR="$TEMPLATE_DIR"

cd "$REPO_ROOT" || exit 1

if [ "${N20_USE_GUNICORN:-}" = "1" ]; then
  exec "$N20_PYTHON" -m gunicorn \
    --chdir "$WEBAPP_DIR" \
    --worker-class eventlet \
    --workers 1 \
    --bind "$FLASK_RUN_HOST:$PORT" \
    --timeout 120 \
    app:app
fi

exec "$N20_PYTHON" -m webapp.app
