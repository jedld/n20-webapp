"""Battle blueprint — combat loop, actions, and turn management.

Extracted from webapp/app.py.
"""
import json
import os
import threading
import time
import uuid
from collections import OrderedDict

from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template

# Timing instrumentation toggle — set N20_DEBUG_TIMING=1 to log per-step timings.
_DEBUG_TIMING = bool(int(os.environ.get('N20_DEBUG_TIMING', '0')))


def _timing_log(msg):
    if _DEBUG_TIMING:
        try:
            get_logger().warning(f"[timing] {msg}")
        except Exception:
            pass

from natural20.utils.serialization import object_type_to_klass
from natural20.actions.attack_action import AttackAction, TwoWeaponAttackAction, LinkedAttackAction
from natural20.actions.move_action import MoveAction
from natural20.actions.spell_action import SpellAction
from natural20.actions.interact_action import InteractAction
from natural20.actions.use_item_action import UseItemAction
from natural20.actions.ready_action import ReadyAction
from natural20.spell.extensions.hit_computations import AttackSpell
from natural20.entity import Entity
from natural20.action import Action, AsyncReactionHandler, BardicInspirationPrompt
from natural20.battle import Battle
from natural20.generic_controller import GenericController
from natural20.llm_controller import LlmMcpController
from natural20.web.web_controller import WebController
from natural20.dm import DungeonMaster
from natural20.die_roll import DieRoll
from natural20.utils.action_builder import acquire_targets
from natural20.utils.target_validation import (
    add_validation_issue,
    issue as targeting_issue,
    validation_response_payload,
)

from webapp.ai_config import combat_llm_provider_for

from .helpers.auth_utils import logged_in, user_role
from .helpers.pvp import autofill_pvp_battle_turn_order
from .helpers.journal_utils import _record_narration_for_pcs
from .helpers.special_effects import (
    special_effects_enabled,
    map_default_effect_payloads,
    has_enabled_effect_payloads,
)
from .helpers.template_globals import (
    entities_controlled_by,
    entity_owners,
    controller_of,
    visible_log_messages_for_username,
    reaction_template_for,
)
from .helpers.npc_stat_block import lookup_info_entity, render_info_page
from .helpers.action_utils import (
    action_type_to_class,
    apply_knock_unconscious_opt,
    resolve_requested_action_type,
    resolve_use_item_name,
)
from .helpers.action_bar import resolve_actions_for_id
from .helpers.runtime_state import (
    get_current_game,
    get_game_session,
    get_socketio,
    get_output_logger,
    get_llm_handler,
    get_logger,
    get_logins,
    get_tile_px,
    get_level,
    get_active_effects,
    get_active_effects_map,
    get_entity_rag_handler,
)

battle_bp = Blueprint('battle', __name__)

# LRU cache and singleflight coalescing for expensive hide-spot scans.
_HIDE_SPOTS_CACHE = OrderedDict()
_HIDE_SPOTS_CACHE_LIMIT = 128
_HIDE_SPOTS_CACHE_LOCK = threading.Lock()
_HIDE_SPOTS_INFLIGHT = {}


def _validate_opposing_groups(battle):
    """Validate that at least one entity in the battle has opposing enemies.

    This prevents accidental "friendly fire only" battles where every combatant
    belongs to the same non-hostile group (e.g. all PCs with no enemies).
    """
    # combat_order is only populated by battle.start(), so check the roster.
    for entity in list(battle.entities.keys()):
        try:
            opponents = battle.opponents_of(entity)
            if opponents:
                return  # At least one entity has enemies — valid battle
        except Exception:
            continue
    raise ValueError(
        "Roll Initiative requires at least one opposing entity group. "
        "Add enemies to the battle or assign different groups."
    )


def _battle_maps_for(current_game, entities):
    """Maps hosting the combatants (falls back to the game's default battle map)."""
    maps = []
    for entity in entities:
        for battle_map in (getattr(current_game, 'maps', None) or {}).values():
            try:
                hosts = entity in battle_map.entities or (
                    battle_map.entity_by_uid(entity.entity_uid) is not None
                )
            except Exception:
                hosts = False
            if hosts and battle_map not in maps:
                maps.append(battle_map)
                break
    if not maps:
        default_map = current_game.get_current_battle_map()
        if default_map is not None:
            maps.append(default_map)
    return maps


def _hide_cache_key(current_game, entity, battle, battle_map):
    """Build a cache key that changes only when hide-relevant state changes."""
    try:
        entity_pos = battle_map.entity_or_object_pos(entity)
    except Exception:
        entity_pos = None

    enemy_signature = []
    if battle is not None:
        try:
            for opp in (battle.opponents_of(entity) or []):
                try:
                    if not opp.conscious():
                        continue
                except Exception:
                    continue
                try:
                    pos = battle.entity_or_object_pos(opp)
                except Exception:
                    pos = None
                if pos is None:
                    continue
                enemy_signature.append((str(getattr(opp, 'entity_uid', '')), int(pos[0]), int(pos[1])))
        except Exception:
            pass
    enemy_signature.sort()

    return (
        str(getattr(battle_map, 'name', '')),
        str(getattr(entity, 'entity_uid', '')),
        tuple(entity_pos) if entity_pos is not None else None,
        tuple(enemy_signature),
    )


@battle_bp.route('/api/combat-log', methods=['GET'])
def combat_log():
    battle = get_current_game().get_current_battle()
    logs = visible_log_messages_for_username(session['username'], user_role())
    response =[{'message': log} for log in logs]
    return jsonify(combat_log=response)

@battle_bp.route('/combat-log', methods=['GET'])
def get_combat_log():
    battle = get_current_game().get_current_battle()
    logs = visible_log_messages_for_username(session['username'], user_role())
    return render_template('combat-log.html', combat_log=logs,
                           username=session['username'], role=user_role())
@battle_bp.route('/start', methods=['POST'])
def start_battle():
    if get_current_game().trigger_event('start_battle'):
        battle_map = get_current_game().get_current_battle_map()
        get_current_game().set_current_battle(Battle(get_game_session(), battle_map, animation_log_enabled=True))
    return jsonify(status='ok')

@battle_bp.route('/stop', methods=['POST'])
def stop_battle():
    battle = get_current_game().get_current_battle()

    if battle:
        get_current_game().end_current_battle()
    return jsonify(status='ok')

@battle_bp.route('/battle', methods=['POST'])
def start_battle_with_initiative():
    if not get_current_game().trigger_event('start_battle'):

        current_game = get_current_game()

        if not request.json or 'battle_turn_order' not in request.json:
            return jsonify(error='No entities in turn order'), 400

        battle_turn_order = autofill_pvp_battle_turn_order(request.json['battle_turn_order'])
        combatants = []
        for param_item in battle_turn_order:
            entity = current_game.get_entity_by_uid(param_item['id'])
            if entity is None:
                return jsonify(error=f"Unknown combatant: {param_item['id']}"), 400
            combatants.append((param_item, entity))

        battle = Battle(
            get_game_session(),
            _battle_maps_for(current_game, [entity for _item, entity in combatants]),
            animation_log_enabled=True,
        )

        for param_item, entity in combatants:
            ctrl_kind = param_item.get('controller')
            controller = None
            if ctrl_kind and ctrl_kind != 'manual':
                # 'ai' | 'llm' | 'hybrid' | 'agent:<name>' (a named .ai.yaml
                # agent bundle: providers + kind + standing directives).
                from webapp.ai_config import (build_combat_controller_for_spec,
                                              is_controller_spec)
                if not is_controller_spec(ctrl_kind):
                    return jsonify(error=f"Unknown controller {ctrl_kind!r}"), 400
                controller = build_combat_controller_for_spec(
                    get_game_session(), get_llm_handler(), entity, ctrl_kind)
            if controller is None:
                controller = current_game.controller_registry.manual_controller_for_entity(entity)

            controller.register_handlers_on(entity)
            battle.add(entity, param_item['group'], controller=controller)

        try:
            # Validate that at least one opposing entity group exists
            _validate_opposing_groups(battle)
        except ValueError as e:
            return jsonify(error=str(e)), 400

        current_game.set_current_battle(battle)
        battle.start()
        try:
            get_current_game().play_battle_music(battle)
        except Exception:
            pass
    else:
        print("skipping default battle start")
    scheduled = get_current_game().execute_game_loop()
    return jsonify(status='ok', game_loop='scheduled' if scheduled else 'already_running')


@battle_bp.route('/end_turn', methods=['POST'])
def end_turn():
    battle = get_current_game().get_current_battle()

    if battle and battle.legendary_phase:
        if not controller_of(battle.legendary_phase, session.get('username')):
            return jsonify(status='error', message='Waiting for a legendary action.'), 409
        get_current_game().advance_legendary_phase(battle)
        return jsonify(status='ok', legendary_phase=battle.legendary_phase)

    held = _end_held_turn()
    if held is not None:
        return held

    if get_current_game().waiting_for_reaction:
        # A reaction window (opportunity attack, Shield, ...) is open. Ending
        # the turn from another tab/DM would advance combat under the pending
        # generator and orphan it, so the loop never resumes.
        return jsonify(status='error', message='Waiting for a reaction.'), 409

    end_turn_state = True
    try:
        battle.end_turn()
        end_turn_state = False
        get_current_game().push_end_of_turn_animations(battle)
        if get_current_game().begin_legendary_phase(battle):
            return jsonify(status='ok', legendary_phase=battle.legendary_phase)
        battle.next_turn()
        continue_game()
        return jsonify(status='ok')
    except AsyncReactionHandler as e:
        for _, entity, valid_actions in e.resolve():
            valid_actions_str = [[str(action.uid), str(action), action] for action in valid_actions]
            get_current_game().waiting_for_reaction = [entity, e, e.resolve(), valid_actions_str]
            get_current_game().end_turn_state = end_turn_state
            get_socketio().emit('message', {'type': 'reaction', 'message': {'id': entity.entity_uid, 'reaction': e.reaction_type}})
        return jsonify(status='ok')


def continue_game():
    get_current_game().schedule_continue_game_loop()


def _end_held_turn():
    """End an AI turn held for DM narration; ``None`` when no turn is held."""
    game = get_current_game()
    if not game.dm_turn_hold:
        return None
    if 'dm' not in user_role():
        return jsonify(status='error', message='Waiting for the DM to end this turn.'), 409
    game.release_dm_turn_hold()
    return jsonify(status='ok', released=True)


