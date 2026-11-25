"""Template global functions extracted from app.py.

These functions are registered as Jinja2 template globals or filters on
the Flask app.  They are kept here so blueprints can share the same
templates without duplicating registration logic.
"""

from flask import session

from .runtime_state import (
    get_current_game,
    get_controllers,
    get_index_data,
    get_output_logger,
)
from .auth_utils import user_role, logged_in, roles_for_username


# ---------------------------------------------------------------------------
# Control / ownership helpers
# ---------------------------------------------------------------------------

def controller_of(entity_uid, username):
    """Return True if ``username`` currently controls ``entity_uid``."""
    if username == 'dm':
        return True

    current_game = get_current_game()
    entity = current_game.get_entity_by_uid(entity_uid)
    if hasattr(entity, 'owner') and entity.owner:
        entity_uid = entity.owner.entity_uid

    for info in get_controllers():
        if info['entity_uid'].lower() == entity_uid.lower() and username in info['controllers']:
            return True

    return False


def can_rest_for(entity_uid):
    """Template helper: True if the current user may issue rest commands."""
    try:
        if 'dm' in user_role():
            return True
        return controller_of(entity_uid, session.get('username'))
    except Exception:
        return False


def battle_active():
    """Return True when a battle is currently in progress."""
    try:
        current_game = get_current_game()
        return current_game.get_current_battle() is not None
    except Exception:
        return False


def within_talking_distance(entity_uid):
    """Return True if the POV entity is within 2 tiles and has line of sight."""
    if not entity_uid:
        return False
    current_game = get_current_game()
    current_map = current_game.get_map_for_user(session['username'])
    pov_entity = current_game.get_pov_entity_for_user(session['username'])
    if not pov_entity:
        return False
    target = current_game.get_entity_by_uid(entity_uid)
    if target is None:
        return False
    try:
        pov_map = current_game.get_map_for_entity(pov_entity) or current_map
        target_map = current_game.get_map_for_entity(target)
        battle = current_game.get_current_battle()

        if pov_map and target_map and pov_map is not target_map and battle is not None:
            from natural20.utils.conversation import _shared_stack
            from natural20.map_stack_targeting import stack_entity_distance_squares
            stack = _shared_stack(pov_map, target_map)
            if stack is not None and battle.can_see(pov_entity, target):
                dist_sq = stack_entity_distance_squares(
                    stack, pov_entity, pov_map, target, target_map,
                )
                return dist_sq <= 2

        if not current_map or pov_entity not in current_map.entities:
            return False
        if current_map.entity_by_uid(entity_uid) is None:
            return False
        return (
            current_map.distance(pov_entity.entity_uid, entity_uid) <= 2
            and current_map.can_see(pov_entity, entity_uid)
        )
    except (ValueError, TypeError):
        return False


def entities_controlled_by(username, battle_map=None):
    """Return entities controlled by ``username``."""
    current_game = get_current_game()
    entities = []
    for info in get_controllers():
        if username in info['controllers']:
            entity_uid = info['entity_uid']
            if battle_map:
                entity = battle_map.entity_by_uid(entity_uid)
            else:
                entity = current_game.get_entity_by_uid(entity_uid)

            if entity and entity not in entities:
                entities.append(entity)
                entities.extend(current_game.entities_owned_by(entity))

    return entities


def visible_log_messages_for_username(username, roles=None):
    """Return combat-log messages visible to ``username``."""
    if roles is None:
        roles = roles_for_username(username)
    return get_output_logger().get_all_logs(username=username, roles=roles)


def entity_owners(entity):
    """Return usernames that control ``entity``."""
    from natural20.entity import Entity

    if isinstance(entity, Entity):
        if hasattr(entity, 'owner') and entity.owner:
            entity_uid = entity.owner.entity_uid
        else:
            entity_uid = entity.entity_uid
    else:
        entity_uid = entity

    ctrl_info = next(
        (c for c in get_controllers() if c['entity_uid'] == entity_uid),
        None,
    )
    return [] if not ctrl_info else ctrl_info['controllers']


# ---------------------------------------------------------------------------
# Tile visual helpers
# ---------------------------------------------------------------------------

def opacity_for(tile):
    if tile['hiding']:
        return 0.7
    elif tile['dead']:
        return 0.4
    else:
        return 1.0


def transform_for(tile):
    transforms = []
    entity_size = tile.get('entity_size', None)
    if entity_size == 'medium':
        transforms.append('scale(0.8)')
    if entity_size == 'small':
        transforms.append('scale(0.6)')
    elif entity_size == 'tiny':
        transforms.append('scale(0.3)')

    if tile.get('prone', False):
        transforms.append('rotate(90deg)')
    if len(transforms) > 0:
        return ' '.join(transforms)
    else:
        return 'none'


