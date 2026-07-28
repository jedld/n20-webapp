"""Load webapp environment for CLI tools (bake scripts, profile generators)."""

from __future__ import annotations

import os
from pathlib import Path


def load_webapp_dotenv() -> None:
    """Load ``webapp/.env`` when python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    webapp_dir = Path(__file__).resolve().parents[1]
    env_path = webapp_dir / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def configure_tts_cuda_device() -> None:
    """Mirror ``start_web.sh`` GPU pinning for standalone TTS scripts."""
    device = (os.environ.get("TTS_DEVICE") or "cpu").strip().lower()
    cuda_device = (os.environ.get("TTS_CUDA_DEVICE") or "").strip()
    if cuda_device and device not in ("cpu", ""):
        os.environ["CUDA_VISIBLE_DEVICES"] = cuda_device
    elif device == "cpu":
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