@battle_bp.route('/battle_pacing', methods=['GET', 'POST'])
def battle_pacing():
    """DM controls: AI turns wait for the DM to end, and the pause between
    consecutive AI turns that lets browsers play each turn's animations."""
    if 'dm' not in user_role():
        return jsonify(error='Only DMs can change battle pacing'), 403
    game = get_current_game()
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        if 'dm_ends_ai_turns' in payload:
            game.set_dm_ends_ai_turns(bool(payload['dm_ends_ai_turns']))
        if 'ai_turn_delay' in payload:
            try:
                game.set_ai_turn_delay(payload['ai_turn_delay'])
            except (TypeError, ValueError):
                return jsonify(error='ai_turn_delay must be a number of seconds'), 400
        if 'dm_ends_ai_turns' not in payload and 'ai_turn_delay' not in payload:
            return jsonify(error='dm_ends_ai_turns or ai_turn_delay is required'), 400
    return jsonify(status='ok', **game.battle_pacing_state())


@battle_bp.route('/turn_order', methods=['GET'])
def get_turn_order():
    game = get_current_game()
    battle = game.get_current_battle()
    current_turn_index = getattr(battle, 'current_turn_index', 0) if battle else 0
    battle_round = getattr(battle, 'round', 0) if battle else 0
    return render_template(
        'battle.html',
        battle=battle,
        username=session['username'],
        role=user_role(),
        current_turn_index=current_turn_index,
        battle_round=battle_round,
        dm_ends_ai_turns=game.dm_ends_ai_turns(),
        dm_turn_hold=game.dm_turn_hold,
        ai_turn_delay=game.ai_turn_delay(),
    )

@battle_bp.route('/next_turn', methods=['POST'])
def next_turn():
    battle = get_current_game().get_current_battle()
    if battle and battle.legendary_phase:
        get_current_game().advance_legendary_phase(battle)
        return jsonify(status='ok', legendary_phase=battle.legendary_phase)
    held = _end_held_turn()
    if held is not None:
        return held
    if battle:
        with get_current_game().game_state_lock:
            current_turn = battle.current_turn()
            if get_current_game().waiting_for_user_input():
                get_current_game().set_waiting_for_user_input(False)
                battle.end_turn()
                get_current_game().push_end_of_turn_animations(battle)
                if get_current_game().begin_legendary_phase(battle):
                    return jsonify(status='ok', legendary_phase=battle.legendary_phase)
                battle.next_turn()
                if battle.battle_ends():
                    get_current_game().end_current_battle()
                    return jsonify(status='ok')

        if get_current_game().get_current_battle():
            get_current_game().schedule_continue_game_loop()

    return jsonify(status='ok')

@battle_bp.route('/reorder_initiative', methods=['POST'])
def reorder_initiative():
    
    # Check if user is DM
    if 'dm' not in user_role():
        return jsonify(error='Only DMs can reorder initiative'), 403
    
    battle = get_current_game().get_current_battle()
    if not battle:
        return jsonify(error='No active battle'), 400
    
    if not request.json or 'entity_order' not in request.json:
        return jsonify(error='No entity order provided'), 400
    
    entity_order = request.json['entity_order']
    
    try:
        # Use the lock to make the operation atomic
        with get_current_game().game_state_lock:
            battle.reorder_initiative(entity_order)
        
        # Notify all clients of the initiative update
        get_socketio().emit('message', { 'type': 'initiative', 'message': {'index': battle.current_turn_index}})
        
        get_logger().info(f"Initiative reordered by {session['username']}: {entity_order}")
        return jsonify(status='ok')
        
    except ValueError as e:
        get_logger().error(f"Failed to reorder initiative: {str(e)}")
        return jsonify(error=str(e)), 400
    except Exception as e:
        get_logger().error(f"Unexpected error reordering initiative: {str(e)}")
        return jsonify(error='Internal server error'), 500
@battle_bp.route('/actions', methods=['GET'])
def get_actions():
    current_user = session['username']
    entity_id = request.args.get('id')
    if entity_id is None:
        return jsonify(error="No entity id provided"), 400

    result = resolve_actions_for_id(entity_id, current_user)
    status = result.pop('status', 200)
    if status != 200:
        return jsonify(error=result.get('error', 'Request failed')), status
    return result['html']


@battle_bp.route('/actions/batch', methods=['GET'])
def get_actions_batch():
    current_user = session['username']
    ids_param = request.args.get('ids', '')
    entity_ids = [part.strip() for part in ids_param.split(',') if part.strip()]
    if not entity_ids:
        return jsonify(error="No entity ids provided"), 400

    sections = []
    for entity_id in entity_ids:
        result = resolve_actions_for_id(entity_id, current_user)
        status = result.pop('status', 200)
        section = {'id': result.get('id', entity_id)}
        if status == 200:
            section['html'] = result.get('html', '')
        else:
            section['error'] = result.get('error', 'Request failed')
        sections.append(section)

    return jsonify(sections=sections)

@battle_bp.route("/hide", methods=['GET'])
def get_hiding_spots():
    _t0 = time.perf_counter()
    current_game = get_current_game()
    battle = current_game.get_current_battle()
    entity_id = request.args.get('id')
    _t_lookup = time.perf_counter()
    entity = current_game.get_entity_by_uid(entity_id)
    lookup_ms = (time.perf_counter() - _t_lookup) * 1000.0
    if entity is None:
        return jsonify(error="Entity not found"), 404
    battle_map = current_game.get_map_for_entity(entity)
    if battle_map is None:
        return jsonify(error="Entity is not currently on a map"), 409

    cache_key = _hide_cache_key(current_game, entity, battle, battle_map)
    _t_cache = time.perf_counter()
    with _HIDE_SPOTS_CACHE_LOCK:
        cached = _HIDE_SPOTS_CACHE.get(cache_key)
        if cached is not None:
            _HIDE_SPOTS_CACHE.move_to_end(cache_key)
    if cached is not None:
        cache_ms = (time.perf_counter() - _t_cache) * 1000.0
        total_ms = (time.perf_counter() - _t0) * 1000.0
        hiding_spots = cached
        try:
            request._perf_meta = {
                **(getattr(request, '_perf_meta', {}) or {}),
                'entity_uid': getattr(entity, 'entity_uid', None),
                'map': battle_map.name,
                'battle': 'yes' if battle else 'no',
                'map_w': battle_map.size[0],
                'map_h': battle_map.size[1],
                'spot_count': len(hiding_spots or []),
                'lookup_ms': round(lookup_ms, 1),
                'compute_ms': 0.0,
                'hide_cache': 'hit',
                'hide_cache_lookup_ms': round(cache_ms, 1),
            }
        except Exception:
            pass
        _timing_log(
            f"hide:lookup={lookup_ms:.1f}ms cache_lookup={cache_ms:.1f}ms total={total_ms:.1f}ms "
            f"cache=hit entity={getattr(entity, 'entity_uid', None)} map={battle_map.name}"
        )
        response = jsonify(hiding_spots=hiding_spots)
        response.headers['Server-Timing'] = (
            f"hide_lookup;dur={lookup_ms:.1f}, "
            f"hide_cache;dur={cache_ms:.1f}, "
            f"hide_total;dur={total_ms:.1f}"
        )
        return response

    inflight_key = ('hide', cache_key)
    with _HIDE_SPOTS_CACHE_LOCK:
        wait_event = _HIDE_SPOTS_INFLIGHT.get(inflight_key)
        if wait_event is None:
            wait_event = threading.Event()
            _HIDE_SPOTS_INFLIGHT[inflight_key] = wait_event
            is_leader = True
        else:
            is_leader = False
    owns_inflight = is_leader

    if not is_leader:
        _t_wait = time.perf_counter()
        wait_event.wait(timeout=20.0)
        wait_ms = (time.perf_counter() - _t_wait) * 1000.0
        with _HIDE_SPOTS_CACHE_LOCK:
            cached = _HIDE_SPOTS_CACHE.get(cache_key)
            if cached is not None:
                _HIDE_SPOTS_CACHE.move_to_end(cache_key)
        if cached is not None:
            total_ms = (time.perf_counter() - _t0) * 1000.0
            hiding_spots = cached
            try:
                request._perf_meta = {
                    **(getattr(request, '_perf_meta', {}) or {}),
                    'entity_uid': getattr(entity, 'entity_uid', None),
                    'map': battle_map.name,
                    'battle': 'yes' if battle else 'no',
                    'map_w': battle_map.size[0],
                    'map_h': battle_map.size[1],
                    'spot_count': len(hiding_spots or []),
                    'lookup_ms': round(lookup_ms, 1),
                    'compute_ms': 0.0,
                    'hide_cache': 'coalesced_hit',
                    'hide_wait_ms': round(wait_ms, 1),
                }
            except Exception:
                pass
            _timing_log(
                f"hide:lookup={lookup_ms:.1f}ms wait={wait_ms:.1f}ms total={total_ms:.1f}ms "
                f"cache=coalesced_hit entity={getattr(entity, 'entity_uid', None)} map={battle_map.name}"
            )
            response = jsonify(hiding_spots=hiding_spots)
            response.headers['Server-Timing'] = (
                f"hide_lookup;dur={lookup_ms:.1f}, "
                f"hide_wait;dur={wait_ms:.1f}, "
                f"hide_total;dur={total_ms:.1f}"
            )
            return response

    _t_compute = time.perf_counter()
    try:
        hiding_spots = battle_map.hiding_spots_for(entity, battle)
        compute_ms = (time.perf_counter() - _t_compute) * 1000.0
        with _HIDE_SPOTS_CACHE_LOCK:
            _HIDE_SPOTS_CACHE[cache_key] = hiding_spots
            _HIDE_SPOTS_CACHE.move_to_end(cache_key)
            if len(_HIDE_SPOTS_CACHE) > _HIDE_SPOTS_CACHE_LIMIT:
                _HIDE_SPOTS_CACHE.popitem(last=False)
    finally:
        if owns_inflight:
            with _HIDE_SPOTS_CACHE_LOCK:
                event = _HIDE_SPOTS_INFLIGHT.pop(inflight_key, None)
                if event is not None:
                    event.set()

    total_ms = (time.perf_counter() - _t0) * 1000.0

    try:
        request._perf_meta = {
            **(getattr(request, '_perf_meta', {}) or {}),
            'entity_uid': getattr(entity, 'entity_uid', None),
            'map': battle_map.name,
            'battle': 'yes' if battle else 'no',
            'map_w': battle_map.size[0],
            'map_h': battle_map.size[1],
            'spot_count': len(hiding_spots or []),
            'lookup_ms': round(lookup_ms, 1),
            'compute_ms': round(compute_ms, 1),
            'hide_cache': 'miss',
        }
    except Exception:
        pass

    _timing_log(
        f"hide:lookup={lookup_ms:.1f}ms compute={compute_ms:.1f}ms total={total_ms:.1f}ms "
        f"entity={getattr(entity, 'entity_uid', None)} map={battle_map.name}"
    )

    response = jsonify(hiding_spots=hiding_spots)
    response.headers['Server-Timing'] = (
        f"hide_lookup;dur={lookup_ms:.1f}, "
        f"hide_compute;dur={compute_ms:.1f}, "
        f"hide_total;dur={total_ms:.1f}"
    )
    return response

