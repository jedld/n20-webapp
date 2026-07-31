"""Navigation blueprint — index, map switching, pathfinding, and map tile updates.

Extracted from webapp/app.py.
Routes: /, /command, /reload_map, /response, /focus, /switch_map,
        /path, /path/cost_map, /jump_info, /refresh-portraits,
        /update, /mark_note_read
"""
import json
import random
import time
from collections import OrderedDict

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    render_template,
    make_response,
)

from natural20.ai.path_compute import PathCompute
from natural20.ai.pathfinding_cost_map import build_pathfinding_snapshot
from natural20.player_character import PlayerCharacter
from webapp.pov_utils import build_portrait_render_context, dm_pov_portrait_entities
from natural20.web.json_renderer import JsonRenderer
from natural20.web.stack_renderer import build_stack_render_layers, stack_for_map, stack_floors_for_ui, annotate_base_tiles_with_stack_overlay_entities

from .helpers.auth_utils import logged_in, user_role
from .helpers.pvp import pvp_team_config
from .helpers.runtime_state import (
    get_current_game,
    get_game_session,
    get_socketio,
    get_controllers,
    get_active_effects,
    get_active_effects_map,
    get_level,
    get_title,
    get_logger,
    get_builder_only_mode,
    get_edit_mode,
    get_logins,
    get_tile_px,
    get_map_padding,
)
from .helpers.special_effects import (
    special_effects_enabled,
    map_default_effect_payloads,
    has_enabled_effect_payloads,
)
from .helpers.template_globals import (
    entities_controlled_by,
    visible_log_messages_for_username,
)

navigation_bp = Blueprint('navigation', __name__)

# In-process per-user LRU cache for /path responses; bounded to keep memory
# flat under sustained mouse hover.
_PATH_RESPONSE_CACHE = {}
_PATH_RESPONSE_CACHE_LIMIT = 256

# Memoize a per-(map, entity, battle) difficult-terrain lookup.
_DIFFICULT_TERRAIN_CACHE = {}

eval_context = {}


def _battle_map_for_render(current_game, username, *, is_dm=False):
    """Map used for tile rendering — follows the user's active map view."""
    return current_game.get_map_for_user(username)


def _sync_stack_pov_to_view_map(current_game, battle_map, pov_entities):
    """Never relocate POV tokens to match the viewed floor — only the camera may change."""
    return


def _stack_composite_mode(stack_layers):
    return bool(stack_layers and stack_layers.get('composite_mode'))


def _stack_underlay_mode(stack_layers):
    """Legacy alias — composite upstairs view."""
    return _stack_composite_mode(stack_layers)


def _stack_floors_for_user(session, battle_map):
    stack = stack_for_map(session, battle_map)
    return stack_floors_for_ui(stack)


def _render_tiles_for_user(current_game, battle_map, battle, map_padding, pov_entities, logger=None):
    """Return (stack_layers dict|None, tiles list for template)."""
    session = current_game.game_session
    stack = stack_for_map(session, battle_map)
    if stack is not None:
        floor = stack.floor_for_map(battle_map.name)
        if floor is not None and floor.is_base:
            renderer = JsonRenderer(battle_map, battle, padding=map_padding, logger=logger)
            tiles = renderer.render(entity_pov=pov_entities)
            annotate_base_tiles_with_stack_overlay_entities(
                stack, tiles, battle, pov_entities, battle_map,
            )
            stack_layers = {
                'id': stack.id,
                'floors': stack_floors_for_ui(stack),
                'active_map': battle_map.name,
                'composite_mode': False,
            }
            return stack_layers, [tiles]
        stack_layers = build_stack_render_layers(
            session, battle_map, battle, padding=map_padding, entity_pov=pov_entities,
        )
        if stack_layers and stack_layers.get('composite_mode'):
            stack_layers['floors'] = stack_floors_for_ui(stack)
            base_layer = stack_layers['layers'][0]
            return stack_layers, [base_layer['tiles']]
    renderer = JsonRenderer(battle_map, battle, padding=map_padding, logger=logger)
    return None, [renderer.render(entity_pov=pov_entities)]


