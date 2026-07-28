"""Tests for player→NPC gift transfer completion notifications."""

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
from webapp.npc_item_exchange import (
    _PENDING_NPC_GIFT_TRANSFERS,
    register_npc_gift_transfer_session,
)


class _StubNpc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label
        self.conversation_buffer = []

    def label(self):
        return self._label

    def is_npc(self):
        return True


class _StubPc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label

    def label(self):
        return self._label


class _StubGame:
    def __init__(self, entities):
        self._entities = entities

    def get_entity_by_uid(self, uid):
        return self._entities.get(uid)


def _build_service(game, entity_owners=None):
    return ConversationService(
        current_game_getter=lambda: game,
        game_session=None,
        socketio=None,
        entity_rag_handler_getter=lambda: Mock(),
        llm_conversation_handler_getter=lambda: Mock(),
        roles_for_username_getter=lambda: (lambda username: ['player']),
        entity_owners_getter=lambda: (entity_owners or (lambda _entity: [])),
        entities_controlled_by_getter=lambda: (lambda _username: []),
        logins_getter=lambda: [],
        logger=logging.getLogger('test_npc_gift_transfer'),
    )


def setup_function():
    _PENDING_NPC_GIFT_TRANSFERS.clear()


def test_handle_npc_gift_transfer_complete_notifies_npc_llm():
    npc = _StubNpc('pip', 'Pip')
    player = _StubPc('pc-1', 'Aldric')
    game = _StubGame({'pip': npc, 'pc-1': player})
    service = _build_service(game, entity_owners=lambda entity: ['player1'] if entity is player else [])

    session_id = register_npc_gift_transfer_session('pip', 'pc-1')

    with patch.object(service, '_run_single_npc_reply_turn', return_value={'reply_plan': {}}) as npc_turn:
        response, status = service.handle_npc_gift_transfer_complete(
            {
                'transfer_session_id': session_id,
                'cancelled': False,
                'items': {
                    'from': {'items': ['healing_potion'], 'qty': ['1']},
                    'to': {'items': [], 'qty': []},
                },
            },
            conversation_system_prompt='{name} {backstory} {attributes} {alignment} {languages} {gear} {appearance}',
            requesting_username='player1',
        )

    assert status == 200
    assert response['success'] is True
    npc_turn.assert_called_once()
    inject_notes = npc_turn.call_args.kwargs['inject_system_messages']
    assert 'healing potion' in inject_notes[0].lower()
    assert 'aldric' in inject_notes[0].lower()


def test_handle_npc_gift_transfer_complete_rejects_unknown_session():
    game = _StubGame({})
    service = _build_service(game)

    response, status = service.handle_npc_gift_transfer_complete(
        {'transfer_session_id': 'missing'},
        conversation_system_prompt='',
        requesting_username='player1',
    )

    assert status == 404


def test_handle_npc_gift_transfer_complete_requires_player_owner():
    npc = _StubNpc('pip', 'Pip')
    player = _StubPc('pc-1', 'Aldric')
    game = _StubGame({'pip': npc, 'pc-1': player})
    service = _build_service(game, entity_owners=lambda entity: ['other'] if entity is player else [])
    session_id = register_npc_gift_transfer_session('pip', 'pc-1')

    response, status = service.handle_npc_gift_transfer_complete(
        {'transfer_session_id': session_id, 'cancelled': True},
        conversation_system_prompt='',
        requesting_username='player1',
    )

    assert status == 403