def _entity_target_preview(entity, target_entity, action_info, opts, battle_map, battle):
    """Validate hover/click targets for entity-target actions (e.g. Bardic Inspiration)."""
    try:
        action_class = action_type_to_class(action_info)
    except ValueError:
        return None

    resolved_action_type = resolve_requested_action_type(
        entity,
        get_game_session(),
        battle,
        battle_map,
        action_class,
        opts.get('action_type'),
    )
    try:
        action_inst = action_class(get_game_session(), entity, resolved_action_type, opts)
    except TypeError:
        action_inst = action_class(get_game_session(), entity, resolved_action_type)
    build_map = action_inst.build_map()
    preview_action = action_inst
    if isinstance(build_map, dict) and build_map.get('action') is not None:
        preview_action = build_map['action']
    if not isinstance(build_map, dict):
        return None

    # Some actions (e.g. TwoWeaponAttackAction) prompt for a weapon/choice
    # before target selection. Resolve those deterministic steps from opts so
    # hover validation can still run on the eventual select_target stage.
    while isinstance(build_map, dict):
        params = build_map.get('param') or []
        if not params:
            break
        step_type = params[0].get('type')
        if step_type == 'select_weapon':
            weapon_choice = opts.get('using') or opts.get('weapon') or opts.get('npc_action')
            if weapon_choice is None:
                return None
            build_map = build_map['next'](weapon_choice)
            if isinstance(build_map, dict) and build_map.get('action') is not None:
                preview_action = build_map['action']
            elif isinstance(build_map, Action):
                preview_action = build_map
            continue
        if step_type == 'select_choice':
            choice = opts.get('choice')
            if choice is None:
                return None
            build_map = build_map['next'](choice)
            if isinstance(build_map, dict) and build_map.get('action') is not None:
                preview_action = build_map['action']
            elif isinstance(build_map, Action):
                preview_action = build_map
            continue
        break

    if not isinstance(build_map, dict):
        return None
    params = build_map.get('param') or []
    if not params or params[0].get('type') != 'select_target':
        return None

    if hasattr(preview_action, 'validate'):
        try:
            preview_action.validate(battle_map, target_entity, battle=battle)
        except TypeError:
            try:
                preview_action.validate(battle_map, target=target_entity, battle=battle)
            except TypeError:
                preview_action.validate(battle_map, target_entity)
        if preview_action.validation_failed():
            return {'valid_target': False, **validation_response_payload(preview_action)}

    if battle is not None:
        valid_targets = battle.valid_targets_for(entity, preview_action)
        if target_entity not in valid_targets:
            try:
                holder = action_class(get_game_session(), entity, resolved_action_type, opts)
            except TypeError:
                holder = action_class(get_game_session(), entity, resolved_action_type)
            add_validation_issue(holder, targeting_issue('validation.targeting.unavailable'))
            return {'valid_target': False, **validation_response_payload(holder)}
    return {'valid_target': True}


def _attack_action_from_opts(entity, opts, battle):
    action = AttackAction(get_game_session(), entity, 'attack')
    action.using = opts.get('using')
    action.npc_action = opts.get('npc_action', None)
    action.thrown = opts.get('thrown', False)
    if battle and battle.in_legendary_phase(entity):
        action.legendary_action = True
    return action


def _targetable_object_at(entity, action, battle, battle_map, x, y):
    objects = [o for o in (battle_map.objects_at(x, y) or []) if not o.dead() and o.allow_targeting()]
    if not objects or battle is None:
        return None
    valid = battle.valid_targets_for(entity, action, include_objects=True)
    return next((o for o in objects if o in valid), None)


def _blind_attack_target_square(entity, action, battle, battle_map, target_entity, target_map, x, y):
    """(x, y) when a click/hover should become a blind attack on that square.

    Applies when nothing the attacker can see is targetable there: the square
    looks empty or holds a creature the attacker can't see. The decision never
    depends on what is actually in the square.
    """
    if battle is None or entity is None or target_map is not battle_map:
        return None
    if target_entity is not None and target_entity is not entity and battle.can_see(entity, target_entity):
        return None
    if _targetable_object_at(entity, action, battle, battle_map, x, y) is not None:
        return None
    return (x, y)


def _blind_attack_preview(entity, action, battle, square):
    from natural20.utils.unseen_targets import blind_attack_issue, sensed_creatures
    issue_key = blind_attack_issue(battle, entity, action, square)
    if issue_key:
        invalid = targeting_issue(issue_key).to_dict()
        return dict(
            valid_target=False,
            blind_attack=True,
            errors=[invalid['key']],
            error_details=[invalid],
            error_messages=[invalid['message']],
            primary_error=invalid['message'],
        )
    try:
        _weapon, _name, attack_mod, _dmg, _ammo = action.get_weapon_info({})
    except Exception:
        attack_mod = None
    sensed = any(tuple(e['pos']) == tuple(square) for e in sensed_creatures(battle, entity))
    return dict(
        valid_target=True,
        blind_attack=True,
        sensed=sensed,
        adv_mod=-1,
        adv_info=[[], ['unseen_target']],
        attack_mod=attack_mod,
        blind_hint=(
            'Unseen target: attack with disadvantage. '
            'If nothing is in this square the attack misses.'
        ),
    )