def _stack_display_map(current_game, battle_map):
    """When compositing an overlay, canvas size matches the base map."""
    stack = stack_for_map(current_game.game_session, battle_map)
    if stack is not None:
        floor = stack.floor_for_map(battle_map.name)
        if floor is not None and not floor.is_base:
            base = next((f for f in stack.floors if f.is_base), None)
            if base is not None:
                return base.map
    return battle_map


def _background_for_stack_view(current_game, username, battle_map, display_map, stack_composite):
    """Base-map background when compositing; otherwise the active map background."""
    if stack_composite and display_map is not battle_map:
        try:
            state = current_game.get_time_of_day_state() if hasattr(current_game, 'get_time_of_day_state') else None
            if state is not None:
                filename = display_map.background_image_for_time_of_day(state)
                if filename:
                    return f'maps/{filename}'
        except Exception:
            pass
        try:
            filename = display_map.background_image()
            if filename:
                return f'maps/{filename}'
        except Exception:
            pass
        return f'maps/{display_map.name}.png'
    return current_game.get_background_image_for_user(username)


def _difficult_terrain_lookup(battle_map, entity, battle):
    """Return a callable(x, y) -> bool with per-tile memoization."""
    entity_uid = None
    try:
        eu = getattr(entity, 'entity_uid', None)
        entity_uid = eu() if callable(eu) else eu
    except Exception:
        entity_uid = None
    key = (id(battle_map), entity_uid, id(battle))
    bucket = _DIFFICULT_TERRAIN_CACHE.get(key)
    if bucket is None:
        bucket = {}
        if len(_DIFFICULT_TERRAIN_CACHE) > 32:
            _DIFFICULT_TERRAIN_CACHE.clear()
        _DIFFICULT_TERRAIN_CACHE[key] = bucket

    def _lookup(x, y):
        k = (x, y)
        v = bucket.get(k)
        if v is None:
            v = bool(battle_map.difficult_terrain(entity, x, y, battle))
            bucket[k] = v
        return v

    return _lookup


def pov_entities():
    current_game = get_current_game()
    if 'dm' in user_role():
        battle_map = current_game.get_map_for_user(session['username'])
        return dm_pov_portrait_entities(current_game, battle_map)
    return entities_controlled_by(session['username'])


def render_pov_entities():
    current_game = get_current_game()
    if 'dm' in user_role():
        pov_entity = current_game.get_pov_entity_for_user(session['username'])
        return [pov_entity] if pov_entity else None
    return entities_controlled_by(session['username'])


