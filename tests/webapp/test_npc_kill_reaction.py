"""Tests for dialog NPC reactions after killing a player character."""

import os
import random
import sys
from unittest.mock import Mock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from natural20.battle import Battle
from natural20.event_manager import EventManager
from natural20.map import Map
from natural20.player_character import PlayerCharacter
from natural20.session import Session
import pytest

from webapp import npc_kill_reaction
from webapp.npc_kill_reaction import handle_died_event, kill_reaction_note


@pytest.fixture(autouse=True)
def _clear_reaction_dedupe():
    npc_kill_reaction._recent_reactions.clear()
    yield
    npc_kill_reaction._recent_reactions.clear()


class _Game:
    def __init__(self, session, battle):
        self.game_session = session
        self.battle = battle
        self.socketio = None

    def get_current_battle(self):
        return self.battle


def _setup():
    random.seed(7000)
    events = []
    em = EventManager()
    em.register_event_listener(['died'], lambda event: events.append(event))
    session = Session(root_path='tests/fixtures', event_manager=em)
    battle_map = Map(session, 'battle_sim_objects')
    battle = Battle(session, battle_map)
    pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    boss = session.npc('goblin')
    boss.dialog = True
    battle.add(pc, 'a', position='spawn_point_1', token='G')
    battle.add(boss, 'b', position='spawn_point_2', token='g')
    session.register_entity(pc)
    session.register_entity(boss)
    return session, battle, pc, boss, events


def _kill(pc, boss, battle):
    item = {'source': boss, 'type': 'damage', 'attack_name': 'scimitar'}
    pc.take_damage(pc.hp() + pc.max_hp(), battle=battle, damage_type='slashing',
                   item=item, critical=True)


def test_kill_schedules_reaction_with_killer_details():
    session, battle, pc, boss, events = _setup()
    _kill(pc, boss, battle)
    service = Mock()
    service.llm_conversation_handler = object()
    assert handle_died_event(events[-1], game=_Game(session, battle), conversation_service=service)
    killer, victim, note = service.handle_npc_kill_reaction.call_args.args
    assert killer is boss and victim is pc
    assert pc.label() in note
    assert 'scimitar' in note
    assert 'critical hit' in note
    assert 'outright' in note
    assert '[NO_RESPONSE]' in note
    assert 'No foes remain standing' in note


def test_repeated_died_event_reacts_once():
    session, battle, pc, boss, events = _setup()
    _kill(pc, boss, battle)
    service = Mock()
    service.llm_conversation_handler = object()
    game = _Game(session, battle)
    assert handle_died_event(events[-1], game=game, conversation_service=service)
    assert not handle_died_event(events[-1], game=game, conversation_service=service)
    assert service.handle_npc_kill_reaction.call_count == 1


def test_non_dialog_or_opted_out_killer_stays_silent():
    session, battle, pc, boss, events = _setup()
    _kill(pc, boss, battle)
    service = Mock()
    service.llm_conversation_handler = object()
    game = _Game(session, battle)
    boss.properties['kill_reactions'] = False
    assert not handle_died_event(events[-1], game=game, conversation_service=service)
    boss.properties.pop('kill_reactions')
    boss.dialog = False
    assert not handle_died_event(events[-1], game=game, conversation_service=service)
    service.handle_npc_kill_reaction.assert_not_called()


def test_campaign_can_disable_kill_reactions():
    session, battle, pc, boss, events = _setup()
    _kill(pc, boss, battle)
    session.game_properties['npc_kill_reactions'] = {'enabled': False}
    service = Mock()
    service.llm_conversation_handler = object()
    assert not handle_died_event(events[-1], game=_Game(session, battle), conversation_service=service)


def test_conversation_service_speaks_aloud_with_note():
    from webapp.conversation_service import ConversationService

    session, battle, pc, boss, events = _setup()
    service = Mock()
    service._npc_system_prompt_template.return_value = 'prompt'
    service.entity_owners.return_value = ['player1']
    service._normalize_username = lambda name: name
    service._run_single_npc_reply_turn.return_value = {'ok': True}

    payload, status = ConversationService.handle_npc_kill_reaction(service, boss, pc, 'You just killed X.')
    assert status == 200 and payload['success']
    service.inject_npc_llm_system_note.assert_called_once_with(boss.entity_uid, 'You just killed X.')
    kwargs = service._run_single_npc_reply_turn.call_args.kwargs
    assert kwargs['inject_system_messages'] == ['You just killed X.']
    assert kwargs['npc_initiated'] is True
    assert not kwargs.get('local_chat_only')


def test_note_lists_surviving_foes():
    session, battle, pc, boss, events = _setup()
    other = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    battle.add(other, 'a', position=[2, 2], token='H')
    _kill(pc, boss, battle)
    note = kill_reaction_note(boss, pc, events[-1]['death_info'], battle)
    assert 'Foes still standing against you' in note
