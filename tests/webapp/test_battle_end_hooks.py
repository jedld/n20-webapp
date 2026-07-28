"""Tests for campaign battle-end hooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from natural20.player_character import PlayerCharacter

from webapp.blueprints.helpers import battle_end_hooks as hooks


class FakePc(PlayerCharacter):
    def __init__(self, entity_uid):
        self.entity_uid = entity_uid
        self.name = entity_uid
        self.inventory = {}
        self.group = 'a'

    def conscious(self):
        return True

    def dead(self):
        return False

    def npc(self):
        return False


class FakeEnemy:
    def __init__(self, entity_uid, group='b', alive=True):
        self.entity_uid = entity_uid
        self.group = group
        self._alive = alive

    def conscious(self):
        return self._alive

    def dead(self):
        return not self._alive


def test_group_defeated_when_no_conscious_enemies_remain():
    enemy = FakeEnemy('guz', alive=False)
    battle_map = SimpleNamespace(entities={enemy: [1, 1]})
    battle = SimpleNamespace(
        combat_order=[enemy],
        entities={enemy: {'group': 'b'}},
    )
    assert hooks._group_defeated_on_map(battle_map, battle, 'b') is True


def test_group_not_defeated_when_enemy_still_conscious():
    enemy = FakeEnemy('guz', alive=True)
    battle_map = SimpleNamespace(entities={enemy: [1, 1]})
    battle = SimpleNamespace(
        combat_order=[enemy],
        entities={enemy: {'group': 'b'}},
    )
    assert hooks._group_defeated_on_map(battle_map, battle, 'b') is False


def test_process_battle_end_hooks_runs_matching_hook():
    pc = FakePc('aldric')
    battle_map = SimpleNamespace(name='town_market', entities={pc: [10, 10]})
    game_manager = MagicMock()
    game_manager.get_current_battle.return_value = SimpleNamespace(
        combat_order=[FakeEnemy('guz', alive=False)],
        entities={FakeEnemy('guz', alive=False): {'group': 'b'}},
        tpk=lambda: False,
    )
    game_manager.get_current_battle_map.return_value = battle_map
    game_manager.get_entity_by_uid.return_value = SimpleNamespace(
        entity_uid='finethir_shinebright',
        label=lambda: 'Finethir',
    )

    session = SimpleNamespace(
        session_state={},
        game_properties={
            'battle_end_hooks': [{
                'id': 'town_market_after_guz',
                'map': 'town_market',
                'once_session_key': 'wild_sheep_dust_settled',
                'require_groups_defeated': ['b'],
                'narration': {'title': 'After', 'text': 'Quiet falls.'},
                'npc_address': {
                    'npc_uid': 'finethir_shinebright',
                    'teleport_to': 'scroll_reader',
                    'open_dialog': True,
                    'system_nudge': 'Plead for help.',
                },
            }],
        },
        update_state=lambda state: session.session_state.update(state),
    )

    conversation_service = MagicMock()
    with patch.object(hooks, '_find_scroll_reader_pc', return_value=pc), patch.object(
        hooks, '_teleport_adjacent', return_value=True
    ), patch.object(hooks, '_emit_narration') as emit_narration:
        handled = hooks.process_battle_end_hooks(
            game_manager,
            session,
            conversation_service=conversation_service,
            record_narration_fn=None,
        )

    assert handled is True
    assert session.session_state['wild_sheep_dust_settled'] is True
    emit_narration.assert_called_once()
    conversation_service.initiate_npc_address.assert_called_once()


def test_process_battle_end_hooks_skips_when_once_key_set():
    session = SimpleNamespace(
        session_state={'wild_sheep_dust_settled': True},
        game_properties={
            'battle_end_hooks': [{
                'id': 'town_market_after_guz',
                'map': 'town_market',
                'once_session_key': 'wild_sheep_dust_settled',
                'require_groups_defeated': ['b'],
            }],
        },
        update_state=lambda state: session.session_state.update(state),
    )
    game_manager = MagicMock()
    game_manager.get_current_battle.return_value = SimpleNamespace(
        combat_order=[FakeEnemy('guz', alive=False)],
        entities={},
        tpk=lambda: False,
    )
    game_manager.get_current_battle_map.return_value = SimpleNamespace(name='town_market', entities={})

    handled = hooks.process_battle_end_hooks(game_manager, session, conversation_service=MagicMock())
    assert handled is False


def test_process_battle_end_hooks_sets_session_flags():
    pc = FakePc('aldric')
    battle_map = SimpleNamespace(name='town_market', entities={pc: [10, 10]})
    game_manager = MagicMock()
    game_manager.get_current_battle.return_value = SimpleNamespace(
        combat_order=[FakeEnemy('guz', alive=False)],
        entities={FakeEnemy('guz', alive=False): {'group': 'b'}},
        tpk=lambda: False,
    )
    game_manager.get_current_battle_map.return_value = battle_map

    session = SimpleNamespace(
        session_state={},
        game_properties={
            'battle_end_hooks': [{
                'id': 'guz_flags',
                'map': 'town_market',
                'once_session_key': 'wild_sheep_dust_settled',
                'session_flags': {'wild_sheep_guz_killed': True},
                'require_groups_defeated': ['b'],
            }],
        },
        update_state=lambda state: session.session_state.update(state),
    )

    with patch.object(hooks, '_emit_narration'):
        handled = hooks.process_battle_end_hooks(game_manager, session, conversation_service=MagicMock())

    assert handled is True
    assert session.session_state['wild_sheep_guz_killed'] is True
