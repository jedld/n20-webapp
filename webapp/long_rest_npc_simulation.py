"""Background NPC simulation during a long rest.

Runs a fixed number of LLM ticks per dialog-enabled NPC while the party sleeps.
Conversation, movement, and goal directives are applied without realtime UI emits;
a single ``refresh_map`` is emitted when the simulation completes.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from natural20.entity import Entity
from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

from webapp.npc_background_llm import resolve_background_llm_enabled, npc_autonomous_loops_enabled

logger = logging.getLogger(__name__)

_DEFAULT_LOOPS = 3
_DEFAULT_RANGE_FT = 60


def load_long_rest_sim_config(game_session) -> Dict[str, Any]:
    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get('long_rest_npc_simulation') or {}
    if not isinstance(block, dict):
        block = {}

    env_enabled = os.environ.get('N20_LONG_REST_NPC_SIM', '1').strip().lower()
    enabled = block.get('enabled', env_enabled not in ('0', 'false', 'no', 'off'))
    enabled = bool(enabled) and npc_autonomous_loops_enabled(game_session)

    try:
        loops = int(block.get('loops_per_npc') or os.environ.get('N20_LONG_REST_NPC_LOOPS') or _DEFAULT_LOOPS)
    except (TypeError, ValueError):
        loops = _DEFAULT_LOOPS

    try:
        range_ft = int(
            block.get('conversation_range_ft')
            or os.environ.get('N20_LONG_REST_NPC_RANGE_FT')
            or _DEFAULT_RANGE_FT
        )
    except (TypeError, ValueError):
        range_ft = _DEFAULT_RANGE_FT

    return {
        'enabled': bool(enabled),
        'llm_enabled': resolve_background_llm_enabled(game_session, config_block=block),
        'loops_per_npc': max(1, loops),
        'conversation_range_ft': max(15, range_ft),
    }


def iter_dialog_npcs(game) -> List[Entity]:
    seen: Set[str] = set()
    npcs: List[Entity] = []
    maps = getattr(game, 'maps', None) or {}
    for battle_map in maps.values():
        try:
            entities = list(battle_map.entities)
        except Exception:
            continue
        for entity in entities:
            uid = str(getattr(entity, 'entity_uid', '') or '')
            if not uid or uid in seen:
                continue
            if isinstance(entity, PlayerCharacter):
                continue
            if not getattr(entity, 'dialog', False):
                continue
            seen.add(uid)
            npcs.append(entity)
    return npcs


def _entity_text_blob(entity: Entity) -> str:
    parts: List[str] = []
    for getter_name in ('backstory', 'description'):
        getter = getattr(entity, getter_name, None)
        if callable(getter):
            try:
                parts.append(str(getter() or ''))
            except Exception:
                pass
    props = getattr(entity, 'properties', None) or {}
    journal = props.get('journal')
    if journal:
        parts.append(str(journal))
    return ' '.join(parts).lower()


def _entity_handles(entity: Entity) -> Set[str]:
    handles: Set[str] = set()
    label = entity_label(entity).lower()
    if label:
        handles.add(label)
        for token in re.split(r'\s+', label):
            if len(token) >= 3:
                handles.add(token)
    props = getattr(entity, 'properties', None) or {}
    for key in ('handle', 'name', 'kind'):
        value = props.get(key)
        if value:
            handles.add(str(value).lower().lstrip('@'))
    uid = str(getattr(entity, 'entity_uid', '') or '')
    if uid:
        handles.add(uid.lower())
        for part in uid.split('_'):
            if len(part) >= 3:
                handles.add(part)
    return {h for h in handles if h}


def _mentions_other(text: str, other: Entity) -> bool:
    if not text:
        return False
    for handle in _entity_handles(other):
        if len(handle) < 3:
            continue
        if re.search(rf'\b{re.escape(handle)}\b', text):
            return True
    return False


def find_related_npcs_in_range(
    entity: Entity,
    candidates: Sequence[Entity],
    rag_handler,
    *,
    range_ft: int,
) -> List[Entity]:
    nearby = rag_handler.get_nearby_entities(entity, range_ft=range_ft, include_extended=True)
    reachable_uids = {
        str(entry['entity'].entity_uid)
        for entry in nearby
        if entry.get('entity') is not None and entry.get('reachable_now', entry.get('status') == 'reachable')
    }
    if not reachable_uids:
        reachable_uids = {
            str(entry['entity'].entity_uid)
            for entry in nearby
            if entry.get('entity') is not None
        }

    my_text = _entity_text_blob(entity)
    related: List[Entity] = []
    for other in candidates:
        if other is entity:
            continue
        uid = str(getattr(other, 'entity_uid', '') or '')
        if uid not in reachable_uids:
            continue
        other_text = _entity_text_blob(other)
        if _mentions_other(my_text, other) or _mentions_other(other_text, entity):
            related.append(other)
    return related


def _heuristic_summary(lines: Sequence[str], *, title: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    if not cleaned:
        return 'Quiet long rest with little notable activity.'
    return '; '.join(cleaned[-6:])[:500]


def _llm_summarize(
    llm_handler,
    *,
    conversation_id: str,
    system_prompt: str,
    user_prompt: str,
) -> Optional[str]:
    if llm_handler is None:
        return None
    try:
        llm_handler.create_conversation(conversation_id, system_prompt)
        llm_handler.add_message(conversation_id, 'user', user_prompt)
        response = llm_handler.generate_response(conversation_id)
    except Exception:
        logger.exception('Long rest summary LLM call failed for %s', conversation_id)
        return None
    text = str(response or '').strip()
    return text or None


def _parse_remember_directive(rag_handler, entity: Entity, response: str) -> Optional[Dict[str, Any]]:
    if not response or entity is None:
        return None
    directives = rag_handler.parse_action_directives(response, entity, speaker=None)
    remember = directives.get('remember') if isinstance(directives, dict) else None
    if remember and remember.get('title'):
        return remember
    return None


def commit_long_rest_core_memory(
    rag_handler,
    entity: Entity,
    activity_log: Sequence[str],
    *,
    game_time: int,
    llm_handler=None,
) -> Optional[Dict[str, Any]]:
    store = rag_handler._npc_memory_store()  # noqa: SLF001 — shared persistence API
    if store is None or not activity_log:
        return None

    uid = getattr(entity, 'entity_uid', None)
    if not uid:
        return None

    label = entity_label(entity)
    user_prompt = (
        f"Summarize what {label} did during the party's long rest.\n"
        "Activity log:\n"
        + '\n'.join(f"- {line}" for line in activity_log[-20:])
        + "\n\nRespond with a single [REMEMBER: title=..., summary=..., body=..., importance=4, tags=long_rest,core_memory] tag."
    )
    system_prompt = (
        "You compress NPC downtime into one durable memory for a D&D campaign. "
        "Return only the [REMEMBER: ...] directive."
    )
    llm_text = _llm_summarize(
        llm_handler,
        conversation_id=f'{uid}:long_rest_memory',
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    remember = _parse_remember_directive(rag_handler, entity, llm_text or '')
    if remember:
        return store.create(
            uid,
            title=remember.get('title') or f'Long rest — {label}',
            summary=remember.get('summary'),
            body=remember.get('body'),
            tags=remember.get('tags') or ['long_rest', 'core_memory'],
            importance=int(remember.get('importance') or 4),
            game_time=game_time,
            source='long_rest',
            created_by='system',
        )

    summary = _heuristic_summary(activity_log, title=label)
    return store.create(
        uid,
        title=f'Long rest — {label}',
        summary=summary,
        body='\n'.join(activity_log)[-8000:],
        tags=['long_rest', 'core_memory'],
        importance=4,
        game_time=game_time,
        source='long_rest',
        created_by='system',
    )


def summarize_conversation_buffer(
    entity: Entity,
    rag_handler,
    *,
    game_time: int,
    llm_handler=None,
) -> None:
    buffer = list(getattr(entity, 'conversation_buffer', None) or [])
    if len(buffer) < 2:
        if hasattr(entity, 'clear_conversation_buffer'):
            entity.clear_conversation_buffer()
        else:
            entity.conversation_buffer = []
        return

    lines: List[str] = []
    for entry in buffer[-40:]:
        if not isinstance(entry, dict):
            continue
        source = entry.get('source')
        message = str(entry.get('message') or '').strip()
        if not message:
            continue
        speaker = entity_label(source) if source is not None else 'someone'
        lines.append(f'{speaker}: {message}')

    if not lines:
        entity.clear_conversation_buffer()
        return

    uid = getattr(entity, 'entity_uid', None)
    summary_text = _llm_summarize(
        llm_handler,
        conversation_id=f'{uid}:long_rest_buffer',
        system_prompt='Summarize NPC conversation snippets into 2-4 sentences of third-person past tense.',
        user_prompt='\n'.join(lines),
    ) or _heuristic_summary(lines, title=entity_label(entity))

    store = rag_handler._npc_memory_store()  # noqa: SLF001
    if store is not None and uid:
        try:
            store.create(
                uid,
                title=f'Conversation recap — {entity_label(entity)}',
                summary=summary_text[:500],
                body='\n'.join(lines)[-8000:],
                tags=['long_rest', 'conversation_recap'],
                importance=2,
                game_time=game_time,
                source='long_rest',
                created_by='system',
            )
        except Exception:
            logger.exception('Failed to store conversation recap for %s', uid)

    entity.clear_conversation_buffer()


def long_rest_goal_state(entity: Entity, partner: Optional[Entity], loop_index: int) -> Dict[str, Any]:
    if partner is not None:
        goal = (
            f"During the long rest, catch up with {entity_label(partner)} "
            f"(loop {loop_index + 1}). Pursue routines and personal desires."
        )
    else:
        goal = (
            'During the long rest while the party sleeps, handle routines and personal business.'
        )
    return {'goal': goal, 'history': [], 'attempts': loop_index, 'status': 'active'}


def build_long_rest_system_context(
    conversation_service,
    rag_handler,
    entity: Entity,
    *,
    partner: Optional[Entity] = None,
    prompt_template: Optional[str] = None,
    include_conversation_rules: bool = True,
) -> str:
    """Standard NPC identity plus memories, logs, and conversation recap for long rest."""
    if conversation_service is None or not prompt_template:
        return rag_handler.npc_recap_context_block(entity)
    if include_conversation_rules:
        system_prompt = conversation_service.build_npc_system_prompt(
            entity,
            prompt_template,
            speaker=partner,
        )
    else:
        system_prompt = conversation_service.build_npc_identity_prompt(entity, prompt_template)
    system_prompt += rag_handler.npc_recap_context_block(entity)
    return system_prompt


def execute_long_rest_turn(
    rag_handler,
    entity: Entity,
    *,
    partner: Optional[Entity],
    loop_index: int,
    llm_handler,
    conversation_service=None,
    npc_prompt_template: Optional[str] = None,
) -> Dict[str, Any]:
    goal_state = long_rest_goal_state(entity, partner, loop_index)
    requester = partner
    conversation_id = f'{entity.entity_uid}:long_rest'

    if conversation_service is not None and npc_prompt_template:
        system_prompt = (
            'LONG REST BACKGROUND SIMULATION: The adventuring party is asleep for 8 hours. '
            'No players are present. Act in character on routines, desires, and relationships.\n'
            + build_long_rest_system_context(
                conversation_service,
                rag_handler,
                entity,
                partner=partner,
                prompt_template=npc_prompt_template,
            )
            + '\n'
            + rag_handler.goal_execution_prompt(entity, goal_state, requester=requester)
        )
    else:
        system_prompt = (
            'LONG REST BACKGROUND SIMULATION: The adventuring party is asleep for 8 hours. '
            'No players are present. Act in character on routines, desires, and relationships.\n'
            + rag_handler.npc_recap_context_block(entity)
            + '\n'
            + rag_handler.goal_execution_prompt(entity, goal_state, requester=requester)
        )

    llm_handler.create_conversation(conversation_id, system_prompt)
    try:
        llm_handler.conversations[conversation_id]['system_prompt'] = system_prompt
    except Exception:
        pass
    llm_handler.update_conversation_history(conversation_id, list(getattr(entity, 'conversation_buffer', None) or []))
    llm_handler.add_message(
        conversation_id,
        'user',
        rag_handler.goal_turn_snapshot(entity, goal_state, requester=requester),
    )
    response = llm_handler.generate_response(conversation_id)
    plan = rag_handler.build_conversation_response_plan(
        response,
        entity,
        speaker=requester,
        llm_conversation_handler=llm_handler,
    )
    directive_result = rag_handler.apply_response_plan_directives(
        plan,
        entity,
        speaker=requester,
        advance_time=False,
        kick_movement=True,
    )
    return {
        'response': response,
        'plan': plan,
        'directive_result': directive_result,
    }


def _activity_note(entity: Entity, turn_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not turn_result:
        return None
    plan = turn_result.get('plan') or {}
    message = str(plan.get('message') or '').strip()
    actions = list((turn_result.get('directive_result') or {}).get('executed_actions') or [])
    if message:
        return f"{entity_label(entity)}: {message[:240]}"
    if actions:
        return f"{entity_label(entity)}: {', '.join(actions)}"
    return None


def run_npc_exchange(
    conversation_service,
    speaker: Entity,
    listener: Entity,
    *,
    llm_handler,
    activity_log: List[str],
    rag_handler=None,
) -> None:
    prompt_template = conversation_service._npc_system_prompt_template()  # noqa: SLF001
    recap = ''
    if rag_handler is not None:
        recap = rag_handler.campaign_log_context_summary(speaker)
    inject = [
        (
            f'LONG REST: You are catching up with {entity_label(listener)} while the party sleeps. '
            'Speak briefly in character.'
        ),
    ]
    if recap:
        inject.append(recap.strip())
    turn = conversation_service._run_single_npc_reply_turn(  # noqa: SLF001
        speaker,
        listener,
        prompt_template,
        llm_conversation_handler=llm_handler,
        inject_system_messages=inject,
    )
    note = _activity_note(speaker, {'plan': (turn or {}).get('reply_plan'), 'directive_result': {}})
    if note:
        activity_log.append(note)

    if turn is None:
        return

    reply_turn = conversation_service._run_single_npc_reply_turn(  # noqa: SLF001
        listener,
        speaker,
        prompt_template,
        llm_conversation_handler=llm_handler,
        inject_system_messages=[
            f'LONG REST: {entity_label(speaker)} just spoke to you. Reply briefly in character.',
        ],
    )
    note = _activity_note(listener, {'plan': (reply_turn or {}).get('reply_plan'), 'directive_result': {}})
    if note:
        activity_log.append(note)


def run_long_rest_npc_simulation(
    game,
    *,
    resting_entity=None,
    game_time: int,
    conversation_service,
    rag_handler,
    llm_handler,
    config: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run the full long-rest NPC simulation (blocking; caller should suppress UI)."""
    cfg = dict(config or load_long_rest_sim_config(game.game_session))
    if not cfg.get('enabled') and not force:
        return {'skipped': True, 'reason': 'disabled'}
    if not cfg.get('llm_enabled') and not force:
        return {'skipped': True, 'reason': 'background_llm_disabled'}

    if game.get_current_battle() is not None:
        return {'skipped': True, 'reason': 'battle_active'}

    npcs = iter_dialog_npcs(game)
    loops = int(cfg.get('loops_per_npc') or _DEFAULT_LOOPS)
    range_ft = int(cfg.get('conversation_range_ft') or _DEFAULT_RANGE_FT)
    npc_prompt_template = None
    try:
        npc_prompt_template = conversation_service._npc_system_prompt_template()  # noqa: SLF001
    except Exception:
        npc_prompt_template = None
    long_rest_context_cache: Dict[str, str] = {}

    def _long_rest_extra_context(entity: Entity) -> str:
        uid = str(getattr(entity, 'entity_uid', '') or '')
        if uid in long_rest_context_cache:
            return long_rest_context_cache[uid]
        block = build_long_rest_system_context(
            conversation_service,
            rag_handler,
            entity,
            prompt_template=npc_prompt_template,
            include_conversation_rules=False,
        )
        long_rest_context_cache[uid] = block
        return block

    stats = {
        'npcs': len(npcs),
        'loops_per_npc': loops,
        'activity_entries': 0,
        'memories_created': 0,
        'buffers_summarized': 0,
    }

    for entity in npcs:
        activity_log: List[str] = []
        related = find_related_npcs_in_range(entity, npcs, rag_handler, range_ft=range_ft)

        for loop_index in range(loops):
            partner = related[loop_index % len(related)] if related else None

            movement_task = game.get_npc_movement_task(entity)
            goal_state = game.get_short_term_goal(entity)

            turn_result = None
            try:
                if movement_task is not None and movement_task.get('status') == 'active':
                    turn_result = {
                        'directive_result': rag_handler.execute_scheduled_movement(
                            entity.entity_uid,
                            llm_handler,
                            advance_time=False,
                            extra_system_context=_long_rest_extra_context(entity),
                        ),
                    }
                elif goal_state is not None and goal_state.get('status') == 'active':
                    turn_result = {
                        'directive_result': rag_handler.execute_scheduled_goal(
                            entity.entity_uid,
                            llm_handler,
                            advance_time=False,
                            extra_system_context=_long_rest_extra_context(entity),
                        ),
                    }
                elif partner is not None and loop_index % 2 == 0:
                    run_npc_exchange(
                        conversation_service,
                        entity,
                        partner,
                        llm_handler=llm_handler,
                        activity_log=activity_log,
                        rag_handler=rag_handler,
                    )
                else:
                    turn_result = execute_long_rest_turn(
                        rag_handler,
                        entity,
                        partner=partner,
                        loop_index=loop_index,
                        llm_handler=llm_handler,
                        conversation_service=conversation_service,
                        npc_prompt_template=npc_prompt_template,
                    )
            except Exception:
                logger.exception('Long rest tick failed for %s', entity_label(entity))
                continue

            note = _activity_note(entity, turn_result)
            if note:
                activity_log.append(note)

        if activity_log:
            stats['activity_entries'] += len(activity_log)
            try:
                if commit_long_rest_core_memory(
                    rag_handler,
                    entity,
                    activity_log,
                    game_time=game_time,
                    llm_handler=llm_handler,
                ):
                    stats['memories_created'] += 1
            except Exception:
                logger.exception('Long rest memory commit failed for %s', entity_label(entity))

        try:
            summarize_conversation_buffer(
                entity,
                rag_handler,
                game_time=game_time,
                llm_handler=llm_handler,
            )
            stats['buffers_summarized'] += 1
        except Exception:
            logger.exception('Long rest buffer summarize failed for %s', entity_label(entity))

    if resting_entity is not None:
        logger.info(
            'Long rest NPC simulation complete after rest by %s: %s',
            entity_label(resting_entity),
            stats,
        )
    else:
        logger.info('Long rest NPC simulation complete: %s', stats)
    return stats


