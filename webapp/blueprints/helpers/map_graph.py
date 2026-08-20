"""Build campaign map connectivity graphs from teleporter-style fixtures."""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

MAP_GRAPH_LAYOUT_FILE = 'map_graph.yml'


def _coord(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'non-finite map graph coordinate: {value!r}')
    rounded = round(number, 1)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def normalize_map_graph_positions(raw) -> Dict[str, Dict[str, float]]:
    """Coerce a positions mapping to ``{map_id: {x, y}}``."""
    if not isinstance(raw, dict):
        return {}
    nested = raw.get('positions')
    source = nested if isinstance(nested, dict) else raw
    positions: Dict[str, Dict[str, float]] = {}
    for map_id, value in source.items():
        name = str(map_id or '').strip()
        if not name or not isinstance(value, dict):
            continue
        try:
            positions[name] = {
                'x': _coord(value.get('x')),
                'y': _coord(value.get('y')),
            }
        except (TypeError, ValueError):
            continue
    return positions


def map_graph_layout_path(session) -> str:
    root = getattr(session, 'root_path', None) or '.'
    return os.path.join(root, MAP_GRAPH_LAYOUT_FILE)


def load_map_graph_layout(session) -> Dict[str, Dict[str, float]]:
    """Read DM-arranged Map Connections positions from campaign ``map_graph.yml``."""
    path = map_graph_layout_path(session)
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
        with open(path, 'r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return {}
    return normalize_map_graph_positions(data)


def save_map_graph_layout(session, positions) -> Dict[str, Dict[str, float]]:
    """Persist Map Connections tile positions to campaign ``map_graph.yml``."""
    import yaml

    normalized = normalize_map_graph_positions(positions)
    path = map_graph_layout_path(session)
    payload = {'positions': normalized}
    with open(path, 'w', encoding='utf-8') as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return normalized


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
        if not target_map:
            continue
        target_pos = getattr(obj, 'target_position', None)
        party_travel = False
        try:
            from natural20.item_library.teleporter import is_party_travel_object
            party_travel = is_party_travel_object(obj)
        except Exception:
            party_travel = False
        if target_pos is None and not party_travel:
            continue
        try:
            from_pos = [int(pos[0]), int(pos[1])]
            if target_pos is None:
                to_pos = [0, 0]
            else:
                to_pos = [int(target_pos[0]), int(target_pos[1])]
        except (TypeError, ValueError, IndexError):
            continue
        yield {
            'from_pos': from_pos,
            'to_map': str(target_map),
            'to_pos': to_pos,
            'label': _object_label(obj),
            'kind': 'party_teleporter' if party_travel else _object_kind(obj),
            'entity_uid': getattr(obj, 'entity_uid', None),
            'party_travel': party_travel,
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
                party_travel = bool(transition.get('party_travel'))
                if (
                    not party_travel
                    and session is not None
                    and hasattr(session, 'same_map_set')
                    and not session.same_map_set(src_name, dest)
                ):
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

    campaign = ''
    try:
        campaign = os.path.basename(os.path.abspath(getattr(session, 'root_path', '') or ''))
    except Exception:
        campaign = ''

    return {
        'current_map': current_map_name,
        'campaign': campaign,
        'nodes': nodes,
        'edges': edges,
        'positions': load_map_graph_layout(session),
    }
