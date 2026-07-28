"""Tests for out-of-combat NPC hit reaction helpers."""

import importlib
import os
import sys
from unittest.mock import Mock, patch

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

importlib.import_module('webapp.conversation_service')

from natural20.utils.npc_hit_reaction import (
    describe_attacker,
    describe_damage_type,
    describe_hit_kind,
    is_damage_hit_item,
    out_of_combat_hit_reaction_note,
)
from webapp.conversation_service import ConversationService
from webapp.npc_damage_reaction import process_out_of_combat_damage_reactions


class _StubNpc:
    def __init__(self, uid, label, *, dead=False, conscious=True):
        self.entity_uid = uid
        self._label = label
        self._dead = dead
        self._conscious = conscious
        self.conversation_buffer = []

    def label(self):
        return self._label

    def npc(self):
        return True

    def dead(self):
        return self._dead

    def conscious(self):
        return self._conscious

    def languages(self):
        return ['common']

    def backstory(self):
        return 'A test npc.'

    def ability_scores(self):
        return {'str': 10}

    def alignment(self):
        return 'neutral'


class _StubPc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label

    def label(self):
        return self._label


def test_describe_hit_kind_for_spell_and_attack():
    assert 'firebolt' in describe_hit_kind({
        'type': 'spell_damage',
        'attack_name': 'firebolt',
        'spell': {'name': 'firebolt'},
    }).lower()
    assert 'longsword' in describe_hit_kind({
        'type': 'damage',
        'attack_name': 'longsword',
    }).lower()


def test_describe_damage_type_normalizes():
    assert describe_damage_type({'damage_type': 'fire'}) == 'fire'
    assert describe_damage_type({}) == 'unspecified'


def test_out_of_combat_hit_reaction_note_includes_context():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    note = out_of_combat_hit_reaction_note(
        npc,
        attacker,
        {
            'type': 'spell_damage',
            'attack_name': 'firebolt',
            'spell': {'name': 'firebolt'},
            'damage_type': 'fire',
            'damage': 7,
        },
        known_attacker=True,
    )
    assert 'firebolt' in note.lower()
    assert 'fire' in note.lower()
    assert '7 damage' in note
    assert 'Aldric' in note


def test_describe_attacker_hidden_when_not_visible():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    battle_map = Mock()
    battle_map.can_see.return_value = False
    assert 'cannot see' in describe_attacker(npc, attacker, battle_map=battle_map)


def test_is_damage_hit_item():
    assert is_damage_hit_item({'type': 'damage', 'target': object()})
    assert is_damage_hit_item({'type': 'spell_damage', 'target': object()})
    assert not is_damage_hit_item({'type': 'miss', 'target': object()})


def _build_service():
    current_game = Mock()
    current_game.entity_owners.return_value = ['aldric']
    llm_handler = Mock()
    service = ConversationService(
        current_game_getter=lambda: current_game,
        game_session=Mock(game_properties={}),
        socketio=Mock(),
        entity_rag_handler_getter=Mock(return_value=Mock()),
        llm_conversation_handler_getter=lambda: llm_handler,
        roles_for_username_getter=Mock(return_value=[]),
        entity_owners_getter=lambda: (lambda entity: ['aldric']),
        entities_controlled_by_getter=Mock(return_value=[]),
        logins_getter=Mock(return_value={}),
        logger=Mock(),
    )
    return service, llm_handler


def test_process_skips_dead_npc():
    npc = _StubNpc('pip', 'Pip', dead=True)
    attacker = _StubPc('aldric', 'Aldric')
    action = Mock()
    action.result = [{
        'type': 'damage',
        'target': npc,
        'source': attacker,
        'attack_name': 'longsword',
        'damage_type': 'slashing',
        'damage': 20,
    }]
    game = Mock()
    service = Mock()
    service.llm_conversation_handler = Mock()

    with patch('webapp.blueprints.helpers.runtime_state.get_conversation_service', return_value=service):
        process_out_of_combat_damage_reactions(game, action)

    service.handle_npc_out_of_combat_hit.assert_not_called()


def test_process_triggers_for_living_npc():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    item = {
        'type': 'damage',
        'target': npc,
        'source': attacker,
        'attack_name': 'longsword',
        'damage_type': 'slashing',
        'damage': 4,
    }
    action = Mock()
    action.result = [item]
    game = Mock()
    game.get_map_for_entity.return_value = Mock()
    service = Mock()
    service.llm_conversation_handler = Mock()

    with patch('webapp.blueprints.helpers.runtime_state.get_conversation_service', return_value=service):
        process_out_of_combat_damage_reactions(game, action)

    service.handle_npc_out_of_combat_hit.assert_called_once_with(
        npc,
        attacker,
        item,
        battle_map=game.get_map_for_entity.return_value,
    )


