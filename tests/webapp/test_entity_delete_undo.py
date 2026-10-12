"""DM Delete-key / Ctrl+Z: entity deletion with in-memory undo support.

Covers ``POST /delete_entity`` (now capturing undo state before removal)
and the new ``POST /undo_delete_entity`` (restoring position, battle
membership, initiative, and the DM's POV).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from flask import Flask

from webapp.blueprints import dm as dm_mod


class FakeEntity:
    def __init__(self, uid, name=None, group='b'):
        self.entity_uid = uid
        self.name = name or uid
        self.group = group

    def label(self):
        return self.name or self.entity_uid


class FakeMap:
    def __init__(self, name, width=20, height=12):
        self.name = name
        self.size = (width, height)
        self.entities = {}

    def __iter__(self):
        return iter(self.entities)

    def position_of(self, entity):
        return self.entities[entity]

    def remove(self, entity):
        self.entities.pop(entity, None)

    def add(self, entity, x, y, group=None, controller=None):
        self.entities[entity] = (x, y)

    def entity_at(self, x, y):
        return (x, y) in self.entities.values()

    def entity_by_uid(self, uid):
        for entity in self.entities:
            if entity.entity_uid == uid:
                return entity
        return None


class FakeBattle:
    def __init__(self):
        self.combat_order = []
        self.entities = {}
        self.current_turn_index = 0

    def remove(self, entity, from_map=True):
        self.combat_order.remove(entity)
        self.entities.pop(entity, None)

    def add(self, entity, group, controller=None):
        self.combat_order.append(entity)
        self.entities[entity] = {'initiative': 0}


class FakeGame:
    def __init__(self, maps, *, session_state=None, pov=None, battle=None):
        self.game_session = SimpleNamespace(session_state=session_state or {})
        self.maps = maps
        self._pov = {}
        if pov is not None:
            self._pov['dm'] = pov
        self._battle = battle
        self.game_state_lock = threading.Lock()
        # Mirrors the session EntityRegistry: entities survive map removal.
        self._registry = {e.entity_uid: e for map_obj in maps.values() for e in map_obj.entities}

    def register_entity(self, entity):
        self._registry[entity.entity_uid] = entity

    def get_entity_by_uid(self, uid):
        for map_obj in self.maps.values():
            entity = map_obj.entity_by_uid(uid)
            if entity is not None:
                return entity
        return self._registry.get(uid)

    def get_map_for_user(self, username):
        return next(iter(self.maps.values()))

    def get_map_for_entity(self, entity):
        for map_obj in self.maps.values():
            if entity in map_obj.entities:
                return map_obj
        return None

    def get_current_battle(self):
        return self._battle

    def get_pov_entity_for_user(self, username):
        return self._pov.get(username)

    def set_pov_entity_for_user(self, username, entity):
        self._pov[username] = entity

    def build_combat_controller_for_entity(self, entity):
        return SimpleNamespace(register_handlers_on=lambda ent: None)


def _client(monkeypatch, *, role=('dm',), username='dm', game):
    game = game  # explicit: tests always pass a FakeGame
    monkeypatch.setattr(dm_mod, 'user_role', lambda: list(role))
    monkeypatch.setattr(dm_mod, 'get_current_game', lambda: game)
    monkeypatch.setattr(
        dm_mod, 'get_logger',
        lambda: SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
    )
    monkeypatch.setattr(dm_mod, 'get_game_session', lambda: game.game_session)
    monkeypatch.setattr(dm_mod, 'get_socketio', lambda: SimpleNamespace(emit=lambda *a, **k: None))
    # Keep the battle rejoin path deterministic.
    monkeypatch.setattr(
        dm_mod, 'GenericController',
        lambda session: SimpleNamespace(register_handlers_on=lambda ent: None),
    )
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(dm_mod.dm_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        if username:
            sess['username'] = username
    return client


def test_delete_entity_captures_undo_state(monkeypatch):
    goblin = FakeEntity('goblin-1', 'Goblin', group='b')
    battle_map = FakeMap('cave')
    battle_map.entities[goblin] = (3, 4)
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, game=game)

    resp = client.post('/delete_entity', json={'entity_uid': 'goblin-1'})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'
    assert goblin not in battle_map.entities

    stack = game.game_session.session_state['entity_delete_undo']['dm']
    assert len(stack) == 1
    entry = stack[0]
    assert entry['entity_uid'] == 'goblin-1'
    assert (entry['x'], entry['y']) == (3, 4)
    assert entry['was_in_battle'] is False
    assert entry['pov_username'] is None


def test_delete_entity_records_pov(monkeypatch):
    goblin = FakeEntity('goblin-1', 'Goblin')
    battle_map = FakeMap('cave')
    battle_map.entities[goblin] = (1, 1)
    game = FakeGame({'cave': battle_map}, pov=goblin)
    client = _client(monkeypatch, game=game)

    resp = client.post('/delete_entity', json={'entity_uid': 'goblin-1'})
    assert resp.status_code == 200
    entry = game.game_session.session_state['entity_delete_undo']['dm'][0]
    assert entry['pov_username'] == 'dm'


def test_undo_delete_entity_restores_position_and_pov(monkeypatch):
    goblin = FakeEntity('goblin-1', 'Goblin')
    battle_map = FakeMap('cave')
    battle_map.entities[goblin] = (5, 6)
    game = FakeGame({'cave': battle_map}, pov=goblin)
    client = _client(monkeypatch, game=game)

    assert client.post('/delete_entity', json={'entity_uid': 'goblin-1'}).status_code == 200

    resp = client.post('/undo_delete_entity', json={'entity_uid': 'goblin-1'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'ok'
    assert (body['x'], body['y']) == (5, 6)
    assert body['pov_restored'] is True
    assert goblin in battle_map.entities and battle_map.entities[goblin] == (5, 6)
    assert game.get_pov_entity_for_user('dm') is goblin
    # Stack consumed.
    assert game.game_session.session_state['entity_delete_undo']['dm'] == []


def test_undo_delete_entity_empty_stack(monkeypatch):
    battle_map = FakeMap('cave')
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, game=game)

    resp = client.post('/undo_delete_entity', json={})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'empty'


def test_undo_delete_entity_shifts_to_free_square_when_occupied(monkeypatch):
    goblin = FakeEntity('goblin-1', 'Goblin')
    blocker = FakeEntity('blocker-1', 'Blocker')
    battle_map = FakeMap('cave', width=4, height=3)
    battle_map.entities[goblin] = (2, 1)
    battle_map.entities[blocker] = (1, 1)
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, game=game)

    assert client.post('/delete_entity', json={'entity_uid': 'goblin-1'}).status_code == 200
    battle_map.entities[blocker] = (2, 1)  # now blocks the original spot

    resp = client.post('/undo_delete_entity', json={'entity_uid': 'goblin-1'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'ok'
    assert goblin in battle_map.entities
    # Original square (2,1) is taken, so the entity must be nearby, not on it.
    restored = battle_map.entities[goblin]
    assert restored != (2, 1)
    assert abs(restored[0] - 2) <= 2 and abs(restored[1] - 1) <= 2


def test_undo_delete_entity_rejoins_active_battle(monkeypatch):
    goblin = FakeEntity('goblin-1', 'Goblin')
    battle_map = FakeMap('cave')
    battle_map.entities[goblin] = (3, 3)
    battle = FakeBattle()
    battle.entities[goblin] = {'initiative': 12}
    battle.combat_order.append(goblin)
    game = FakeGame({'cave': battle_map}, battle=battle)
    client = _client(monkeypatch, game=game)

    assert client.post('/delete_entity', json={'entity_uid': 'goblin-1'}).status_code == 200
    assert goblin not in battle.combat_order
    entry = game.game_session.session_state['entity_delete_undo']['dm'][0]
    assert entry['was_in_battle'] is True
    assert entry['initiative'] == 12

    resp = client.post('/undo_delete_entity', json={'entity_uid': 'goblin-1'})
    assert resp.status_code == 200
    assert goblin in battle.combat_order
    assert goblin in battle.entities
    assert battle.entities[goblin]['initiative'] == 12
    assert goblin in battle_map.entities and battle_map.entities[goblin] == (3, 3)


def test_undo_delete_entity_forbidden_for_players(monkeypatch):
    battle_map = FakeMap('cave')
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, role=('player',), username='gomerin', game=game)
    resp = client.post('/undo_delete_entity', json={})
    assert resp.status_code == 403


def test_delete_entity_unknown_uid_returns_404(monkeypatch):
    battle_map = FakeMap('cave')
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, game=game)
    resp = client.post('/delete_entity', json={'entity_uid': 'nope'})
    assert resp.status_code == 404


def test_delete_entity_stack_is_capped(monkeypatch):
    """Undo keeps only the most recent 20 deletions per DM."""
    battle_map = FakeMap('cave', width=30, height=10)
    game = FakeGame({'cave': battle_map})
    client = _client(monkeypatch, game=game)
    for i in range(25):
        entity = FakeEntity(f'npc-{i}', f'NPC {i}')
        battle_map.entities[entity] = (i % 30, i % 10)
        resp = client.post('/delete_entity', json={'entity_uid': f'npc-{i}'})
        assert resp.status_code == 200

    stack = game.game_session.session_state['entity_delete_undo']['dm']
    assert len(stack) == 20
    assert stack[-1]['entity_uid'] == 'npc-24'
    assert stack[0]['entity_uid'] == 'npc-5'
