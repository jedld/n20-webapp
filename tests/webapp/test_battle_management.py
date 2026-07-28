from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webapp.battle_management import (
    battle_admin,
    configure_group_relationship,
    get_battle_snapshot,
    list_campaign_groups,
)


class _Ent:
    def __init__(self, uid, label, *, is_npc=False):
        self.entity_uid = uid
        self._label = label
        self.is_npc = staticmethod(lambda: is_npc)
        self.properties = {}
        self.group = None

    def label(self):
        return self._label


def _battle_with_order():
    a = _Ent('pc1', 'Aldric')
    b = _Ent('npc1', 'Guz', is_npc=True)
    battle = SimpleNamespace(
        combat_order=[a, b],
        current_turn_index=0,
        round=1,
        entities={
            a: {'group': 'a', 'initiative': 12},
            b: {'group': 'b', 'initiative': 8},
        },
        groups={'a': {a}, 'b': {b}},
    )

    def current_turn():
        return battle.combat_order[battle.current_turn_index]

    def reorder_initiative(order):
        uid_map = {ent.entity_uid: ent for ent in battle.combat_order}
        battle.combat_order = [uid_map[uid] for uid in order]

    battle.current_turn = current_turn
    battle.reorder_initiative = reorder_initiative
    return battle, a, b


def test_get_battle_snapshot_includes_initiative():
    battle, a, b = _battle_with_order()
    game = SimpleNamespace(get_current_battle=lambda: battle)
    snapshot = get_battle_snapshot(game)
    assert snapshot['active'] is True
    assert snapshot['turn_order'][0]['entity_uid'] == 'pc1'
    assert snapshot['turn_order'][0]['initiative'] == 12


def test_battle_admin_set_initiative_resorts_order():
    battle, a, b = _battle_with_order()
    game = SimpleNamespace(
        get_current_battle=lambda: battle,
        get_entity_by_uid=lambda uid: {'pc1': a, 'npc1': b}[uid],
        socketio=MagicMock(),
    )

    result = battle_admin(
        game,
        SimpleNamespace(),
        'set_initiative',
        entity_uid='npc1',
        initiative=20,
    )
    assert result['initiative'] == 20
    assert battle.combat_order[0] is b


def test_configure_group_relationship_makes_enemies():
    session = SimpleNamespace(game_properties={'groups': {'a': {}, 'b': {}}})
    result = configure_group_relationship(session, 'a', 'b', 'enemy')
    assert 'b' in session.game_properties['groups']['a']['enemies']
    assert 'a' in session.game_properties['groups']['b']['enemies']
    assert result['relationship'] == 'enemy'


def test_list_campaign_groups():
    session = SimpleNamespace(game_properties={'groups': {'c': {'neutral': ['a', 'b']}}})
    payload = list_campaign_groups(session)
    assert payload['groups']['c']['neutral'] == ['a', 'b']