def filter_for(tile):
    filters = []
    if tile['dead']:
        filters.append('brightness(50%) sepia(100%) hue-rotate(180deg)')
    elif tile['unconscious']:
        filters.append('brightness(50%)')
    elif tile['darkvision_color']:
        filters.append('grayscale(100%)')

    if len(filters) > 0:
        return ' '.join(filters)
    else:
        return 'none'


# ---------------------------------------------------------------------------
# Action display helpers
# ---------------------------------------------------------------------------

def interact_flavors(action):
    from natural20.actions.interact_action import InteractAction
    if isinstance(action, InteractAction):
        if action.object_action == 'give':
            return action.target.profile_image()
    return ""


def action_flavors(action):
    from natural20.actions.attack_action import AttackAction
    from natural20.actions.interact_action import InteractAction
    if isinstance(action, AttackAction):
        if action.second_hand():
            return "_second"
        elif action.unarmed():
            return "_melee"
        elif action.thrown:
            return "_thrown"
        elif action.ranged_attack():
            return "_ranged"
        else:
            return ""
    elif isinstance(action, InteractAction):
        if action.object_action:
            return f"_{action.object_action_name()}"
    return ""


def action_descriptor(action):
    from natural20.utils.action_bar import action_descriptor as _action_descriptor
    return _action_descriptor(action)


def spell_descriptor(spell_name, at_level=0):
    from natural20.utils.action_bar import spell_descriptor as _spell_descriptor
    return _spell_descriptor(spell_name, at_level)


def item_descriptor(item):
    from natural20.utils.action_bar import item_descriptor as _item_descriptor
    return _item_descriptor(item)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def ability_mod_str(ability_mod):
    if ability_mod is None:
        return ""
    if ability_mod >= 0:
        return f"+{ability_mod}"
    else:
        return str(ability_mod)


def casting_time(casting_time):
    if not casting_time:
        return ""
    text = str(casting_time)
    if ":" not in text:
        return text
    qty, resource = text.split(":", 1)
    if resource == "no_action":
        return "Free"
    mapping = {
        "action": "A", "reaction": "R", "bonus_action": "B", "bonus": "B",
        "hour": "H", "minute": "M", "round": "R",
    }
    r_str = mapping.get(resource)
    if r_str is None:
        return text
    return f"{qty}{r_str}"


_RESOURCE_POOL_MINOR_WORDS = {"of", "the", "and", "to", "in", "on", "for", "with", "a", "an"}
_RESOURCE_POOL_ACRONYMS = {"hp", "la", "d20"}


def resource_pool_label(name):
    """Display label for a snake_case resource pool key.

    ``'flash_of_genius'`` -> ``'Flash of Genius'``,
    ``'lay_on_hands'`` -> ``'Lay on Hands'``,
    ``'superiority_dice'`` -> ``'Superiority Dice'``.
    """
    if not name:
        return ""
    words = str(name).replace("_", " ").split()
    out = []
    for i, word in enumerate(words):
        if word.isupper() or word.lower() in _RESOURCE_POOL_ACRONYMS:
            out.append(word.upper())
        elif i > 0 and word.lower() in _RESOURCE_POOL_MINOR_WORDS:
            out.append(word.lower())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def format_languages(entity):
    """Return a display string of languages known by ``entity``, or empty string."""
    langs = []
    try:
        if hasattr(entity, 'languages') and callable(entity.languages):
            langs = entity.languages() or []
    except Exception:
        langs = []
    if not langs:
        props = getattr(entity, 'properties', None) or {}
        langs = props.get('languages') or []
    if isinstance(langs, str):
        langs = [langs]
    formatted = []
    for lang in langs:
        label = str(lang).strip().replace('_', ' ')
        if label:
            formatted.append(label.title())
    # Preserve order while deduplicating (PlayerCharacter.languages already sorts).
    seen = set()
    unique = []
    for label in formatted:
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(label)
    return ', '.join(unique)


def format_game_time(total_seconds):
    """Format game time in seconds to a human-readable format."""
    if total_seconds is None:
        return "0 seconds"
    total_seconds = int(total_seconds)
    days = total_seconds // (24 * 60 * 60)
    hours = (total_seconds % (24 * 60 * 60)) // (60 * 60)
    minutes = (total_seconds % (60 * 60)) // 60
    seconds = total_seconds % 60

    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return ', '.join(parts)


def t(key):
    import i18n
    from natural20.utils.localization import resolve_locale_text
    return resolve_locale_text(i18n.t, key)


def describe_terrain(tile):
    """Legacy Jinja helper; tooltips are precomputed in JsonRenderer."""
    from flask import g
    from natural20.web.terrain_tooltip import build_terrain_tooltip

    current_game = get_current_game()
    if not hasattr(g, '_describe_terrain_ctx'):
        g._describe_terrain_ctx = (
            current_game.get_map_for_user(session['username']),
            current_game.get_current_battle(),
        )
    battle_map, battle = g._describe_terrain_ctx
    if tile.get('terrain_tooltip') is not None:
        return tile['terrain_tooltip']
    return build_terrain_tooltip(tile, battle_map, battle)