@navigation_bp.route('/', endpoint='index')
def index():
    logger = get_logger()
    current_game = get_current_game()
    tile_px = get_tile_px()
    map_padding = get_map_padding()

    if get_builder_only_mode():
        return redirect(url_for('character.character_builder'))
    if get_edit_mode() and 'username' not in session:
        session['username'] = next(
            (str(login['name']).lower() for login in (get_logins() or []) if 'dm' in login.get('role', [])),
            'dm',
        )
    if not logged_in():
        print("not logged in")
        return redirect(url_for('auth.login'))

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
            battle_map = _battle_map_for_render(current_game, session['username'])
    else:
        battle_map = _battle_map_for_render(current_game, session['username'], is_dm=True)

    battle = current_game.get_current_battle()
    available_maps = current_game.get_available_maps()
    rendered_pov_entities = entities_controlled_by(session['username'], battle_map)
    if not rendered_pov_entities:
        rendered_pov_entities = render_pov_entities()
    _sync_stack_pov_to_view_map(current_game, battle_map, rendered_pov_entities)

    stack_layers, my_2d_array = _render_tiles_for_user(
        current_game, battle_map, battle, map_padding, rendered_pov_entities, logger=logger,
    )
    display_map = _stack_display_map(current_game, battle_map)
    stack_underlay_mode = _stack_underlay_mode(stack_layers)
    stack_composite = _stack_composite_mode(stack_layers)
    stack_floors = _stack_floors_for_user(current_game.game_session, battle_map)
    map_width, map_height = display_map.size
    left_offset_px, top_offset_px = display_map.image_offset_px
    background = _background_for_stack_view(
        current_game, session['username'], battle_map, display_map, stack_composite,
    )

    tiles_dimension_height = map_height * tile_px
    tiles_dimension_width = map_width * tile_px
    messages = visible_log_messages_for_username(session['username'], user_role())

    entity_ids = []
    for info in get_controllers():
        if session['username'] in info['controllers']:
            entity_ids.append(info['entity_uid'])
    web_extensions = battle_map.properties.get('extensions', {"web": {}})
    web_extensions = web_extensions.get('web', {})
    background_color = web_extensions.get('background_color', '#FFFFFF')
    width_px = (map_width + 2) * tile_px
    height_px = (map_height + 2) * tile_px
    if current_game.current_soundtrack:
        duration = max(1, int(current_game.current_soundtrack.get('duration') or 1))
        time_s = (time.time() - current_game.current_soundtrack['start_time']) % duration
        current_game.current_soundtrack['time'] = int(time_s)

    current_pov_entity = current_game.get_pov_entity_for_user(session['username'])
    if current_pov_entity and not battle:
        try:
            current_game.update_ambient_soundtrack(entity=current_pov_entity)
        except Exception:
            pass
    portrait_ctx = build_portrait_render_context(current_game, session['username'], user_role())
    return render_template(
        'index.html',
        tiles=my_2d_array,
        stack_layers=stack_layers,
        stack_underlay_mode=stack_underlay_mode,
        stack_floors=stack_floors,
        tile_size_px=tile_px,
        pov_entity=current_pov_entity,
        background_path=f"assets/{background}",
        background_width=tiles_dimension_width,
        messages=messages,
        current_map=battle_map.name,
        current_map_name=battle_map.name,
        read_notes=current_game.read_notes,
        is_setup=False,
        background_height=tiles_dimension_height,
        battle=battle,
        entity_ids=entity_ids,
        background_color=background_color,
        width_px=width_px,
        height_px=height_px,
        waiting_for_reaction=current_game.waiting_for_reaction,
        soundtrack=current_game.current_soundtrack,
        title=get_title(),
        top_offset_px=top_offset_px,
        left_offset_px=left_offset_px,
        available_maps=available_maps,
        user_entity_ids=[e.entity_uid for e in entities_controlled_by(session['username'])],
        current_pov=current_pov_entity.entity_uid if current_pov_entity else None,
        **portrait_ctx,
        game_session=current_game.game_session,
        username=session['username'],
        role=user_role(),
        DEFAULT_NPC_CONTROLLER=current_game.effective_npc_combat_controller(),
        NPC_LLM_COMBAT_ENABLED=current_game.force_llm_npc_combat,
        pvp_enabled=bool(pvp_team_config()),
        narration=battle_map.narration(),
        special_effects_enabled=special_effects_enabled(),
        edit_mode=get_edit_mode(),
    )


@navigation_bp.route('/command', methods=['POST'], endpoint='command')
def command():
    global eval_context
    current_game = get_current_game()
    game_session = get_game_session()
    logger = get_logger()
    battle_map = current_game.get_map_for_user(session['username'])
    battle = current_game.get_current_battle()

    command_text = request.form['command']
    logger.info(f"command: {command_text}")
    try:
        if command_text:
            battle_map = current_game.get_map_for_user(session['username'])
            battle = current_game.get_current_battle()
            eval_context.update({
                'map': battle_map,
                'battle': battle,
                'session': game_session,
                'game': current_game,
                'json': json,
            })

            if command_text.startswith('.'):
                command_text = f"map{command_text}"

            if command_text.startswith('!'):
                entity_uid = command_text[1:]
                entity = current_game.get_entity_by_uid(entity_uid)
                if entity:
                    eval_context[entity.name] = entity
                    eval_context['entity'] = entity
                    eval_context['entity_uid'] = entity_uid
                    return jsonify(str(entity))
                return jsonify(error=f"Entity {entity_uid} not found")

            output = eval(command_text, eval_context)
            eval_context['__output'] = output
            return jsonify(output)
    except Exception as e:
        return jsonify(error=str(e))
    return jsonify(status='ok')


