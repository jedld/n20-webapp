"""Auth blueprint — login, logout, character selection routes.

Extracted from webapp/app.py.
Routes: /login, /logout, /character_selection, /select_character
"""
from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template

from .helpers.runtime_state import (
    get_app,
    get_logins,
    get_current_game,
    get_index_data,
    get_title,
    get_character_selection_background,
    get_builder_only_mode,
    get_logger,
    get_game_session,
)
from .helpers.auth_utils import logged_in, user_role
from .helpers.pvp import (
    selectable_character_entry,
    pvp_team_config,
    pvp_team_counts,
    ensure_controller_entry,
    assign_character_team_and_spawn,
    ensure_character_entity_loaded,
    restore_player_character_for_user,
)
from .helpers.character_selection import (
    collect_selectable_entries,
    is_sidekick_selectable,
    load_character_selection_settings,
    partition_selectables,
    taken_selectable_uids,
)
from .helpers.template_globals import entities_controlled_by, entity_owners
from .helpers.login_ui import build_login_page_context

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'], endpoint='login')
def login():
    logger = get_logger()
    if get_builder_only_mode():
        return redirect(url_for('character.character_builder'))
    if request.method == 'POST':
        username = request.form['username'].lower()
        password = request.form['password']

        logins = get_logins()
        login_info = next((login for login in logins if login["name"].lower() == username), None)
        if login_info and login_info["password"] == password:
            session['username'] = username

            if 'dm' in login_info.get('role', []):
                current_game = get_current_game()
                current_game.set_pov_entity_for_user(username, None)
                try:
                    current_game.switch_map_for_user(username, current_game.get_current_battle_map().name)
                except Exception:
                    logger.exception(f"Failed to switch DM {username} to the current battle map")
                if get_app().config.get('EDIT_MODE'):
                    session['edit_mode'] = True

            # Spawn deferred PCs for this user on first login
            current_game = get_current_game()
            restore_player_character_for_user(username)
            current_game.ensure_user_entities_spawned(username)

            # Check if user has any assigned controllers
            user_entities = entities_controlled_by(username)
            if not user_entities and 'dm' not in user_role():
                # Redirect to character selection if no characters assigned
                return jsonify(status='character_selection_required')

            return jsonify(status='ok')
        return jsonify(error="Invalid Login Credentials")

    return render_template(
        'login.html',
        **build_login_page_context(title=get_title(), index_data=get_index_data()),
    )


@auth_bp.route('/character_selection', methods=['GET'], endpoint='character_selection')
def character_selection():
    if not logged_in():
        return redirect(url_for('auth.login'))

    # Check if user already has characters assigned
    user_entities = entities_controlled_by(session['username'])
    if user_entities:
        return redirect(url_for('navigation.index'))

    selectable_characters = collect_selectable_entries(get_index_data(), get_game_session())
    player_characters, selectable_sidekicks = partition_selectables(selectable_characters)
    taken_characters = taken_selectable_uids(get_game_session())
    selection_settings = load_character_selection_settings(
        get_game_session(), get_index_data()
    )

    return render_template('character_selection.html',
                         title=get_title(),
                         background=get_character_selection_background(),
                         selectable_characters=selectable_characters,
                         player_characters=player_characters,
                         selectable_sidekicks=selectable_sidekicks,
                         taken_characters=taken_characters,
                         character_selection_config=selection_settings,
                         pvp_team_config=pvp_team_config(),
                         pvp_team_counts=pvp_team_counts())


def _unclaim_controller(entity_uid, username):
    entry = ensure_controller_entry(entity_uid)
    controllers = entry.get('controllers') or []
    entry['controllers'] = [name for name in controllers if str(name).lower() != str(username).lower()]
    return entry


def _claim_controller(entity_uid, username):
    entry = ensure_controller_entry(entity_uid)
    if username not in entry['controllers']:
        entry['controllers'].append(username)
    return entry


def _register_sidekick_selection(character_name, username, logger):
    current_game = get_current_game()
    selectable = selectable_character_entry(character_name) or {}
    if not is_sidekick_selectable(selectable):
        return None
    entity = current_game.get_entity_by_uid(character_name)
    if entity is None:
        entity = ensure_character_entity_loaded(character_name)
    if entity is None:
        return 'Failed to load sidekick'
    try:
        from webapp.blueprints.helpers.sidekick_control import join_party_and_control
        join_party_and_control(
            entity,
            [username],
            notify=False,
            sidekick_class=selectable.get('sidekick_class'),
            sidekick_level=selectable.get('sidekick_level'),
            role=selectable.get('role'),
        )
    except Exception:
        logger.exception('Failed to register selected sidekick')
        return 'Failed to register sidekick'
    return None


