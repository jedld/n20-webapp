"""Character-sheet attunement: ``POST /attunement`` and the equipment / item card views."""
import os
import sys
from unittest.mock import Mock, patch

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402


@pytest.fixture
def fighter():
    session = Session(root_path='tests/fixtures', event_manager=EventManager())
    pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    pc.properties['attuned'] = []
    pc.add_item('ring_of_protection')
    pc.add_item('cloak_of_protection')
    return pc


@pytest.fixture
def game(fighter):
    battle_map = Mock()
    battle_map.entity_by_uid.return_value = fighter
    game = Mock()
    game.get_map_for_user.return_value = battle_map
    game.get_current_battle.return_value = None
    return game


def _client(role):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'dm' if 'dm' in role else 'player1'
    return client


@pytest.fixture
def as_role(game):
    patches = []

    def _apply(role):
        patches.extend([
            patch('webapp.blueprints.dm.get_current_game', return_value=game),
            patch('webapp.blueprints.dm.get_socketio', return_value=Mock()),
            patch('webapp.blueprints.dm.user_role', new=lambda: list(role)),
            patch('webapp.blueprints.dm.controller_of', new=lambda uid, user: True),
        ])
        for p in patches:
            p.start()
        return _client(role)

    yield _apply
    for p in patches:
        p.stop()


def test_player_attunement_is_queued_for_the_next_rest(fighter, as_role):
    client = as_role([])
    response = client.post('/attunement', data={'id': fighter.entity_uid, 'item_id': 'ring_of_protection'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['result']['status'] == 'pending'
    assert body['pending'] == [{'item': 'ring_of_protection', 'mode': 'attune'}]
    assert fighter.properties['attuned'] == []

    response = client.post('/attunement', data={
        'id': fighter.entity_uid, 'item_id': 'ring_of_protection', 'op': 'cancel'})
    assert response.status_code == 200
    assert 'attunement_pending' not in fighter.properties


def test_player_cannot_force(fighter, as_role):
    client = as_role([])
    response = client.post('/attunement', data={
        'id': fighter.entity_uid, 'item_id': 'ring_of_protection', 'force': '1'})
    assert response.status_code == 403


def test_dm_force_attunes_immediately(fighter, as_role, game):
    client = as_role(['dm'])
    response = client.post('/attunement', json={
        'id': fighter.entity_uid, 'item_id': 'ring_of_protection', 'force': True})
    assert response.status_code == 200
    assert response.get_json()['attuned'] == ['ring_of_protection']
    assert fighter.properties['attuned'] == ['ring_of_protection']
    game.recalc_lights_for_user.assert_called_once()


def test_rule_violations_return_400(fighter, as_role):
    client = as_role([])
    response = client.post('/attunement', data={'id': fighter.entity_uid, 'item_id': 'amulet_of_health'})
    assert response.status_code == 400
    assert 'holding or carrying' in response.get_json()['error']
    response = client.post('/attunement', data={
        'id': fighter.entity_uid, 'item_id': 'ring_of_protection', 'op': 'end'})
    assert response.status_code == 400


def test_equipment_view_shows_attunement_panel(fighter, as_role):
    client = as_role([])
    client.post('/attunement', data={'id': fighter.entity_uid, 'item_id': 'ring_of_protection'})
    html = client.get('/equipment', query_string={'id': fighter.entity_uid}).get_data(as_text=True)
    assert 'attunement-panel' in html
    assert '0 / 3' in html
    assert 'Attuning at next rest' in html
    assert 'data-attune-op="attune"' in html


def test_item_card_shows_attunement_controls(fighter, as_role):
    client = as_role(['dm'])
    html = client.get('/item_card', query_string={
        'id': fighter.entity_uid, 'item': 'cloak_of_protection'}).get_data(as_text=True)
    assert 'Not attuned' in html
    assert 'data-attune-op="attune"' in html
    assert 'data-force="1"' in html
