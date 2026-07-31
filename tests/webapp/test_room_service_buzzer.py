"""Webapp dispatch for guest-room service buzzers."""

from unittest.mock import MagicMock, patch

from webapp.room_service_buzzer import handle_room_service_buzz


def test_handle_room_service_buzz_schedules_pip_movement():
    pip = MagicMock()
    pip.entity_uid = 'pip_barmaid'
    guest = MagicMock()
    guest.entity_uid = 'pc-1'
    guest.label.return_value = 'Aldric'

    current_game = MagicMock()
    session = MagicMock()
    session.entity_by_uid.return_value = pip
    current_game.game_session = session

    target_info = {'kind': 'annotation', 'target_spec': 'landmark_standard_room_1'}
    rag_handler = MagicMock()
    rag_handler.parse_move_target_info.return_value = target_info

    with patch('webapp.blueprints.helpers.runtime_state.get_current_game', return_value=current_game), \
         patch('webapp.blueprints.helpers.runtime_state.get_entity_rag_handler', return_value=rag_handler), \
         patch('webapp.blueprints.helpers.runtime_state.get_socketio', return_value=MagicMock()), \
         patch('webapp.blueprints.helpers.contextual_sound_ui.emit_contextual_sound_payload') as emit_sound:
        handle_room_service_buzz({
            'notify_npc': 'pip_barmaid',
            'room_label': 'Standard Room 1',
            'room_landmark': 'landmark_standard_room_1',
            'source': guest,
            'message': 'Buzz from room 1',
            'position': (7, 1),
        })

    session.entity_by_uid.assert_called_with('pip_barmaid')
    current_game.schedule_short_term_goal.assert_called_once()
    current_game.schedule_npc_move_to.assert_called_once_with(
        pip,
        target_info,
        distance_ft=5,
        speaker=guest,
    )
    rag_handler._trigger_immediate_npc_movement.assert_called_once_with(pip)
    emit_sound.assert_called_once()
