"""Tests for NPC LLM notifications after item offer acceptance."""

import importlib
import logging
import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

importlib.import_module('webapp.conversation_service')

from webapp.conversation_service import ConversationService


class _StubNpc:
    def __init__(self, uid, label, languages=None):
        self.entity_uid = uid
        self._label = label
        self.conversation_buffer = []
        self._languages = list(languages or ['sheep'])

    def label(self):
        return self._label

    def languages(self):
        return list(self._languages)

    def backstory(self):
        return 'A sheep wizard.'

    def ability_scores(self):
        return {'str': 10}

    def alignment(self):
        return 'neutral_good'


class _StubPc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label
        self.conversation_buffer = []

    def label(self):
        return self._label


def _build_service():
    current_game = Mock()
    current_game.entity_owners.return_value = ['aldric']
    game_session = Mock()
    game_session.game_properties = {
        'conversation_item_offers': {
            'scroll_speak_animals_modified': {
                'item_label': 'modified Speak with Animals scroll',
                'aliases': ['scroll_speak_animals'],
            },
        },
    }
    llm_handler = Mock()
    llm_handler.add_message = Mock()
    llm_handler.conversations = {}
    llm_handler.create_conversation = Mock()
    llm_handler.update_conversation_history = Mock()
    llm_handler.generate_response = Mock(return_value='[NO_RESPONSE]')

    service = ConversationService(
        current_game_getter=lambda: current_game,
        game_session=game_session,
        socketio=Mock(),
        entity_rag_handler_getter=Mock(),
        llm_conversation_handler_getter=lambda: llm_handler,
        roles_for_username_getter=Mock(),
        entity_owners_getter=lambda: (lambda entity: ['aldric']),
        entities_controlled_by_getter=Mock(),
        logins_getter=Mock(return_value=[]),
        logger=logging.getLogger('test_npc_item_offer_notify'),
    )
    return service, llm_handler


def test_handle_npc_item_offer_resolution_injects_system_note_on_decline():
    service, llm_handler = _build_service()
    npc = _StubNpc('finethir', 'Finethir (sheep)')
    pc = _StubPc('aldric', 'Aldric')

    with patch.object(service, '_run_single_npc_reply_turn') as npc_turn:
        response, status = service.handle_npc_item_offer_resolution(
            npc,
            pc,
            'scroll_speak_animals_modified',
            accepted=False,
            conversation_system_prompt='{backstory}',
            proactive_reply=False,
        )

    assert status == 200
    assert response['notified'] is True
    llm_handler.add_message.assert_called_once()
    args = llm_handler.add_message.call_args[0]
    assert args[0] == 'finethir'
    assert args[1] == 'system'
    assert 'declined' in args[2].lower()
    npc_turn.assert_not_called()


def test_handle_npc_item_offer_resolution_skips_spoken_reply_on_simple_accept():
    service, llm_handler = _build_service()
    npc = _StubNpc('finethir', 'Finethir (sheep)')
    pc = _StubPc('aldric', 'Aldric')

    with patch.object(service, '_run_single_npc_reply_turn') as npc_turn:
        response, status = service.handle_npc_item_offer_resolution(
            npc,
            pc,
            'scroll_speak_animals_modified',
            accepted=True,
            conversation_system_prompt='{backstory}',
            requesting_username='aldric',
        )

    assert status == 200
    assert response['notified'] is True
    llm_handler.add_message.assert_called_once()
    npc_turn.assert_not_called()


def test_handle_npc_item_offer_resolution_runs_npc_reply_on_accept():
    service, _llm_handler = _build_service()
    npc = _StubNpc('finethir', 'Finethir (sheep)')
    pc = _StubPc('aldric', 'Aldric')

    with patch.object(service, '_run_single_npc_reply_turn', return_value={'response': 'ok'}) as npc_turn:
        response, status = service.handle_npc_item_offer_resolution(
            npc,
            pc,
            'scroll_speak_animals_modified',
            accepted=True,
            effect_message='Aldric reads the scroll and can understand beasts until game time 120.',
            conversation_system_prompt='{backstory}',
            requesting_username='aldric',
        )

    assert status == 200
    assert response['notified'] is True
    npc_turn.assert_called_once()
    inject_notes = npc_turn.call_args.kwargs['inject_system_messages']
    assert inject_notes
    assert 'understand' in inject_notes[0].lower()


def test_offer_callback_notifies_npc_on_accept():
    from webapp.entity_rag_handler import EntityRAGHandler

    mock_game_session = Mock()
    mock_game_session.game_time = 0
    mock_game_session.load_state.return_value = {}
    mock_game_session.game_properties = {}
    mock_current_game = Mock()
    mock_current_game.get_current_battle.return_value = None
    mock_current_game.entity_owners.return_value = ['aldric']
    mock_current_game.prompt = Mock()
    mock_current_game.socketio = Mock()

    rag = EntityRAGHandler(mock_game_session, mock_current_game)
    actor = Mock(entity_uid='finethir', inventory={'scroll_speak_animals_modified': {'qty': 1}})
    actor.label = Mock(return_value='Finethir')
    target = Mock(entity_uid='aldric', inventory={})
    target.label = Mock(return_value='Aldric')

    plan = {
        'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
    }

    with patch.object(rag, '_notify_item_offer_resolution') as notify:
        rag.apply_response_plan_directives(plan, actor, speaker=target, advance_time=False)
        callback = mock_current_game.prompt.call_args.kwargs['callback']
        callback({'response': 'Yes'})

    notify.assert_called_once()
    assert notify.call_args.kwargs['accepted'] is True