@battle_bp.route('/target', methods=['GET'])
def get_target():
   
    battle = get_current_game().get_current_battle()
    payload = json.loads(request.args.get('payload'))
   
    entity_id = payload.get('id')
    x = int(payload.get('x'))
    y = int(payload.get('y'))
    action_info = payload.get('action_info')
    opts = payload.get('opts', {})
    choice = opts.get('choice')
    success_rate = None
    entity = get_current_game().get_entity_by_uid(entity_id)
    battle_map = get_current_game().get_map_for_entity(entity)
    stack = getattr(battle_map, 'map_stack', None)
    from natural20.map_stack_targeting import entity_at_stack_position
    target_position = [x, y]
    target_entity, target_map, tx, ty = entity_at_stack_position(stack, battle_map, x, y)

    if entity and battle and action_info in ['AttackAction', 'LinkedAttackAction']:
        blind_action = _attack_action_from_opts(entity, opts, battle)
        square = _blind_attack_target_square(
            entity, blind_action, battle, battle_map, target_entity, target_map, x, y,
        )
        if square is not None:
            return jsonify(**_blind_attack_preview(entity, blind_action, battle, square))

    if entity and target_entity and action_info in ['AttackAction', 'LinkedAttackAction']:
        action = _attack_action_from_opts(entity, opts, battle)
        action.target = target_entity

        adv_mod, adv_info, attack_mod = action.compute_advantage_info(battle)
        valid_target = target_entity.allow_targeting()
        if battle:
            valid_targets = battle.valid_targets_for(entity, action)
            valid_target = target_entity in valid_targets
        if not valid_target:
            invalid = targeting_issue('validation.targeting.not_enemy').to_dict()
            return jsonify(
                valid_target=False,
                adv_mod=adv_mod,
                adv_info=adv_info,
                attack_mod=attack_mod,
                errors=[invalid['key']],
                error_details=[invalid],
                error_messages=[invalid['message']],
                primary_error=invalid['message'],
            )
        return jsonify(valid_target=valid_target, adv_mod=adv_mod, adv_info=adv_info, attack_mod=attack_mod)

    elif entity and (target_entity or target_position) and action_info =='SpellAction':
        build_map = SpellAction.build(get_game_session(), entity)
        spell_choice = (opts['spell'], opts['at_level'])
        build_map = build_map['next'](spell_choice)
        if target_entity:
            target = target_entity
        else:
            target = target_position

        target_squares = []
        while not isinstance(build_map, Action):
            target_squares = []
            if build_map['param'][0]['type'] == 'select_choice':
                build_map = build_map['next'](choice)
            elif build_map['param'][0]['type'] == 'text_input':
                # Free-text step (Message, Sending): the hover preview just
                # carries whatever text is already in opts (or none).
                _field = build_map['param'][0].get('field', 'message')
                build_map = build_map['next'](
                    opts.get(_field) or build_map['param'][0].get('default') or '')
            elif build_map['param'][0]['type'] == 'select_empty_space':
                build_map = build_map['next'](target)
            elif build_map['param'][0]['type'] == 'select_target':
                build_map = build_map['next'](target)
            elif build_map['param'][0]['type'] == 'select_cone':
                entity_x, entity_y = battle_map.entity_or_object_pos(entity)
                require_los = build_map['param'][0]['require_los']
                target_squares = battle_map.squares_in_cone((entity_x, entity_y), (x, y), build_map['param'][0]['range'] // battle_map.feet_per_grid, require_los=require_los)
                build_map = build_map['next']([x, y])
            elif build_map['param'][0]['type'] == 'select_cube':
                # For directional cube AoE (e.g., Thunderwave) originating from caster
                entity_x, entity_y = battle_map.entity_or_object_pos(entity)
                target_squares = battle_map.squares_in_adjacent_cube((entity_x, entity_y), (x, y), size_squares=3)
                build_map = build_map['next']([x, y])
            elif build_map['param'][0]['type'] == 'select_radius':
                # Sphere AoE centered on the targeted square.
                radius_ft = build_map['param'][0].get('radius', 20)
                require_los = build_map['param'][0].get('require_los', False)
                target_squares = battle_map.squares_in_radius(
                    (x, y), radius_ft, require_los=require_los)
                build_map = build_map['next']([x, y])
            elif build_map['param'][0]['type'] == 'select_square':
                # Square/rectangular AoE. By default it extends from the
                # clicked square to the south-east; params with
                # ``centered: true`` (wall spells) are centered on it, and
                # ``width``/``height`` give non-square footprints.
                param_details = build_map['param'][0]
                size_ft = int(param_details.get('size', 10))
                side_w = max(1, int(param_details.get('width', size_ft)) // battle_map.feet_per_grid)
                side_h = max(1, int(param_details.get('height', size_ft)) // battle_map.feet_per_grid)
                ox = -(side_w // 2) if param_details.get('centered') else 0
                oy = -(side_h // 2) if param_details.get('centered') else 0
                target_squares = []
                for dx in range(side_w):
                    for dy in range(side_h):
                        tx, ty = x + ox + dx, y + oy + dy
                        if 0 <= tx < battle_map.size[0] and 0 <= ty < battle_map.size[1]:
                            target_squares.append([tx, ty])
                build_map = build_map['next']([x, y])
            elif build_map['param'][0]['type'] == 'select_line':
                entity_x, entity_y = battle_map.entity_or_object_pos(entity)
                length_ft = build_map['param'][0].get('range', 30)
                width_ft = build_map['param'][0].get('width', 5)
                target_squares = battle_map.squares_in_line(
                    (entity_x, entity_y), (x, y), length_ft, width_ft)
                build_map = build_map['next']([x, y])
            elif build_map['param'][0]['type'] == 'select_emanation':
                entity_x, entity_y = battle_map.entity_or_object_pos(entity)
                radius_ft = build_map['param'][0].get('radius', 10)
                target_squares = battle_map.squares_in_emanation(
                    entity, radius_ft, include_origin=True)
                build_map = build_map['next']([entity_x, entity_y])
            else:
                raise ValueError(f"Unknown action type {build_map['param'][0]['type']}")

        action = build_map

        if isinstance(action, AttackSpell):
            if not isinstance(target, Entity):
                # Spell attack must resolve to a real creature; otherwise
                # downstream advantage checks call ``target.has_effect`` on
                # raw [x, y] coords and crash. Surface as not-targetable.
                return jsonify(valid_target=False, target_squares=target_squares,
                               errors=['No valid target at the selected square.'])
            adv_mod, adv_info, attack_mod = action.compute_advantage_info(battle)
            valid_target = target.allow_targeting()

            if battle:
                valid_targets = battle.valid_targets_for(entity, action)
                valid_target = target in valid_targets
            return jsonify(valid_target=valid_target, target_squares=target_squares, adv_mod=adv_mod, adv_info=adv_info, attack_mod=attack_mod)
        else:
            validate_target = target_entity if target_entity is not None else [x, y]
            action.validate(battle_map, validate_target, battle=battle)
            if action.validation_failed():
                return jsonify(valid_target=False, target_squares=target_squares, **validation_response_payload(action))
            return jsonify(valid_target=True, target_squares=target_squares, errors=action.errors)
    elif entity and target_entity and action_info == 'UseItemAction':
        action = UseItemAction(get_game_session(), entity, 'use_item')
        valid_target = True
        return jsonify(valid_target=valid_target, adv_info=[[],[]], attack_mod=0)
    elif entity and target_entity:
        preview = _entity_target_preview(entity, target_entity, action_info, opts, battle_map, battle)
        if preview is not None:
            return jsonify(**preview)
        invalid = targeting_issue('validation.targeting.unavailable').to_dict()
        return jsonify(
            valid_target=False,
            errors=[invalid['key']],
            error_details=[invalid],
            error_messages=[invalid['message']],
            primary_error=invalid['message'],
        )

    return jsonify(success_rate=success_rate)

@battle_bp.route('/spells', methods=['GET'])
def get_spell():
    from natural20.utils.action_bar import build_spell_tab_groups

    battle_map = get_current_game().get_map_for_user(session['username'])
    battle = get_current_game().get_current_battle()

    entity_id = request.args.get('id')
    entity = battle_map.entity_by_uid(entity_id)
    spells_by_level = entity.castable_spells_by_level(battle)
    spell_tab_groups = build_spell_tab_groups(entity, spells_by_level)

    entity_x, entity_y = battle_map.entity_or_object_pos(entity)
    return render_template(
        'spells.html',
        entity=entity,
        spell_tab_groups=spell_tab_groups,
        entity_x=entity_x,
        entity_y=entity_y,
    )


@battle_bp.route('/reaction', methods=['GET'])
def get_reaction():
    battle = get_current_game().get_current_battle()
    pending = get_current_game().waiting_for_reaction_input()
    if not pending or not isinstance(pending, (list, tuple)) or len(pending) < 2:
        # Nothing pending: the POST that resolved it raced this GET, the
        # socket event was a duplicate, or the page loaded after resolution.
        # Return an empty fragment so the client hides the modal instead of
        # answering with a 500 (which left the reaction UI unrendered and
        # combat waiting for an input nobody was ever shown).
        return ''
    return render_template(reaction_template_for(pending[1].reaction_type),
                           username=session['username'],
                           waiting_for_reaction=get_current_game().waiting_for_reaction,
                           battle=battle)

@battle_bp.route('/reaction', methods=['POST'])
def handle_reaction():
    battle = get_current_game().get_current_battle()
    reaction_id = request.form.get('reaction')
    if not reaction_id:
        return jsonify(error="No reaction provided"), 400
    if not get_current_game().waiting_for_reaction:
        return jsonify(error="No reaction expected"), 400
    entity, handler, generator, valid_actions_str = get_current_game().waiting_for_reaction_input()

    if reaction_id == 'no-reaction':
        handler.send(None)
    else:
        for _, _, action in valid_actions_str:
            print(f"action {action.uid} == reaction {reaction_id}")
            if str(action.uid) == reaction_id:
                print(f"selected action {action}")
                handler.send(action)
                break
    get_current_game().clear_reaction_input()
    try:
        # Use the lock to make the operation atomic
        with get_current_game().game_state_lock:
            battle.action(handler.action)
            battle.commit(handler.action)
            interrupted = getattr(handler, 'interrupted_action', None)
            if interrupted is not None and interrupted is not handler.action:
                # The reaction interrupted an action (e.g. the mover's
                # Shield against an opportunity attack). 5e: the attack
                # interrupts the move, it does not end it — replay the
                # interrupted action now that the reaction has resolved.
                # Already-spent reactions are skipped by
                # retrieve_opportunity_attacks, so the path runs clean.
                battle.action(interrupted)
                battle.commit(interrupted)
        get_socketio().emit('message', {'type': 'dismiss_reaction', 'message': {}})

        # Resumes mid-turn, or (end_turn_state) moves on to the next turn.
        get_current_game().schedule_after_reaction()
    except AsyncReactionHandler as e:
        for _, entity, valid_actions in e.resolve():
            valid_actions_str = [[str(action.uid), str(action), action] for action in valid_actions]
            get_current_game().waiting_for_reaction = [entity, e, e.resolve(), valid_actions_str]
        get_socketio().emit('message', {'type': 'reaction', 'message': {'id': entity.entity_uid, 'reaction': e.reaction_type}})

    return jsonify(status='ok')

@battle_bp.route('/manual_roll', methods=['POST'])
def manual_roll():
    battle = get_current_game().get_current_battle()
    battle_map = get_current_game().get_map_for_user(session['username'])
    entity_id = request.json['id']
    entity = battle_map.entity_by_uid(entity_id)
    roll = request.json['roll']
    advantage = request.json.get('advantage', False)
    disadvantage = request.json.get('disadvantage', False)
    description = request.json.get('description', None)
    roll_result = DieRoll.roll(roll, disadvantage=disadvantage, advantage=advantage,
                entity=entity, battle=battle, description=description)
    get_output_logger().log(
        f"{entity.name} rolled a {roll_result}={roll_result.result()} for {description}",
        visibility={'kind': 'combat', 'entities': [entity]},
    )
  
    return jsonify(roll_result=roll_result.result(), roll_explaination=str(roll_result))

@battle_bp.route('/switch_pov', methods=['POST'])
def switch_pov():
    battle_map = get_current_game().get_map_for_user(session['username'])
    entity_id = request.form['entity_uid']
    entity = get_current_game().get_entity_by_uid(entity_id)
    entity_battle_map = get_current_game().get_map_for_entity(entity)
    get_current_game().set_pov_entity_for_user(session['username'], entity)
    # Switch the user's current map BEFORE resolving the background so a POV
    # change to an entity on a different map returns that map's background
    # (otherwise the previous map's background is returned and the client UI
    # appears stuck on the old map until a manual refresh).
    map_changed = battle_map != entity_battle_map
    if map_changed:
        get_current_game().switch_map_for_user(session['username'], entity_battle_map.name)
    background = get_current_game().get_background_image_for_user(session['username'])
    map_width, map_height = entity_battle_map.size
    tiles_dimension_height = map_height * get_tile_px()
    tiles_dimension_width = map_width * get_tile_px()
    dm_active = False
    # Include map default effect and whether DM has an active override
    map_default = None
    map_defaults = []
    if map_changed:
        try:
            map_defaults = map_default_effect_payloads(entity_battle_map)
            map_default = map_defaults[0] if map_defaults else None
        except Exception:
            map_default = None

        try:
            game_key = getattr(get_current_game().get_game_session(), 'root_path', None) or getattr(get_game_session(), 'root_path', None) or get_level()
            dm_active = has_enabled_effect_payloads(get_active_effects().get(game_key, {}).values())
            try:
                dm_active = dm_active or has_enabled_effect_payloads(get_active_effects_map().get(game_key, {}).get(entity_battle_map.name, {}).values())
            except Exception:
                pass
        except Exception:
            dm_active = False
    return jsonify(background=f"assets/{background}",
        name=entity_battle_map.name,
        pov_entity=entity_id,
        image_offset_px=entity_battle_map.image_offset_px,
        height=tiles_dimension_height,
        width=tiles_dimension_width,
        map_default_effect=map_default,
        map_default_effects=map_defaults,
        dm_active=dm_active,
        special_effects_enabled=special_effects_enabled())

@battle_bp.route('/read_letter', methods=['POST'])
def read_letter():
    battle_map = get_current_game().get_map_for_user(session['username'])
    battle = get_current_game().get_current_battle()
    entity_id = request.form['id']
    item_id = request.form['item_id']

    entity = battle_map.entity_by_uid(entity_id)
    if not entity:
        return jsonify(error="Entity not found"), 404

    # Process the letter for the entity using the provided item_id.
    item, letter_content = entity.read_item(item_id)

    get_output_logger().log(
        f"{entity.name} read {item.get('label', item['name'])}: {letter_content}",
        visibility={'kind': 'entity_only', 'entities': [entity]},
    )

    # process raw text so that linebreaks are preserved when rendering on the web page
    letter_content = letter_content.replace('\n', '<br>')

    return render_template('letter.html', letter_label=item.get('label', item['name']), letter_content=letter_content)

@battle_bp.route('/action', methods=['POST'])
def action():
    _t0 = time.perf_counter()
    _ctx = {
        'last': _t0,
        'phase_parse_ms': 0.0,
        'phase_entity_ms': 0.0,
        'phase_target_ms': 0.0,
        'phase_build_ms': 0.0,
        'phase_validate_ms': 0.0,
        'phase_post_commit_ms': 0.0,
        'commit_ms_total': 0.0,
        'commit_calls': 0,
    }
    _action_type = None
    _entity_id = None
    opts = {}

    def _mark(name):
        now = time.perf_counter()
        _ctx[name] = (now - _ctx['last']) * 1000.0
        _ctx['last'] = now

    def _commit_and_update(action_obj, pov_entities):
        apply_knock_unconscious_opt(action_obj, opts if isinstance(opts, dict) else {})
        _t_commit = time.perf_counter()
        result = get_current_game().commit_and_update(session['username'], action_obj, pov_entities)
        _ctx['commit_ms_total'] += (time.perf_counter() - _t_commit) * 1000.0
        _ctx['commit_calls'] += 1
        return result

    def _emit_action_perf_meta():
        total_ms = (time.perf_counter() - _t0) * 1000.0
        try:
            request._perf_meta = {
                **(getattr(request, '_perf_meta', {}) or {}),
                'route': 'action',
                'entity_id': _entity_id,
                'action': _action_type,
                'action_phase_parse_ms': round(_ctx['phase_parse_ms'], 1),
                'action_phase_entity_ms': round(_ctx['phase_entity_ms'], 1),
                'action_phase_target_ms': round(_ctx['phase_target_ms'], 1),
                'action_phase_build_ms': round(_ctx['phase_build_ms'], 1),
                'action_phase_validate_ms': round(_ctx['phase_validate_ms'], 1),
                'action_phase_post_commit_ms': round(_ctx['phase_post_commit_ms'], 1),
                'action_commit_calls': int(_ctx['commit_calls']),
                'action_commit_ms': round(_ctx['commit_ms_total'], 1),
                'action_total_ms': round(total_ms, 1),
            }
        except Exception:
            pass
        _timing_log(
            "action:timing "
            f"parse={_ctx['phase_parse_ms']:.1f}ms "
            f"entity={_ctx['phase_entity_ms']:.1f}ms "
            f"target={_ctx['phase_target_ms']:.1f}ms "
            f"build={_ctx['phase_build_ms']:.1f}ms "
            f"validate={_ctx['phase_validate_ms']:.1f}ms "
            f"commit_calls={_ctx['commit_calls']} "
            f"commit_ms={_ctx['commit_ms_total']:.1f}ms "
            f"post_commit={_ctx['phase_post_commit_ms']:.1f}ms "
            f"total={total_ms:.1f}ms action={_action_type} entity={_entity_id}"
        )

    try:
        battle = get_current_game().get_current_battle()
        action_request = request.json or {}
        entity_id = action_request.get('id')
        _entity_id = entity_id
        if not entity_id:
            return jsonify({'status': 'error', 'message': 'Missing entity id'}), 400
        action_type = action_request.get('action')
        _action_type = action_type
        if not action_type:
            return jsonify({'status': 'error', 'message': 'Missing action'}), 400
        opts = action_request.get('opts', {})
        _mark('phase_parse_ms')

        selected_spell = opts.get('spell')
        at_level = opts.get('at_level')
        choice = action_request.get('choice', opts.get('choice'))
        entity = get_current_game().get_entity_by_uid(entity_id)
        if entity is not None:
            entity = get_current_game().canonical_map_entity(entity)
        if entity is None:
            return jsonify({'status': 'error', 'message': 'Entity not found'}), 404

        battle_map = get_current_game().get_map_for_entity(entity)
        if battle_map is None:
            return jsonify({'status': 'error', 'message': 'Entity is not currently on a map'}), 409

        pov_entities = entities_controlled_by(session['username'])
        action_info = {}
        action_hash = None
        target_coords = action_request.get('target', None)
        target = None
        _mark('phase_entity_ms')

        # ReadyAction is collected through a chat dialog with the DM rather than
        # the standard target/param flow. Tell the client to open the dialog.
        if action_type == 'ReadyAction':
            return jsonify({
                'action': 'ReadyAction',
                'type': 'requires_dialog',
                'dialog': 'ready_action',
                'endpoint': '/ready_action',
                'entity_uid': entity_id,
                'prompt': 'Describe the trigger and the action you are readying.',
            })

        if target_coords:
            mode = action_request.get('mode', None)
            if mode == 'cone' or mode == 'point_target' or mode == 'cube' or mode == 'square' or mode == 'radius' or mode == 'line':
                target = [target_coords['x'], target_coords['y']]
            else:
                if isinstance(target_coords, list):
                    target = []
                    for entity_uids in target_coords:
                        target.append(battle_map.entity_by_uid(entity_uids))
                elif isinstance(target_coords, str):
                    # Target is an entity UID
                    target = get_current_game().get_entity_by_uid(target_coords)
                    if target is None:
                        target = battle_map.entity_by_uid(target_coords)
                else:
                    # Target is coordinates
                    tx, ty = int(target_coords['x']), int(target_coords['y'])
                    stack = getattr(battle_map, 'map_stack', None)
                    from natural20.map_stack_targeting import entity_at_stack_position
                    resolved_entity, _target_map, _tx, _ty = entity_at_stack_position(stack, battle_map, tx, ty)
                    if resolved_entity is not None:
                        target = resolved_entity
                    else:
                        targets = battle_map.entities_at(tx, ty)
                        if len(targets) == 1:
                            target = targets[0]
                        elif len(targets) == 0:
                            if stack is not None:
                                from natural20.map_stack_targeting import things_at_stack_position
                                for _map, _lx, _ly, thing in things_at_stack_position(stack, tx, ty):
                                    if _map.entity_at(_lx, _ly) is thing:
                                        target = thing
                                        break
                            if target is None:
                                fallback = battle_map.entity_at(tx, ty)
                                target = fallback if fallback is not None else [tx, ty]
                        else:
                            target_list = [[target.label(), target.entity_uid] for target in targets]
                            return jsonify(status='multiple_targets', message=f"Multiple entities at {target_coords['x']}, {target_coords['y']}",
                                           entities=target_list)
        _mark('phase_target_ms')

        if action_type == 'MoveAction':
            _t_build = time.perf_counter()
            path = action_request.get('path', None)
            manual_jump = action_request.get('manual_jump') or []
            action = MoveAction(get_game_session(), entity, 'move')
            _ctx['phase_build_ms'] += (time.perf_counter() - _t_build) * 1000.0
            if path:
                move_path = sorted([(int(index), [int(coord[0]), int(coord[1])]) for index, coord in enumerate(path)])
                move_path = [coords for _, coords in move_path]
                action.move_path = move_path
                stack_descent = action_request.get('stack_descent')
                if stack_descent:
                    action.stack_descent = stack_descent
                # When this PC isn't a combatant in an active battle, treat
                # the move as exploration and allow paths longer than the
                # entity's standard speed so the user can traverse the map.
                # Still refuse movement when speed is currently 0.
                if not battle or entity not in getattr(battle, 'entities', {}):
                    if entity.speed() <= 0:
                        return jsonify(error='Cannot move while speed is 0'), 400
                    action.unlimited_movement = True
                # store jump indices for backend computation if provided.
                # The web UI sends [takeoff_index, landing_index] where
                # ``takeoff_index`` is the path index of the square the
                # entity launches FROM (the last walked square) and
                # ``landing_index`` is the square it lands ON. The squares
                # that are actually "in flight" — and therefore the ones
                # that need to be marked as jump squares so area triggers
                # treat them as flying — are ``takeoff_index + 1 ..
                # landing_index`` inclusive. Including the takeoff itself
                # would (a) burn a square of jump budget on the spot the
                # PC is standing on, and (b) prevent the long-jump
                # running-start budget from kicking in (because the
                # takeoff square is no longer counted as a "walk").
                try:
                    if isinstance(manual_jump, list) and len(manual_jump) == 2:
                        start_i, end_i = int(manual_jump[0]), int(manual_jump[1])
                        if 0 <= start_i <= end_i < len(move_path):
                            action.jump_index = list(range(start_i + 1, end_i + 1))
                    elif isinstance(manual_jump, list):
                        # already a list of in-flight indices
                        action.jump_index = [int(i) for i in manual_jump if 0 <= int(i) < len(move_path)]
                except Exception:
                    # ignore malformed manual_jump to remain backwards compatible
                    pass
                if battle:
                    result = _commit_and_update(action, pov_entities)
                    _t_post = time.perf_counter()
                    # Check area narrations after battle movement
                    area_narration = battle_map.check_area_narration(entity, move_path[-1])
                    if area_narration:
                        get_socketio().emit('message', {'type': 'narration', 'message': area_narration, 'map_name': battle_map.name})
                        narration_entry = area_narration.get('on_enter', {})
                        narration_text = narration_entry.get('text', '')
                        if narration_text:
                            get_output_logger().log(narration_text, visibility={'kind': 'entities', 'entity_uids': [entity.entity_uid]})
                        try:
                            _record_narration_for_pcs(area_narration, map_name=battle_map.name, target_uids=[entity.entity_uid])
                        except Exception:
                            pass
                    try:
                        get_current_game().update_ambient_soundtrack(
                            entity=entity, battle_map=battle_map, position=move_path[-1]
                        )
                    except Exception:
                        pass
                    _ctx['phase_post_commit_ms'] += (time.perf_counter() - _t_post) * 1000.0
                    return jsonify(result)
                else:
                    last_coords = move_path[-1]
                    if battle_map.placeable(entity, last_coords[0], last_coords[1]):

                        _commit_and_update(action, pov_entities)
                        _t_post = time.perf_counter()
                        if battle:
                            # POV-aware logs are emitted via commit_and_update; avoid duplicate raw emission
                            logs = battle.get_animation_logs()
                            if logs:
                                get_socketio().emit('message', {'type': 'move', 'message': {
                                    'from': move_path[0], 'to': move_path[-1], 'animation_log': logs
                                }})
                            battle.clear_animation_logs()
                        else:
                            from webapp.utils import (
                                _coalesce_animation_log,
                                build_move_animation_log_entries,
                            )
                            animation_entries = build_move_animation_log_entries(
                                action,
                                entity_uid=entity.entity_uid,
                                fallback_path=move_path,
                            )
                            if not animation_entries:
                                return jsonify({'status': 'error', 'message': 'Move produced no path'})
                            resolved_path = animation_entries[-1][1]
                            get_socketio().emit('message', {'type': 'move', 'message': {
                                'from': resolved_path[0],
                                'to': resolved_path[-1],
                                'animation_log': _coalesce_animation_log(animation_entries),
                            }})
                        # Flush any deferred map switch now that the move
                        # animation event has been queued on the client. This
                        # ensures the entity finishes animating into the
                        # teleporter/stairs tile before the destination map
                        # is rendered.
                        try:
                            get_current_game().flush_pending_map_switch(session['username'])
                        except Exception:
                            pass
                        # Check area narrations after free movement
                        area_narration = battle_map.check_area_narration(entity, resolved_path[-1])
                        if area_narration:
                            get_socketio().emit('message', {'type': 'narration', 'message': area_narration, 'map_name': battle_map.name})
                            narration_entry = area_narration.get('on_enter', {})
                            narration_text = narration_entry.get('text', '')
                            if narration_text:
                                get_output_logger().log(narration_text, visibility={'kind': 'entities', 'entity_uids': [entity.entity_uid]})
                            try:
                                _record_narration_for_pcs(area_narration, map_name=battle_map.name, target_uids=[entity.entity_uid])
                            except Exception:
                                pass
                        try:
                            get_current_game().update_ambient_soundtrack(
                                entity=entity, battle_map=battle_map, position=resolved_path[-1]
                            )
                        except Exception:
                            pass
                        get_current_game().loop_environment()
                        _ctx['phase_post_commit_ms'] += (time.perf_counter() - _t_post) * 1000.0
                        return jsonify({'status': 'ok'})
                    else:
                        return jsonify({'status': 'error', 'message': 'Entity not placeable at target location'})
            else:
                action_info['action'] = 'movement'
                action_info['type'] = 'select_path'
                build_map = action.build_map()
                action_info['param'] = build_map['param']
                return jsonify(action_info)
        elif action_type in ['LinkedAttackAction', 'AttackAction', 'TwoWeaponAttackAction']:
            _t_build = time.perf_counter()
            if action_type == 'AttackAction':
                action = AttackAction(get_game_session(), entity, 'attack')
            else:
                action = TwoWeaponAttackAction(get_game_session(), entity, 'attack')
            action.using = opts.get('using')
            action.npc_action = opts.get('npc_action', None)
            action.thrown = opts.get('thrown', False)
            if battle and battle.in_legendary_phase(entity):
                action.legendary_action = True
            _ctx['phase_build_ms'] += (time.perf_counter() - _t_build) * 1000.0

            stack = getattr(battle_map, 'map_stack', None)

            if action.npc_action:
                weapon_details = action.npc_action
            else:
                weapon_details = get_game_session().load_weapon(action.using)

            if target_coords:
                if battle and isinstance(target_coords, dict):
                    tx, ty = int(target_coords['x']), int(target_coords['y'])
                    resolved = target if hasattr(target, 'entity_uid') else None
                    resolved_map = (battle.map_for(resolved) if resolved is not None else None) or battle_map
                    square = _blind_attack_target_square(
                        entity, action, battle, battle_map, resolved, resolved_map, tx, ty,
                    )
                    if square is not None:
                        from natural20.utils.unseen_targets import blind_attack_issue
                        issue_key = blind_attack_issue(battle, entity, action, square)
                        if issue_key:
                            return jsonify(status='error', message=targeting_issue(issue_key).to_dict()['message'])
                        action.target = None
                        action.target_square = square
                        return jsonify(_commit_and_update(action, pov_entities))

                # ``target`` was resolved above (with entity_at fallback when
                # entities_at is empty). Coordinate-only targets are invalid
                # for attacks.
                if target is None or isinstance(target, list):
                    return jsonify(status='error', message=f"Invalid Target {target_coords}")

                # When a concrete target is already provided, skip the expensive
                # valid_targets_for + valid_target_positions scan that the UI
                # needs for the hover-selection flow.  Validate the target
                # directly instead.
                if battle:
                    allowed = battle.valid_targets_for(entity, action, include_objects=True)
                    target_ok = target in allowed
                else:
                    # Non-battle path: compute valid targets only if we
                    # haven't already done so (defensive fallback).
                    target_list = battle_map.valid_targets_for(entity, action, include_objects=True)
                    valid_targets = {t.entity_uid: battle_map.entity_or_object_pos(t) for t in target_list}
                    target_ok = bool(valid_targets.get(target.entity_uid))
                if target_ok:
                    action.target = target
                    return jsonify(_commit_and_update(action, pov_entities))
                else:
                    return jsonify(status='error', message=f"Invalid Target {target_coords}")
            else:
                # No concrete target yet — compute the full valid-targets list
                # so the UI can render hover highlighting.  This is the
                # expensive path that the client needs for target selection.
                if battle:
                    from natural20.map_stack_targeting import valid_target_positions
                    from natural20.utils.unseen_targets import blind_attack_squares, enabled as unseen_enabled
                    target_list = battle.valid_targets_for(entity, action, include_objects=True)
                    valid_targets = valid_target_positions(stack, battle_map, target_list, battle=battle)
                    action_info['blind_attacks'] = unseen_enabled(get_game_session())
                    action_info['unseen_squares'] = [
                        list(sq) for sq in blind_attack_squares(battle, entity, action)
                    ]
                else:
                    target_list = battle_map.valid_targets_for(entity, action, include_objects=True)
                    valid_targets = {target.entity_uid: battle_map.entity_or_object_pos(target) for target in target_list}
                action_info['action'] = 'attack'
                action_info['type'] = 'select_target'
                action_info['valid_targets'] = valid_targets
                action_info['total_targets'] = 1
                if action.thrown:
                    action_info['range'] = weapon_details.get('thrown', {}).get('range')
                    action_info['range_max'] = weapon_details.get('thrown', {}).get('range_max', weapon_details.get('thrown', {}).get('range'))
                else:
                    action_info['range'] = weapon_details['range']
                    action_info['range_max'] = weapon_details.get('range_max', weapon_details['range'])
                action_info['param'] = [
                    {
                        'type': 'select_target',
                        'num': 1,
                        'weapon': action.using,
                        'target_types': ['enemies'],
                    }
                    ]
        else:
            _t_build = time.perf_counter()
            action_class = action_type_to_class(action_type)
            opts = action_request.get('opts', {})
            resolved_action_type = resolve_requested_action_type(
                entity,
                get_game_session(),
                battle,
                battle_map,
                action_class,
                opts.get('action_type'),
            )
            action = action_class(get_game_session(), entity, resolved_action_type)
            action = action.build_map()
            _ctx['phase_build_ms'] += (time.perf_counter() - _t_build) * 1000.0

            while not isinstance(action, Action):
                if len(action['param'])==1:
                    param_details = action['param'][0]
                    if param_details['type'] == 'select_spell':
                        if selected_spell:
                            action = action['next']((selected_spell, at_level))
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_spell'
                            action_info['param'] = action['param']
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_choice':
                        if choice:
                            action = action['next'](choice)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_choice'
                            action_info['param'] = action['param']
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_empty_space':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_empty_space'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 5)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 5))
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_cone':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_cone'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 5)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 5))
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_cube':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_cube'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 5)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 5))
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_radius':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_radius'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 60)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 60))
                            action_info['radius'] = param_details.get('radius', 20)
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_square':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_square'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 60)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 60))
                            action_info['size'] = param_details.get('size', 10)
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_line':
                        if target:
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_line'
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 30)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 30))
                            action_info['width'] = param_details.get('width', 5)
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_emanation':
                        # Emanation centers on the caster — auto-resolve.
                        entity_x, entity_y = battle_map.entity_or_object_pos(entity)
                        action = action['next']([entity_x, entity_y])
                        continue
                    elif param_details['type'] == 'select_target':
                        stack = getattr(battle_map, 'map_stack', None)
                        if battle:
                            from natural20.map_stack_targeting import valid_target_positions
                            acquired = acquire_targets(param_details, entity, battle, battle_map)
                            valid_targets = valid_target_positions(stack, battle_map, acquired, battle=battle)
                        else:
                            acquired = battle_map.valid_targets_for(entity, param_details)
                            valid_targets = {
                                target.entity_uid: battle_map.entity_or_object_pos(target)
                                for target in acquired
                            }
                        if target:
                            # ``select_target`` requires an actual Entity (or a
                            # list of entities for multi-target spells). If
                            # the client clicked an empty square the upstream
                            # resolver hands us a ``[x, y]`` coordinate pair,
                            # which would explode deep inside the spell when
                            # something like ``target.has_effect(...)`` is
                            # called. Reject those up front with a clear
                            # error so the UI can prompt the user again.
                            # ``allow_effects`` spells (e.g. Dispel Magic) may
                            # instead aim at a persistent spell zone covering
                            # the square; overlapping zones raise an effect
                            # picker the caster answers with ``opts.effect_id``.
                            is_entity_target = isinstance(target, Entity) or (
                                isinstance(target, list)
                                and target
                                and all(isinstance(t, Entity) for t in target)
                            )
                            if not is_entity_target and param_details.get('allow_effects'):
                                from natural20.utils.dispel import (
                                    zone_label,
                                    zone_spell_level,
                                    zones_at,
                                )
                                target_pos = [int(target[0]), int(target[1])]
                                covering = zones_at(battle, battle_map, target_pos)
                                picked = opts.get('effect_id')
                                if picked and any(
                                    str(getattr(z, 'id', None)) == str(picked)
                                    for z in covering
                                ):
                                    action = action['next'](target_pos)
                                    continue
                                if len(covering) > 1:
                                    return jsonify(
                                        status='multiple_effects',
                                        target=target_pos,
                                        effects=[
                                            {
                                                'id': z.id,
                                                'label': zone_label(z),
                                                'level': zone_spell_level(
                                                    z, get_game_session()),
                                            }
                                            for z in covering
                                        ],
                                    )
                                if len(covering) == 1:
                                    opts = dict(opts)
                                    opts['effect_id'] = covering[0].id
                                    action = action['next'](target_pos)
                                    continue
                            if not is_entity_target:
                                return jsonify(
                                    status='error',
                                    error='No valid target at the selected square. Click on a creature.',
                                ), 400
                            action = action['next'](target)
                            continue
                        else:
                            action_info['action'] = action_type
                            action_info['type'] = 'select_target'
                            action_info['valid_targets'] = valid_targets
                            action_info['total_targets'] = param_details['num']
                            action_info['param'] = action['param']
                            action_info['range'] = param_details.get('range', 5)
                            action_info['range_max'] = param_details.get('max_range', param_details.get('range', 5))
                            if param_details.get('num', 1) > 1:
                                target_hints = [ t.entity_uid for t in acquire_targets(param_details, entity, battle, battle_map)]
                                action_info['target_hints'] = target_hints
                                action_info['unique_targets'] = param_details.get('unique_targets', False)
                            return jsonify(action_info)
                    elif param_details['type'] == 'text_input':
                        field = param_details.get('field', 'value')
                        value = opts.get(field) or opts.get('text') or opts.get('value')
                        if value:
                            action = action['next'](value)
                            continue
                        action_info['action'] = action_type
                        action_info['type'] = 'text_input'
                        action_info['param'] = action['param']
                        action_info['label'] = param_details.get('label', 'Enter a value')
                        action_info['placeholder'] = param_details.get('placeholder', '')
                        action_info['field'] = field
                        return jsonify(action_info)
                    elif param_details['type'] == 'select_item':
                        target_item = resolve_use_item_name(opts)
                        if target_item:
                            action = action['next'](target_item)
                            continue
                        else:
                            valid_items = entity.usable_items()
                            action_info['action'] = action_type
                            action_info['type'] = 'select_item'
                            action_info['valid_items'] = valid_items
                            action_info['param'] = action['param']
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_object':
                        object_action_a = opts.get('object_action')
                        if isinstance(object_action_a, list):
                            object_action_a = object_action_a[0]

                        if entity.object() and 'dm' in user_role():
                            gm = DungeonMaster(get_game_session(), name='dm')
                            interact = InteractAction(get_game_session(), gm, 'interact')
                            interact.object_action = object_action_a
                            interact.target = entity
                            return jsonify(_commit_and_update(interact, pov_entities))
                        else:
                            interact = InteractAction(get_game_session(), entity, 'interact')
                            object =  battle_map.entity_by_uid(opts.get('target'))
                            interact.object_action =  object_action_a
                            interact.target = object
                            action = interact.build_custom_action(interact.object_action, object)
                            continue
                    elif param_details['type'] == 'pickpocket_attempt':
                        from natural20.actions.pickpocket_action import PickpocketAction

                        inner = action.get('action')
                        if not isinstance(inner, PickpocketAction):
                            raise ValueError('pickpocket_attempt requires PickpocketAction')

                        cached = opts.get('pickpocket_attempt')
                        if cached:
                            attempt = PickpocketAction.deserialize_attempt(cached)
                        else:
                            attempt = inner.roll_pickpocket_attempt(
                                get_game_session(), battle_map, battle,
                            )
                            if not attempt.get('success'):
                                failed_action = action['next'](attempt)
                                if isinstance(failed_action, Action):
                                    failed_action.validate(battle_map)
                                    if len(failed_action.errors) > 0:
                                        return jsonify(status='error', **validation_response_payload(failed_action))
                                    _commit_and_update(failed_action, pov_entities)
                                return jsonify(
                                    status='ok',
                                    pickpocket_attempt=PickpocketAction.serialize_attempt(attempt),
                                )
                            opts = dict(opts)
                            opts['pickpocket_attempt'] = PickpocketAction.serialize_attempt(attempt)

                        action = action['next'](attempt)
                        continue
                    elif param_details['type'] == 'select_pickpocket_item':
                        from natural20.actions.pickpocket_action import PickpocketAction

                        item_name = opts.get('item_name')
                        if item_name:
                            action = action['next'](item_name)
                            continue
                        target_uid = None
                        if target is not None and hasattr(target, 'entity_uid'):
                            target_uid = target.entity_uid
                        elif isinstance(action, dict):
                            inner = action.get('action')
                            inner_target = getattr(inner, 'target', None)
                            if inner_target is not None:
                                target_uid = getattr(inner_target, 'entity_uid', None)
                        target_uid = target_uid or param_details.get('target_uid')
                        action_info['action'] = action_type
                        action_info['type'] = 'select_pickpocket_item'
                        action_info['target_uid'] = target_uid
                        action_info['label'] = param_details.get('label', 'Item lifted')
                        action_info['param'] = action['param']
                        inner = action.get('action') if isinstance(action, dict) else None
                        attempt_payload = opts.get('pickpocket_attempt')
                        if inner is not None and getattr(inner, 'pickpocket_attempt', None):
                            attempt_payload = PickpocketAction.serialize_attempt(
                                inner.pickpocket_attempt,
                            )
                        if attempt_payload:
                            action_info['pickpocket_attempt'] = attempt_payload
                        return jsonify(action_info)
                    elif param_details['type'] == 'select_items':
                        target_items = opts.get('items', [])
                        if target_items:
                            action = action['next'](target_items)
                            continue
                        else:
                            valid_items = entity.usable_items()
                            action_info['action'] = action_type
                            action_info['type'] = 'select_items'
                            action_info['mode'] = param_details.get('mode', 'transfer')
                            action_info['valid_items'] = param_details['items']
                            action_info['param'] = action['param']
                            return jsonify(action_info)
                    elif param_details['type'] == 'select_weapon':
                        # Handle weapon selection for spells like Booming Blade / Green-Flame Blade
                        if hasattr(entity, 'attack_options'):
                            usable_weapons = entity.attack_options(battle)
                        else:
                            usable_weapons = entity.equipped_weapons(get_game_session())
                        weapon_name = opts.get('weapon')
                        if weapon_name:
                            action = action['next'](weapon_name)
                            continue
                        action_info['action'] = action_type
                        action_info['type'] = 'select_weapon'
                        action_info['valid_weapons'] = usable_weapons
                        action_info['param'] = action['param']
                        return jsonify(action_info)
                    else:
                        raise ValueError(f"Unknown action type {action_type} {param_details['type']}")
                else:
                    raise ValueError(f"Invalid action map {action}")

            # Dispel Magic-style magical-effect picks (multiple_effects
            # picker): carry the chosen zone id onto the built action so the
            # spell can resolve it through natural20.utils.dispel.
            if isinstance(action, SpellAction) and opts.get('effect_id'):
                action.effect_id = opts['effect_id']

            _t_validate = time.perf_counter()
            action.validate(battle_map, target=target)
            _ctx['phase_validate_ms'] += (time.perf_counter() - _t_validate) * 1000.0
           
            if action.validation_failed():
                return jsonify(status='error', **validation_response_payload(action))

            result = _commit_and_update(action, pov_entities)
            if isinstance(result, dict) and result.get('status') == 'bardic_inspiration_prompt':
                return jsonify(result)
            response = {'status': 'ok'}
            if getattr(action, 'action_type', None) == 'look':
                from natural20.battle import _look_perception_roll_payload
                perception_roll, perception_roll_label = _look_perception_roll_payload(action)
                if perception_roll is not None:
                    response['perception_roll'] = perception_roll
                if perception_roll_label:
                    response['perception_roll_label'] = perception_roll_label
            return jsonify(response)
        return jsonify(action_info)
    except BardicInspirationPrompt as e:
        get_logger().info("BardicInspirationPrompt during action: %s", e)
        from webapp.bardic_inspiration_prompt import emit_bardic_inspiration_prompt
        get_current_game().waiting_for_bardic_inspiration = [e, action]
        emit_bardic_inspiration_prompt(get_socketio(), e)
        return jsonify(status='bardic_inspiration_prompt', entity_id=getattr(e.entity, 'entity_uid', None))
    except AsyncReactionHandler as e:
        get_logger().info(f"AsyncReactionHandler during action: {e}")
        get_current_game().end_turn_state = False
        for battle, entity, valid_actions in e.resolve():
            valid_actions_str = [[str(action.uid), str(action), action] for action in valid_actions]
            get_current_game().set_waiting_for_reaction_input([entity, e, e.resolve(), valid_actions_str])
        get_socketio().emit('message', {'type': 'reaction', 'message': {'id': entity.entity_uid, 'reaction': e.reaction_type}})
        return jsonify(status='ok')
    finally:
        _emit_action_perf_meta()


