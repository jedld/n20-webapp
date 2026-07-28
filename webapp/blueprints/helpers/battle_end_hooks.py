"""Campaign-configurable hooks that run when a battle ends."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from natural20.player_character import PlayerCharacter
from natural20.utils.animal_communication import has_animal_communication

logger = logging.getLogger(__name__)


def _entity_group(entity, battle) -> Optional[str]:
    group = getattr(entity, 'group', None)
    if group:
        return str(group).lower()
    try:
        return str(battle.entities.get(entity, {}).get('group') or '').lower()
    except Exception:
        return None


def _battle_included_group(battle, group_name: str) -> bool:
    target = str(group_name or '').lower()
    if not target:
        return False
    for entity in getattr(battle, 'combat_order', []) or []:
        if _entity_group(entity, battle) == target:
            return True
    return False


def _group_defeated_on_map(battle_map, battle, group_name: str) -> bool:
    target = str(group_name or '').lower()
    if not target or not _battle_included_group(battle, target):
        return False

    for entity in list(getattr(battle_map, 'entities', {}).keys()):
        if _entity_group(entity, battle) != target:
            continue
        try:
            if entity.conscious() and not entity.dead():
                return False
        except Exception:
            return False
    return True


def _find_scroll_reader_pc(game_session, battle_map, current_game):
    scored: List[tuple[int, PlayerCharacter]] = []
    for entity in list(getattr(battle_map, 'entities', {}).keys()):
        if not isinstance(entity, PlayerCharacter):
            continue
        score = 0
        if has_animal_communication(game_session, entity=entity):
            score += 10
        inventory = getattr(entity, 'inventory', {}) or {}
        for slug in ('scroll_speak_animals_modified', 'scroll_speak_animals'):
            try:
                if int((inventory.get(slug) or {}).get('qty') or 0) > 0:
                    score += 5
                    break
            except Exception:
                pass
        if score > 0:
            scored.append((score, entity))

    if scored:
        return max(scored, key=lambda item: item[0])[1]

    for entity in list(getattr(battle_map, 'entities', {}).keys()):
        if isinstance(entity, PlayerCharacter):
            return entity
    return None


def _teleport_adjacent(current_game, battle_map, entity, target_pc, battle=None) -> bool:
    try:
        target_pos = list(battle_map.position_of(target_pc))
    except Exception:
        return False

    source_map = current_game.get_map_for_entity(entity)
    if source_map is not None and source_map is not battle_map:
        try:
            source_map.remove(entity)
        except Exception:
            pass

    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
        nx, ny = target_pos[0] + dx, target_pos[1] + dy
        try:
            if not battle_map.placeable(entity, nx, ny, battle):
                continue
        except Exception:
            continue
        try:
            if entity in battle_map.entities:
                battle_map.move_to(entity, nx, ny, battle)
            else:
                battle_map.place((nx, ny), entity)
            return True
        except Exception:
            continue

    try:
        nx, ny = battle_map.find_empty_placeable_position(entity, target_pos[0], target_pos[1])
        if entity in battle_map.entities:
            battle_map.move_to(entity, nx, ny, battle)
        else:
            battle_map.place((nx, ny), entity)
        return True
    except Exception:
        logger.exception('Failed to teleport %s adjacent to %s', getattr(entity, 'entity_uid', entity), getattr(target_pc, 'entity_uid', target_pc))
        return False


def _emit_narration(game_manager, session, record_narration_fn, narration: Dict[str, Any], map_name: Optional[str]):
    if not narration:
        return
    text = narration.get('text')
    if not text:
        return
    payload = {
        'on_enter': {
            'title': narration.get('title'),
            'text': text,
            'once': False,
            'outcome': 'victory',
        }
    }
    try:
        from webapp.blueprints.helpers.runtime_state import get_socketio

        get_socketio().emit('message', {
            'type': 'narration',
            'message': payload,
            'map_name': map_name,
        })
    except Exception as exc:
        logger.warning('Failed to emit battle-end hook narration: %s', exc)
    try:
        if record_narration_fn:
            record_narration_fn(payload, map_name=map_name)
    except Exception as exc:
        logger.debug('Failed to record battle-end hook narration: %s', exc)


def _hook_matches(game_manager, session, battle, hook: Dict[str, Any]) -> bool:
    battle_map = game_manager.get_current_battle_map()
    map_name = getattr(battle_map, 'name', None)
    expected_map = hook.get('map')
    if expected_map and str(map_name) != str(expected_map):
        return False

    once_key = hook.get('once_session_key')
    if once_key:
        state = getattr(session, 'session_state', None) or {}
        if state.get(once_key):
            return False

    if hook.get('require_victory', True):
        try:
            if battle.tpk():
                return False
        except Exception:
            return False

    for group_name in hook.get('require_groups_defeated') or []:
        if not _group_defeated_on_map(battle_map, battle, group_name):
            return False

    return True


def _apply_hook(game_manager, session, battle, hook: Dict[str, Any], *, conversation_service=None, record_narration_fn=None):
    battle_map = game_manager.get_current_battle_map()
    map_name = getattr(battle_map, 'name', None)

    once_key = hook.get('once_session_key')
    if once_key:
        session.update_state({once_key: True})

    session_flags = hook.get('session_flags') or {}
    if isinstance(session_flags, dict) and session_flags:
        session.update_state(session_flags)

    _emit_narration(game_manager, session, record_narration_fn, hook.get('narration') or {}, map_name)

    npc_cfg = hook.get('npc_address') or {}
    npc_uid = npc_cfg.get('npc_uid')
    if not npc_uid or conversation_service is None:
        return

    npc = game_manager.get_entity_by_uid(npc_uid)
    if npc is None:
        logger.warning('Battle-end hook NPC %s not found on map %s', npc_uid, map_name)
        return

    target_pc = _find_scroll_reader_pc(session, battle_map, game_manager)
    if target_pc is None:
        logger.warning('Battle-end hook could not find a player target for %s', npc_uid)
        return

    if npc_cfg.get('teleport_to') in ('scroll_reader', 'adjacent_to_target', True):
        if _teleport_adjacent(game_manager, battle_map, npc, target_pc, battle=battle):
            try:
                game_manager.socketio.emit('message', {'type': 'refresh_map'})
            except Exception:
                pass

    conversation_service.initiate_npc_address(
        npc,
        [target_pc],
        context=npc_cfg.get('system_nudge'),
        open_dialog=bool(npc_cfg.get('open_dialog', True)),
        map_name=map_name,
    )


def process_battle_end_hooks(game_manager, session, *, conversation_service=None, record_narration_fn=None) -> bool:
    """Run any ``battle_end_hooks`` declared in campaign ``game.yml``."""
    battle = game_manager.get_current_battle()
    if battle is None:
        return False

    properties = getattr(session, 'game_properties', None) or {}
    hooks = properties.get('battle_end_hooks') or []
    if not isinstance(hooks, list):
        return False

    handled = False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        try:
            if not _hook_matches(game_manager, session, battle, hook):
                continue
            _apply_hook(
                game_manager,
                session,
                battle,
                hook,
                conversation_service=conversation_service,
                record_narration_fn=record_narration_fn,
            )
            handled = True
            logger.info('Applied battle-end hook %s', hook.get('id') or hook.get('map'))
        except Exception:
            logger.exception('Battle-end hook failed: %r', hook.get('id'))
    return handled


def attach_battle_end_hooks(conversation_service, record_narration_fn=None):
    """Register the battle-end hook processor on the live game manager."""
    from webapp.blueprints.helpers.runtime_state import get_current_game

    def _wrapped(game_manager, session):
        return process_battle_end_hooks(
            game_manager,
            session,
            conversation_service=conversation_service,
            record_narration_fn=record_narration_fn,
        )

    try:
        get_current_game().register_event_handler('on_battle_end', _wrapped)
    except Exception:
        logger.exception('Failed to register battle-end hooks')
