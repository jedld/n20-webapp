#!/bin/sh

WEBAPP_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$WEBAPP_DIR/.." && pwd)"

# Load .env file if it exists (provides default values for all settings)
if [ -f "$WEBAPP_DIR/.env" ]; then
    set -a
    . "$WEBAPP_DIR/.env"
    set +a
fi

# Use command-line arguments: optional --edit, then TEMPLATE_DIR
EDIT_MODE=0
if [ -n "$1" ] && [ "$1" = "--edit" ]; then
    EDIT_MODE=1
    shift
fi
if [ -n "$1" ]; then
    TEMPLATE_DIR="$1"
else
    TEMPLATE_DIR="../templates"
fi
export N20_EDIT_MODE="$EDIT_MODE"

PORT="${FLASK_RUN_PORT:-${PORT:-5001}}"
export FLASK_RUN_PORT="$PORT"
export FLASK_RUN_HOST="${FLASK_RUN_HOST:-0.0.0.0}"
export FLASK_DEBUG=0
export N20_BOOT_ID="${N20_BOOT_ID:-boot-$(date +%s)-$$}"

# Prefer the dedicated CosyVoice env when present (base Python lacks modelscope etc.)
if [ -z "${N20_PYTHON:-}" ]; then
  if [ -x "${HOME}/miniforge3/envs/n20-tts/bin/python" ]; then
    N20_PYTHON="${HOME}/miniforge3/envs/n20-tts/bin/python"
  elif [ -x "${HOME}/miniconda3/envs/n20-tts/bin/python" ]; then
    N20_PYTHON="${HOME}/miniconda3/envs/n20-tts/bin/python"
  elif [ -x "$WEBAPP_DIR/../n20-tts/bin/python" ]; then
    N20_PYTHON="$(cd "$WEBAPP_DIR/../n20-tts" && pwd)/bin/python"
  else
    N20_PYTHON=python
  fi
fi

# NPC LLM defaults (override .env values if not already set via script args)
export NPC_LLM_ENABLED="${NPC_LLM_ENABLED:-1}"
export NPC_LLM_PROVIDER="${NPC_LLM_PROVIDER:-llamacpp}"
export NPC_BASE_URL="${NPC_BASE_URL:-http://localhost:8017}"
export NPC_MODEL="${NPC_MODEL:-gemma-4-26B-A4B-it-qat-GGUF}"
export LLAMA_CPP_BASE_URL="${LLAMA_CPP_BASE_URL:-http://jedld-strix.local:8000}"

# Ensure TTS defaults
export TTS_ENABLED="${TTS_ENABLED:-1}"
export TTS_PROVIDER="${TTS_PROVIDER:-cosyvoice}"
export TTS_DEVICE="${TTS_DEVICE:-cpu}"
# Low-latency stack (optional): run services/vllm-omni-tts/start_with_voices.sh,
# then set TTS_PROVIDER=qwen3_vllm, TTS_DELIVERY=stream, VLLM_OMNI_TTS_URL=http://127.0.0.1:8091
# Pin TTS to a specific GPU (e.g. TTS_CUDA_DEVICE=1 for a second Blackwell card).
if [ -n "${TTS_CUDA_DEVICE:-}" ] && [ "$TTS_DEVICE" != "cpu" ]; then
  export CUDA_VISIBLE_DEVICES="$TTS_CUDA_DEVICE"
fi
if [ "$TTS_DEVICE" = "gpu" ] || [ "$TTS_DEVICE" = "cuda" ] || [ "${TTS_DEVICE#cuda:}" != "$TTS_DEVICE" ]; then
  "$N20_PYTHON" -c "
import torch
if not torch.cuda.is_available():
    raise SystemExit(1)
idx = 0
d = '${TTS_DEVICE}'.lower()
if d.startswith('cuda:'):
    idx = int(d.split(':', 1)[1])
major, minor = torch.cuda.get_device_capability(idx)
sm = f'sm_{major}{minor}'
arch = torch.cuda.get_arch_list()
if arch and sm not in arch:
    raise SystemExit(1)
" 2>/dev/null || export TTS_DEVICE=cpu
fi
# CosyVoice ships with a Chinese reference clip; default NPCs to American English
# unless YAML sets voice.accent. Use TTS_DEFAULT_ACCENT=none for model-native.
export TTS_DEFAULT_ACCENT="${TTS_DEFAULT_ACCENT:-american}"
if [ "$TTS_DEVICE" = "cpu" ]; then
  export CUDA_VISIBLE_DEVICES=""
fi

# CosyVoice runtime deps that are easy to miss in the n20-tts env
"$N20_PYTHON" -c "import pkg_resources" 2>/dev/null || "$N20_PYTHON" -m pip install 'setuptools<81' >/dev/null 2>&1 || true
"$N20_PYTHON" -c "import pyarrow" 2>/dev/null || "$N20_PYTHON" -m pip install pyarrow >/dev/null 2>&1 || true
# numba 0.66+ (pulled in by openai-whisper) expects coverage.types; without it
# CosyVoice/OpenVoice imports fail with "module 'coverage' has no attribute 'types'".
"$N20_PYTHON" -c "import coverage.types" 2>/dev/null || "$N20_PYTHON" -m pip install 'coverage>=7.0' >/dev/null 2>&1 || true
"$N20_PYTHON" -c "import soundfile" 2>/dev/null || "$N20_PYTHON" -m pip install soundfile >/dev/null 2>&1 || true
"$N20_PYTHON" -c "import hyperpyyaml" 2>/dev/null || "$N20_PYTHON" -m pip install hyperpyyaml >/dev/null 2>&1 || true
# Real OpenCV is required; a blank cv2 stub (sitecustomize under pytest) breaks CosyVoice.
# Pin OpenCV 4.x + NumPy 1.26: OpenCV 5 wants NumPy 2.x which breaks onnxruntime 1.18.
"$N20_PYTHON" -c "import cv2, numpy; assert cv2.__spec__ is not None and numpy.__version__.startswith('1.')" 2>/dev/null \
  || "$N20_PYTHON" -m pip install 'opencv-python-headless==4.10.0.84' 'numpy==1.26.4' >/dev/null 2>&1 || true

export PYTHONPATH="$ROOT_DIR:$WEBAPP_DIR:$ROOT_DIR/third_party/CosyVoice:$ROOT_DIR/third_party/CosyVoice/third_party/Matcha-TTS"

# TEMPLATE_DIR has historically been relative to webapp/ (e.g. ../user_levels/foo).
# Resolve to an absolute path so campaign assets work when cwd is the repo root.
case "$TEMPLATE_DIR" in
  /*) ;;
  *)
    if [ -d "$WEBAPP_DIR/$TEMPLATE_DIR" ]; then
      TEMPLATE_DIR="$(cd "$WEBAPP_DIR/$TEMPLATE_DIR" && pwd)"
    elif [ -d "$ROOT_DIR/$TEMPLATE_DIR" ]; then
      TEMPLATE_DIR="$(cd "$ROOT_DIR/$TEMPLATE_DIR" && pwd)"
    fi
    ;;
esac
export TEMPLATE_DIR="$TEMPLATE_DIR"

cd "$ROOT_DIR" || exit 1

# Run as a package module so only one app instance exists (never `cd webapp && python app.py`,
# which registers the app as `app` while background workers import `webapp.app` and reboot).
# Optional: N20_USE_GUNICORN=1 for production-style single worker (no dev server at all).
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