@navigation_bp.route('/reload_map', methods=['POST'], endpoint='reload_map')
def reload_map():
    get_current_game().reload_map_for_user(session['username'])
    return jsonify(status='ok')


@navigation_bp.route('/response', methods=['POST'], endpoint='response')
def response():
    callback_id = request.json['callback']
    callback = get_current_game().callbacks.pop(callback_id, None)
    if callback:
        callback(request.json)
    return jsonify(status='ok')


@navigation_bp.route('/focus', methods=['POST'], endpoint='focus')
def focus():
    x = request.form['x']
    y = request.form['y']
    get_socketio().emit('message', {'type': 'focus', 'message': {'x': x, 'y': y}})
    return jsonify(status='ok')


@navigation_bp.route('/switch_map', methods=['POST'], endpoint='switch_map')
def switch_map():
    current_game = get_current_game()
    game_session = get_game_session()
    active_effects = get_active_effects()
    active_effects_map = get_active_effects_map()
    level = get_level()
    tile_px = get_tile_px()

    map_name = request.form['map']
    current_game.switch_map_for_user(session['username'], map_name)
    battle_map = current_game.get_map_for_user(session['username'])
    battle = current_game.get_current_battle()
    display_map = _stack_display_map(current_game, battle_map)
    stack = stack_for_map(current_game.game_session, battle_map)
    floor = stack.floor_for_map(battle_map.name) if stack else None
    stack_underlay_mode = bool(stack and floor and not floor.is_base)
    stack_composite = stack_underlay_mode
    background = _background_for_stack_view(
        current_game, session['username'], battle_map, display_map, stack_composite,
    )
    map_width, map_height = display_map.size
    tiles_dimension_height = map_height * tile_px
    tiles_dimension_width = map_width * tile_px

    map_default = None
    map_defaults = []
    try:
        map_defaults = map_default_effect_payloads(battle_map)
        map_default = map_defaults[0] if map_defaults else None
    except Exception:
        map_default = None

    dm_active = False
    try:
        game_key = (
            getattr(current_game.game_session, 'root_path', None)
            or getattr(game_session, 'root_path', None)
            or level
        )
        dm_active = has_enabled_effect_payloads(active_effects.get(game_key, {}).values())
        try:
            dm_active = dm_active or has_enabled_effect_payloads(
                active_effects_map.get(game_key, {}).get(map_name, {}).values()
            )
        except Exception:
            pass
    except Exception:
        dm_active = False

    return jsonify(
        background=f"assets/{background}",
        name=map_name,
        image_offset_px=display_map.image_offset_px,
        height=tiles_dimension_height,
        width=tiles_dimension_width,
        map_default_effect=map_default,
        map_default_effects=map_defaults,
        dm_active=dm_active,
        narration=battle_map.narration(),
        special_effects_enabled=special_effects_enabled(),
    )


