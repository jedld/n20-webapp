"""3D VTT sibling client — GET /3d.

Serves a thin Jinja shell around the Three.js map viewport. Auth and POV
gates match GET / so players land on the same campaign session.

Blueprints must not import each other; map lookup goes through GameManagement.
"""
from flask import (
    Blueprint,
    redirect,
    render_template,
    session,
    url_for,
)

from .helpers.auth_utils import logged_in, session_username, user_role
from .helpers.pvp import pvp_team_config
from .helpers.runtime_state import (
    get_builder_only_mode,
    get_controllers,
    get_current_game,
    get_edit_mode,
    get_title,
)
from .helpers.template_globals import entities_controlled_by

client_3d_bp = Blueprint('client_3d', __name__)


@client_3d_bp.route('/3d', endpoint='index')
def index_3d():
    if get_builder_only_mode():
        return redirect(url_for('character.character_builder'))
    if not logged_in():
        return redirect(url_for('auth.login'))

    current_game = get_current_game()
    username = session_username()
    current_game.ensure_user_entities_spawned(session['username'])

    if 'dm' not in user_role():
        user_entities = entities_controlled_by(session['username'])
        if not user_entities:
            return redirect(url_for('auth.character_selection'))
        pov_entity = current_game.get_pov_entity_for_user(session['username'])
        if not pov_entity:
            current_game.set_pov_entity_for_user(session['username'], user_entities[0])
            pov_entity = user_entities[0]
            entity_map = current_game.get_map_for_entity(pov_entity)
            if entity_map is not None:
                try:
                    current_game.switch_map_for_user(session['username'], entity_map.name)
                except Exception:
                    pass
            battle_map = entity_map or current_game.get_map_for_user(session['username'])
        else:
            battle_map = current_game.get_map_for_user(session['username'])
    else:
        battle_map = current_game.get_map_for_user(session['username'])

    battle = current_game.get_current_battle()
    current_pov_entity = current_game.get_pov_entity_for_user(session['username'])
    entity_ids = []
    for info in get_controllers():
        if session['username'] in info['controllers']:
            entity_ids.append(info['entity_uid'])

    return render_template(
        'index_3d.html',
        title=get_title(),
        username=username,
        role=user_role(),
        current_map=getattr(battle_map, 'name', None),
        current_map_name=getattr(battle_map, 'name', None),
        current_pov=current_pov_entity.entity_uid if current_pov_entity else None,
        battle=battle,
        entity_ids=entity_ids,
        user_entity_ids=[e.entity_uid for e in entities_controlled_by(session['username'])],
        pvp_enabled=bool(pvp_team_config()),
        edit_mode=get_edit_mode(),
    )
