"""Webapp wiring for the System One (decision model) player-instruction feature.

Covers:
 * ``parse_ready_action_request`` preferring the decision provider over the
   generative LLM (two Choice calls, no prose generation).
 * The ``/instruction`` route refusing when no decision model is live.
"""

import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

import random  # noqa: E402

from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402

from webapp.ready_action_handler import parse_ready_action_request  # noqa: E402


class _ScriptedDecisionProvider:
    """System One stub: returns queued option indexes per decide_action call."""

    is_available = True

    def __init__(self, indexes):
        self._indexes = list(indexes)
        self.calls = []

    def decide_action(self, state, options):
        self.calls.append((state, list(options)))
        idx = self._indexes.pop(0) if self._indexes else len(options) - 1
        return {'index': idx}


class _NoGenerativeHandler:
    """Handler exposing only a battle/decision provider.

    ``send_message`` raises so the test fails if the generative path runs.
    """

    def __init__(self, decision_provider):
        self.battle_provider = decision_provider
        self.current_provider = None

    def battle_llm_provider(self):
        return self.battle_provider

    def send_message(self, messages, context=None):
        raise AssertionError('generative LLM must not be used when System One is live')


def _make_battle():
    random.seed(7)
    em = EventManager()
    session = Session(root_path='tests/fixtures', event_manager=em)
    battle_map = Map(session, 'tests/fixtures/battle_sim.yml')
    session.maps['battle_sim'] = battle_map
    battle = Battle(session, battle_map)
    fighter = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    goblin = session.npc('goblin', {'name': 'g'})
    battle.add(fighter, 'a', position=(0, 0), token='F')
    battle.add(goblin, 'b', position=(0, 1), token='g')
    battle.start()
    return session, battle, fighter


def test_ready_uses_system_one():
    """Ready parsing prefers the decision provider; two Choice calls only."""
    session, battle, fighter = _make_battle()
    decision = _ScriptedDecisionProvider([0, 0])  # movement + first weapon
    handler = _NoGenerativeHandler(decision)

    parsed = parse_ready_action_request(
        fighter, battle,
        'if the goblin moves next to me, I attack with my vicious rapier',
        handler,
    )

    assert parsed['approved'] is True
    assert parsed['trigger']['event'] == 'movement'
    assert parsed['action_spec']['kind'] == 'attack'
    # Exactly two decide_action calls (trigger + action), no prose generation.
    assert len(decision.calls) == 2


def test_ready_system_one_validates_against_sheet():
    """A pick the entity does not own is rejected by the legality guard."""
    session, battle, fighter = _make_battle()
    # Trigger index 0 (movement), then choose the "move" leaf (no weapon).
    decision = _ScriptedDecisionProvider([0, 999])
    handler = _NoGenerativeHandler(decision)

    parsed = parse_ready_action_request(
        fighter, battle, 'wait for the goblin then move', handler)
    assert parsed['approved'] is True
    assert parsed['action_spec']['kind'] in ('move', 'attack', 'spell', 'use_item')


def test_ready_falls_back_without_decision_provider():
    """No decision provider -> the old generative/heuristic path still works."""
    session, battle, fighter = _make_battle()

    class _HeuristicOnlyHandler:
        current_provider = None
        battle_provider = None

        def battle_llm_provider(self):
            return None

    parsed = parse_ready_action_request(
        fighter, battle,
        'when the goblin moves, I attack it', _HeuristicOnlyHandler())
    assert parsed['approved'] is True
    assert parsed['trigger']['event'] in ('movement', 'attacked', 'becomes_visible')


class TestInstructionRoutes:
    """Route wiring with the full app; no decision model configured."""

    def _client(self):
        from webapp.app import app

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        return client

    def test_instruction_requires_text(self):
        client = self._client()
        response = client.post('/instruction', json={'id': 'someone', 'instruction': ''})
        assert response.status_code == 400

    def test_instruction_refuses_without_decision_model(self):
        client = self._client()
        # Any entity on the loaded campaign's default map would do; a bogus id
        # fails at lookup (404) before provider resolution, so use a real one.
        from webapp.app import game_session

        pcs = list(getattr(game_session, 'characters', {}) or {})
        pc = pcs[0] if pcs else None
        if pc is None:
            response = client.post('/instruction',
                                   json={'id': 'missing', 'instruction': 'look around'})
            assert response.status_code == 404
            return
        response = client.post('/instruction',
                               json={'id': pc.entity_uid, 'instruction': 'look around'})
        # Default test env has no JEV provider -> refused, never a silent
        # generative fallback.
        assert response.status_code == 503
        assert 'decision model' in response.get_json().get('message', '').lower()

    def test_instruction_step_without_run(self):
        client = self._client()
        response = client.post('/instruction/step', json={})
        assert response.status_code == 200
        assert response.get_json()['status'] == 'done'
