"""Out-of-combat NPC LLM ticks after each environment time advance.

When exploration time advances (``advance_world_time``), each dialog-enabled NPC
gets one autonomous LLM turn: active movement, active short-term goal, or a
lightweight ambient routine tick.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from natural20.entity import Entity
from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

from webapp.long_rest_npc_simulation import (
    build_long_rest_system_context,
    iter_dialog_npcs,
)
from webapp.npc_background_llm import resolve_background_llm_enabled, npc_autonomous_loops_enabled

logger = logging.getLogger(__name__)

_DEFAULT_ENABLED = True


def load_npc_environment_tick_config(game_session) -> Dict[str, Any]:
    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get('npc_environment_ticks') or {}
    if not isinstance(block, dict):
        block = {}

    env_enabled = os.environ.get('N20_NPC_ENV_TICKS', '1').strip().lower()
    enabled = block.get('enabled', env_enabled not in ('0', 'false', 'no', 'off'))
    enabled = bool(enabled) and npc_autonomous_loops_enabled(game_session)

    return {
        'enabled': enabled,
        'llm_enabled': resolve_background_llm_enabled(game_session, config_block=block),
    }


def _npc_eligible(entity: Entity) -> bool:
    if entity is None or isinstance(entity, PlayerCharacter):
        return False
    if not getattr(entity, 'dialog', False):
        return False
    try:
        if hasattr(entity, 'conscious') and not entity.conscious():
            return False
    except Exception:
        pass
    try:
        if hasattr(entity, 'dead') and entity.dead():
            return False
    except Exception:
        pass
    return True


def default_environment_goal_state(entity: Entity) -> Dict[str, Any]:
    return {
        'goal': (
            f"Continue your routines and react naturally to the scene around "
            f"{entity_label(entity)}."
        ),
        'history': [],
        'attempts': 0,
        'status': 'active',
    }


def execute_npc_ambient_tick(
    rag_handler,
    entity: Entity,
    *,
    llm_handler,
    conversation_service=None,
    npc_prompt_template: Optional[str] = None,
) -> Dict[str, Any]:
    """Single ambient LLM turn when the NPC has no movement task or short-term goal."""
    goal_state = default_environment_goal_state(entity)
    conversation_id = f'{entity.entity_uid}:environment'

    if conversation_service is not None and npc_prompt_template:
        system_prompt = (
            'OUT-OF-COMBAT ENVIRONMENT TICK: Take one autonomous 6-second turn.\n'
            + build_long_rest_system_context(
                conversation_service,
                rag_handler,
                entity,
                prompt_template=npc_prompt_template,
                include_conversation_rules=False,
            )
            + '\n'
            + rag_handler.goal_execution_prompt(entity, goal_state, requester=None)
        )
    else:
        system_prompt = (
            'OUT-OF-COMBAT ENVIRONMENT TICK: Take one autonomous 6-second turn.\n'
            + rag_handler.npc_recap_context_block(entity)
            + '\n'
            + rag_handler.goal_execution_prompt(entity, goal_state, requester=None)
        )

    llm_handler.create_conversation(conversation_id, system_prompt)
    try:
        llm_handler.conversations[conversation_id]['system_prompt'] = system_prompt
    except Exception:
        pass
    llm_handler.update_conversation_history(
        conversation_id,
        list(getattr(entity, 'conversation_buffer', None) or []),
    )
    llm_handler.add_message(
        conversation_id,
        'user',
        rag_handler.goal_turn_snapshot(entity, goal_state, requester=None),
    )
    response = llm_handler.generate_response(conversation_id)
    plan = rag_handler.build_conversation_response_plan(
        response,
        entity,
        speaker=None,
        llm_conversation_handler=llm_handler,
    )
    directive_result = rag_handler.apply_response_plan_directives(
        plan,
        entity,
        speaker=None,
        advance_time=False,
        kick_movement=True,
    )
    return {
        'response': response,
        'plan': plan,
        'directive_result': directive_result,
    }


def run_npc_environment_tick(
    game,
    entity: Entity,
    *,
    rag_handler,
    llm_handler,
    conversation_service=None,
    npc_prompt_template: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run one LLM loop for a single NPC (movement > goal > ambient)."""
    if not _npc_eligible(entity):
        return None

    uid = str(getattr(entity, 'entity_uid', '') or '')
    if not uid:
        return None

    movement_task = game.get_npc_movement_task(entity)
    if movement_task is not None and movement_task.get('status') == 'active':
        if movement_task.get('running'):
            return None
        return {
            'kind': 'movement',
            'result': rag_handler.execute_scheduled_movement(
                uid,
                llm_handler,
                advance_time=False,
            ),
        }

    goal_state = game.get_short_term_goal(entity)
    if goal_state is not None and goal_state.get('status') == 'active':
        if goal_state.get('running'):
            return None
        return {
            'kind': 'goal',
            'result': rag_handler.execute_scheduled_goal(
                uid,
                llm_handler,
                advance_time=False,
            ),
        }

    return {
        'kind': 'ambient',
        'result': execute_npc_ambient_tick(
            rag_handler,
            entity,
            llm_handler=llm_handler,
            conversation_service=conversation_service,
            npc_prompt_template=npc_prompt_template,
        ),
    }


