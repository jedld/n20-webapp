"""Tests for player entity respawn after map reload or missing placement."""

from __future__ import annotations

from types import SimpleNamespace

from webapp.game_management_components import GameEntityRegistry


class FakePlayerCharacter:
    def __init__(self, entity_uid):
        self.entity_uid = entity_uid
        self.name = entity_uid


class FakeMap:
    def __init__(self, name):
        self.name = name
        self.entities = {}
        self.player_spawn_points = []

    def position_of(self, entity):
        return self.entities[entity]

    def remove(self, entity):
        self.entities.pop(entity, None)

    def placeable(self, entity, x, y):
        return (x, y) not in self.entities.values()

    def find_empty_placeable_position(self, entity, x, y):
        return x, y

    def place(self, position, entity):
        self.entities[entity] = tuple(position)

    def entity_by_uid(self, entity_uid):
        for entity in self.entities:
            if getattr(entity, 'entity_uid', None) == entity_uid:
                return entity
        return None

    def allocate_player_spawn_point(self, entity_uid, group=None):
        return None


class FakeManager:
    def __init__(self):
        self.maps = {'town_market': FakeMap('town_market')}
        self.deferred_players = {}
        self.controllers = []
        self.pov_entity_for_user = {}
        self.current_map_for_user = {}
        self.game_session = SimpleNamespace(game_properties={})
        self.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )


def test_defer_entities_on_map_stashes_players_for_reload(monkeypatch):
    monkeypatch.setattr(
        'webapp.game_management_components.PlayerCharacter',
        FakePlayerCharacter,
    )
    manager = FakeManager()
    registry = GameEntityRegistry(manager)
    battle_map = manager.maps['town_market']
    pc = FakePlayerCharacter('aldric')
    battle_map.place((4, 5), pc)

    registry.defer_entities_on_map('town_market')

    assert battle_map.entities == {}
    assert 'aldric' in manager.deferred_players
    assert manager.deferred_players['aldric']['position'] == [4, 5]
    assert manager.deferred_players['aldric']['map_name'] == 'town_market'


def test_respawn_deferred_on_map_replaces_entities(monkeypatch):
    manager = FakeManager()
    registry = GameEntityRegistry(manager)
    pc = FakePlayerCharacter('aldric')
    manager.deferred_players['aldric'] = {
        'entity': pc,
        'map_name': 'town_market',
        'position': [2, 3],
    }

    from webapp.blueprints.helpers import pvp

    monkeypatch.setattr(pvp, 'get_current_game', lambda: manager)

    registry.respawn_deferred_on_map('town_market')

    battle_map = manager.maps['town_market']
    assert pc in battle_map.entities
    assert battle_map.entities[pc] == (2, 3)
    assert 'aldric' not in manager.deferred_players


def test_ensure_user_entities_spawned_rematerializes_missing_assignment(monkeypatch):
    manager = FakeManager()
    registry = GameEntityRegistry(manager)
    manager.controllers = [{'entity_uid': 'aldric', 'controllers': ['aldric']}]
    manager.current_map_for_user['aldric'] = ('town_market', manager.maps['town_market'])

    pc = FakePlayerCharacter('aldric')

    from webapp.blueprints.helpers import pvp, runtime_state

    monkeypatch.setattr(pvp, 'ensure_character_entity_loaded', lambda _uid: pc)

    def _spawn(uid):
        deferred = manager.deferred_players.pop(uid, None)
        if not deferred:
            return manager.maps['town_market'].entity_by_uid(uid)
        entity = deferred['entity']
        pos = deferred['position']
        manager.maps['town_market'].place(pos, entity)
        return entity

    monkeypatch.setattr(pvp, 'spawn_deferred_entity', _spawn)
    monkeypatch.setattr(
        runtime_state,
        'get_index_data',
        lambda: {'player_spawn_map': 'town_market', 'selectable_characters': []},
    )

    spawned = registry.ensure_user_entities_spawned('aldric')

    assert spawned == [pc]
    assert pc in manager.maps['town_market'].entities


def test_ensure_user_entities_spawned_logs_missing_assignment_once(monkeypatch):
    manager = FakeManager()
    warnings = []
    manager.logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: warnings.append(args[0] if args else ''),
        exception=lambda *args, **kwargs: None,
    )
    registry = GameEntityRegistry(manager)
    manager.controllers = [{'entity_uid': 'shorvalu', 'controllers': ['shorvalu']}]

    from webapp.blueprints.helpers import pvp

    monkeypatch.setattr(pvp, 'ensure_character_entity_loaded', lambda _uid: None)
    monkeypatch.setattr(pvp, 'spawn_deferred_entity', lambda _uid: None)

    registry.ensure_user_entities_spawned('shorvalu')
    registry.ensure_user_entities_spawned('shorvalu')
    registry.ensure_user_entities_spawned('shorvalu')

    assert len(warnings) == 1
    assert 'Could not materialize assigned character shorvalu for user shorvalu' in warnings[0]
