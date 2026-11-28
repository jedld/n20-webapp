"""Shared logic for the DM Teleport-destination panel, MCP tool, and parity
harness. Thin wrapper over ``natural20.utils.teleport_destinations`` that
serializes destinations for the UI and validates maps/anchors.

Routes: ``GET/POST /dm/teleport_destinations`` (blueprints/dm.py, DM-only).
MCP: ``dm.teleport_destination`` (mcp/tools_dm.py).
"""

from __future__ import annotations

from natural20.utils.teleport_destinations import (
    DESTINATIONS_KEY,
    FAMILIARITY_LABELS,
    FAMILIARITY_LEVELS,
    find_destination,
    list_destinations,
    normalize_familiarity,
    remove_destination,
    resolve_destination,
    set_familiarity,
    upsert_destination,
)


def knows_teleport(entity) -> bool:
    """True when the entity can learn Teleport (PC spell lists or NPC innate
    spellcasting). Best-effort; the DM panel lets the DM pick any entity."""
    props = getattr(entity, 'properties', None) or {}
    for key in ('prepared_spells', 'spellbook', 'spells', 'bonus_spells'):
        if 'teleport' in (props.get(key) or []):
            return True
    innate = props.get('innate_spellcasting') or {}
    if isinstance(innate, dict):
        for group in ('at_will', 'spells', 'daily', 'n_day'):
            for entry in (innate.get(group) or []):
                name = entry if isinstance(entry, str) else (entry or {}).get('spell')
                if name == 'teleport':
                    return True
    try:
        battle = getattr(entity, 'battle', None)
        return 'teleport' in (entity.available_spells(battle) or [])
    except Exception:
        return False


def _entity_label(entity) -> str:
    label = getattr(entity, 'label', None)
    try:
        if callable(label):
            return str(label())
    except Exception:
        pass
    return str(getattr(entity, 'name', None) or entity.entity_uid)


def caster_options(session):
    """Entities that can hold destination lists (all placed entities,
    flagged by whether Teleport is on their list)."""
    seen, out = set(), []
    for battle_map in (getattr(session, 'maps', None) or {}).values():
        for entity in list(getattr(battle_map, 'entities', {}) or {}):
            uid = getattr(entity, 'entity_uid', None)
            if entity is None or uid in seen:
                continue
            seen.add(uid)
            if getattr(entity, 'object', lambda: False)():
                continue
            out.append({
                'entity_uid': uid,
                'label': _entity_label(entity),
                'knows_teleport': knows_teleport(entity),
                'destination_count': len(list_destinations(entity)),
            })
    out.sort(key=lambda e: (not e['knows_teleport'], e['label'].lower()))
    return out


def map_options(session):
    maps = getattr(session, 'maps', None) or {}
    out = []
    for name, battle_map in maps.items():
        if battle_map is None:
            continue
        out.append({
            'name': name,
            'label': str(getattr(battle_map, 'name', name)),
            'area_themes': getattr(battle_map, 'properties', {}).get('area_themes') or [],
        })
    out.sort(key=lambda m: m['name'])
    return out


def anchor_options(session, exclude_uid=None):
    out = []
    for entry in caster_options(session):
        if entry['entity_uid'] == exclude_uid:
            continue
        out.append({'entity_uid': entry['entity_uid'], 'label': entry['label']})
    return out


def destination_view(entity, battle=None):
    """UI-ready list: familiarity labels + resolved landing preview."""
    out = []
    session = getattr(entity, 'session', None)
    for entry in list_destinations(entity):
        familiarity = normalize_familiarity(entry.get('familiarity'))
        resolved = resolve_destination(session, entity, entry, battle=battle) \
            if session is not None else None
        view = {
            'id': entry.get('id'),
            'name': entry.get('name'),
            'kind': entry.get('kind', 'coords'),
            'familiarity': familiarity,
            'familiarity_label': FAMILIARITY_LABELS.get(familiarity, familiarity),
            'map': entry.get('map'),
            'x': entry.get('x'),
            'y': entry.get('y'),
            'entity_uid': entry.get('entity_uid'),
            'off_target_map': entry.get('off_target_map'),
            'similar_maps': entry.get('similar_maps') or [],
            'notes': entry.get('notes') or '',
            'unresolved': resolved is None,
        }
        if resolved is not None and entry.get('kind') == 'anchor':
            view['resolved_map'] = getattr(resolved[0], 'name', None)
            view['resolved_pos'] = [resolved[1], resolved[2]]
        out.append(view)
    return out


def apply_op(session, payload: dict) -> dict:
    """Shared mutation entrypoint for the Flask route and the MCP tool.

    ops: ``list`` | ``add`` | ``update`` | ``delete`` | ``set_familiarity``.
    Returns ``{'status': 'ok', ...}`` or raises ``ValueError`` with a
    user-facing message.
    """
    op = str(payload.get('op') or 'list').strip().lower()
    entity_uid = str(payload.get('entity_uid') or '')
    entity = session.entity_by_uid(entity_uid) if entity_uid else None
    if entity is None:
        raise ValueError(f'Entity not found: {entity_uid or "(none given)"}')

    if op == 'list':
        return {'status': 'ok', 'destinations': destination_view(entity)}

    dest_id = str(payload.get('dest_id') or payload.get('id') or '')

    if op == 'delete':
        if not find_destination(entity, dest_id):
            raise ValueError('Destination not found')
        remove_destination(entity, dest_id)
        return {'status': 'ok', 'destinations': destination_view(entity)}

    if op == 'set_familiarity':
        entry = set_familiarity(entity, dest_id, payload.get('familiarity'))
        if entry is None:
            raise ValueError('Destination not found')
        return {'status': 'ok', 'destinations': destination_view(entity)}

    if op in ('add', 'update'):
        raw = dict(payload.get('destination') or {})
        if not raw:
            # flat form from the UI / MCP
            for key in ('id', 'name', 'kind', 'map', 'x', 'y',
                        'familiarity', 'off_target_map', 'similar_maps', 'notes'):
                if payload.get(key) is not None:
                    raw[key] = payload.get(key)
        if payload.get('anchor_entity_uid') is not None:
            raw['entity_uid'] = payload.get('anchor_entity_uid')
        raw.setdefault('id', dest_id)
        raw.setdefault('name', '')
        # Validate the referenced map / anchor exists in the session.
        kind = str(raw.get('kind') or ('anchor' if raw.get('entity_uid') else 'coords'))
        if kind == 'coords' and raw.get('map'):
            if raw['map'] not in (getattr(session, 'maps', None) or {}):
                raise ValueError(f"Unknown map: {raw['map']}")
        if kind == 'anchor' and raw.get('entity_uid'):
            if session.entity_by_uid(str(raw['entity_uid'])) is None:
                raise ValueError(f"Unknown anchor entity: {raw['entity_uid']}")
        raw.setdefault('added_by', 'dm')
        entry = upsert_destination(entity, raw)
        return {'status': 'ok', 'destination': entry,
                'destinations': destination_view(entity)}

    raise ValueError(f'Unknown op: {op}')
