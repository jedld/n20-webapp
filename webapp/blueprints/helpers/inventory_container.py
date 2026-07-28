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