def _resolve_path_source(battle_map, battle, username, source_x, source_y):
    """Resolve the moving entity and map-local path coordinates.

    In stack-composite overlay views the client may send world/base grid
    coordinates (from a projected base-layer token) while the active map is
    the overlay floor, which uses local coordinates.
    """
    source_x = int(source_x)
    source_y = int(source_y)
    entity = battle_map.entity_at(source_x, source_y)
    if entity is not None:
        return entity, source_x, source_y

    stack = getattr(battle_map, 'map_stack', None)
    floor = stack.floor_for_map(battle_map.name) if stack is not None else None

    if floor is not None and not floor.is_base:
        ax, ay = floor.anchor
        lx = source_x - int(ax)
        ly = source_y - int(ay)
        if 0 <= lx < battle_map.size[0] and 0 <= ly < battle_map.size[1]:
            entity = battle_map.entity_at(lx, ly)
            if entity is not None:
                return entity, lx, ly
            try:
                if battle_map.placeable(None, lx, ly, battle, True):
                    current_game = get_current_game()
                    pov = current_game.get_pov_entity_for_user(username)
                    if pov is not None:
                        return pov, lx, ly
            except Exception:
                pass

    current_game = get_current_game()
    pov = current_game.get_pov_entity_for_user(username)
    if pov is None:
        return None, source_x, source_y

    try:
        pos = battle_map.position_of(pov)
    except Exception:
        pos = None
    if pos is None:
        return None, source_x, source_y

    if floor is not None and not floor.is_base:
        return pov, int(pos[0]), int(pos[1])

    if stack is not None:
        local = stack.world_to_local(source_x, source_y, battle_map.name)
        if local is not None:
            lx, ly = local
            try:
                at_cell = battle_map.entity_at(lx, ly)
                if at_cell is pov or battle_map.placeable(pov, lx, ly, battle, True):
                    return pov, lx, ly
            except Exception:
                pass

    try:
        if battle_map.entity_at(int(pos[0]), int(pos[1])) is pov:
            return pov, int(pos[0]), int(pos[1])
    except Exception:
        pass

    return None, source_x, source_y


