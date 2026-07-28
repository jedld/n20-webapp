"""Unit tests for player-side gift offers in conversation."""

import logging
import os
import sys
from unittest.mock import Mock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

import importlib

conversation_service = importlib.import_module('webapp.conversation_service')

from natural20.player_character import PlayerCharacter
from natural20.npc import Npc
from webapp.conversation_service import (
    ConversationService,
    is_player_gift_offer,
    parse_player_gift_offer,
)


def test_recognises_basic_gift_phrasings():
    assert is_player_gift_offer('I give her 1 silver')
    assert is_player_gift_offer('I hand him the letter')
    assert is_player_gift_offer("Here's 5 gp for your trouble")
    assert is_player_gift_offer('*hands over a silver coin*', actions=[])


def test_does_not_match_requests_for_items():
    assert not is_player_gift_offer('Give me a drink')
    assert not is_player_gift_offer('Can you give me directions?')
    assert not is_player_gift_offer('Perception check on Pip')


def test_parse_currency_hint_from_offer():
    assert parse_player_gift_offer('I give her 1 silver') == {'item_hint': 'silver_piece'}
    assert parse_player_gift_offer('I pay 5 gp') == {'item_hint': 'gold_piece'}
    assert parse_player_gift_offer('I hand her the note') == {'item_hint': None}


def test_maybe_open_player_gift_transfer_opens_dialog_for_npc_target():
    rag_handler = Mock()
    rag_handler._accept_gift_from_speaker.return_value = True

    service = ConversationService(
        current_game_getter=lambda: Mock(),
        game_session=Mock(),
        socketio=Mock(),
        entity_rag_handler_getter=lambda: rag_handler,
        llm_conversation_handler_getter=lambda: Mock(),
        roles_for_username_getter=lambda: (lambda username: []),
        entity_owners_getter=lambda: (lambda entity: []),
        entities_controlled_by_getter=lambda: (lambda username: []),
        logins_getter=lambda: [],
        logger=logging.getLogger('test_player_gift_offer'),
    )
    speaker = Mock(spec=PlayerCharacter)
    speaker.entity_uid = 'aldric'
    npc = Mock(spec=Npc)
    npc.entity_uid = 'pip_barmaid'

    opened = service.maybe_open_player_gift_transfer(
        speaker,
        [npc],
        'I give her 1 silver',
    )

    assert opened is True
    rag_handler._accept_gift_from_speaker.assert_called_once_with(
        npc,
        speaker,
        item_hint='silver_piece',
    )