def _materialize_login_pick(character_name, username, selected_team, logger):
    """Claim a selectable PC or sidekick for ``username``. Returns an error string or None."""
    taken = taken_selectable_uids(get_game_session())
    if character_name in taken:
        return 'Character is already taken'

    team_config = pvp_team_config()
    selectable = selectable_character_entry(character_name) or {}
    as_sidekick = is_sidekick_selectable(selectable)

    _claim_controller(character_name, username)
    if as_sidekick:
        error = _register_sidekick_selection(character_name, username, logger)
        if error:
            _unclaim_controller(character_name, username)
        return error

    if team_config:
        try:
            assign_character_team_and_spawn(character_name, selected_team)
        except ValueError as exc:
            _unclaim_controller(character_name, username)
            return str(exc)
        return None

    try:
        entity = ensure_character_entity_loaded(character_name)
        if entity is None:
            _unclaim_controller(character_name, username)
            return 'No spawn slot available for this character'
    except Exception as exc:
        logger.exception(f"Failed to materialize character {character_name}")
        _unclaim_controller(character_name, username)
        return f'Failed to load character: {exc}'

    # The materialized entity may already exist under a different uid (the
    # sheet stem, e.g. 'high_elf_mage' for the 'crysania' pick). Ownership and
    # action-bar access are keyed by entity uid, so claim that uid too; if a
    # different player already controls it, the slot is taken.
    entity_uid = str(getattr(entity, 'entity_uid', '') or '')
    if entity_uid and entity_uid != str(character_name):
        other_owners = [owner for owner in entity_owners(entity) if owner != username]
        if other_owners:
            _unclaim_controller(character_name, username)
            return 'Character is already taken'
        _claim_controller(entity_uid, username)
    return None


@auth_bp.route('/select_character', methods=['POST'], endpoint='select_character')
def select_character():
    logger = get_logger()
    if not logged_in():
        return jsonify(error="Not logged in"), 401

    character_name = (request.form.get('character') or '').strip()
    sidekick_name = (request.form.get('sidekick') or '').strip()
    selected_team = (request.form.get('team') or '').lower()
    username = session['username']

    if not character_name:
        return jsonify(error="No character specified")

    hero_entry = selectable_character_entry(character_name)
    if hero_entry is None:
        return jsonify(error="Invalid character selection")

    settings = load_character_selection_settings(get_game_session(), get_index_data())
    sidekick_slots = int(settings.get('sidekick_slots') or 0)
    taken = taken_selectable_uids(get_game_session())
    available_sidekicks = [
        entry for entry in collect_selectable_entries(get_index_data(), get_game_session())
        if is_sidekick_selectable(entry) and str(entry.get('name') or '') not in taken
    ]

    if sidekick_slots > 0:
        if is_sidekick_selectable(hero_entry):
            return jsonify(error="Choose a player character and a sidekick")
        if sidekick_name:
            sidekick_entry = selectable_character_entry(sidekick_name)
            if sidekick_entry is None or not is_sidekick_selectable(sidekick_entry):
                return jsonify(error="Invalid sidekick selection")
            if sidekick_name == character_name:
                return jsonify(error="Choose two different characters")
        elif available_sidekicks:
            return jsonify(error="Choose a sidekick companion")
    elif sidekick_name:
        return jsonify(error="This campaign does not use a separate sidekick pick")

    team_config = pvp_team_config()
    if team_config and not selected_team:
        return jsonify(error='Choose Team A or Team B before confirming your slot')

    error = _materialize_login_pick(character_name, username, selected_team, logger)
    if error:
        return jsonify(error=error)

    if sidekick_name:
        error = _materialize_login_pick(sidekick_name, username, selected_team, logger)
        if error:
            _unclaim_controller(character_name, username)
            return jsonify(error=error)

    session['assigned_character_uid'] = character_name

    current_game = get_current_game()
    current_game._setup_controllers()
    current_game.spawn_player_for_user(username)

    try:
        entity = current_game.get_entity_by_uid(character_name)
        if entity is not None:
            current_game.set_pov_entity_for_user(username, entity)
            try:
                entity_map = current_game.get_map_for_entity(entity)
                if entity_map is not None:
                    current_game.switch_map_for_user(username, entity_map.name)
            except Exception:
                pass
    except Exception:
        pass

    if sidekick_name:
        logger.info(f"User {username} selected character {character_name} with sidekick {sidekick_name}")
    else:
        logger.info(f"User {username} selected character {character_name}")
    return jsonify(status='ok')


@auth_bp.route('/logout', methods=['POST', 'GET'], endpoint='logout')
def logout():
    session.pop('assigned_character_uid', None)
    session.pop('edit_mode', None)
    session['username'] = None
    return redirect(url_for('auth.login'))
