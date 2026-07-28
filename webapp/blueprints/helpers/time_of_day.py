"""Web-layer helpers for campaign day/night cycles."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from natural20.time_of_day import (
    load_time_of_day_config,
    resolve_map_background_image,
    resolve_time_of_day,
    world_time_context_lines,
)

logger = logging.getLogger(__name__)

STATE_KEY = 'time_of_day'


def _session_state(game) -> Dict[str, Any]:
    session = getattr(game, 'game_session', None)
    state = getattr(session, 'session_state', None)
    if not isinstance(state, dict):
        state = {}
        if session is not None:
            session.session_state = state
    return state


def get_time_of_day_config(game) -> Dict[str, Any]:
    session = getattr(game, 'game_session', None)
    props = getattr(session, 'game_properties', None) or {}
    return load_time_of_day_config(props)


def get_time_of_day_state(game) -> Any:
    session = getattr(game, 'game_session', None)
    game_time = int(getattr(session, 'game_time', 0) or 0)
    config = get_time_of_day_config(game)
    override = (_session_state(game).get(STATE_KEY) or {}).get('override_phase_id')
    return resolve_time_of_day(game_time, config, override_phase_id=override)


def set_time_of_day_override(game, phase_id: Optional[str] = None) -> Dict[str, Any]:
    state = _session_state(game)
    block = dict(state.get(STATE_KEY) or {})
    if phase_id:
        block['override_phase_id'] = str(phase_id)
    else:
        block.pop('override_phase_id', None)
    state[STATE_KEY] = block
    game.game_session.session_state = state
    apply_time_of_day_to_maps(game)
    return get_time_of_day_state(game).to_dict()


def background_path_for_map(battle_map, state) -> Optional[str]:
    filename = resolve_map_background_image(getattr(battle_map, 'properties', {}) or {}, state)
    if filename:
        return f'maps/{filename}'
    map_name = getattr(battle_map, 'name', None) or 'index'
    return f'maps/{map_name}.png'


def apply_time_of_day_to_maps(game) -> None:
    """Recompute outdoor ambient light on all loaded maps."""
    state = get_time_of_day_state(game)
    illumination = float(state.illumination)
    maps = getattr(game, 'maps', None) or {}
    for battle_map in maps.values():
        try:
            if hasattr(battle_map, 'apply_outdoor_ambient_illumination'):
                battle_map.apply_outdoor_ambient_illumination(illumination)
        except Exception:
            logger.exception('Failed to apply time-of-day lighting on map %s', getattr(battle_map, 'name', '?'))


def time_of_day_turn_payload(game) -> Dict[str, Any]:
    state = get_time_of_day_state(game)
    payload = state.to_dict()
    try:
        battle_map = game.get_map_for_user('dm')
    except Exception:
        battle_map = None
    if battle_map is None:
        maps = getattr(game, 'maps', None) or {}
        battle_map = next(iter(maps.values()), None)
    if battle_map is not None:
        bg = background_path_for_map(battle_map, state)
        if bg:
            payload['background'] = f'assets/{bg}'
    return payload


def notify_time_of_day_change(game, *, refresh_map: bool = True) -> None:
    if getattr(game, 'realtime_ui_suppressed', lambda: False)():
        return
    socketio = getattr(game, 'socketio', None)
    if socketio is None:
        return
    try:
        if refresh_map:
            socketio.emit('message', {'type': 'refresh_map'})
    except Exception:
        logger.exception('Failed to emit time-of-day refresh')


def on_game_time_changed(game, *, old_time: int, new_time: int) -> bool:
    """Apply lighting side effects; return True if phase changed."""
    config = get_time_of_day_config(game)
    if not config.get('enabled', True):
        return False

    override = (_session_state(game).get(STATE_KEY) or {}).get('override_phase_id')
    old_state = resolve_time_of_day(int(old_time or 0), config, override_phase_id=override)
    new_state = resolve_time_of_day(int(new_time or 0), config, override_phase_id=override)

    apply_time_of_day_to_maps(game)

    phase_changed = old_state.phase_id != new_state.phase_id
    if phase_changed or old_state.illumination != new_state.illumination:
        notify_time_of_day_change(game, refresh_map=True)
    return phase_changed


def world_time_prompt_block(game) -> str:
    state = get_time_of_day_state(game)
    lines = world_time_context_lines(state)
    return '\n' + '\n'.join(lines) + '\n'
