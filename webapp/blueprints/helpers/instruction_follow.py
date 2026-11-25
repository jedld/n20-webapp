"""Free-text player instructions executed by the System One decision model.

A human types an instruction ("open the door and walk in", "attack the
goblin then dash back"); the JEV/"System One" planner
(:class:`natural20.utils.instruction_follow.InstructionPlanner`) fulfils it
**one step at a time**. Each step re-derives the entity's legal actions,
feeds the model the entity context + instruction + what has already been
done, and either commits the chosen action or returns a loop-control
sentinel (stop / end turn / ready).

Availability rule: the instruction button shows only when a decision
provider (anything exposing ``decide_action``, i.e. ``JevProvider``) is
live for the entity. There is **no generative-LLM fallback** — without a
System One provider the feature is simply absent.

Turn gating mirrors the normal action bar:
  * out of combat  -> any time;
  * in combat      -> only on the creature's own turn (or its legendary
    action phase), and never while a reaction prompt is open.
Out-of-turn intent is expressed through the Ready (hold) action, which the
planner can select as the ``ready`` sentinel.

Instruction state lives in ``session_state['active_instructions']`` keyed
by ``entity_uid`` so it survives requests and is reachable from MCP calls;
the browser session only keeps a pointer (``session['instruction_entity']``)
to the entity the current user last started an instruction for.
"""

import logging

from flask import session

from natural20.actions.ready_action import ReadyAction
from natural20.utils.instruction_follow import (
    DEFAULT_INSTRUCTION_BUDGET,
    InstructionEndTurn,
    InstructionReady,
    InstructionStop,
    follow_instruction_step,
    parse_player_instruction,
    provider_can_follow_instructions,
)

from .auth_utils import user_role
from .runtime_state import get_current_game, get_game_session, get_llm_handler
from .template_globals import controller_of, entities_controlled_by, entity_owners

ENTITY_POINTER_KEY = 'instruction_entity'
STATE_MAP_KEY = 'active_instructions'

logger = logging.getLogger(__name__)


def instruction_decision_provider(entity):
    """Decision (System One) provider for ``entity``, or ``None``.

    Resolved like the battle AI: per-entity ``.ai.yaml`` profile ->
    ``combat.llm_provider`` -> battle provider -> DM provider, then gated
    by :func:`provider_can_follow_instructions` so generative providers
    never qualify.
    """
    try:
        from webapp.ai_config import combat_llm_provider_for

        provider = combat_llm_provider_for(get_llm_handler(), entity)
    except Exception:
        provider = getattr(get_llm_handler(), 'battle_llm_provider', None)
    if provider_can_follow_instructions(provider):
        return provider
    return None


def instruction_available(entity, battle):
    """True when the instruction button should render for ``entity``."""
    try:
        if entity is None or isinstance(entity, str):
            return False
        # Map objects and other non-actors never get the button.
        if not hasattr(entity, 'available_actions'):
            return False
        try:
            if entity.dead() or entity.incapacitated():
                return False
        except Exception:
            pass
        return instruction_decision_provider(entity) is not None
    except Exception:
        return False


def start_instruction(username, entity_id, instruction):
    """Validate and begin an instruction run; returns ``(payload, code)``."""
    try:
        return _start_instruction(username, entity_id, instruction)
    except Exception as exc:  # JSON error body, never an HTML 500
        logger.exception("[instruction] start failed")
        return {'status': 'error',
                'message': f'Could not start the instruction: {exc}'}, 500


def _start_instruction(username, entity_id, instruction):
    instruction = (instruction or '').strip()
    if not instruction:
        return {'status': 'error', 'message': 'Missing instruction'}, 400

    game = get_current_game()
    entity = game.get_entity_by_uid(entity_id)
    if entity is not None:
        entity = game.canonical_map_entity(entity)
    if entity is None:
        return {'status': 'error', 'message': 'Entity not found'}, 404

    err = _ownership_error(entity, username)
    if err:
        return err

    if instruction_decision_provider(entity) is None:
        return {'status': 'error',
                'message': 'No decision model (System One) is available for this character.'}, 503

    _state_map()[entity.entity_uid] = {
        'instruction': instruction,
        'done': [],
        'steps': 0,
    }
    try:
        session[ENTITY_POINTER_KEY] = entity.entity_uid
    except Exception:
        pass
    return run_instruction_step(username, entity.entity_uid)


def run_instruction_step(username, entity_uid=None):
    """Execute one step of the running instruction; returns ``(payload, code)``."""
    try:
        return _run_instruction_step(username, entity_uid)
    except Exception as exc:  # never hand the browser an HTML 500 to parse
        logger.exception("[instruction] step failed")
        return {'status': 'error',
                'message': f'The instruction step failed: {exc}'}, 500


