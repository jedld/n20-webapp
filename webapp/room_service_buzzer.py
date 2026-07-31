"""Dispatch guest-room buzzers to bar staff (Pip) for room service."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def handle_room_service_buzz(event) -> None:
    from webapp.blueprints.helpers.runtime_state import (
        get_current_game,
        get_entity_rag_handler,
        get_socketio,
    )

    current_game = get_current_game()
    if current_game is None:
        return

    notify_uid = str(event.get('notify_npc') or 'pip_barmaid').strip()
    staff = current_game.game_session.entity_by_uid(notify_uid)
    if staff is None:
        logger.debug('room_service_buzz: staff npc %s not found', notify_uid)
        return

    guest = event.get('source')
    room_label = event.get('room_label') or 'a guest room'
    room_landmark = event.get('room_landmark')
    staff_message = event.get('message') or f'{room_label} is ringing for room service.'

    try:
        current_game.schedule_short_term_goal(
            staff,
            f'Room service buzz from {room_label} — go answer the guest.',
            speaker=guest,
        )
    except Exception as exc:
        logger.debug('room_service_buzz: goal schedule failed: %s', exc)

    rag_handler = get_entity_rag_handler()
    if rag_handler is not None and room_landmark:
        try:
            target_info = rag_handler.parse_move_target_info(
                staff,
                {'target': room_landmark},
                speaker=guest,
            )
            if target_info:
                current_game.schedule_npc_move_to(
                    staff,
                    target_info,
                    distance_ft=5,
                    speaker=guest,
                )
                rag_handler._trigger_immediate_npc_movement(staff)
        except Exception as exc:
            logger.debug('room_service_buzz: movement schedule failed: %s', exc)

    socketio = get_socketio()
    if socketio is not None:
        try:
            from webapp.blueprints.helpers.contextual_sound_ui import emit_contextual_sound_payload
            emit_contextual_sound_payload(socketio, {
                'message': staff_message,
                'position': event.get('position'),
                'source': staff,
                'duration_ms': 4000,
                'label': 'Room service',
            })
        except Exception:
            pass


def register_room_service_listeners(event_manager) -> None:
    event_manager.register_event_listener('room_service_buzz', handle_room_service_buzz)
