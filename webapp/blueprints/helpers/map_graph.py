"""Build campaign map connectivity graphs from teleporter-style fixtures."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _object_kind(obj) -> str:
    try:
        return str(getattr(obj, 'object_type', None) or type(obj).__name__).lower()
    except Exception:
        return 'object'


def _object_label(obj) -> str:
    for attr in ('name', 'label'):
        try:
            value = getattr(obj, attr, None)
            if callable(value):
                value = value()
            if value:
                return str(value)
        except Exception:
            continue
    uid = getattr(obj, 'entity_uid', None)
    return str(uid or 'link')


def _serialize_map_node(name: str, battle_map, *, is_current: bool = False) -> Dict[str, Any]:
    size = list(getattr(battle_map, 'size', None) or [0, 0])
    description = ''
    try:
        description = str(battle_map.properties.get('description') or '')
    except Exception:
        description = ''

    return {
        'id': name,
        'label': name,
        'description': description,
        'background_url': f'/assets/maps/{name}.png',
        'width': int(size[0] or 0),
        'height': int(size[1] or 0),
        'is_current': bool(is_current),
    }


def _iter_map_transitions(battle_map):
    try:
        items = list(battle_map.interactable_objects.items())
    except Exception:
        return

    for obj, pos in items:
        target_map = getattr(obj, 'target_map', None)
        target_pos = getattr(obj, 'target_position', None)
        if not target_map or target_pos is None:
            continue
        try:
            from_pos = [int(pos[0]), int(pos[1])]
            to_pos = [int(target_pos[0]), int(target_pos[1])]
        except (TypeError, ValueError, IndexError):
            continue
        yield {
            'from_pos': from_pos,
            'to_map': str(target_map),
            'to_pos': to_pos,
            'label': _object_label(obj),
            'kind': _object_kind(obj),
            'entity_uid': getattr(obj, 'entity_uid', None),
        }


def build_campaign_map_graph(
    current_game,
    *,
    current_map_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Return nodes (maps) and edges (teleporter / trap-door links) for DM graph view."""
    session = current_game.game_session
    maps = getattr(session, 'maps', None) or {}

    if current_map_name is None:
        try:
            username = getattr(current_game, 'current_username', None)
            if username and hasattr(current_game, 'get_map_for_user'):
                battle_map = current_game.get_map_for_user(username)
                if battle_map is not None:
                    current_map_name = battle_map.name
        except Exception:
            current_map_name = None

    nodes: List[Dict[str, Any]] = []
    for name in sorted(maps.keys()):
        battle_map = maps[name]
        nodes.append(
            _serialize_map_node(
                name,
                battle_map,
                is_current=(name == current_map_name),
            )
        )

    edges: List[Dict[str, Any]] = []
    edge_index = 0
    session = current_game.game_session
    for src_name, battle_map in maps.items():
        for transition in _iter_map_transitions(battle_map):
            dest = transition['to_map']
            try:
                if session is not None and hasattr(session, 'same_map_set') and not session.same_map_set(src_name, dest):
                    continue
            except Exception:
                pass
            edge_index += 1
            edges.append({
                'id': f'{src_name}:{edge_index}',
                'from_map': src_name,
                'from_pos': transition['from_pos'],
                'to_map': dest,
                'to_pos': transition['to_pos'],
                'label': transition['label'],
                'kind': transition['kind'],
                'entity_uid': transition['entity_uid'],
            })

    return {
        'current_map': current_map_name,
        'nodes': nodes,
        'edges': edges,
    }
