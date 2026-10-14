"""Load campaign ``index.json`` settings for the webapp bootstrap."""
import json
import os


def dialog_description_suppressed_for(entity=None, index_data=None):
    """Return True when entity descriptions should be hidden for a non-DM viewer.

    ``index.json`` key ``suppress_dialog_descriptions``:
    - ``false`` / omitted — show descriptions (default)
    - ``true`` or ``"all"`` — hide for every entity
    - ``"player_characters"`` — hide only for player characters (pregen spoilers)
    """
    if index_data is None:
        from webapp.blueprints.helpers.runtime_state import get_index_data

        try:
            index_data = get_index_data() or {}
        except KeyError:
            index_data = {}

    raw = index_data.get('suppress_dialog_descriptions')
    if not raw:
        return False
    if raw is True or raw == 'all':
        return True
    if raw in ('player_characters', 'players'):
        if entity is None:
            return True
        from natural20.player_character import PlayerCharacter

        return isinstance(entity, PlayerCharacter)
    if isinstance(raw, dict):
        scope = (raw.get('scope') or 'all').lower()
        if scope in ('player_characters', 'players'):
            if entity is None:
                return True
            from natural20.player_character import PlayerCharacter

            return isinstance(entity, PlayerCharacter)
        return scope == 'all'
    return bool(raw)


def character_description_suppressed_for(entity=None, *, viewer_is_dm=False, index_data=None):
    """Return True when descriptions should be hidden in player-facing UI."""
    if viewer_is_dm:
        return False
    return dialog_description_suppressed_for(entity, index_data=index_data)


def highlight_doors_enabled(index_data=None):
    """Return True when the campaign wants visible door fixtures highlighted on the map."""
    if index_data is None:
        from webapp.blueprints.helpers.runtime_state import get_index_data

        try:
            index_data = get_index_data() or {}
        except KeyError:
            index_data = {}

    return bool(index_data.get('highlight_doors'))


def pickpocket_enabled(*, pc_to_pc=True, index_data=None):
    """Return True when pickpocket attempts are allowed.

    Campaign ``index.json`` keys (checked in this order):
    - ``pickpocket.enabled`` – global toggle (default ``true``).
    - ``pickpocket.allow_pc_to_pc`` – when ``false`` (or when the global
      ``pickpocket.enabled`` is ``false``) PC-to-PC pickpocket is blocked.

    Parameters
    ----------
    pc_to_pc : bool
        Whether the caller is a PC targeting another PC.  When this is
        ``True`` the function additionally checks
        ``pickpocket.allow_pc_to_pc``.
    """
    if index_data is None:
        from webapp.blueprints.helpers.runtime_state import get_index_data

        try:
            index_data = get_index_data() or {}
        except KeyError:
            index_data = {}

    pickpocket_cfg = index_data.get('pickpocket', {})
    if isinstance(pickpocket_cfg, dict):
        enabled = pickpocket_cfg.get('enabled', True)
        if not enabled:
            return False
        if pc_to_pc:
            allow_pc = pickpocket_cfg.get('allow_pc_to_pc', True)
            if not allow_pc:
                return False
        return True
    # Backwards compat: top-level boolean
    if pickpocket_cfg in (False, None):
        return False
    if pc_to_pc:
        allow_pc = index_data.get('pickpocket_allow_pc_to_pc', True)
        return bool(allow_pc)
    return bool(pickpocket_cfg)


