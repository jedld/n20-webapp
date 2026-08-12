"""Tests for per-NPC conversation mode (LLM vs DM manual)."""

import os
import sys
from unittest.mock import Mock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from natural20.player_character import PlayerCharacter
from webapp.npc_conversation_mode import (
    get_npc_conversation_mode,
    is_npc_manual_conversation,
    npc_conversation_mode_payload,
    partner_candidates_for_npc,
    set_npc_conversation_mode,
)


def _session():
    session = Mock()
    session.session_state = {}
    return session


def test_default_mode_is_llm():
    session = _session()
    assert get_npc_conversation_mode(session, 'npc-1') == 'llm'
    assert is_npc_manual_conversation(session, Mock(entity_uid='npc-1')) is False


def test_set_manual_and_clear_on_llm():
    session = _session()
    assert set_npc_conversation_mode(session, 'npc-1', 'manual') == 'manual'
    assert session.session_state['npc_conversation_modes'] == {'npc-1': 'manual'}
    assert is_npc_manual_conversation(session, Mock(entity_uid='npc-1')) is True

    assert set_npc_conversation_mode(session, 'npc-1', 'llm') == 'llm'
    assert 'npc-1' not in session.session_state['npc_conversation_modes']
    assert is_npc_manual_conversation(session, Mock(entity_uid='npc-1')) is False


def test_partner_candidates_include_map_pcs():
    npc = Mock(entity_uid='npc-1', conversation_buffer=[])
    pc = Mock(spec=PlayerCharacter)
    pc.entity_uid = 'pc-1'
    pc.label = Mock(return_value='Aldric')

    battle_map = Mock()
    battle_map.entities = [npc, pc]
    current_game = Mock()
    current_game.get_map_for_entity.return_value = battle_map

    candidates = partner_candidates_for_npc(current_game, npc)
    assert candidates == [{'id': 'pc-1', 'name': 'Aldric'}]


def test_npc_conversation_mode_payload_for_dialog_npc():
    session = _session()
    set_npc_conversation_mode(session, 'npc-1', 'manual')
    npc = Mock(entity_uid='npc-1', dialog=True, conversation_buffer=[])
    npc.label = Mock(return_value='Mara')
    current_game = Mock()
    current_game.get_map_for_entity.return_value = None

    payload = npc_conversation_mode_payload(current_game, session, npc)
    assert payload['conversation_mode'] == 'manual'
    assert payload['dialog'] is True
    assert payload['partner_candidates'] == []
