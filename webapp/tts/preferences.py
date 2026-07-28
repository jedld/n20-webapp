"""Campaign-level TTS delivery mode (async background vs stream vs manual)."""
from __future__ import annotations

import os
from typing import Any, Optional

_VALID_MODES = frozenset({'async', 'stream', 'manual', 'sync'})

# Set at bootstrap / save load from campaign game.yml
_campaign_delivery_mode: Optional[str] = None


def default_delivery_mode() -> str:
    """Env fallback when campaign game.yml does not set a mode."""
    mode = os.environ.get('TTS_DELIVERY', 'async').strip().lower()
    if mode in ('sync', 'blocking', 'inline'):
        return 'sync'
    if mode in ('manual', 'on_demand', 'on-demand', 'click'):
        return 'manual'
    if mode in ('stream', 'streaming'):
        return 'stream'
    return 'async'


def normalize_delivery_mode(mode: Optional[str]) -> Optional[str]:
    if mode is None:
        return None
    value = str(mode).strip().lower()
    if value in ('streaming',):
        return 'stream'
    if value in ('on_demand', 'on-demand', 'click'):
        return 'manual'
    if value in _VALID_MODES:
        return value
    return None


def campaign_delivery_mode_from_game(game_properties: Optional[dict[str, Any]]) -> Optional[str]:
    """Read ``tts_delivery`` or ``tts.delivery`` from campaign ``game.yml``."""
    if not isinstance(game_properties, dict):
        return None
    tts_block = game_properties.get('tts')
    if isinstance(tts_block, dict):
        for key in ('delivery', 'delivery_mode', 'mode'):
            resolved = normalize_delivery_mode(tts_block.get(key))
            if resolved:
                return resolved
    return normalize_delivery_mode(game_properties.get('tts_delivery'))


def refresh_campaign_delivery_mode(game_properties: Optional[dict[str, Any]] = None) -> str:
    """Apply campaign YAML to the in-process delivery mode cache."""
    global _campaign_delivery_mode
    if game_properties is None:
        game_properties = _game_properties_from_session()
    resolved = campaign_delivery_mode_from_game(game_properties)
    _campaign_delivery_mode = resolved
    return get_delivery_mode()


def _game_properties_from_session() -> dict[str, Any]:
    try:
        from webapp.blueprints.helpers.runtime_state import get_game_session

        session = get_game_session()
        props = getattr(session, 'game_properties', None)
        return props if isinstance(props, dict) else {}
    except Exception:
        return {}


def get_delivery_mode(username: Optional[str] = None) -> str:
    """Return the campaign TTS delivery mode (``username`` ignored; kept for API compat)."""
    del username
    if _campaign_delivery_mode:
        return _campaign_delivery_mode
    from_game = campaign_delivery_mode_from_game(_game_properties_from_session())
    if from_game:
        return from_game
    return default_delivery_mode()


def available_delivery_modes() -> list[str]:
    """Modes valid in campaign ``game.yml`` (excludes legacy sync from player docs)."""
    return ['async', 'stream', 'manual']
