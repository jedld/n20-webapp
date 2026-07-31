"""Tests for DM speak-as-NPC conversation flow."""

import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.conversation_service import ConversationService


def _build_service(*, speaker, listener):
    service = ConversationService.__new__(ConversationService)
    service.logger = Mock()
    service.game_session = Mock()
    service.game_session.session_state = {}

    current_game = Mock()
    current_game.get_entity_by_uid.return_value = speaker
    current_game.get_current_battle.return_value = None
    current_game.increment_game_time = Mock()
    service._current_game_getter = lambda: current_game

    service._normalize_username = Mock(return_value='dm_user')
    service._requester_is_dm = Mock(return_value=True)
    service.effective_talk_volume = Mock(return_value='normal')
    service.resolve_conversation_targets = Mock(return_value=([listener], []))
    service.conversation_audience_usernames = Mock(return_value={'dm_user'})
    service.conversation_visible_whisper_usernames = Mock(return_value=set())
    service.emit_conversation_to_usernames = Mock(return_value={'dm_user': {'message': 'Hello there.'}})
    service._persist_conversation_line = Mock()
    service._append_http_reply_for_requester = Mock()
    service.select_conversation_responders = Mock(return_value=[listener])
    service._run_single_npc_reply_turn = Mock(return_value=None)
    service.select_npc_chain_responders = Mock(return_value=[])
    service.message_requests_npc_relay = Mock(return_value=False)
    service.message_summons_npc = Mock(return_value=False)
    service.message_invites_listener_approach = Mock(return_value=False)
    service._receiver_was_addressed = Mock(return_value=False)
    service.maybe_open_player_gift_transfer = Mock()
    llm_handler = Mock()
    service._llm_conversation_handler_getter = lambda: llm_handler
    rag_handler = Mock()
    rag_handler.escort_navigation_inject_note = Mock(return_value=None)
    service._entity_rag_handler_getter = lambda: rag_handler

    speaker_payload = {'message': 'Hello there.', 'volume': 'normal'}
    service.conversation_payload = Mock(return_value=speaker_payload)
    service._speaker_payload_for_test = speaker_payload
    return service


def test_dm_impersonate_runs_npc_responders_and_tts():
    speaker = Mock()
    speaker.entity_uid = 'mara-1'
    speaker.is_npc = Mock(return_value=True)
    speaker.dialog = True
    speaker.send_conversation = Mock(return_value=[(speaker, 'Hello there.', [speaker])])

    listener = Mock()
    listener.entity_uid = 'pip-1'
    listener.is_npc = Mock(return_value=True)
    listener.dialog = True
    listener.label = Mock(return_value='Pip')

    service = _build_service(speaker=speaker, listener=listener)

    with patch('webapp.conversation_service.parse_player_conversation_input', return_value={'spoken': 'Hello there.', 'actions': []}):
        with patch('webapp.conversation_service.session', {'username': 'dm_user'}):
            payload, status = service.talk_response(
                {
                    'entity_id': 'mara-1',
                    'message': 'Hello there.',
                    'dm_impersonate': True,
                },
                'system prompt',
                requesting_username='dm_user',
            )

    assert status == 200
    assert payload['success'] is True
    assert payload['dm_impersonate'] is True
    assert service._speaker_payload_for_test.get('tts_pending') is True
    service.select_conversation_responders.assert_called_once()
    service._run_single_npc_reply_turn.assert_called_once()
    service._append_http_reply_for_requester.assert_called_once()


def test_manual_mode_skips_npc_llm_replies():
    from natural20.player_character import PlayerCharacter

    speaker = Mock(spec=PlayerCharacter)
    speaker.entity_uid = 'pc-1'
    speaker.is_npc = Mock(return_value=False)
    speaker.send_conversation = Mock(return_value=[(speaker, 'Hello?', [speaker])])

    listener = Mock()
    listener.entity_uid = 'mara-1'
    listener.is_npc = Mock(return_value=True)
    listener.dialog = True
    listener.label = Mock(return_value='Mara')

    service = _build_service(speaker=speaker, listener=listener)
    service._requester_is_dm = Mock(return_value=False)
    service._normalize_username = Mock(return_value='player1')
    service.npc_conversation_mode_is_manual = Mock(return_value=True)

    with patch('webapp.conversation_service.parse_player_conversation_input', return_value={'spoken': 'Hello?', 'actions': []}):
        with patch('webapp.conversation_service.session', {'username': 'player1'}):
            payload, status = service.talk_response(
                {'entity_id': 'pc-1', 'message': 'Hello?'},
                'system prompt',
                requesting_username='player1',
            )

    assert status == 200
    assert payload['manual_mode_npcs'] == ['mara-1']
    service._run_single_npc_reply_turn.assert_not_called()


def test_dm_impersonate_requires_dm_privileges():
    speaker = Mock()
    speaker.entity_uid = 'mara-1'
    speaker.is_npc = Mock(return_value=True)

    service = ConversationService.__new__(ConversationService)
    service.logger = Mock()
    current_game = Mock()
    current_game.get_entity_by_uid.return_value = speaker
    service._current_game_getter = lambda: current_game
    service._normalize_username = Mock(return_value='player1')
    service._requester_is_dm = Mock(return_value=False)

    with patch('webapp.conversation_service.parse_player_conversation_input', return_value={'spoken': 'Hi', 'actions': []}):
        payload, status = service.talk_response(
            {'entity_id': 'mara-1', 'message': 'Hi', 'dm_impersonate': True},
            'system prompt',
            requesting_username='player1',
        )

    assert status == 403
    assert 'DM impersonation requires DM privileges' in payload['error']


def test_npc_actor_can_roll_skill_check_via_talk():
    mara = Mock()
    mara.entity_uid = 'mara-1'
    mara.is_npc = Mock(return_value=True)
    mara.persuasion_check = Mock()

    service = ConversationService.__new__(ConversationService)
    service.logger = Mock()
    current_game = Mock()
    current_game.get_entity_by_uid.return_value = mara
    service._current_game_getter = lambda: current_game
    service._normalize_username = Mock(return_value='dm_user')
    service.handle_player_skill_check_button_request = Mock(
        return_value=({'success': True, 'skill_check': {'roll_total': 14}}, 200),
    )

    payload, status = service.talk_response(
        {
            'entity_id': 'mara-1',
            'skill_check': {
                'requester_uid': 'guard-1',
                'skill': 'persuasion',
                'reply_id': 'reply-abc',
            },
        },
        'system prompt',
        requesting_username='dm_user',
    )

    assert status == 200
    assert payload['success'] is True
    service.handle_player_skill_check_button_request.assert_called_once_with(
        mara,
        {
            'requester_uid': 'guard-1',
            'skill': 'persuasion',
            'reply_id': 'reply-abc',
        },
        conversation_system_prompt='system prompt',
        requesting_username='dm_user',
    )


def test_skill_check_rejected_when_actor_lacks_skill_method():
    mara = Mock(spec=[])
    mara.entity_uid = 'mara-1'

    service = ConversationService.__new__(ConversationService)
    service.logger = Mock()
    current_game = Mock()
    current_game.get_entity_by_uid.return_value = mara
    service._current_game_getter = lambda: current_game
    service._normalize_username = Mock(return_value='dm_user')

    payload, status = service.talk_response(
        {
            'entity_id': 'mara-1',
            'skill_check': {'requester_uid': 'guard-1', 'skill': 'persuasion'},
        },
        'system prompt',
        requesting_username='dm_user',
    )

    assert status == 400
    assert payload['error'] == 'Skill check not available for this character'
