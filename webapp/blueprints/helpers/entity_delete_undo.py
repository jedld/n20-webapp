"""Undo support for DM entity deletions (Delete key / Ctrl+Z).

Shared by the ``dm`` blueprint (``POST /delete_entity`` +
``POST /undo_delete_entity``) and the ``dm.remove_entity`` MCP tool.

Undo state lives in ``session.session_state['entity_delete_undo'][username]``:
an in-memory, per-user stack (newest last) capped at :data:`UNDO_LIMIT`.
Each entry snapshots the entity's map position, group, battle membership
(initiative), and whether the user's POV was on the entity, so Ctrl+Z can
restore all of it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

UNDO_LIMIT = 20

_STATE_KEY = 'entity_delete_undo'


def entity_delete_undo_stack(session, username: str) -> List[Dict[str, Any]]:
    """Return (creating if needed) the per-user undo stack."""
    state = getattr(session, 'session_state', None)
    if not isinstance(state, dict):
        state = {}
        if session is not None:
            try:
                session.session_state = state
            except Exception:
                pass
    return state.setdefault(_STATE_KEY, {}).setdefault(username, [])


def push_entity_delete_undo(session, username: str, entry: Dict[str, Any]) -> None:
    """Record a deletion for Ctrl+Z; keep only the most recent UNDO_LIMIT."""
    if not entry or not entry.get('entity_uid'):
        return
    stack = entity_delete_undo_stack(session, username)
    stack.append(entry)
    del stack[:-UNDO_LIMIT]


def find_nearest_free_square(battle_map, x: Optional[int], y: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
    """Spiral outward from (x, y) until a square with no entity is found."""
    try:
        width, height = battle_map.size
    except Exception:
        width = height = None
    if width is None or height is None or x is None or y is None:
        return x, y
    for radius in range(0, max(width, height) + 1):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and not battle_map.entity_at(nx, ny):
                    return nx, ny
    return x, y


def capture_delete_state(game, entity, battle_map, username: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot everything undo needs, *before* the entity is removed."""
    try:
        pos_x, pos_y = battle_map.position_of(entity)
    except Exception:
        pos_x, pos_y = 0, 0
    battle = game.get_current_battle()
    entry: Dict[str, Any] = {
        'entity_uid': entity.entity_uid,
        'name': entity.label(),
        'x': pos_x,
        'y': pos_y,
        'group': getattr(entity, 'group', None) or 'b',
        'was_in_battle': bool(battle and entity in battle.combat_order),
        'initiative': None,
        'pov_username': None,
    }
    if entry['was_in_battle']:
        state = battle.entities.get(entity)
        if state is not None:
            entry['initiative'] = state.get('initiative')
    if username:
        pov_entity = game.get_pov_entity_for_user(username)
        if pov_entity is entity:
            entry['pov_username'] = username
    return entry


def remove_entity_with_undo(game, entity, battle_map, username: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot + remove the entity from the active battle and the map.

    When ``username`` is given the snapshot is pushed onto that user's undo
    stack (the VTT Delete-key flow). Returns the snapshot.
    """
    entry = capture_delete_state(game, entity, battle_map, username)
    if username:
        push_entity_delete_undo(getattr(game, 'game_session', None), username, entry)
    battle = game.get_current_battle()
    if battle is not None and entity in battle.combat_order:
        battle.remove(entity, from_map=False)
    battle_map.remove(entity)
    return entry


def undo_entity_delete(game, session, username: str,
                       entity_uid: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    """Restore the most recent deletion for ``username``.

    Re-places the token on its original square (nearest free square via spiral
    search if it is now occupied), re-adds it to the active battle when it was
    removed mid-battle (keeping its saved initiative), and restores the user's
    POV if it had pointed at the deleted entity.

    Returns ``(payload, status_code)``; callers emit their own SocketIO
    refreshes. 404 = entity no longer exists, 409 = no map available.
    """
    stack = entity_delete_undo_stack(session, username)
    if not stack:
        return {'status': 'empty', 'error': 'Nothing to undo'}, 200
    entry = stack[-1]
    if entity_uid and str(entry.get('entity_uid')) != str(entity_uid):
        return {'status': 'empty', 'error': 'Nothing to undo'}, 200

    entity = game.get_entity_by_uid(entry['entity_uid'])
    if entity is None:
        stack.pop()
        return {'error': 'Entity no longer exists'}, 404

    x = entry.get('x')
    y = entry.get('y')
    group = entry.get('group') or 'b'

    with game.game_state_lock:
        # Prefer the original square; fall back to the user's current map
        # (or the nearest free square) if it is now occupied.
        target_map = None
        for map_obj in (getattr(game, 'maps', {}) or {}).values():
            if x is not None and y is not None and not map_obj.entity_at(x, y):
                target_map = map_obj
                break
        if target_map is None:
            target_map = game.get_map_for_user(username)
        if target_map is None:
            stack.pop()
            return {'error': 'No map available to restore entity onto'}, 409

        if x is None or y is None or target_map.entity_at(x, y):
            if x is None or y is None:
                x, y = 0, 0
            x, y = find_nearest_free_square(target_map, x, y)
        target_map.add(entity, x, y, group)

        # Rejoin an active battle when the deletion happened mid-battle.
        battle = game.get_current_battle()
        if battle is not None and entry.get('was_in_battle'):
            controller = game.build_combat_controller_for_entity(entity)
            if controller is None:
                from natural20.generic_controller import GenericController
                controller = GenericController(getattr(game, 'game_session', None))
            try:
                controller.register_handlers_on(entity)
            except Exception:
                pass
            battle.add(entity, group, controller=controller)
            state = battle.entities.get(entity)
            if state is not None:
                try:
                    state['initiative'] = entry.get('initiative')
                    if state['initiative'] is None:
                        state['initiative'] = entity.initiative(battle)
                except Exception:
                    state['initiative'] = 0
            if entity not in battle.combat_order:
                insert_at = (battle.current_turn_index + 1) if battle.combat_order else 0
                battle.combat_order.insert(insert_at, entity)

        # Restore the user's POV if it had pointed at the deleted entity.
        pov_restored = False
        if entry.get('pov_username') == username:
            game.set_pov_entity_for_user(username, entity)
            pov_restored = True

    stack.pop()
    return (
        {
            'status': 'ok',
            'entity_uid': entry['entity_uid'],
            'name': entry.get('name') or entity.label(),
            'x': x,
            'y': y,
            'pov_restored': pov_restored,
        },
        200,
    )