def _run_instruction_step(username, entity_uid=None):
    if not entity_uid:
        entity_uid = session.get(ENTITY_POINTER_KEY)
    state_map = _state_map()
    state = state_map.get(entity_uid) if entity_uid else None
    if not state:
        return {'status': 'done', 'reason': 'No instruction in progress.'}, None

    game = get_current_game()
    entity = game.get_entity_by_uid(entity_uid)
    if entity is not None:
        entity = game.canonical_map_entity(entity)
    if entity is None:
        clear_instruction(entity_uid)
        return {'status': 'done', 'reason': 'The character is no longer available.'}, None

    err = _ownership_error(entity, username)
    if err:
        clear_instruction(entity_uid)
        return err

    battle = game.get_current_battle()
    effective_battle = battle if (battle and entity in battle.entities) else None

    turn_err = _turn_error(game, effective_battle, entity, username)
    if turn_err:
        return turn_err

    provider = instruction_decision_provider(entity)
    if provider is None:
        clear_instruction(entity_uid)
        return {'status': 'error',
                'message': 'The decision model became unavailable.'}, 503

    action, desc = follow_instruction_step(
        provider, effective_battle, entity, state['instruction'],
        done=state['done'])

    if isinstance(action, InstructionStop):
        clear_instruction(entity_uid)
        return {
            'status': 'done',
            'reason': desc or 'No remaining action matches the instruction.',
        }, None

    if isinstance(action, InstructionEndTurn):
        clear_instruction(entity_uid)
        if effective_battle is None:
            return {'status': 'done', 'reason': 'Not in combat; nothing to end.'}, None
        return {'status': 'end_turn', 'reason': desc or 'Ending the turn.'}, None

    if isinstance(action, InstructionReady):
        clear_instruction(entity_uid)
        return _commit_instruction_ready(provider, entity, state, username)

    # A real action: commit it exactly like the action bar would.
    pov_entities = entities_controlled_by(username)
    try:
        result = game.commit_and_update(username, action, pov_entities)
    except Exception as exc:  # engine refusal surfaces as a progress note
        clear_instruction(entity_uid)
        return {'status': 'error', 'message': f'Could not perform the step: {exc}'}, 500
    if isinstance(result, dict) and result.get('status') == 'error':
        clear_instruction(entity_uid)
        return {
            'status': 'done',
            'reason': result.get('message') or 'The step was refused.',
        }, None

    state['done'].append(desc or str(action))
    state['steps'] = int(state.get('steps', 0)) + 1
    if state['steps'] >= DEFAULT_INSTRUCTION_BUDGET:
        clear_instruction(entity_uid)
        return {
            'status': 'done',
            'reason': 'Reached the instruction step limit.',
            'step': {'label': state['done'][-1]},
            'steps': state['steps'],
        }, None

    return {
        'status': 'step',
        'step': {'label': desc or str(action)},
        'steps': state['steps'],
        'remaining': DEFAULT_INSTRUCTION_BUDGET - state['steps'],
    }, None


def stop_instruction(username, entity_uid=None):
    if not entity_uid:
        entity_uid = session.get(ENTITY_POINTER_KEY)
    if entity_uid:
        clear_instruction(entity_uid)
    return {'status': 'stopped'}, None


def clear_instruction(entity_uid):
    try:
        _state_map().pop(entity_uid, None)
    except Exception:
        pass
    try:
        if session.get(ENTITY_POINTER_KEY) == entity_uid:
            session.pop(ENTITY_POINTER_KEY, None)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

def _state_map():
    """Per-entity active instruction runs, persisted on the game session."""
    state = get_game_session().session_state
    return state.setdefault(STATE_MAP_KEY, {})


def _ownership_error(entity, username):
    if username == 'dm' or 'dm' in user_role():
        return None
    if username not in entity_owners(entity):
        return {'status': 'error', 'message': 'Forbidden'}, 403
    return None


def _turn_error(game, battle, entity, username):
    """Gate *when* a player instruction may act, mirroring the action bar."""
    if game is not None:
        if getattr(game, 'waiting_for_reaction', None):
            return {'status': 'error',
                    'message': 'Resolve the pending reaction first.'}, 409
        if getattr(game, 'waiting_for_bardic_inspiration', None):
            return {'status': 'error',
                    'message': 'Resolve the pending bardic inspiration first.'}, 409
    if battle is None:
        return None  # out of combat: any time
    try:
        if battle.in_legendary_phase(entity):
            return None
    except Exception:
        pass
    try:
        current = battle.current_turn()
    except Exception:
        current = None
    if current is not None and current.entity_uid == entity.entity_uid:
        return None
    if controller_of(entity.entity_uid, username) and battle.legendary_phase:
        return None
    return {'status': 'error',
            'message': "It is not this character's turn. "
                       'Use Ready (hold) to act out of turn.'}, 409


def _commit_instruction_ready(provider, entity, state, username=None):
    """Commit the instruction itself as a Ready (hold) action."""
    description = state['instruction']
    game = get_current_game()
    battle = game.get_current_battle()
    if battle is None or not ReadyAction.can(entity, battle):
        return {'status': 'error',
                'message': 'You cannot ready an action right now.'}, 400

    parsed = parse_player_instruction(provider, battle, entity, description)
    if parsed is None:
        return {'status': 'error',
                'message': 'The decision model could not classify the ready action.'}, 503
    if getattr(battle, '_ready_action_resolver', None) is None:
        from webapp.ready_action_handler import make_llm_resolver
        battle.set_ready_action_resolver(make_llm_resolver(get_llm_handler()))

    username = username or session.get('username')
    ready = ReadyAction(get_game_session(), entity, 'ready', opts={
        'description': description,
        'trigger': parsed['trigger'],
        'action_spec': parsed['action_spec'],
    })
    game.commit_and_update(username, ready, entities_controlled_by(username))
    return {
        'status': 'done',
        'reason': 'You ready your action.',
        'ready': {'trigger': parsed['trigger'], 'action_spec': parsed['action_spec']},
    }, None


__all__ = [
    'clear_instruction',
    'instruction_available',
    'instruction_decision_provider',
    'run_instruction_step',
    'start_instruction',
    'stop_instruction',
]