@navigation_bp.route('/path', methods=['GET'], endpoint='compute_path')
def compute_path():
    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session['username'])
    battle = current_game.get_current_battle()

    source = {
        'x': request.args.get('from[x]'),
        'y': request.args.get('from[y]'),
    }
    destination = {
        'x': request.args.get('to[x]'),
        'y': request.args.get('to[y]'),
    }
    dest = (int(destination['x']), int(destination['y']))
    entity_x = int(source['x'])
    entity_y = int(source['y'])

    allow_stack_descent = request.args.get('allow_stack_descent') in ('1', 'true', 'True')
    dest_map_name = request.args.get('to_map') or request.args.get('dest_map')

    accumulated_path = request.args.get('accumulatedPath')
    if accumulated_path:
        accumulated_path = json.loads(accumulated_path)
        accumulated_path = [tuple(coord) for coord in accumulated_path]
        accumulated_path = list(dict.fromkeys(accumulated_path))
        if len(accumulated_path) > 0:
            entity_x, entity_y = accumulated_path[0]
            entity_x = int(entity_x)
            entity_y = int(entity_y)
    else:
        accumulated_path = []

    entity = battle_map.entity_at(entity_x, entity_y)
    if entity is None:
        entity, entity_x, entity_y = _resolve_path_source(
            battle_map,
            battle,
            session.get('username') or 'anonymous',
            entity_x,
            entity_y,
        )
    if entity is None:
        return jsonify(error='No entity at source'), 400

    cache_key = (
        id(battle_map),
        getattr(entity, 'entity_uid', lambda: None)()
        if callable(getattr(entity, 'entity_uid', None))
        else getattr(entity, 'entity_uid', None),
        int(source['x']), int(source['y']),
        dest[0], dest[1],
        tuple(accumulated_path),
        bool(allow_stack_descent),
        dest_map_name or '',
    )
    user_key = session.get('username') or 'anonymous'
    proc_cache = _PATH_RESPONSE_CACHE.setdefault(user_key, OrderedDict())
    cached = proc_cache.get(cache_key)
    if cached is not None:
        proc_cache.move_to_end(cache_key)
        return jsonify(cached)

    if battle and entity in getattr(battle, 'entities', {}):
        available_movement = entity.available_movement(battle)
    else:
        available_movement = None

    path_compute = PathCompute(battle, battle_map, entity)

    target_map = battle_map
    if dest_map_name and dest_map_name != battle_map.name:
        target_map = current_game.session.maps.get(dest_map_name)
    elif allow_stack_descent:
        stack = getattr(battle_map, 'map_stack', None)
        if stack is not None:
            base_floor = next((f for f in stack.floors if f.is_base), None)
            if base_floor and base_floor.map_name != battle_map.name:
                blx, bly = stack.world_to_local(dest[0], dest[1], base_floor.map_name)
                if blx is not None and base_floor.map.passable(entity, blx, bly, battle, True):
                    target_map = base_floor.map

    if allow_stack_descent and target_map is not battle_map:
        from natural20.map_stack_descent import plan_voluntary_stack_route
        stack_plan = plan_voluntary_stack_route(
            path_compute,
            current_game.session,
            battle,
            entity,
            battle_map,
            entity_x,
            entity_y,
            target_map,
            dest[0],
            dest[1],
            available_movement_cost=available_movement,
            accumulated_path=accumulated_path,
        )
        if stack_plan:
            proc_cache[cache_key] = stack_plan
            if len(proc_cache) > _PATH_RESPONSE_CACHE_LIMIT:
                proc_cache.popitem(last=False)
            return jsonify(stack_plan)

    path = path_compute.compute_path(
        entity_x,
        entity_y,
        dest[0], dest[1],
        accumulated_path=accumulated_path,
        available_movement_cost=available_movement,
    )
    if not path or dest not in path:
        path = path_compute.compute_path(
            entity_x,
            entity_y,
            dest[0], dest[1],
            accumulated_path=accumulated_path,
            available_movement_cost=available_movement,
            door_navigation=True,
        )

    if accumulated_path:
        full_path = list(accumulated_path)
        full_path.extend(path[1:])
    else:
        full_path = path

    cost = battle_map.movement_cost(entity, full_path)
    placeable = battle_map.placeable(entity, dest[0], dest[1], battle, False)

    terrain_info = []
    if path:
        diff_lookup = _difficult_terrain_lookup(battle_map, entity, battle)
        for x, y in path:
            terrain_info.append({
                'x': x,
                'y': y,
                'difficult': bool(diff_lookup(x, y)),
            })

    path_data = {
        "path": path,
        "cost": cost.to_dict(),
        "placeable": placeable,
        "terrain_info": terrain_info,
    }

    proc_cache[cache_key] = path_data
    if len(proc_cache) > _PATH_RESPONSE_CACHE_LIMIT:
        proc_cache.popitem(last=False)
    return jsonify(path_data)


@navigation_bp.route('/path/cost_map', methods=['GET'], endpoint='path_cost_map')
def path_cost_map():
    """Return a pathfinding snapshot for client-side A*."""
    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session['username'])
    battle = current_game.get_current_battle()

    try:
        source_x = int(request.args.get('from[x]'))
        source_y = int(request.args.get('from[y]'))
    except (TypeError, ValueError):
        return jsonify(error='from[x] and from[y] are required'), 400

    entity = battle_map.entity_at(source_x, source_y)
    if entity is None:
        entity, source_x, source_y = _resolve_path_source(
            battle_map,
            battle,
            session.get('username') or 'anonymous',
            source_x,
            source_y,
        )
    if entity is None:
        return jsonify(error='No entity at source'), 400

    if battle and entity in getattr(battle, 'entities', {}):
        available_movement = entity.available_movement(battle)
    else:
        available_movement = None

    snapshot = build_pathfinding_snapshot(
        battle_map,
        entity,
        battle,
        ignore_opposing=False,
    )
    return jsonify({
        'snapshot': snapshot,
        'available_movement': available_movement,
        'feet_per_grid': battle_map.feet_per_grid,
    })


