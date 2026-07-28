"""Trigger NPC LLM reactions when PCs steal items from containers outside combat.

After an InteractAction resolves, this module checks if any NPCs noticed the
theft based on vision and perception, and injects system notes into the NPC's
LLM context so they can react in character.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from natural20.utils.conversation import entity_label
from natural20.utils.steal_detection import evaluate_steal_detection
from natural20.utils.steal_notification import pc_steal_detected_message

logger = logging.getLogger(__name__)


def _is_interact_action(action) -> bool:
    """Check if the action is an InteractAction (loot/store/transfer)."""
    if action is None:
        return False
    return type(action).__name__ == 'InteractAction'


def _get_interact_items(action) -> Optional[Dict[str, Any]]:
    """Extract the items payload from an InteractAction result.

    Returns None if no items were found or if the action wasn't an interact.
    """
    if not _is_interact_action(action):
        return None

    result = getattr(action, 'result', None) or []
    for item in result:
        if isinstance(item, dict) and item.get('type') == 'interact':
            # The 'items' field contains the transfer dict
            items = item.get('items')
            if items and isinstance(items, dict):
                return items
    return None


def _is_pc(entity) -> bool:
    """Check if an entity is a Player Character (not an NPC)."""
    if entity is None:
        return False
    if hasattr(entity, 'is_npc'):
        try:
            if callable(entity.is_npc):
                return not entity.is_npc()
            return not entity.is_npc
        except Exception:
            pass
    # Fallback: PlayerCharacter instances are PCs
    from natural20.player_character import PlayerCharacter
    return isinstance(entity, PlayerCharacter)


def _items_taken_from_container(items_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items_from = items_payload.get('from', {}) or {}
    items_list: List[Dict[str, Any]] = []
    for item_name, qty in zip(
        items_from.get('items', []) or [],
        items_from.get('qty', []) or [],
    ):
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            qty_int = 0
        if qty_int > 0:
            items_list.append({'item': item_name, 'qty': qty_int})
    return items_list


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


def _emit_pc_steal_warning(game, username: Optional[str], message: str) -> None:
    if not message:
        return
    socketio = getattr(game, 'socketio', None)
    if socketio is None:
        return
    sids: List[str] = []
    if username:
        sids = list(getattr(game, 'username_to_sid', {}).get(username, []) or [])
    payload = {
        'type': 'steal_detected',
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
        logger.warning('Failed to emit steal_detected UI message', exc_info=True)


def _schedule_immediate_steal_reaction(
    game,
    conversation_service,
    npc,
    thief,
    *,
    container_info,
    items_list,
    detection_type: str,
    ownership_info,
    can_see: bool,
    username: Optional[str],
) -> None:
    """Run an immediate NPC LLM reply in the background (non-blocking)."""

    def _job() -> None:
        try:
            conversation_service.handle_npc_steal_detected(
                npc,
                thief,
                container_info=container_info,
                items=items_list,
                detection_type=detection_type,
                ownership_info=ownership_info,
                can_see=can_see,
                requesting_username=username,
            )
        except Exception:
            logger.warning(
                'Immediate steal reaction failed for NPC %s',
                getattr(npc, 'entity_uid', npc),
                exc_info=True,
            )

    socketio = getattr(game, 'socketio', None)
    if socketio is not None and hasattr(socketio, 'start_background_task'):
        socketio.start_background_task(_job)
    else:
        _job()


def process_steal_reactions(
    game,
    action,
    *,
    in_combat: bool = False,
    username: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """After an out-of-combat interact action, detect theft and notify NPCs.

    This is the main entry point, called from ``commit_and_update`` after
    the action resolves.

    Returns:
        List of dicts describing detected thefts (for logging/telemetry).
    """
    if in_combat or action is None:
        return []

    # Only process InteractAction (loot/store/transfer)
    items_payload = _get_interact_items(action)
    if items_payload is None:
        return []

    source = getattr(action, 'source', None)
    if not _is_pc(source):
        return []

    target = None
    for item in (getattr(action, 'result', None) or []):
        if isinstance(item, dict) and item.get('type') == 'interact':
            target = item.get('target')
            break

    if target is None:
        return []

    items_list = _items_taken_from_container(items_payload)
    if not items_list:
        return []

    detections: List[Dict[str, Any]] = []
    try:
        session = getattr(game, 'game_session', None)
        if session is None:
            return []

        battle = game.get_current_battle()
        battle_map = game.get_map_for_entity(source)

        detections = evaluate_steal_detection(
            session,
            source,
            target,
            items_list,
            battle=battle,
            battle_map=battle_map,
        )
    except Exception:
        logger.warning(
            'Steal detection failed for %s interacting with %s',
            getattr(source, 'entity_uid', source),
            getattr(target, 'entity_uid', target),
            exc_info=True,
        )
        return []

    if not detections:
        return []

    container_info = {
        'name': entity_label(target),
        'uid': getattr(target, 'entity_uid', ''),
    }

    try:
        from webapp.blueprints.helpers.runtime_state import get_conversation_service

        conversation_service = get_conversation_service()
    except Exception:
        conversation_service = None

    scheduled = 0
    witness_names: List[str] = []
    for detection in detections:
        npc_entity = _resolve_npc_entity(session, detection)
        if npc_entity is None:
            continue

        witness_name = entity_label(npc_entity)
        if witness_name:
            witness_names.append(witness_name)

        ownership_info = None
        ownership = detection.get('ownership')
        if ownership:
            owner = ownership.get('owner', '')
            location = ownership.get('location', '')
            if owner or location:
                ownership_info = {
                    'owner': owner,
                    'location': location,
                }

        if conversation_service is None or conversation_service.llm_conversation_handler is None:
            continue

        _schedule_immediate_steal_reaction(
            game,
            conversation_service,
            npc_entity,
            source,
            container_info=container_info,
            items_list=items_list,
            detection_type=detection.get('detection_type', 'seen'),
            ownership_info=ownership_info,
            can_see=bool(detection.get('can_see_pc', True)),
            username=username,
        )
        scheduled += 1

    if scheduled > 0:
        logger.info(
            'Steal detected: %s stole from %s, %d NPC(s) reacting',
            getattr(source, 'entity_uid', source),
            getattr(target, 'entity_uid', target),
            scheduled,
        )
        pc_message = pc_steal_detected_message(
            source,
            container_info,
            items_list,
            witness_names,
        )
        _emit_pc_steal_warning(game, username, pc_message)

    return detections


def collect_detections_for_tests(action_result) -> List[Dict[str, Any]]:
    """Utility for testing: extract detection results from a processed action."""
    return action_result if isinstance(action_result, list) else []
