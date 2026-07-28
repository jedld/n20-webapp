"""Shared keys and paths for deduplicated NPC line synthesis."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional


def synthesis_cache_key(
    npc_uid: str,
    text: str,
    emotion: Optional[str],
    instruct: Optional[str],
    volume: Optional[str],
) -> str:
  """Stable hash for one speakable NPC line (ignores per-listener gain)."""
  parts = [
      str(npc_uid or ""),
      str(text or ""),
      str(emotion or ""),
      str(instruct or ""),
      str(volume or ""),
  ]
  return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def cached_line_path(cache_dir: str, cache_key: str) -> Path:
    lines_dir = Path(cache_dir) / "lines"
    lines_dir.mkdir(parents=True, exist_ok=True)
    return lines_dir / f"{cache_key}.wav"


def line_cache_enabled() -> bool:
    return os.environ.get("N20_TTS_LINE_CACHE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