@navigation_bp.route('/jump_info', methods=['GET'], endpoint='jump_info')
def jump_info():
    """Return jump distance information for an entity."""
    logger = get_logger()
    current_game = get_current_game()
    try:
        entity_id = request.args.get('id') or request.args.get('entity_id')
        if not entity_id:
            return jsonify(error='Missing entity id'), 400
        entity = current_game.get_entity_by_uid(entity_id)
        if not entity:
            return jsonify(error='Entity not found'), 404

        battle_map = current_game.get_map_for_entity(entity)
        feet_per_grid = getattr(battle_map, 'feet_per_grid', 5)
        try:
            standing_grids = int(entity.standing_jump_distance() / feet_per_grid)
        except Exception:
            standing_grids = 0
        try:
            running_grids = int(entity.long_jump_distance() / feet_per_grid)
        except Exception:
            running_grids = standing_grids

        running_flag = request.args.get('running')
        if running_flag is not None:
            running_flag = running_flag in ('1', 'true', 'True')
            grids = running_grids if running_flag else standing_grids
        else:
            grids = running_grids

        return jsonify({
            'feet_per_grid': feet_per_grid,
            'standing_grids': standing_grids,
            'running_grids': running_grids,
            'grids': grids,
        })
    except Exception as e:
        logger.exception('Failed to compute jump info')
        return jsonify(error=str(e)), 500


@navigation_bp.route('/switch_pov', methods=['POST'], endpoint='switch_pov')
def switch_pov():
    from webapp.pov_utils import user_may_set_pov_entity

    current_game = get_current_game()
    tile_px = get_tile_px()
    active_effects = get_active_effects()
    active_effects_map = get_active_effects_map()
    battle_map = current_game.get_map_for_user(session['username'])
    if request.is_json:
        entity_id = (request.get_json(silent=True) or {}).get('entity_uid')
    else:
        entity_id = request.form.get('entity_uid')
    entity = current_game.get_entity_by_uid(entity_id)
    if entity is None:
        return jsonify(error='Entity not found'), 404
    if not user_may_set_pov_entity(
        current_game,
        session['username'],
        user_role(),
        entity,
        battle_map=battle_map,
    ):
        return jsonify(error='Forbidden'), 403

    entity_battle_map = current_game.get_map_for_entity(entity)
    if entity_battle_map is None:
        return jsonify(error='Entity is not currently on a map'), 409

    current_game.set_pov_entity_for_user(session['username'], entity)
    map_changed = battle_map != entity_battle_map
    if map_changed:
        current_game.switch_map_for_user(session['username'], entity_battle_map.name)
    background = current_game.get_background_image_for_user(session['username'])
    map_width, map_height = entity_battle_map.size
    tiles_dimension_height = map_height * tile_px
    tiles_dimension_width = map_width * tile_px
    dm_active = False
    map_default = None
    map_defaults = []
    if map_changed:
        try:
            map_defaults = map_default_effect_payloads(entity_battle_map)
            map_default = map_defaults[0] if map_defaults else None
        except Exception:
            map_default = None
        try:
            game_key = (
                getattr(current_game.game_session, 'root_path', None)
                or get_level()
            )
            dm_active = has_enabled_effect_payloads(active_effects.get(game_key, {}).values())
            try:
                dm_active = dm_active or has_enabled_effect_payloads(
                    active_effects_map.get(game_key, {}).get(entity_battle_map.name, {}).values()
                )
            except Exception:
                pass
        except Exception:
            dm_active = False
    return jsonify(
        background=f"assets/{background}",
        name=entity_battle_map.name,
        pov_entity=entity_id,
        image_offset_px=entity_battle_map.image_offset_px,
        height=tiles_dimension_height,
        width=tiles_dimension_width,
        map_default_effect=map_default,
        map_default_effects=map_defaults,
        dm_active=dm_active,
        special_effects_enabled=special_effects_enabled(),
    )


@navigation_bp.route('/refresh-portraits', methods=['GET'], endpoint='refresh_portraits')
def refresh_portraits():
    """Endpoint to refresh the floating entity portraits."""
    current_game = get_current_game()
    username = session.get('username')
    if not username:
        return "", 200
    portrait_ctx = build_portrait_render_context(current_game, username, user_role())
    return render_template(
        'floating_portraits.html',
        **portrait_ctx,
    )