@battle_bp.route('/bardic_inspiration', methods=['POST'])
def bardic_inspiration_choice():
    payload = request.json or {}
    use_die = bool(payload.get('use'))
    waiting = getattr(get_current_game(), 'waiting_for_bardic_inspiration', None)
    if not waiting:
        return jsonify(status='error', message='No Bardic Inspiration choice pending'), 400
    exc, pending_action = waiting
    from webapp.bardic_inspiration_prompt import resume_bardic_inspiration_prompt
    with get_current_game().game_state_lock:
        resume_bardic_inspiration_prompt(get_current_game(), exc, pending_action, use_die)
    get_current_game().waiting_for_bardic_inspiration = False
    get_socketio().emit('message', {'type': 'dismiss_bardic_inspiration_prompt', 'message': {}})
    return jsonify(status='ok', used=use_die)


@battle_bp.route('/tiny_hut/lighting', methods=['POST'])
def tiny_hut_lighting():
    """Command the interior lighting of the caster's active Tiny Hut."""
    from natural20.spell.leomunds_tiny_hut_spell import LeomundsTinyHutSpell

    payload = request.json or {}
    entity_id = payload.get('id') or payload.get('entity_uid')
    mode = (payload.get('mode') or 'default').strip().lower()
    if mode not in ('default', 'dim', 'dark'):
        return jsonify(status='error', message='Invalid lighting mode'), 400

    game = get_current_game()
    session = game.session
    entity = session.entity_by_uid(entity_id) if entity_id else None
    if entity is None:
        return jsonify(status='error', message='Entity not found'), 404

    if not LeomundsTinyHutSpell.set_interior_lighting(entity, mode, session):
        return jsonify(status='error', message='No active Tiny Hut for this character'), 400

    get_socketio().emit('message', {'type': 'refresh_map', 'message': {}})
    return jsonify(status='ok', mode=mode)


