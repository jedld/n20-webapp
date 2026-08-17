"""DM map-set operations: activate, create, assign, place party, persist."""

from __future__ import annotations

import re
from typing import Any, Optional

from natural20.map_set import (
    ROOT_MAP_SET_ID,
    assignment_blockers,
    is_valid_map_set_id,
    persist_map_sets_to_game_yml,
    place_party_on_map,
    slug_map_set_id,
)
from natural20.player_character import PlayerCharacter


def map_sets_payload(current_game, current_map_name: Optional[str] = None) -> dict[str, Any]:
    session = current_game.game_session
    registry = getattr(session, 'map_sets', None)
    active = getattr(session, 'active_map_set', ROOT_MAP_SET_ID)
    sets = []
    if registry is None:
        maps = list((getattr(current_game, 'maps', {}) or {}).keys())
        sets.append({
            'id': ROOT_MAP_SET_ID,
            'label': 'Root',
            'active': True,
            'maps': [{'name': name, 'current': name == current_map_name} for name in maps],
        })
        return {'active': ROOT_MAP_SET_ID, 'sets': sets}

    for item in registry.all_sets():
        sets.append({
            'id': item.id,
            'label': item.label,
            'active': item.id == active,
            'maps': [
                {'name': name, 'current': name == current_map_name}
                for name in item.maps
                if name in (current_game.maps or {})
            ],
        })
    return {'active': active, 'sets': sets}


def _emit_switch_map(current_game, username: str, map_name: str, entity_uid=None):
    socketio = getattr(current_game, 'socketio', None)
    if socketio is None:
        return
    sids = (getattr(current_game, 'username_to_sid', {}) or {}).get(username, [])
    payload = {'type': 'switch_map', 'message': {'map': map_name, 'entity_uid': entity_uid}}
    for sid in sids:
        try:
            socketio.emit('message', payload, to=sid)
        except Exception:
            pass


def activate_map_set(current_game, set_id: str) -> dict[str, Any]:
    session = current_game.game_session
    registry = getattr(session, 'map_sets', None)
    if registry is None or registry.get(set_id) is None:
        raise ValueError(f'Unknown map set: {set_id}')

    battle = current_game.get_current_battle()
    if battle is not None and getattr(battle, 'started', False):
        raise ValueError('Cannot activate a map set while a battle is in progress')

    session.active_map_set = set_id
    skipped = []
    switched = []
    usernames = list((getattr(current_game, 'username_to_sid', {}) or {}).keys())
    pov_map = getattr(current_game, 'pov_entity_for_user', {}) or {}
    for username in set(usernames) | set(pov_map.keys()):
        entity = current_game.get_pov_entity_for_user(username)
        if entity is None:
            continue
        entity_map = current_game.get_map_for_entity(entity)
        if entity_map is None:
            skipped.append({
                'username': username,
                'entity_uid': getattr(entity, 'entity_uid', None),
                'reason': f'no token on map set {set_id}',
            })
            continue
        current_game.switch_map_for_user(username, entity_map.name)
        _emit_switch_map(current_game, username, entity_map.name, getattr(entity, 'entity_uid', None))
        switched.append({
            'username': username,
            'map': entity_map.name,
            'entity_uid': getattr(entity, 'entity_uid', None),
        })
    return {
        'active': set_id,
        'switched': switched,
        'skipped': skipped,
    }


def create_map_set(current_game, set_id: str, label: Optional[str] = None) -> dict[str, Any]:
    session = current_game.game_session
    set_id = slug_map_set_id(set_id)
    if not is_valid_map_set_id(set_id):
        raise ValueError(f'Invalid map set id: {set_id!r}')
    registry = getattr(session, 'map_sets', None)
    if registry is None:
        from natural20.map_set import MapSetRegistry
        session.map_sets = MapSetRegistry.from_game_config(session.game_properties, session.maps)
        registry = session.map_sets
    if registry.get(set_id) is not None:
        raise ValueError(f'Map set already exists: {set_id}')
    registry.ensure_set(set_id, label=label or set_id.replace('_', ' ').title())
    persist_map_sets_to_game_yml(session)
    return {'id': set_id, 'label': registry.get(set_id).label}


def assign_map_to_set(current_game, map_name: str, set_id: str) -> dict[str, Any]:
    session = current_game.game_session
    set_id = slug_map_set_id(set_id)
    if map_name not in (session.maps or {}):
        raise ValueError(f'Unknown map: {map_name}')
    if not is_valid_map_set_id(set_id):
        raise ValueError(f'Invalid map set id: {set_id!r}')
    registry = session.map_sets
    registry.ensure_set(set_id)
    blockers = assignment_blockers(session, map_name, set_id)
    if blockers:
        raise ValueError('; '.join(blockers))
    registry.assign_map(map_name, set_id)
    session._link_maps_within_sets()
    persist_map_sets_to_game_yml(session)
    return {'map': map_name, 'map_set': set_id}


def place_party(current_game, map_name: str, x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
    session = current_game.game_session
    if map_name not in (session.maps or {}):
        raise ValueError(f'Unknown map: {map_name}')
    players = []
    seen = set()
    for entity in _known_player_characters(current_game):
        uid = str(getattr(entity, 'entity_uid', '') or '')
        if uid and uid not in seen:
            seen.add(uid)
            players.append(entity)
    placed = place_party_on_map(session, map_name, x=x, y=y, players=players)
    return {'map': map_name, 'placed': placed}


def _known_player_characters(current_game) -> list:
    session = current_game.game_session
    seen = set()
    out = []
    for battle_map in (session.maps or {}).values():
        for entity in list(getattr(battle_map, 'entities', {}).keys()):
            if not isinstance(entity, PlayerCharacter):
                continue
            uid = str(getattr(entity, 'entity_uid', '') or '')
            if uid and uid not in seen:
                seen.add(uid)
                out.append(entity)
    for deferred in (getattr(current_game, 'deferred_players', {}) or {}).values():
        entity = deferred.get('entity') if isinstance(deferred, dict) else None
        if not isinstance(entity, PlayerCharacter):
            continue
        uid = str(getattr(entity, 'entity_uid', '') or '')
        if uid and uid not in seen:
            seen.add(uid)
            out.append(entity)
    return out


_MAP_SET_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_\-]*$')