def run_npc_environment_ticks(
    game,
    *,
    rag_handler=None,
    llm_handler=None,
    conversation_service=None,
) -> Dict[str, Any]:
    """Run one LLM loop for every eligible dialog NPC."""
    if game.get_current_battle() is not None:
        return {'skipped': True, 'reason': 'battle_active'}

    cfg = load_npc_environment_tick_config(game.game_session)
    if not cfg.get('enabled'):
        return {'skipped': True, 'reason': 'disabled'}
    if not cfg.get('llm_enabled'):
        return {'skipped': True, 'reason': 'background_llm_disabled'}

    try:
        from webapp.long_rest_npc_simulation import npc_long_rest_simulation_running
        if npc_long_rest_simulation_running():
            return {'skipped': True, 'reason': 'long_rest_simulation'}
    except Exception:
        pass

    if getattr(game, 'realtime_ui_suppressed', lambda: False)():
        return {'skipped': True, 'reason': 'ui_suppressed'}

    if rag_handler is None or llm_handler is None or conversation_service is None:
        try:
            from webapp.blueprints.helpers.runtime_state import (
                get_conversation_service,
                get_entity_rag_handler,
                get_llm_conversation_handler,
            )
        except Exception:
            logger.exception('NPC environment ticks: runtime imports failed')
            return {'skipped': True, 'reason': 'imports_failed'}
        rag_handler = rag_handler or get_entity_rag_handler()
        llm_handler = llm_handler or get_llm_conversation_handler()
        conversation_service = conversation_service or get_conversation_service()

    if rag_handler is None or llm_handler is None:
        return {'skipped': True, 'reason': 'handlers_missing'}

    npc_prompt_template = None
    try:
        npc_prompt_template = conversation_service._npc_system_prompt_template()  # noqa: SLF001
    except Exception:
        npc_prompt_template = None

    npcs = [npc for npc in iter_dialog_npcs(game) if _npc_eligible(npc)]
    stats = {
        'npcs': len(npcs),
        'ticks': 0,
        'movement': 0,
        'goal': 0,
        'ambient': 0,
        'errors': 0,
    }

    for entity in npcs:
        try:
            outcome = run_npc_environment_tick(
                game,
                entity,
                rag_handler=rag_handler,
                llm_handler=llm_handler,
                conversation_service=conversation_service,
                npc_prompt_template=npc_prompt_template,
            )
            if outcome is None:
                continue
            stats['ticks'] += 1
            kind = outcome.get('kind')
            if kind in stats:
                stats[kind] += 1
        except Exception:
            stats['errors'] += 1
            logger.exception('NPC environment tick failed for %s', entity_label(entity))

    return stats


_env_tick_lock = threading.Lock()
_env_tick_running = False
_env_tick_pending = False


def npc_environment_ticks_running() -> bool:
    return _env_tick_running


def schedule_npc_environment_ticks(game, *, force: bool = False) -> bool:
    """Queue a background batch of NPC environment ticks; returns False if skipped."""
    global _env_tick_running, _env_tick_pending

    if not npc_autonomous_loops_enabled(getattr(game, 'game_session', None)):
        return False

    cfg = load_npc_environment_tick_config(game.game_session)
    if not cfg.get('enabled') and not force:
        return False
    if not cfg.get('llm_enabled') and not force:
        return False

    if game.get_current_battle() is not None:
        return False

    if getattr(game, 'realtime_ui_suppressed', lambda: False)():
        return False

    try:
        from webapp.long_rest_npc_simulation import npc_long_rest_simulation_running
        if npc_long_rest_simulation_running():
            return False
    except Exception:
        pass

    with _env_tick_lock:
        if _env_tick_running:
            _env_tick_pending = True
            return True
        _env_tick_running = True

    def _worker():
        global _env_tick_running, _env_tick_pending
        try:
            while True:
                _env_tick_pending = False
                stats = run_npc_environment_ticks(game)
                logger.debug('NPC environment ticks complete: %s', stats)
                with _env_tick_lock:
                    if not _env_tick_pending:
                        break
        except Exception:
            logger.exception('NPC environment tick worker failed')
        finally:
            _env_tick_running = False
            try:
                if not getattr(game, 'realtime_ui_suppressed', lambda: False)():
                    game.socketio.emit('message', {'type': 'refresh_map'})
            except Exception:
                logger.exception('Failed to emit post-environment-tick refresh')

    try:
        game.socketio.start_background_task(_worker)
        return True
    except Exception:
        logger.exception('Failed to start NPC environment tick task')
        thread = threading.Thread(target=_worker, name='npc-environment-ticks', daemon=True)
        thread.start()
        return True