# ---------------------------------------------------------------------------
# Registration helper — call once from app.py or each blueprint
# ---------------------------------------------------------------------------

def reaction_template_for(reaction_type):
    """Resolve a reaction partial template, falling back to generic."""
    from jinja2 import TemplateNotFound

    if not reaction_type:
        return 'reactions/generic.html'
    candidate = f'reactions/{reaction_type}.html'
    try:
        from flask import current_app
        current_app.jinja_env.get_template(candidate)
        return candidate
    except (TemplateNotFound, Exception):
        return 'reactions/generic.html'


def format_outward_appearance(entity):
    from natural20.utils.outward_appearance import (
        explicit_outward_appearance,
        resolve_outward_appearance,
    )
    from .runtime_state import get_game_session

    if entity is None:
        return ''
    props = getattr(entity, 'properties', {}) or {}
    explicit = explicit_outward_appearance(props)
    resolved = resolve_outward_appearance(entity, session=get_game_session())
    return {
        'explicit': explicit,
        'resolved': resolved,
        'is_derived': not bool(explicit),
    }


def ai_agent_options():
    """Named agent bundles from the ``.ai.yaml`` ``agents:`` registry.

    Returns ``[{'name', 'label', 'controller'}, ...]`` for the controller
    selects (turn order, add-to-combat, DM console default)."""
    try:
        from webapp.ai_config import get_ai_config
        config = get_ai_config()
        if config is None:
            return []
        return [
            {'name': name,
             'label': (definition or {}).get('label') or name,
             'controller': (definition or {}).get('controller') or 'llm'}
            for name, definition in (config.merged.get('agents') or {}).items()
        ]
    except Exception:
        return []


def register_template_globals(flask_app):
    """Register all template globals/filters on ``flask_app``."""
    from webapp.blueprints.helpers.action_utils import process_action_hash
    from webapp.blueprints.helpers.asset_utils import asset_build_version, asset_url

    @flask_app.context_processor
    def _inject_asset_context():
        version = asset_build_version()
        return {'salt': version, 'asset_url': asset_url}

    flask_app.add_template_global(asset_url, name='asset_url')
    flask_app.add_template_global(controller_of, name='controller_of')
    flask_app.add_template_global(can_rest_for, name='can_rest_for')
    flask_app.add_template_global(battle_active, name='battle_active')
    flask_app.add_template_global(within_talking_distance, name='within_talking_distance')
    flask_app.add_template_global(opacity_for, name='opacity_for')
    flask_app.add_template_global(transform_for, name='transform_for')
    flask_app.add_template_global(filter_for, name='filter_for')
    flask_app.add_template_global(action_flavors, name='action_flavors')
    flask_app.add_template_global(action_descriptor, name='action_descriptor')
    flask_app.add_template_global(spell_descriptor, name='spell_descriptor')
    flask_app.add_template_global(item_descriptor, name='item_descriptor')
    flask_app.add_template_global(entity_owners, name='entity_owners')
    flask_app.add_template_global(format_languages, name='format_languages')
    flask_app.add_template_global(format_outward_appearance, name='format_outward_appearance')
    flask_app.add_template_global(t, name='t')
    flask_app.add_template_global(describe_terrain, name='describe_terrain')
    flask_app.add_template_global(process_action_hash, name='process_action_hash')
    flask_app.add_template_global(reaction_template_for, name='reaction_template_for')
    flask_app.add_template_global(ai_agent_options, name='ai_agent_options')

    from webapp.blueprints.helpers.campaign_config import character_description_suppressed_for, highlight_doors_enabled

    @flask_app.context_processor
    def _inject_campaign_ui_flags():
        try:
            index_data = get_index_data() or {}
        except KeyError:
            index_data = {}
        raw = index_data.get('suppress_dialog_descriptions')
        suppress_all = raw is True or raw == 'all'
        viewer_is_dm = 'dm' in user_role()
        tts_delivery_mode = None
        enabled_raw = str(flask_app.config.get('TTS_ENABLED', '0')).lower()
        if enabled_raw in ('1', 'true', 'yes'):
            try:
                from webapp.tts.preferences import get_delivery_mode

                tts_delivery_mode = get_delivery_mode()
            except Exception:
                tts_delivery_mode = 'async'
        return {
            'viewer_is_dm': viewer_is_dm,
            'suppress_dialog_descriptions': suppress_all and not viewer_is_dm,
            'character_description_suppressed_for': character_description_suppressed_for,
            'highlight_doors': highlight_doors_enabled(index_data),
            'tts_delivery_mode': tts_delivery_mode,
        }
    flask_app.add_template_filter(ability_mod_str, name='mod_str')
    flask_app.add_template_filter(casting_time, name='casting_time')
    flask_app.add_template_filter(format_game_time, name='format_game_time')
    flask_app.add_template_filter(resource_pool_label, name='resource_pool_label')
