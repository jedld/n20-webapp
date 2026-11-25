"""Campaign-level spawner library tests (``/dm/library`` + spawner flags).

Adding/deleting NPCs and Objects from the combined Spawner panel edits the
campaign YAML (``npcs/<slug>.yml`` / ``items/objects.yml``); marking a NPC
unique writes an authored ``entity_uid`` so the engine keeps one instance per
map set, and duplicate spawning forces an independent copy.
"""

import os
import shutil
import sys
from unittest.mock import Mock, patch

import pytest
import yaml

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

ENGINE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
template_root = os.path.join(ENGINE_ROOT, 'templates')
os.environ.setdefault('TEMPLATE_DIR', template_root)

FIXTURES = os.path.join(ENGINE_ROOT, 'tests', 'fixtures')

from webapp.app import app  # noqa: E402
from webapp.blueprints.helpers import runtime_state  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.session import Session  # noqa: E402


@pytest.fixture
def world(tmp_path):
    """A real campaign (fixture copy) + map + mock game for library edits."""
    campaign = tmp_path / 'campaign'
    shutil.copytree(FIXTURES, campaign,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    session = Session(root_path=str(campaign), event_manager=EventManager())
    battle_map = Map(session, 'battle_sim.yml')
    session.maps[battle_map.name] = battle_map

    game = Mock()
    game.get_map_for_user.return_value = battle_map
    game.deferred_players = {}
    return session, game, battle_map, campaign


def _dm_client():
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'dm'
    return client


def _patches(session, game):
    socketio = Mock()
    return [
        patch.dict(runtime_state._globals, {
            'current_game': game,
            'game_session': session,
            'socketio': socketio,
        }),
        patch('webapp.blueprints.dm.get_socketio', return_value=socketio),
        patch('webapp.blueprints.dm.user_role', new=lambda: ['dm']),
    ]


def _run(world, fn):
    session, game, battle_map, campaign = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        return fn(client, session, battle_map, campaign)
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------- NPC types

def test_create_npc_from_base_writes_campaign_sheet(world):
    def body(client, session, battle_map, campaign):
        resp = client.post('/dm/library', json={
            'op': 'create_npc', 'name': 'Test Sentry', 'base_type': 'boar',
        })
        assert resp.status_code == 200, resp.get_json()
        sheet = campaign / 'npcs' / 'test_sentry.yml'
        assert sheet.is_file()
        data = yaml.safe_load(sheet.read_text())
        assert data['name'] == 'Test Sentry'
        assert 'entity_uid' not in data  # copy is generic unless asked unique

        listing = client.get('/available_npcs').get_json()['npcs']
        entry = next(n for n in listing if n['id'] == 'test_sentry')
        assert entry['campaign_owned'] is True
        assert entry['unique'] is False

        # Second create with the same name is refused.
        dup = client.post('/dm/library', json={'op': 'create_npc', 'name': 'Test Sentry'})
        assert dup.status_code == 409
        return True
    assert _run(world, body)


def test_create_blank_npc_with_stats(world):
    def body(client, session, battle_map, campaign):
        resp = client.post('/dm/library', json={
            'op': 'create_npc', 'name': 'Test Clockwork',
            'max_hp': 22, 'default_ac': 15, 'unique': True,
        })
        assert resp.status_code == 200, resp.get_json()
        data = yaml.safe_load((campaign / 'npcs' / 'test_clockwork.yml').read_text())
        assert data['max_hp'] == 22
        assert data['default_ac'] == 15
        assert data['entity_uid'] == 'test_clockwork'
        assert data['unique'] is True

        # It is immediately spawnable and marked unique.
        listing = client.get('/available_npcs').get_json()['npcs']
        entry = next(n for n in listing if n['id'] == 'test_clockwork')
        assert entry['unique'] is True
        return True
    assert _run(world, body)


def test_set_unique_then_unmark(world):
    def body(client, session, battle_map, campaign):
        resp = client.post('/dm/library', json={
            'op': 'set_unique_npc', 'npc_type': 'boar', 'unique': True})
        assert resp.status_code == 200, resp.get_json()
        listing = client.get('/available_npcs').get_json()['npcs']
        entry = next(n for n in listing if n['id'] == 'boar')
        assert entry['unique'] is True
        assert entry['entity_uid'] == 'boar'
        assert entry['campaign_owned'] is True
        # Bundled sheet untouched; campaign shadow carries the authored uid.
        assert (campaign / 'npcs' / 'boar.yml').is_file()
        assert 'entity_uid' not in yaml.safe_load(
            (__import__('pathlib').Path(__file__).resolve().parents[3]
             / 'tests' / 'fixtures' / 'npcs' / 'boar.yml').read_text())

        resp = client.post('/dm/library', json={
            'op': 'set_unique_npc', 'npc_type': 'boar', 'unique': False})
        assert resp.status_code == 200, resp.get_json()
        listing = client.get('/available_npcs').get_json()['npcs']
        entry = next(n for n in listing if n['id'] == 'boar')
        assert entry['unique'] is False
        return True
    assert _run(world, body)


def test_duplicate_unique_npc_moves_then_duplicates(world):
    def body(client, session, battle_map, campaign):
        assert client.post('/dm/library', json={
            'op': 'set_unique_npc', 'npc_type': 'boar', 'unique': True}).status_code == 200

        first = client.post('/spawn_npc', json={'npc_type': 'boar', 'x': 1, 'y': 0})
        assert first.status_code == 200, first.get_json()
        assert first.get_json()['entity_uid'] == 'boar'

        # Default behavior on a unique type: move the same token.
        moved = client.post('/spawn_npc', json={'npc_type': 'boar', 'x': 3, 'y': 5})
        assert moved.status_code == 200, moved.get_json()
        assert moved.get_json()['moved'] is True
        assert moved.get_json()['entity_uid'] == 'boar'

        # duplicate=true forces an independent copy at an auto-chosen square.
        dup = client.post('/spawn_npc', json={'npc_type': 'boar', 'duplicate': True})
        assert dup.status_code == 200, dup.get_json()
        payload = dup.get_json()
        assert payload['duplicate'] is True
        dup_uid = payload['entity_uid']
        assert dup_uid != 'boar'
        assert session.entity_by_uid(dup_uid) is not None
        on_map = [str(getattr(e, 'entity_uid', '')) for e in battle_map.entities]
        assert 'boar' in on_map and dup_uid in on_map
        return True
    assert _run(world, body)


def test_delete_npc_rules(world):
    def body(client, session, battle_map, campaign):
        # Bundled types cannot be deleted (acolyte ships in templates/ only).
        resp = client.post('/dm/library', json={'op': 'delete_npc', 'npc_type': 'acolyte'})
        assert resp.status_code == 400
        assert 'bundled' in resp.get_json()['error'].lower()

        # Campaign-authored type deletes fine.
        assert client.post('/dm/library', json={
            'op': 'create_npc', 'name': 'Test Doomed'}).status_code == 200
        resp = client.post('/dm/library', json={'op': 'delete_npc', 'npc_type': 'test_doomed'})
        assert resp.status_code == 200, resp.get_json()
        assert not (campaign / 'npcs' / 'test_doomed.yml').exists()
        listing = client.get('/available_npcs').get_json()['npcs']
        assert not any(n['id'] == 'test_doomed' for n in listing)

        # A campaign copy shadowing a bundled type: deleting reveals the base.
        assert client.post('/dm/library', json={
            'op': 'set_unique_npc', 'npc_type': 'owl', 'unique': True}).status_code == 200
        resp = client.post('/dm/library', json={'op': 'delete_npc', 'npc_type': 'owl'})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['revealed_base'] is True

        # Placed campaign NPCs must be removed from maps first.
        assert client.post('/dm/library', json={
            'op': 'create_npc', 'name': 'Test Guard', 'base_type': 'human_guard'}).status_code == 200
        spawn = client.post('/spawn_npc', json={'npc_type': 'test_guard', 'x': 4, 'y': 3})
        assert spawn.status_code == 200, spawn.get_json()
        resp = client.post('/dm/library', json={'op': 'delete_npc', 'npc_type': 'test_guard'})
        assert resp.status_code == 400
        assert 'placed' in resp.get_json()['error'].lower()
        return True
    assert _run(world, body)


# ------------------------------------------------------------------- objects

def test_create_and_delete_campaign_object(world):
    def body(client, session, battle_map, campaign):
        resp = client.post('/dm/library', json={
            'op': 'create_object', 'name': 'Grandfather Clock',
            'description': 'Tall ticking cabinet', 'category': 'props',
            'passable': False, 'interactable': True,
        })
        assert resp.status_code == 200, resp.get_json()
        raw = yaml.safe_load((campaign / 'items' / 'objects.yml').read_text())
        assert 'grandfather_clock' in raw
        assert raw['grandfather_clock']['interactable'] is True

        listing = client.get('/available_objects').get_json()['objects']
        entry = next(o for o in listing if o['id'] == 'grandfather_clock')
        assert entry['campaign_defined'] is True
        assert entry['category'] == 'props'

        resp = client.post('/dm/library', json={
            'op': 'delete_object', 'object_id': 'grandfather_clock'})
        assert resp.status_code == 200, resp.get_json()
        raw = yaml.safe_load((campaign / 'items' / 'objects.yml').read_text())
        assert 'grandfather_clock' not in raw
        listing = client.get('/available_objects').get_json()['objects']
        assert not any(o['id'] == 'grandfather_clock' for o in listing)
        return True
    assert _run(world, body)


def test_delete_bundled_object_tombstones_it(world):
    def body(client, session, battle_map, campaign):
        listing = client.get('/available_objects').get_json()['objects']
        # difficult_terrain ships only in bundled templates (not the fixture campaign)
        statue = next(o for o in listing if o['id'] == 'difficult_terrain')
        assert statue['campaign_defined'] is False

        resp = client.post('/dm/library', json={
            'op': 'delete_object', 'object_id': 'difficult_terrain'})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['tombstone'] is True

        listing = client.get('/available_objects').get_json()['objects']
        assert not any(o['id'] == 'difficult_terrain' for o in listing)
        # Campaign-defined objects survive, and spawning the bundled one fails.
        assert any(o['id'] == 'barrel' for o in listing)
        spawn = client.post('/spawn_object', json={'object_type': 'difficult_terrain', 'x': 1, 'y': 1})
        assert spawn.status_code == 400
        return True
    assert _run(world, body)


def test_library_requires_dm(world):
    session, game, battle_map, campaign = world
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'player'
    with patch.dict(runtime_state._globals, {
            'current_game': game, 'game_session': session,
            'socketio': Mock()}):
        resp = client.post('/dm/library', json={'op': 'delete_npc', 'npc_type': 'boar'})
    assert resp.status_code == 403
