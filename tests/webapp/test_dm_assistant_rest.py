from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webapp.game_context import GameContextProvider


class _Pc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label

    def label(self):
        return self._label

    def class_descriptor(self):
        return 'Fighter 1'

    def hp(self):
        return 10

    def max_hp(self):
        return 10

    def armor_class(self):
        return 16

    def inventory_items(self, _session):
        return []

    def equipped_items(self):
        return {}

    @property
    def attributes(self):
        return {}


def _provider_with_maps(maps, *, player_character_type=_Pc):
    def _all_entities():
        for battle_map in maps.values():
            for ent in battle_map.entities:
                yield ent

    game = SimpleNamespace(
        maps=maps,
        get_entity_by_uid=lambda uid: next(
            (ent for ent in _all_entities() if ent.entity_uid == uid),
            None,
        ),
        get_map_for_entity=lambda ent: next(
            (battle_map for battle_map in maps.values() for candidate in battle_map.entities if candidate is ent),
            None,
        ),
        get_current_battle=lambda: None,
        get_current_battle_map=lambda: list(maps.values())[0],
        get_map_for_user=lambda _username: list(maps.values())[0],
        socketio=MagicMock(),
    )
    provider = GameContextProvider(SimpleNamespace(game_time=0), game)
    return provider


@pytest.fixture(autouse=True)
def _player_character_type(monkeypatch):
    monkeypatch.setattr('webapp.game_context.PlayerCharacter', _Pc)


def test_get_player_characters_searches_all_maps():
    pc_market = _Pc('aldric', 'Aldric')
    other_map = SimpleNamespace(entities=[_Pc('unused', 'Unused')])
    market_map = SimpleNamespace(entities=[pc_market], name='town_market')
    provider = _provider_with_maps({'town_market': market_map, 'index': other_map})

    pcs = provider.get_player_characters()
    assert len(pcs) == 2
    assert any(row['entity_uid'] == 'aldric' and row['map'] == 'town_market' for row in pcs)


def test_find_entity_by_ref_partial_name():
    pc = _Pc('aldric_fighter', 'Aldric')
    battle_map = SimpleNamespace(entities=[pc], name='town_market')
    provider = _provider_with_maps({'town_market': battle_map})

    entity, _battle_map, error = provider._find_entity_by_ref('Aldric')
    assert error is None
    assert entity is pc


def test_run_rest_delegates_to_execute_entity_rest(monkeypatch):
    pc = _Pc('aldric', 'Aldric')
    battle_map = SimpleNamespace(entities=[pc], name='town_market')
    provider = _provider_with_maps({'town_market': battle_map})

    called = {}

    def fake_execute(game, entity, rest_type, **kwargs):
        called['entity'] = entity
        called['rest_type'] = rest_type
        called['kwargs'] = kwargs
        return {'success': True, 'entity_uid': 'aldric', 'type': rest_type}

    monkeypatch.setattr('webapp.game_context.execute_entity_rest', fake_execute)
    result = provider.run_rest('Aldric', 'long', force=True)
    assert result['success'] is True
    assert called['entity'] is pc
    assert called['rest_type'] == 'long'
    assert called['kwargs']['force'] is True


def test_run_npc_long_rest_simulation_schedules(monkeypatch):
    provider = _provider_with_maps({'town_market': SimpleNamespace(entities=[], name='town_market')})
    provider.current_game.get_current_battle = MagicMock(return_value=None)

    scheduled = {'called': False}

    def fake_schedule(game, **kwargs):
        scheduled['called'] = True
        scheduled['kwargs'] = kwargs
        return True

    monkeypatch.setattr(
        'webapp.long_rest_npc_simulation.npc_long_rest_simulation_running',
        lambda: False,
    )
    monkeypatch.setattr(
        'webapp.long_rest_npc_simulation.schedule_long_rest_npc_simulation',
        fake_schedule,
    )
    monkeypatch.setattr(
        'webapp.long_rest_npc_simulation.load_long_rest_sim_config',
        lambda _session: {'enabled': False, 'loops_per_npc': 3},
    )

    result = provider.run_npc_long_rest_simulation(force=True)
    assert result['success'] is True
    assert scheduled['called'] is True
    assert scheduled['kwargs']['force'] is True
