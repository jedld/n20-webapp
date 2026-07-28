"""Shared helpers for pickpocket modal/UI rendering."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from flask import request

from natural20.event_manager import format_pickpocket_roll_text


def pickpocket_attempt_from_request() -> Optional[Dict[str, Any]]:
    """Parse a serialized pickpocket attempt from GET query parameters."""
    raw = request.args.get('pickpocket_attempt')
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    opts_raw = request.args.get('opts')
    if not opts_raw:
        return None
    try:
        opts = json.loads(opts_raw) if isinstance(opts_raw, str) else opts_raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(opts, dict):
        return None
    attempt = opts.get('pickpocket_attempt')
    return attempt if isinstance(attempt, dict) else None


def pickpocket_attempt_roll_summary(attempt: Optional[Dict[str, Any]]) -> str:
    """Human-readable Sleight of Hand vs passive Insight line for templates."""
    if not isinstance(attempt, dict):
        return ''
    return format_pickpocket_roll_text(attempt).strip().lstrip('(').rstrip(')')