def pickpocket_enabled_direct(*, pc_to_pc=True, index_data=None):
    """Direct lookup of pickpocket settings (no runtime_state dependency).

    Useful when called from the core engine (natural20 package) where
    Flask runtime state may not be available.

    ``index_data`` should be the campaign dict loaded from ``index.json``.
    """
    pickpocket_cfg = index_data.get('pickpocket', {})
    if isinstance(pickpocket_cfg, dict):
        enabled = pickpocket_cfg.get('enabled', True)
        if not enabled:
            return False
        if pc_to_pc:
            allow_pc = pickpocket_cfg.get('allow_pc_to_pc', True)
            if not allow_pc:
                return False
        return True
    # Backwards compat: top-level boolean
    if pickpocket_cfg in (False, None):
        return False
    if pc_to_pc:
        allow_pc = index_data.get('pickpocket_allow_pc_to_pc', True)
        return bool(allow_pc)
    return bool(pickpocket_cfg)


def _resolve_campaign_level(level=None):
    """Return an absolute campaign root (directory containing index.json)."""
    level = level or os.getenv('TEMPLATE_DIR', '../templates')
    level = level.rstrip('/\\')
    campaigns_dir = os.getenv('N20_CAMPAIGNS_DIR')

    helpers_dir = os.path.dirname(os.path.abspath(__file__))
    webapp_pkg = os.path.dirname(os.path.dirname(helpers_dir))
    webapp_repo = os.path.dirname(webapp_pkg)
    engine_root = os.path.dirname(webapp_repo)
    if os.path.isdir(os.path.join(engine_root, 'natural20')):
        if not campaigns_dir:
            default_campaigns = os.path.join(engine_root, 'user_levels')
            if os.path.isdir(default_campaigns):
                campaigns_dir = default_campaigns
    else:
        engine_root = None

    if campaigns_dir and not os.path.isabs(level) and os.sep not in level and '/' not in level:
        slug_candidate = os.path.join(campaigns_dir, level)
        if os.path.isfile(os.path.join(slug_candidate, 'index.json')):
            return os.path.normpath(os.path.abspath(slug_candidate))

    if os.path.isabs(level):
        return os.path.normpath(level)

    search_roots = [webapp_pkg, webapp_repo, os.getcwd()]
    if engine_root:
        search_roots.insert(0, engine_root)
    if campaigns_dir:
        search_roots.insert(0, os.path.abspath(campaigns_dir))
    for base in search_roots:
        candidate = os.path.normpath(os.path.join(base, level))
        if os.path.isfile(os.path.join(candidate, 'index.json')):
            return candidate
    # user_levels/foo when campaigns_dir is the user_levels root
    if campaigns_dir and level.startswith('user_levels/'):
        tail = level.split('user_levels/', 1)[-1]
        candidate = os.path.normpath(os.path.join(campaigns_dir, tail))
        if os.path.isfile(os.path.join(candidate, 'index.json')):
            return candidate
    return os.path.abspath(level)


def load_campaign_config(level=None):
    """Return campaign settings dict from ``index.json`` under ``level``."""
    level = _resolve_campaign_level(level)
    with open(os.path.join(level, 'index.json')) as f:
        index_data = json.load(f)

    return {
        'LEVEL': level,
        'index_data': index_data,
        'TITLE': index_data['title'],
        'TILE_PX': int(index_data['tile_size']),
        'LOGIN_BACKGROUND': index_data.get('login_background', ''),
        'CHARACTER_SELECTION_BACKGROUND': index_data.get(
            'character_selection_background',
            index_data.get('login_background', ''),
        ),
        'BATTLEMAP': index_data['map'],
        'OTHERMAPS': index_data.get('other_maps', {}),
        'SOUNDTRACKS': index_data.get('soundtracks', []),
        'LOGINS': index_data['logins'],
        'DEFAULT_NPC_CONTROLLER': index_data.get('npc_default_controller', 'ai'),
        'CONTROLLERS': index_data.get('default_controllers', []),
        'AUTOSAVE': index_data.get('autosave', False),
        'DEFER_PLAYER_SPAWN': index_data.get('defer_player_spawn', False),
        'SUPPRESS_DIALOG_DESCRIPTIONS': index_data.get('suppress_dialog_descriptions', False),
        'EXTENSIONS': [],
        'MAP_PADDING': [6, 15],
    }
