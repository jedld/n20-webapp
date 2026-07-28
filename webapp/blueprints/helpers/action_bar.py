"""Action bar rendering helpers for /actions and /actions/batch."""

from flask import render_template

from natural20.utils.merchant import is_merchant

from .auth_utils import user_role
from .template_globals import entity_owners
from .runtime_state import get_current_game, get_game_session


def _action_template_context(entity, effective_battle, entity_map, is_dm):
    game_session = get_game_session()
    interact_actions = entity.available_actions(
        game_session,
        effective_battle,
        auto_target=False,
        map=entity_map,
        interact_only=True,
        admin_actions=is_dm,
    )
    other_actions = entity.available_actions(
        game_session,
        effective_battle,
        auto_target=False,
        map=entity_map,
        except_interact=True,
        admin_actions=is_dm,
    )
    return {
        'entity': entity,
        'battle': effective_battle,
        'session': game_session,
        'map': entity_map,
        'interact_actions': interact_actions,
        'available_actions': other_actions,
        'entity_map': entity_map.entities,
        'is_dm': is_dm,
        'is_merchant': is_merchant(entity),
    }


def render_actions_html(entity, *, effective_battle, entity_map, is_dm):
    return render_template(
        'actions.html',
        **_action_template_context(entity, effective_battle, entity_map, is_dm),
    )


def resolve_actions_for_id(entity_id, current_user):
    """Resolve one entity/object id to rendered action-bar HTML or an error."""
    battle_map = get_current_game().get_map_for_user(current_user)
    battle = get_current_game().get_current_battle()
    is_dm = 'dm' in user_role()

    entity = get_current_game().get_entity_by_uid(entity_id)
    if entity:
        entity_map = get_current_game().get_map_for_entity(entity) or battle_map
        if entity_map is None:
            return {'id': entity_id, 'error': 'Entity is not currently on a map', 'status': 409}
        if not (is_dm or current_user in entity_owners(entity)):
            return {'id': entity_id, 'error': 'Forbidden', 'status': 403}
        effective_battle = battle if (battle and entity in battle.entities) else None
        html = render_actions_html(
            entity,
            effective_battle=effective_battle,
            entity_map=entity_map,
            is_dm=is_dm,
        )
        return {'id': entity_id, 'html': html, 'status': 200}

    if battle_map is None:
        return {'id': entity_id, 'error': 'Entity not found', 'status': 404}

    object_ = battle_map.object_by_uid(entity_id)
    if object_:
        html = render_actions_html(
            object_,
            effective_battle=battle,
            entity_map=battle_map,
            is_dm=is_dm,
        )
        return {'id': entity_id, 'html': html, 'status': 200}

    return {'id': entity_id, 'error': 'Entity not found', 'status': 404}
