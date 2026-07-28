"""Trigger NPC LLM reactions when PCs attempt pickpocket.

After a PickpocketAction resolves, this module checks whether nearby NPCs
noticed the attempt (failed or successful) and schedules immediate NPC LLM
replies.  On a failed attempt the NPC victim also reacts immediately because
they caught the pickpocket via passive Insight.

Core detection logic lives in ``natural20.utils.pickpocket_detection``
and notification templates in ``natural20.utils.pickpocket_notification``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from natural20.utils.conversation import entity_label
from natural20.utils.pickpocket_detection import (
    collect_witness_npcs as _core_collect_witness_npcs,
    evaluate_pickpocket_detection as _core_evaluate_pickpocket_detection,
    sleight_of_hand_total_from_roll,
)
from natural20.utils.pickpocket_notification import (
    pc_pickpocket_detected_message,
    pickpocket_attempt_note as _core_pickpocket_attempt_note,
)

logger = logging.getLogger(__name__)


def _is_pickpocket_action(action) -> bool:
    if action is None:
        return False
    return type(action).__name__ == 'PickpocketAction'


def _is_pc(entity) -> bool:
    if entity is None:
        return False
    if hasattr(entity, 'is_npc'):
        try:
            if callable(entity.is_npc):
                return not entity.is_npc()
            return not entity.is_npc
        except Exception:
            pass
    from natural20.player_character import PlayerCharacter
    return isinstance(entity, PlayerCharacter)


def _is_npc(entity) -> bool:
    if entity is None:
        return False
    if hasattr(entity, 'is_npc'):
        try:
            if callable(entity.is_npc):
                return bool(entity.is_npc())
            return bool(entity.is_npc)
        except Exception:
            pass
    from natural20.npc import Npc
    return isinstance(entity, Npc)


def _npc_can_react(npc) -> bool:
    if npc is None or not _is_npc(npc):
        return False
    if callable(getattr(npc, 'dead', None)) and npc.dead():
        return False
    if callable(getattr(npc, 'conscious', None)) and not npc.conscious():
        return False
    return True


def pickpocket_attempt_note(
    witness_npc,
    pickpocketer,
    target,
    item_name: str,
    success: bool,
    can_see: bool = True,
    detection_type: str = 'seen',
) -> str:
    return _core_pickpocket_attempt_note(
        witness_npc,
        pickpocketer,
        target,
        item_name,
        success,
        detection_type=detection_type,
        can_see=can_see,
    )


def evaluate_pickpocket_detection(
    session,
    pickpocketer,
    target,
    battle=None,
    battle_map=None,
    *,
    success: bool = False,
    sleight_of_hand_total=None,
) -> List[Dict[str, Any]]:
    return _core_evaluate_pickpocket_detection(
        session,
        pickpocketer,
        target,
        battle=battle,
        battle_map=battle_map,
        success=success,
        sleight_of_hand_total=sleight_of_hand_total,
    )


def collect_witness_npcs(
    session,
    pickpocketer,
    target,
    battle=None,
    battle_map=None,
) -> List[Dict[str, Any]]:
    return _core_collect_witness_npcs(
        pickpocketer,
        target,
        session,
        battle=battle,
        battle_map=battle_map,
    )


def _resolve_npc_entity(session, detection: Dict[str, Any]):
    npc_entity = detection.get('npc_entity')
    if npc_entity is not None:
        return npc_entity
    npc_uid = (detection.get('npc') or {}).get('uid')
    if session is None or not npc_uid:
        return None
    try:
        return session.entity_by_uid(str(npc_uid))
    except Exception:
        return None


def _emit_pc_pickpocket_warning(game, username: Optional[str], message: str) -> None:
    if not message:
        return
    socketio = getattr(game, 'socketio', None)
    if socketio is None:
        return
    sids: List[str] = []
    if username:
        sids = list(getattr(game, 'username_to_sid', {}).get(username, []) or [])
    payload = {
        'type': 'pickpocket_detected',
        'message': {
            'text': message,
        },
    }
    try:
        if sids:
            for sid in sids:
                socketio.emit('message', payload, to=sid)
        else:
            socketio.emit('message', payload)
    except Exception:
        logger.warning('Failed to emit pickpocket_detected UI message', exc_info=True)


def _schedule_immediate_pickpocket_reaction(
    game,
    conversation_service,
    npc,
    pickpocketer,
    *,
    target,
    item_name: str,
    success: bool,
    detection_type: str,
    can_see: bool,
    role: str,
    username: Optional[str],
) -> None:
    def _job() -> None:
        try:
            conversation_service.handle_npc_pickpocket_detected(
                npc,
                pickpocketer,
                target=target,
                item_name=item_name,
                success=success,
                detection_type=detection_type,
                can_see=can_see,
                role=role,
                requesting_username=username,
            )
        except Exception:
            logger.warning(
                'Immediate pickpocket reaction failed for NPC %s',
                getattr(npc, 'entity_uid', npc),
                exc_info=True,
            )

    socketio = getattr(game, 'socketio', None)
    if socketio is not None and hasattr(socketio, 'start_background_task'):
        socketio.start_background_task(_job)
    else:
        _job()


def process_pickpocket_reactions(
    game,
    action,
    *,
    in_combat: bool = False,
    username: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """After a pickpocket action, detect witnesses and schedule NPC reactions."""
    del in_combat
    if action is None or not _is_pickpocket_action(action):
        return []

    source = getattr(action, 'source', None)
    if not _is_pc(source):
        return []

    result_items = getattr(action, 'result', None) or []
    target = None
    pickpocket_success = False
    item_name = 'unknown item'
    sleight_of_hand_total = None
    for item in result_items:
        if isinstance(item, dict) and item.get('type') == 'pickpocket':
            target = item.get('target')
            pickpocket_success = item.get('success', False)
            item_name = item.get('item_name') or 'something from their belongings'
            sleight_of_hand_total = sleight_of_hand_total_from_roll(item.get('roll'))
            if sleight_of_hand_total is None:
                try:
                    sleight_of_hand_total = int(item.get('roll_total'))
                except (TypeError, ValueError):
                    sleight_of_hand_total = None
            break

    if target is None:
        return []

    session = getattr(game, 'game_session', None)
    if session is None:
        return []

    battle = game.get_current_battle()
    battle_map = game.get_map_for_entity(source)

    detections: List[Dict[str, Any]] = []
    try:
        detections = evaluate_pickpocket_detection(
            session,
            source,
            target,
            battle=battle,
            battle_map=battle_map,
            success=pickpocket_success,
            sleight_of_hand_total=sleight_of_hand_total,
        )
    except Exception:
        logger.warning(
            'Pickpocket detection failed for %s attempting on %s',
            getattr(source, 'entity_uid', source),
            getattr(target, 'entity_uid', target),
            exc_info=True,
        )

    try:
        from webapp.blueprints.helpers.runtime_state import get_conversation_service

        conversation_service = get_conversation_service()
    except Exception:
        conversation_service = None

    if conversation_service is None or conversation_service.llm_conversation_handler is None:
        return detections

    scheduled = 0
    reactor_names: List[str] = []

    if not pickpocket_success and _is_npc(target) and _npc_can_react(target):
        _schedule_immediate_pickpocket_reaction(
            game,
            conversation_service,
            target,
            source,
            target=target,
            item_name=item_name,
            success=False,
            detection_type='seen',
            can_see=True,
            role='victim',
            username=username,
        )
        scheduled += 1
        victim_name = entity_label(target)
        if victim_name:
            reactor_names.append(victim_name)

    for detection in detections:
        npc_entity = _resolve_npc_entity(session, detection)
        if npc_entity is None:
            continue

        reactor_name = entity_label(npc_entity)
        if reactor_name:
            reactor_names.append(reactor_name)

        _schedule_immediate_pickpocket_reaction(
            game,
            conversation_service,
            npc_entity,
            source,
            target=target,
            item_name=item_name,
            success=pickpocket_success,
            detection_type=detection.get('detection_type', 'seen'),
            can_see=bool(detection.get('can_see_pickpocketer', True)),
            role='witness',
            username=username,
        )
        scheduled += 1

    if scheduled > 0:
        outcome = 'succeeded' if pickpocket_success else 'failed'
        logger.info(
            'Pickpocket witnessed (%s): %s attempted on %s, %d NPC(s) reacting',
            outcome,
            getattr(source, 'entity_uid', source),
            getattr(target, 'entity_uid', target),
            scheduled,
        )
        pc_message = pc_pickpocket_detected_message(
            source,
            target,
            item_name,
            reactor_names,
            success=pickpocket_success,
        )
        _emit_pc_pickpocket_warning(game, username, pc_message)
    elif not pickpocket_success:
        pc_message = pc_pickpocket_detected_message(
            source,
            target,
            item_name,
            [],
            success=False,
        )
        _emit_pc_pickpocket_warning(game, username, pc_message)

    return detections


def collect_detections_for_tests(action_result) -> List[Dict[str, Any]]:
    return action_result if isinstance(action_result, list) else []
