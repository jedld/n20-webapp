"""Tests for per-NPC manual vs LLM conversation mode."""

import os
import sys
from unittest.mock import Mock

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.npc_conversation_mode import (
    get_npc_conversation_mode,
    is_npc_manual_conversation,
    set_npc_conversation_mode,
)


def test_manual_mode_round_trip():
    session = Mock()
    session.session_state = {}

    assert get_npc_conversation_mode(session, 'mara-1') == 'llm'
    assert not is_npc_manual_conversation(session, Mock(entity_uid='mara-1'))

    set_npc_conversation_mode(session, 'mara-1', 'manual')
    assert get_npc_conversation_mode(session, 'mara-1') == 'manual'
    assert is_npc_manual_conversation(session, Mock(entity_uid='mara-1'))
    assert session.session_state['npc_conversation_mode'] == {'mara-1': 'manual'}

    set_npc_conversation_mode(session, 'mara-1', 'llm')
    assert get_npc_conversation_mode(session, 'mara-1') == 'llm'
    assert 'npc_conversation_mode' not in session.session_state


def test_invalid_mode_raises():
    session = Mock()
    session.session_state = {}
    with pytest.raises(ValueError):
        set_npc_conversation_mode(session, 'npc-1', 'autopilot')