def test_process_skips_in_combat():
    npc = _StubNpc('pip', 'Pip')
    action = Mock()
    action.result = [{'type': 'damage', 'target': npc, 'source': Mock()}]
    game = Mock()
    service = Mock()
    service.llm_conversation_handler = Mock()

    with patch('webapp.blueprints.helpers.runtime_state.get_conversation_service', return_value=service):
        process_out_of_combat_damage_reactions(game, action, in_combat=True)

    service.handle_npc_out_of_combat_hit.assert_not_called()


def test_handle_npc_out_of_combat_hit_runs_reply_turn():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    item = {
        'type': 'spell_damage',
        'attack_name': 'ray_of_frost',
        'spell': {'name': 'ray_of_frost'},
        'damage_type': 'cold',
        'damage': 5,
    }
    service, _llm_handler = _build_service()
    service._run_single_npc_reply_turn = Mock(return_value={'message': 'Ow!'})

    payload, status = service.handle_npc_out_of_combat_hit(
        npc,
        attacker,
        item,
        conversation_system_prompt='{backstory}',
    )

    assert status == 200
    assert payload['success'] is True
    service._run_single_npc_reply_turn.assert_called_once()
    inject_args = service._run_single_npc_reply_turn.call_args
    assert inject_args[0][0] is npc
    assert inject_args[0][1] is attacker
    assert 'ray of frost' in inject_args[1]['inject_system_messages'][0].lower()
    assert inject_args[1]['npc_initiated'] is True
    assert inject_args[1]['local_chat_only'] is True


def test_run_single_npc_reply_turn_local_chat_only_targets_speaker():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    service, llm_handler = _build_service()
    rag = service.entity_rag_handler
    rag.build_conversation_response_plan.return_value = {
        'skip': False,
        'message': 'Hey! What was that for?',
        'targets': [],
        'volume': 'normal',
        'language': 'common',
        'narrative': [],
    }
    service.expand_reply_targets_from_message = Mock(side_effect=lambda receiver, plan: plan)
    rag.apply_response_plan_directives = Mock()
    rag.apply_conversation_keywords = Mock()
    llm_handler.generate_response.return_value = 'Hey! What was that for?'
    npc.send_conversation = Mock(return_value=[])
    service.conversation_audience_usernames = Mock(return_value={'aldric'})
    service.emit_conversation_to_usernames = Mock(return_value={'aldric': {}})
    service._open_entity_dialog_for_targets = Mock()
    service.build_npc_identity_prompt = Mock(return_value='identity')
    service.conversation_response_prompt = Mock(return_value='rules')

    result = service._run_single_npc_reply_turn(
        npc,
        attacker,
        '{backstory}',
        local_chat_only=True,
        npc_initiated=True,
    )

    assert result is not None
    emit_args = service.emit_conversation_to_usernames.call_args
    payload = emit_args[0][1]
    assert payload.get('local_chat_only') is True
    assert payload.get('targets') == [attacker.entity_uid]
    service._open_entity_dialog_for_targets.assert_not_called()


def test_run_single_npc_reply_turn_opens_dialog_when_npc_speaks():
    npc = _StubNpc('pip', 'Pip')
    attacker = _StubPc('aldric', 'Aldric')
    service, llm_handler = _build_service()
    rag = service.entity_rag_handler
    rag.build_conversation_response_plan.return_value = {
        'skip': False,
        'message': 'Hey! What was that for?',
        'targets': [attacker],
        'volume': 'normal',
        'language': 'common',
        'narrative': [],
    }
    service.expand_reply_targets_from_message = Mock(side_effect=lambda receiver, plan: plan)
    rag.apply_response_plan_directives = Mock()
    rag.apply_conversation_keywords = Mock()
    llm_handler.generate_response.return_value = 'Hey! What was that for?'
    npc.send_conversation = Mock(return_value=[])
    service.conversation_audience_usernames = Mock(return_value={'aldric'})
    service.emit_conversation_to_usernames = Mock(return_value={'aldric'})
    service._open_entity_dialog_for_targets = Mock()
    service.build_npc_identity_prompt = Mock(return_value='identity')
    service.conversation_response_prompt = Mock(return_value='rules')

    result = service._run_single_npc_reply_turn(
        npc,
        attacker,
        '{backstory}',
        open_dialog=True,
        npc_initiated=True,
    )

    assert result is not None
    service._open_entity_dialog_for_targets.assert_called_once_with(npc, [attacker])


def test_handle_npc_out_of_combat_hit_skips_dead_npc():
    npc = _StubNpc('pip', 'Pip', dead=True)
    service, _llm_handler = _build_service()
    service._run_single_npc_reply_turn = Mock()

    payload, status = service.handle_npc_out_of_combat_hit(npc, Mock(), {'type': 'damage'})

    assert status == 200
    assert payload.get('error') == 'npc_dead'
    service._run_single_npc_reply_turn.assert_not_called()