class RealtimeUiSuppressor:
    """Thread-safe UI suppression counter on GameManagement."""

    _ATTR = '_realtime_ui_suppressed'

    @classmethod
    def suppressed(cls, game) -> bool:
        return int(getattr(game, cls._ATTR, 0) or 0) > 0

    @classmethod
    @contextmanager
    def suppress(cls, game):
        counter = getattr(game, cls._ATTR, None)
        if counter is None:
            setattr(game, cls._ATTR, 0)
        setattr(game, cls._ATTR, int(getattr(game, cls._ATTR, 0)) + 1)
        try:
            yield
        finally:
            setattr(game, cls._ATTR, max(0, int(getattr(game, cls._ATTR, 1)) - 1))


_long_rest_sim_lock = threading.Lock()
_long_rest_sim_running = False


def npc_long_rest_simulation_running() -> bool:
    return _long_rest_sim_running


def schedule_long_rest_npc_simulation(
    game,
    *,
    resting_entity=None,
    game_time: Optional[int] = None,
    force: bool = False,
) -> bool:
    """Queue background long-rest NPC simulation; returns False if skipped.

    When ``force`` is True (DM manual trigger), runs even if disabled in game.yml
    unless the master ``npc_autonomous_loops`` switch is off.
    """
    global _long_rest_sim_running

    if not npc_autonomous_loops_enabled(getattr(game, 'game_session', None)):
        return False

    config = load_long_rest_sim_config(game.game_session)
    if not config.get('enabled') and not force:
        return False
    if not config.get('llm_enabled') and not force:
        return False

    if _long_rest_sim_running:
        return False

    if game.get_current_battle() is not None:
        return False

    if game_time is None:
        game_time = getattr(getattr(game, 'game_session', None), 'game_time', 0) or 0

    try:
        from webapp.blueprints.helpers.runtime_state import (
            get_conversation_service,
            get_entity_rag_handler,
            get_llm_conversation_handler,
        )
    except Exception:
        logger.exception('Long rest NPC simulation: runtime imports failed')
        return False

    conversation_service = get_conversation_service()
    rag_handler = get_entity_rag_handler()
    llm_handler = get_llm_conversation_handler()
    if conversation_service is None or rag_handler is None:
        return False

    def _worker():
        global _long_rest_sim_running
        with _long_rest_sim_lock:
            if _long_rest_sim_running:
                logger.info('Long rest NPC simulation already running; skipping duplicate')
                return
            _long_rest_sim_running = True
        try:
            with RealtimeUiSuppressor.suppress(game):
                run_long_rest_npc_simulation(
                    game,
                    resting_entity=resting_entity,
                    game_time=game_time,
                    conversation_service=conversation_service,
                    rag_handler=rag_handler,
                    llm_handler=llm_handler,
                    config=config,
                    force=force,
                )
        except Exception:
            logger.exception('Long rest NPC simulation worker failed')
        finally:
            _long_rest_sim_running = False
            try:
                game.socketio.emit('message', {'type': 'refresh_map'})
                game.socketio.emit(
                    'message',
                    {'type': 'turn', 'message': {'game_time': game.game_session.game_time}},
                )
            except Exception:
                logger.exception('Failed to emit post-long-rest refresh')

    try:
        game.socketio.start_background_task(_worker)
        return True
    except Exception:
        logger.exception('Failed to start long rest NPC simulation task')
        thread = threading.Thread(target=_worker, name='long-rest-npc-sim', daemon=True)
        thread.start()
        return True
