"""Shared battle management helpers for MCP tools and the DM assistant."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from natural20.battle import Battle
from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

logger = logging.getLogger(__name__)

_VALID_RELATIONSHIPS = frozenset({'enemy', 'ally', 'neutral', 'none'})


def _emit_refresh(game) -> None:
    try:
        game.socketio.emit('message', {'type': 'refresh_map'})
    except Exception:
        logger.exception('Failed to emit refresh_map after battle management action')


def _resolve_entity(game, entity_uid: str):
    ent = game.get_entity_by_uid(entity_uid)
    if ent is None:
        raise ValueError(f'Entity not found: {entity_uid}')
    return ent


def _resolve_combatant_group(game, ent) -> str:
    """Resolve combat group from entity/map metadata, with PC/NPC fallback."""
    group = getattr(ent, 'group', None)
    if group is None and isinstance(getattr(ent, 'properties', None), dict):
        group = ent.properties.get('group')
    if group is None:
        try:
            group = game._entity_group(ent)
        except Exception:
            group = None
    if group:
        return str(group)
    is_npc = bool(getattr(ent, 'is_npc', lambda: False)())
    return 'a' if not is_npc else 'b'


def _resolve_map(game, map_name: Optional[str] = None):
    if map_name:
        battle_map = (getattr(game, 'maps', None) or {}).get(map_name)
        if battle_map is None:
            raise ValueError(f'Map not found: {map_name}')
        return battle_map
    battle = game.get_current_battle()
    if battle is not None and getattr(battle, 'map', None) is not None:
        return battle.map
    return game.get_current_battle_map()


def get_battle_snapshot(game) -> Dict[str, Any]:
    battle = game.get_current_battle()
    if battle is None:
        return {'active': False, 'message': 'No active battle'}

    order = []
    for entity in getattr(battle, 'combat_order', None) or []:
        state = (getattr(battle, 'entities', None) or {}).get(entity) or {}
        order.append({
            'entity_uid': getattr(entity, 'entity_uid', None),
            'name': entity_label(entity),
            'group': state.get('group'),
            'initiative': state.get('initiative'),
        })

    current_uid = None
    try:
        current = battle.current_turn()
        current_uid = getattr(current, 'entity_uid', None)
    except Exception:
        pass

    groups = {}
    for group_name, group_entities in (getattr(battle, 'groups', None) or {}).items():
        groups[group_name] = [entity_label(ent) for ent in group_entities]

    return {
        'active': True,
        'round': getattr(battle, 'round', None),
        'current_turn_index': getattr(battle, 'current_turn_index', 0),
        'current_turn_uid': current_uid,
        'turn_order': order,
        'groups': groups,
    }


def start_battle(
    game,
    game_session,
    *,
    combatant_uids: Optional[Sequence[str]] = None,
    map_name: Optional[str] = None,
) -> Dict[str, Any]:
    if game.get_current_battle() is not None:
        raise ValueError('A battle is already active; end it first.')

    battle_map = _resolve_map(game, map_name)
    battle = Battle(game_session, battle_map, animation_log_enabled=True)

    added = 0
    if combatant_uids:
        for uid in combatant_uids:
            ent = _resolve_entity(game, uid)
            battle.add(ent, _resolve_combatant_group(game, ent))
            added += 1
    else:
        for ent in (getattr(battle_map, 'entities', {}) or {}).keys():
            battle.add(ent, _resolve_combatant_group(game, ent))
            added += 1

    battle.start()
    game.set_current_battle(battle)
    logger.info(
        "Battle started via battle_management: map=%s combatants=%s explicit_uids=%s",
        getattr(battle_map, 'name', None),
        added,
        bool(combatant_uids),
    )
    try:
        game.play_battle_music(battle)
    except Exception:
        logger.exception('Failed to start battle music')
    _emit_refresh(game)
    return {
        'started': True,
        'map': getattr(battle_map, 'name', None),
        'combatants': added,
        'round': getattr(battle, 'round', 0),
        'turn_order': get_battle_snapshot(game).get('turn_order', []),
    }


def end_battle(game) -> Dict[str, Any]:
    if game.get_current_battle() is None:
        raise ValueError('No active battle')
    game.end_current_battle()
    _emit_refresh(game)
    return {'ended': True}


def _resort_initiative(battle, *, preserve_current: bool = True) -> None:
    current_entity = None
    if preserve_current and getattr(battle, 'combat_order', None):
        try:
            current_entity = battle.current_turn()
        except Exception:
            current_entity = None
    battle.combat_order = sorted(
        list(battle.combat_order),
        key=lambda ent: (battle.entities.get(ent) or {}).get('initiative', 0),
        reverse=True,
    )
    if current_entity is not None:
        try:
            battle.current_turn_index = battle.combat_order.index(current_entity)
        except ValueError:
            battle.current_turn_index = 0


def battle_admin(
    game,
    game_session,
    op: str,
    *,
    entity_uid: Optional[str] = None,
    group: Optional[str] = None,
    order: Optional[Sequence[str]] = None,
    initiative: Optional[int] = None,
    controller: Optional[str] = None,
) -> Dict[str, Any]:
    op = (op or '').lower()
    battle = game.get_current_battle()
    if battle is None:
        raise ValueError('No active battle')

    if op == 'next_turn':
        before = getattr(battle.current_turn(), 'entity_uid', None)
        battle.next_turn()
        after = getattr(battle.current_turn(), 'entity_uid', None)
        _emit_refresh(game)
        return {
            'op': op,
            'previous_uid': before,
            'current_uid': after,
            'round': getattr(battle, 'round', None),
        }

    if op == 'reorder':
        if not order:
            raise ValueError('order (list of entity_uids) is required')
        battle.reorder_initiative(list(order))
        _emit_refresh(game)
        return {
            'op': op,
            'order': list(order),
            'current_index': getattr(battle, 'current_turn_index', 0),
            'turn_order': get_battle_snapshot(game).get('turn_order', []),
        }

    if not entity_uid:
        raise ValueError('entity_uid is required for this op')
    ent = _resolve_entity(game, entity_uid)

    if op == 'add_combatant':
        if ent in battle.entities:
            return {'op': op, 'entity_uid': entity_uid, 'added': False, 'reason': 'already in battle'}
        controller_obj = None
        try:
            controller_obj = game.build_combat_controller_for_entity(ent, controller_kind=controller)
        except Exception:
            controller_obj = None
        if controller_obj is None:
            from natural20.generic_controller import GenericController
            if isinstance(ent, PlayerCharacter):
                controller_obj = game.controller_registry.ensure_web_controller_for_entity(ent)
            if controller_obj is None:
                controller_obj = GenericController(game_session)
        try:
            controller_obj.register_handlers_on(ent)
        except Exception:
            pass
        default_group = group or _resolve_combatant_group(game, ent)
        battle.add(ent, default_group, controller=controller_obj)
        state = battle.entities.get(ent)
        if state is not None:
            if initiative is not None:
                state['initiative'] = int(initiative)
            else:
                try:
                    state['initiative'] = ent.initiative(battle)
                except Exception:
                    state['initiative'] = 0
            if ent not in battle.combat_order:
                insert_at = (battle.current_turn_index + 1) if battle.combat_order else 0
                battle.combat_order.insert(insert_at, ent)
            _resort_initiative(battle)
        _emit_refresh(game)
        return {
            'op': op,
            'entity_uid': entity_uid,
            'added': True,
            'group': default_group,
            'initiative': (state or {}).get('initiative'),
        }

    if op == 'remove_combatant':
        if ent not in battle.entities and ent not in battle.combat_order:
            return {'op': op, 'entity_uid': entity_uid, 'removed': False}
        battle.remove(ent, from_map=False)
        try:
            if battle.battle_ends():
                game.end_current_battle()
        except Exception:
            pass
        _emit_refresh(game)
        return {'op': op, 'entity_uid': entity_uid, 'removed': True}

    if op == 'set_group':
        if not group:
            raise ValueError('group is required')
        if ent not in battle.entities:
            raise ValueError('Entity is not in the current battle')
        old = battle.entities[ent].get('group')
        battle.entities[ent]['group'] = group
        try:
            if old in battle.groups:
                battle.groups[old].discard(ent)
            battle.groups.setdefault(group, set()).add(ent)
        except Exception:
            pass
        ent.group = group
        if isinstance(getattr(ent, 'properties', None), dict):
            ent.properties['group'] = group
        _emit_refresh(game)
        return {'op': op, 'entity_uid': entity_uid, 'group': group, 'previous': old}

    if op == 'set_initiative':
        if initiative is None:
            raise ValueError('initiative is required')
        if ent not in battle.entities:
            raise ValueError('Entity is not in the current battle')
        battle.entities[ent]['initiative'] = int(initiative)
        if ent not in battle.combat_order:
            battle.combat_order.append(ent)
        _resort_initiative(battle)
        _emit_refresh(game)
        return {
            'op': op,
            'entity_uid': entity_uid,
            'initiative': int(initiative),
            'turn_order': get_battle_snapshot(game).get('turn_order', []),
        }

    raise ValueError(f'Unknown op: {op}')


def set_entity_group(game, game_session, entity_uid: str, group: str, *, in_battle_only: bool = False) -> Dict[str, Any]:
    ent = _resolve_entity(game, entity_uid)
    battle = game.get_current_battle()
    if battle is not None and ent in battle.entities:
        return battle_admin(
            game,
            game_session,
            'set_group',
            entity_uid=entity_uid,
            group=group,
        )
    if in_battle_only:
        raise ValueError('Entity is not in the current battle')

    ent.group = group
    if isinstance(getattr(ent, 'properties', None), dict):
        ent.properties['group'] = group
    try:
        game.update_group(ent, group)
    except Exception:
        pass
    _emit_refresh(game)
    return {'entity_uid': entity_uid, 'group': group, 'scope': 'exploration'}


def configure_group_relationship(
    game_session,
    group: str,
    target_group: str,
    relationship: str,
) -> Dict[str, Any]:
    relationship = (relationship or '').lower()
    if relationship not in _VALID_RELATIONSHIPS:
        raise ValueError("relationship must be one of: enemy, ally, neutral, none")

    groups = game_session.game_properties.setdefault('groups', {})
    if group not in groups:
        groups[group] = {}
    if target_group not in groups:
        groups[target_group] = {}

    def _list(key):
        raw = groups[group].get(key) or []
        return [str(item) for item in raw]

    def _set_list(key, values):
        groups[group][key] = sorted(set(values))

    def _remove_from_all_lists(gname, other):
        info = groups.setdefault(gname, {})
        for key in ('enemies', 'allies', 'neutral'):
            items = [str(item) for item in (info.get(key) or []) if str(item) != other]
            info[key] = items

    if relationship == 'none':
        _remove_from_all_lists(group, target_group)
        _remove_from_all_lists(target_group, group)
    elif relationship == 'enemy':
        enemies = _list('enemies')
        if target_group not in enemies:
            enemies.append(target_group)
        _set_list('enemies', enemies)
        allies = [item for item in _list('allies') if item != target_group]
        _set_list('allies', allies)
        neutral = [item for item in _list('neutral') if item != target_group]
        _set_list('neutral', neutral)
        other_enemies = [str(item) for item in (groups[target_group].get('enemies') or [])]
        if group not in other_enemies:
            other_enemies.append(group)
        groups[target_group]['enemies'] = sorted(set(other_enemies))
        groups[target_group]['allies'] = [
            item for item in (groups[target_group].get('allies') or []) if item != group
        ]
        groups[target_group]['neutral'] = [
            item for item in (groups[target_group].get('neutral') or []) if item != group
        ]
    elif relationship == 'ally':
        allies = _list('allies')
        if target_group not in allies:
            allies.append(target_group)
        _set_list('allies', allies)
        _set_list('enemies', [item for item in _list('enemies') if item != target_group])
        _set_list('neutral', [item for item in _list('neutral') if item != target_group])
        other_allies = [str(item) for item in (groups[target_group].get('allies') or [])]
        if group not in other_allies:
            other_allies.append(group)
        groups[target_group]['allies'] = sorted(set(other_allies))
        groups[target_group]['enemies'] = [
            item for item in (groups[target_group].get('enemies') or []) if item != group
        ]
    elif relationship == 'neutral':
        neutral = _list('neutral')
        if target_group not in neutral:
            neutral.append(target_group)
        _set_list('neutral', neutral)
        _set_list('enemies', [item for item in _list('enemies') if item != target_group])
        _set_list('allies', [item for item in _list('allies') if item != target_group])
        other_neutral = [str(item) for item in (groups[target_group].get('neutral') or [])]
        if group not in other_neutral:
            other_neutral.append(group)
        groups[target_group]['neutral'] = sorted(set(other_neutral))
        groups[target_group]['enemies'] = [
            item for item in (groups[target_group].get('enemies') or []) if item != group
        ]

    return {
        'group': group,
        'target_group': target_group,
        'relationship': relationship,
        'groups': groups,
    }


def list_campaign_groups(game_session) -> Dict[str, Any]:
    groups = None
    if hasattr(game_session, 'groups') and callable(game_session.groups):
        groups = game_session.groups()
    if groups is None:
        groups = (getattr(game_session, 'game_properties', None) or {}).get('groups', {})
    return {'groups': dict(groups or {})}