@navigation_bp.route('/update', endpoint='update')
def update():
    logger = get_logger()
    current_game = get_current_game()
    map_padding = get_map_padding()
    tile_px = get_tile_px()

    x = int(request.args.get('x'))
    y = int(request.args.get('y'))
    entity_uid = request.args.get('entity_uid')
    is_pov = request.args.get('pov', 'false') == 'true'
    battle_map = _battle_map_for_render(current_game, session['username'])
    battle = current_game.get_current_battle()

    entity = battle_map.entity_by_uid(entity_uid) if entity_uid else battle_map.entity_at(x, y)
    pov_entity = current_game.get_pov_entity_for_user(session['username'])
    is_setup = request.args.get('is_setup') == 'true'
    if is_setup:
        # Battle setup must reveal every party PC on this map, not only the DM's
        # current POV camera (which would hide off-screen allies from initiative UI).
        if 'dm' in user_role():
            _pov_entities = [
                e for e in battle_map.entities
                if isinstance(e, PlayerCharacter)
            ]
        else:
            _pov_entities = entities_controlled_by(session['username'], battle_map) or []
        if not _pov_entities:
            _pov_entities = render_pov_entities()
    else:
        _pov_entities = entities_controlled_by(session['username'], battle_map)
        if not _pov_entities:
            _pov_entities = render_pov_entities()

    if entity and ('dm' in user_role() or entity in entities_controlled_by(session['username'], battle_map)):
        current_game.set_pov_entity_for_user(session['username'], entity)
        pov_entity = entity
        _pov_entities = entities_controlled_by(session['username'], battle_map) or [entity]
    elif is_pov and not entity:
        current_game.set_pov_entity_for_user(session['username'], None)
        pov_entity = None
        _pov_entities = entities_controlled_by(session['username'], battle_map) or render_pov_entities()

    if 'dm' not in user_role() and pov_entity is None and (_pov_entities is None or len(_pov_entities) == 0):
        user_entities = entities_controlled_by(session['username'], battle_map)
        _pov_entities = user_entities if user_entities else []

    logger.debug(f"entity: {entity}, pov_entity: {pov_entity}, _pov_entities: {_pov_entities}")
    _sync_stack_pov_to_view_map(current_game, battle_map, _pov_entities)
    _t_render = time.perf_counter()
    stack_layers, my_2d_array = _render_tiles_for_user(
        current_game, battle_map, battle, map_padding, _pov_entities, logger=logger,
    )
    stack_underlay_mode = _stack_underlay_mode(stack_layers)
    stack_floors = _stack_floors_for_user(current_game.game_session, battle_map)
    _t_after_render = time.perf_counter()
    response = render_template(
        'map.html',
        pov_entity=pov_entity,
        tiles=my_2d_array,
        stack_layers=stack_layers,
        stack_underlay_mode=stack_underlay_mode,
        stack_floors=stack_floors,
        tile_size_px=tile_px,
        random=random,
        is_setup=is_setup,
        current_map_name=battle_map.name,
        read_notes=current_game.read_notes,
    )
    _t_after_tpl = time.perf_counter()
    resp = make_response(response)
    resp.headers['Server-Timing'] = (
        f"render;dur={(_t_after_render - _t_render) * 1000:.1f}, "
        f"template;dur={(_t_after_tpl - _t_after_render) * 1000:.1f}"
    )
    return resp


@navigation_bp.route('/mark_note_read', methods=['POST'], endpoint='mark_note_read')
def mark_note_read():
    note_id = request.json.get('note_id') if request.is_json else request.form.get('note_id')
    if not note_id:
        return jsonify(error="No note_id provided"), 400
    get_current_game().read_notes.add(note_id)
    return jsonify(ok=True)
