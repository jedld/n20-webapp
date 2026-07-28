"""In-memory cache of completed async/stream TTS replies for HTTP polling fallback."""
from __future__ import annotations

import threading
from typing import Any, Optional

_lock = threading.Lock()
_ready_by_reply_id: dict[str, dict[str, Any]] = {}


def store_ready_reply(reply_id: str, payload: dict[str, Any]) -> None:
    token = str(reply_id or '').strip()
    if not token:
        return
    with _lock:
        _ready_by_reply_id[token] = dict(payload)


def get_ready_reply(reply_id: str) -> Optional[dict[str, Any]]:
    token = str(reply_id or '').strip()
    if not token:
        return None
    with _lock:
        payload = _ready_by_reply_id.get(token)
        return dict(payload) if payload else None


def clear_ready_reply(reply_id: str) -> None:
    token = str(reply_id or '').strip()
    if not token:
        return
    with _lock:
        _ready_by_reply_id.pop(token, None)
