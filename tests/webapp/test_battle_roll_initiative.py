"""Roll Initiative (/battle) helpers: group validation, map selection, DM fallback."""
from types import SimpleNamespace

import pytest

from natural20.web.web_controller import WebController
from webapp.blueprints.battle import _battle_maps_for, _validate_opposing_groups
from webapp.game_management_components import GameControllerRegistry


class _Ent(SimpleNamespace):
    def dead(self):
        return False

    def __hash__(self):
        return hash(self.entity_uid)


class _UnstartedBattle:
    """Mimics a Battle after add() but before start(): empty combat_order."""

    def __init__(self, groups):
        self.combat_order = []
        self.entities = {ent: {'group': group} for ent, group in groups}

    def opponents_of(self, entity):
        mine = self.entities[entity]['group']
        return [e for e, s in self.entities.items() if s['group'] != mine]


def test_validate_opposing_groups_uses_roster_before_start():
    pc, foe = _Ent(entity_uid='pc'), _Ent(entity_uid='foe')
    _validate_opposing_groups(_UnstartedBattle([(pc, 'a'), (foe, 'b')]))


def test_validate_opposing_groups_rejects_single_group():
    pc1, pc2 = _Ent(entity_uid='pc1'), _Ent(entity_uid='pc2')
    with pytest.raises(ValueError):
        _validate_opposing_groups(_UnstartedBattle([(pc1, 'a'), (pc2, 'a')]))


def _map(name, entities):
    by_uid = {e.entity_uid: e for e in entities}
    return SimpleNamespace(
        name=name,
        entities={e: None for e in entities},
        entity_by_uid=by_uid.get,
    )


def test_battle_maps_for_uses_combatant_maps_not_default():
    pc = _Ent(entity_uid='pc')
    start_map = _map('start', [])
    lair = _map('lair', [pc])
    game = SimpleNamespace(
        maps={'start': start_map, 'lair': lair},
        get_current_battle_map=lambda: start_map,
    )
    assert _battle_maps_for(game, [pc]) == [lair]


def test_battle_maps_for_falls_back_to_default_map():
    start_map = _map('start', [])
    game = SimpleNamespace(maps={'start': start_map}, get_current_battle_map=lambda: start_map)
    assert _battle_maps_for(game, [_Ent(entity_uid='ghost')]) == [start_map]


def _registry(web_controllers=None):
    manager = SimpleNamespace(
        game_session=None,
        web_controllers=web_controllers if web_controllers is not None else {},
    )
    return GameControllerRegistry(manager), manager


def test_manual_controller_without_users_goes_to_dm():
    registry, manager = _registry()
    npc = _Ent(entity_uid='npc')
    controller = registry.manual_controller_for_entity(npc)
    assert 'dm' in controller.get_users()
    assert manager.web_controllers[npc] is controller


def test_manual_controller_keeps_assigned_player():
    npc = _Ent(entity_uid='pc')
    existing = WebController(None, None)
    existing.add_user('alice')
    registry, _manager = _registry({npc: existing})
    controller = registry.manual_controller_for_entity(npc)
    assert controller is existing
    assert controller.get_users() == {None, 'alice'}


def test_manual_controller_with_only_placeholder_user_gets_dm():
    npc = _Ent(entity_uid='pc')
    existing = WebController(None, None)
    registry, _manager = _registry({npc: existing})
    assert 'dm' in registry.manual_controller_for_entity(npc).get_users()