@battle_bp.route('/ready_action', methods=['POST'])
def ready_action_endpoint():
    """Declare a 5e Ready (Hold) action.

    Body: ``{ id: <entity_uid>, description: "<player free text>" }``

    The webapp passes the description through the configured LLM (with a
    rule-based fallback) to produce a structured trigger + action_spec, and
    if approved commits a :class:`ReadyAction` for the entity.
    """
    from webapp.ready_action_handler import (
        parse_ready_action_request,
        make_llm_resolver,
    )

    payload = request.json or {}
    entity_id = payload.get('id')
    description = (payload.get('description') or '').strip()
    if not entity_id:
        return jsonify(status='error', message='Missing entity id'), 400

    entity = get_current_game().get_entity_by_uid(entity_id)
    if entity is None:
        return jsonify(status='error', message='Entity not found'), 404
    battle = get_current_game().get_current_battle()
    if battle is None:
        return jsonify(status='error', message='No active battle'), 400
    if not ReadyAction.can(entity, battle):
        return jsonify(status='error',
                       message='You cannot ready an action right now.'), 400

    parsed = parse_ready_action_request(entity, battle, description, get_llm_handler())
    if not parsed.get('approved'):
        return jsonify(status='rejected',
                       reason=parsed.get('reason'),
                       trigger=parsed.get('trigger'),
                       action_spec=parsed.get('action_spec')), 200

    # Make sure the engine knows how to resolve trigger time (idempotent).
    if getattr(battle, '_ready_action_resolver', None) is None:
        battle.set_ready_action_resolver(make_llm_resolver(get_llm_handler()))

    ready = ReadyAction(get_current_game().get_game_session(), entity, 'ready', opts={
        'description': description,
        'trigger': parsed['trigger'],
        'action_spec': parsed['action_spec'],
    })
    pov_entities = entities_controlled_by(session['username'])
    get_current_game().commit_and_update(session['username'], ready, pov_entities)
    return jsonify(status='ok',
                   reason=parsed.get('reason'),
                   trigger=parsed['trigger'],
                   action_spec=parsed['action_spec'])


