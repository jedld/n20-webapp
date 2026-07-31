"""POV (point-of-view) helpers for DM camera focus and player control."""

from __future__ import annotations

from typing import List, Optional

from natural20.player_character import PlayerCharacter


def dm_pov_portrait_entities(current_game, battle_map=None) -> List:
    """PCs campaign-wide plus NPCs on the DM's current view map."""
    entities = []
    seen = set()
    for battle_map_obj in getattr(current_game, 'maps', {}).values():
        for entity in getattr(battle_map_obj, 'entities', []) or []:
            if not isinstance(entity, PlayerCharacter):
                continue
            uid = getattr(entity, 'entity_uid', None)
            if uid and uid not in seen:
                seen.add(uid)
                entities.append(entity)
    if battle_map is None:
        return entities
    for entity in getattr(battle_map, 'entities', []) or []:
        if not callable(getattr(entity, 'is_npc', None)) or not entity.is_npc():
            continue
        uid = getattr(entity, 'entity_uid', None)
        if uid and uid not in seen:
            seen.add(uid)
            entities.append(entity)
    return entities


def dm_pov_portrait_groups(current_game, battle_map=None):
    """Split DM portrait choices into party PCs and NPCs on the view map."""
    entities = dm_pov_portrait_entities(current_game, battle_map)
    party = [entity for entity in entities if isinstance(entity, PlayerCharacter)]
    npcs = [entity for entity in entities if not isinstance(entity, PlayerCharacter)]
    npcs.sort(key=lambda entity: (getattr(entity, 'name', '') or entity.label()).lower())
    return {'party': party, 'npcs': npcs}


def build_portrait_render_context(current_game, username, roles):
    """Template context for floating_portraits.html."""
    from webapp.blueprints.helpers.template_globals import entities_controlled_by

    roles = roles or []
    viewer_is_dm = 'dm' in roles
    current_pov = current_game.get_pov_entity_for_user(username)
    if viewer_is_dm:
        battle_map = current_game.get_map_for_user(username)
        groups = dm_pov_portrait_groups(current_game, battle_map)
        return {
            'viewer_is_dm': True,
            'current_pov_entity': current_pov,
            'pov_party_entities': groups['party'],
            'pov_npc_entities': groups['npcs'],
            'pov_entities': groups['party'] + groups['npcs'],
        }
    controlled = entities_controlled_by(username) or []
    return {
        'viewer_is_dm': False,
        'current_pov_entity': current_pov,
        'pov_party_entities': [],
        'pov_npc_entities': [],
        'pov_entities': controlled,
    }


def user_may_set_pov_entity(current_game, username, roles, entity, *, battle_map=None) -> bool:
    if entity is None:
        return False
    if 'dm' in (roles or []):
        return True
    from webapp.blueprints.helpers.template_globals import entities_controlled_by

    if battle_map is None:
        battle_map = current_game.get_map_for_user(username)
    return entity in entities_controlled_by(username, battle_map)
