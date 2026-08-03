"""Action bar rendering helpers for /actions and /actions/batch."""

from flask import render_template

from natural20.actions.use_item_action import UseItemAction
from natural20.utils.action_bar import (
    TAB_ITEMS,
    TAB_SPELLS,
    action_bar_tab_for,
    build_spell_tab_groups,
    filter_spell_tab_groups,
    partition_standard_actions,
    resolve_action_bar_config,
)
from natural20.utils.merchant import is_merchant

from .auth_utils import user_role
from .template_globals import entity_owners
from .runtime_state import get_current_game, get_game_session


def _entity_supports_spellcasting(entity):
  """True only for PCs/NPCs that prepare spells, not generic Entity subclasses."""
  return callable(getattr(entity, 'prepared_spells', None))


def _filter_actions_for_inline_pickers(actions, *, inline_spells, inline_items):
    """Drop generic spell / use-item buttons when those choices are inlined."""
    filtered = []
    for action in actions:
        cls_name = action.__class__.__name__
        if inline_spells and cls_name == 'SpellAction' and not getattr(action, 'spell_class', None):
            continue
        if inline_items and cls_name == 'UseItemAction' and not getattr(action, 'target_item', None):
            continue
        filtered.append(action)
    return filtered


def _favorite_spells_by_level(castable_spells_by_level, favorites):
    if not favorites:
        return {}
    fav_set = {str(item) for item in favorites}
    grouped = {}
    for level, spells in castable_spells_by_level.items():
        picked = [spell for spell in spells if spell in fav_set]
        if picked:
            grouped[level] = picked
    return grouped


def _favorite_items(usable_items, favorites):
    if not favorites:
        return []
    fav_set = {str(item) for item in favorites}
    return [item for item in usable_items if str(item.get('name') or '') in fav_set]


def _action_template_context(entity, effective_battle, entity_map, is_dm):
    game_session = get_game_session()
    game_config = {}
    try:
        game_config = getattr(get_game_session(), 'game_properties', None) or {}
    except Exception:
        game_config = {}

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

    castable_spells_by_level = {}
    usable_items = []
    entity_class_level = []
    entity_x = entity_y = 0
    if _entity_supports_spellcasting(entity):
        castable_spells_by_level = entity.castable_spells_by_level(effective_battle)
    if hasattr(entity, 'usable_items'):
        usable_items = list(entity.usable_items() or [])
        usable_items.sort(key=lambda item: item.get('label') or item.get('name', ''))
    if hasattr(entity, 'class_and_level'):
        if getattr(entity, 'familiar', lambda: False)():
            entity_class_level = entity.owner.class_and_level()
        else:
            entity_class_level = entity.class_and_level()
    if entity_map is not None:
        entity_x, entity_y = entity_map.entity_or_object_pos(entity)

    inline_spells = bool(castable_spells_by_level)
    inline_items = bool(usable_items)
    other_actions = _filter_actions_for_inline_pickers(
        other_actions,
        inline_spells=inline_spells,
        inline_items=inline_items,
    )

    bar_config = resolve_action_bar_config(entity, game_config)
    favorites = bar_config['favorites']
    common_types = bar_config['common_action_types']

    spell_tab_groups = build_spell_tab_groups(entity, castable_spells_by_level)
    favorite_spell_tab_groups = filter_spell_tab_groups(spell_tab_groups, favorites)

    standard_actions = [
        action for action in other_actions
        if action_bar_tab_for(action, common_types=common_types) not in (TAB_SPELLS, TAB_ITEMS)
    ]
    common_actions, combat_actions = partition_standard_actions(
        standard_actions,
        favorites=favorites,
        common_types=common_types,
    )

    favorite_spells_by_level = _favorite_spells_by_level(castable_spells_by_level, favorites)
    favorite_items = _favorite_items(usable_items, favorites)

    return {
        'entity': entity,
        'battle': effective_battle,
        'session': game_session,
        'map': entity_map,
        'interact_actions': interact_actions,
        'available_actions': other_actions,
        'common_actions': common_actions,
        'combat_actions': combat_actions,
        'castable_spells_by_level': castable_spells_by_level,
        'spell_tab_groups': spell_tab_groups,
        'favorite_spell_tab_groups': favorite_spell_tab_groups,
        'favorite_spells_by_level': favorite_spells_by_level,
        'usable_items': usable_items,
        'favorite_items': favorite_items,
        'use_item_action': UseItemAction(game_session, entity, 'use_item'),
        'entity_class_level': entity_class_level,
        'entity_x': entity_x,
        'entity_y': entity_y,
        'entity_map': entity_map.entities if entity_map else {},
        'is_dm': is_dm,
        'is_merchant': is_merchant(entity),
        'action_bar_config': bar_config,
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