@battle_bp.route('/instruction', methods=['POST'])
def instruction_start():
    """Start a free-text player instruction (System One / JEV driven).

    Body: ``{ id: <entity_uid>, instruction: "<player free text>" }``

    The decision model fulfils the instruction one step at a time; call
    ``/instruction/step`` to advance. Each response carries ``status``:
    ``step`` (an action was committed), ``done`` (finished/stopped),
    ``end_turn`` (the player asked to end the turn), or ``error``.
    """
    from webapp.blueprints.helpers.instruction_follow import start_instruction

    payload = request.json or {}
    entity_id = payload.get('id')
    instruction = (payload.get('instruction') or '').strip()
    if not entity_id:
        return jsonify(status='error', message='Missing entity id'), 400
    if not instruction:
        return jsonify(status='error', message='Missing instruction'), 400

    result, code = start_instruction(session['username'], entity_id, instruction)
    if code:
        return jsonify(result), code
    if result.get('status') == 'end_turn':
        return end_turn()
    return jsonify(result)


@battle_bp.route('/instruction/step', methods=['POST'])
def instruction_step():
    """Advance the running instruction by one System One decision."""
    from webapp.blueprints.helpers.instruction_follow import run_instruction_step

    payload = request.json or {}
    result, code = run_instruction_step(session['username'], payload.get('id'))
    if code:
        return jsonify(result), code
    if result.get('status') == 'end_turn':
        return end_turn()
    return jsonify(result)


