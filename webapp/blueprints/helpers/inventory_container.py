"""Shared helpers for player/DM inventory container routes."""

from __future__ import annotations

from flask import session

from webapp.blueprints.helpers.auth_utils import user_role
from webapp.blueprints.helpers.runtime_state import get_current_game
from webapp.blueprints.helpers.template_globals import entities_controlled_by


def resolve_inventory_entity(entity_id: str):
    entity_id = (entity_id or '').strip()
    if not entity_id:
        return None
    current_game = get_current_game()
    entity = current_game.get_entity_by_uid(entity_id)
    if entity is not None:
        return entity
    try:
        battle_map = current_game.get_map_for_user(session.get('username'))
    except Exception:
        battle_map = None
    if battle_map is not None:
        return battle_map.entity_by_uid(entity_id)
    return None


def can_manage_inventory_entity(entity, username=None) -> bool:
    if entity is None:
        return False
    if 'dm' in user_role():
        return True
    username = username or session.get('username')
    if not username:
        return False
    try:
        battle_map = get_current_game().get_map_for_user(username)
    except Exception:
        battle_map = None
    return entity in entities_controlled_by(username, battle_map)


def use_inventory_item(entity, item_name, container_name=None, target=None):
    """Use a usable inventory item on the owner (or ``target``), including items in containers."""
    from natural20.actions.use_item_action import UseItemAction
    from webapp.blueprints.helpers.runtime_state import get_socketio

    if entity is None or not item_name:
        return False, 'Missing item'
    if hasattr(entity, 'item_count') and entity.item_count(item_name) <= 0:
        return False, 'Item not found in inventory'

    current_game = get_current_game()
    battle = None
    try:
        battle = current_game.get_current_battle()
    except Exception:
        battle = None
    if battle is not None and entity not in getattr(battle, 'entities', {}):
        battle = None
    if not UseItemAction.can(entity, battle):
        return False, 'You cannot use an item right now.'

    game_session = getattr(entity, 'session', None)
    try:
        action = UseItemAction.build_self_use(
            game_session,
            entity,
            item_name,
            target=target or entity,
            container_name=container_name or None,
        )
    except Exception as exc:
        return False, str(exc) or 'This item needs to be used from the action bar.'

    username = session.get('username')
    current_game.commit_and_update(username, action, [entity])
    try:
        get_socketio().emit('message', {'type': 'refresh_map'})
    except Exception:
        pass
    hp = entity.hp() if hasattr(entity, 'hp') else None
    max_hp = entity.max_hp() if hasattr(entity, 'max_hp') else None
    return True, {'hp': hp, 'max_hp': max_hp}