@battle_bp.route('/instruction/stop', methods=['POST'])
def instruction_stop():
    """Stop the running instruction and hand control back to the player."""
    from webapp.blueprints.helpers.instruction_follow import stop_instruction

    payload = request.json or {}
    result, code = stop_instruction(session['username'], payload.get('id'))
    if code:
        return jsonify(result), code
    return jsonify(result)


@battle_bp.route('/pickpocket_items', methods=['GET'])
def get_pickpocket_items():
    entity_id = request.args.get('id')
    entity = get_current_game().get_entity_by_uid(entity_id)
    if entity is None:
        return jsonify(error="Entity not found"), 404
    entity = get_current_game().canonical_map_entity(entity)
    battle_map = get_current_game().get_map_for_entity(entity)
    if battle_map is None:
        return jsonify(error="Entity is not on a map"), 404

    target_uid = request.args.get('target') or request.args.get('opts[target]')
    target = get_current_game().get_entity_by_uid(target_uid) if target_uid else None
    if target is None and target_uid:
        target = battle_map.entity_by_uid(target_uid)
    if target is None:
        return jsonify(error="Target not found"), 404
    target = get_current_game().canonical_map_entity(target)

    from natural20.utils.pickpocket_items import pickpocketable_inventory_items
    from webapp.blueprints.helpers.pickpocket_ui import (
        pickpocket_attempt_from_request,
        pickpocket_attempt_roll_summary,
    )

    inventory = pickpocketable_inventory_items(get_game_session(), target)
    pickpocket_attempt = pickpocket_attempt_from_request()
    return render_template(
        'pickpocket_items.html',
        entity=entity,
        target=target,
        inventory=inventory,
        pickpocket_attempt=pickpocket_attempt,
        pickpocket_roll_summary=pickpocket_attempt_roll_summary(pickpocket_attempt),
    )


@battle_bp.route('/items', methods=['GET'])
# GET /items?id=rumblebelly&action=InteractAction&opts[action_type]=interact&opts[object_action]=loot&opts[target]=3fb25042-df48-4003-8ddc-dd2b04d5fbeb HTTP/1.1
def get_items():
    battle_map = get_current_game().get_map_for_user(session['username'])
    entity_id = request.args.get('id')
    entity = battle_map.entity_by_uid(entity_id)
    if entity is None:
        return jsonify(error="Entity not found"), 404
    action_type = request.args.get('opts[object_action][]')
    target_object = battle_map.entity_by_uid(request.args.get("opts[target]"))
    if action_type == 'give':
        inventory = entity.inventory_items(get_game_session()) or []
        source_inventory = []
        return render_template('loot_items.html', entity=target_object, source_inventory=source_inventory, inventory=inventory, action_type=action_type, target_object=entity)
    else:
        inventory = target_object.inventory_items(get_game_session()) or []
        source_inventory = entity.inventory_items(get_game_session()) or []
    return render_template('loot_items.html', entity=entity, source_inventory=source_inventory, inventory=inventory, action_type=action_type, target_object=target_object)


@battle_bp.route('/info', methods=['GET'])
def get_info():
    resolved, error = lookup_info_entity(request.args.get('id'))
    if error is not None:
        return error
    entity, battle = resolved
    return render_info_page(entity, battle=battle)

@battle_bp.route('/entity_info', methods=['GET'])
def get_entity_info():
    """Get entity information for the JRPG dialog modal."""
    
    entity_id = request.args.get('entity_id')
    if not entity_id:
        return jsonify({'success': False, 'error': 'Entity ID is required'}), 400
    
    try:
        entity = get_current_game().get_entity_by_uid(entity_id)
        if not entity:
            return jsonify({'success': False, 'error': 'Entity not found'}), 404
        
        # Use EntityRAGHandler to get comprehensive entity context
        entity_info = get_entity_rag_handler().get_entity_context(
            entity,
            viewer_is_dm='dm' in user_role(),
        )
        
        return jsonify({'success': True, 'entity': entity_info})
        
    except Exception as e:
        get_logger().error(f"Error getting entity info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@battle_bp.route('/reset_narrations', methods=['POST'])
def reset_narrations():
    if not session.get('username'):
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    for m in get_current_game().maps.values():
        m._triggered_area_narrations.clear()
    return jsonify({'status': 'ok'})

@battle_bp.route('/turn')
def get_turn():
    battle = get_current_game().get_current_battle()
    if battle:
        print(f"current turn: {battle.current_turn().entity_uid} {session['username']}")
        if 'dm' in user_role() or controller_of(battle.current_turn().entity_uid, session['username']):
            return render_template('turn.jinja', battle=battle, game_session=get_current_game().game_session, username=session['username'])
        else:
            return render_template('turn.jinja', battle=battle, game_session=get_current_game().game_session, username=session['username'], readonly=True)
    else:
        # Exploration mode (no active battle): keep endpoint successful so
        # client refresh calls don't flood console with 400 errors.
        return "", 200

@battle_bp.route('/game_time')
def get_game_time():
    from webapp.blueprints.helpers.time_of_day import get_time_of_day_state
    payload = {'game_time': get_current_game().game_session.game_time}
    try:
        payload['time_of_day'] = get_time_of_day_state(get_current_game()).to_dict()
    except Exception:
        pass
    return jsonify(payload)


@battle_bp.route('/targets_at_position', methods=['GET'])
def get_targets_at_position():
    """Get all valid targets at a specific tile position for target selection modal."""
    
    try:
        entity_id = request.args.get('entity_id')
        x = int(request.args.get('x'))
        y = int(request.args.get('y'))
        action_info = request.args.get('action_info')
        opts = json.loads(request.args.get('opts', '{}'))
        
        if not entity_id or x is None or y is None or not action_info:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400
        
        entity = get_current_game().get_entity_by_uid(entity_id)
        if not entity:
            return jsonify({'success': False, 'error': 'Entity not found'}), 404
        
        battle_map = get_current_game().get_map_for_entity(entity)
        battle = get_current_game().get_current_battle()
        stack = getattr(battle_map, 'map_stack', None)
        from natural20.map_stack_targeting import things_at_stack_position

        things_at_position = battle_map.thing_at(x, y)
        if stack is not None:
            stack_hits = things_at_stack_position(stack, x, y)
            seen = {getattr(t, 'entity_uid', id(t)) for t in things_at_position}
            for _map, _lx, _ly, thing in stack_hits:
                uid = getattr(thing, 'entity_uid', id(thing))
                if uid not in seen:
                    things_at_position.append(thing)
                    seen.add(uid)
        
        # Filter to only valid targets based on the action
        valid_targets = []
        
        if action_info in ['AttackAction', 'LinkedAttackAction']:
            action = AttackAction(get_game_session(), entity, 'attack')
            action.using = opts.get('using')
            action.npc_action = opts.get('npc_action', None)
            action.thrown = opts.get('thrown', False)
            if battle and battle.in_legendary_phase(entity):
                action.legendary_action = True
            
            # Check each thing at the position
            for thing in things_at_position:
                if thing and thing.allow_targeting():
                    action.target = thing
                    action.validate(battle_map, target=thing)
                    
                    if not action.errors:
                        if battle:
                            battle_valid_targets = battle.valid_targets_for(entity, action)
                            if thing in battle_valid_targets:
                                valid_targets.append({
                                    'id': thing.entity_uid,
                                    'name': thing.label() if hasattr(thing, 'label') else str(thing),
                                    'type': thing.__class__.__name__,
                                    'image': getattr(thing, 'profile_image', lambda: None)()
                                })
                        else:
                            map_valid_targets = battle_map.valid_targets_for(entity, action)
                            if thing in map_valid_targets:
                                valid_targets.append({
                                    'id': thing.entity_uid,
                                    'name': thing.label() if hasattr(thing, 'label') else str(thing),
                                    'type': thing.__class__.__name__,
                                    'image': getattr(thing, 'profile_image', lambda: None)()
                                })
        
        elif action_info == 'SpellAction':
            build_map = SpellAction.build(get_game_session(), entity)
            spell_choice = (opts['spell'], opts['at_level'])
            build_map = build_map['next'](spell_choice)
            
            # Check each thing at the position
            for thing in things_at_position:
                if thing and thing.allow_targeting():
                    try:
                        # Try to build the action with this target
                        test_build = build_map
                        while not isinstance(test_build, Action):
                            if test_build['param'][0]['type'] == 'select_target':
                                test_build = test_build['next'](thing)
                            elif test_build['param'][0]['type'] == 'select_empty_space':
                                test_build = test_build['next']([x, y])
                            elif test_build['param'][0]['type'] == 'select_choice':
                                test_build = test_build['next'](
                                    opts.get('choice') or test_build['param'][0]['choices'][0][1])
                            else:
                                break
                        
                        if isinstance(test_build, Action):
                            test_build.validate(battle_map, target=thing)
                            if not test_build.errors:
                                valid_targets.append({
                                    'id': thing.entity_uid,
                                    'name': thing.label() if hasattr(thing, 'label') else str(thing),
                                    'type': thing.__class__.__name__,
                                    'image': getattr(thing, 'profile_image', lambda: None)()
                                })
                    except:
                        # If validation fails, skip this target
                        continue
        
        # Also check if the position itself is a valid target (for area spells)
        try:
            if action_info == 'SpellAction':
                build_map = SpellAction.build(get_game_session(), entity)
                spell_choice = (opts['spell'], opts['at_level'])
                build_map = build_map['next'](spell_choice)
                
                test_build = build_map
                while not isinstance(test_build, Action):
                    param_type = test_build['param'][0]['type']
                    if param_type == 'select_empty_space':
                        test_build = test_build['next']([x, y])
                    elif param_type in ('select_radius', 'select_square', 'select_line', 'select_cone', 'select_cube'):
                        test_build = test_build['next']([x, y])
                    elif param_type == 'select_choice':
                        test_build = test_build['next'](
                            opts.get('choice') or test_build['param'][0]['choices'][0][1])
                    else:
                        break
                
                if isinstance(test_build, Action):
                    test_build.validate(battle_map, target=[x, y])
                    if not test_build.errors:
                        valid_targets.append({
                            'id': f'position_{x}_{y}',
                            'name': f'Position ({x}, {y})',
                            'type': 'position',
                            'image': None
                        })
        except:
            # If position validation fails, skip
            pass
        
        return jsonify({
            'success': True,
            'targets': valid_targets,
            'position': {'x': x, 'y': y}
        })
        
    except Exception as e:
        get_logger().error(f"Error getting targets at position: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
