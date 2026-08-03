"""Regression tests for /target hover preview resolution."""

import json
import os
import sys
from unittest.mock import Mock

from flask import Flask

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.blueprints import battle as battle_module


class _FakeTwoStepAction:
    def __init__(self, session, entity, action_type, opts=None):
        self.session = session
        self.entity = entity
        self.action_type = action_type
        self.opts = opts or {}

    def build_map(self):
        def _after_weapon(_weapon_choice):
            return {
                'param': [{'type': 'select_target', 'num': 1}],
                'next': lambda _target: self,
            }

        return {
            'param': [{'type': 'select_weapon'}],
            'next': _after_weapon,
        }

    def validate(self, _battle_map, _target_entity, battle=None):
        return None

    def validation_failed(self):
        return False


class _FakeActionWithStateCarrier:
    def __init__(self, session, entity, action_type, opts=None):
        self.session = session
        self.entity = entity
        self.action_type = action_type
        self.opts = opts or {}
        self.using = None

    def build_map(self):
        def _after_weapon(weapon_choice):
            resolved = _FakeActionWithStateCarrier(self.session, self.entity, self.action_type, self.opts)
            resolved.using = weapon_choice
            return {
                'action': resolved,
                'param': [{'type': 'select_target', 'num': 1}],
                'next': lambda _target: resolved,
            }

        return {
            'param': [{'type': 'select_weapon'}],
            'next': _after_weapon,
        }

    def validate(self, _battle_map, _target_entity, battle=None):
        return None

    def validation_failed(self):
        return False


class TestBattleTargetPreview:
    def test_entity_target_preview_resolves_select_weapon_then_target(self, monkeypatch):
        entity = Mock(entity_uid='pc-1')
        target = Mock(entity_uid='npc-1')
        battle_map = Mock()
        battle = Mock()
        battle.valid_targets_for.return_value = [target]

        monkeypatch.setattr(battle_module, 'action_type_to_class', lambda _name: _FakeTwoStepAction)
        monkeypatch.setattr(battle_module, 'resolve_requested_action_type', lambda *args, **kwargs: 'two_weapon_attack')
        monkeypatch.setattr(battle_module, 'get_game_session', lambda: object())

        preview = battle_module._entity_target_preview(
            entity,
            target,
            'TwoWeaponAttackAction',
            {'using': 'shortsword'},
            battle_map,
            battle,
        )

        assert preview == {'valid_target': True}

    def test_entity_target_preview_returns_none_without_weapon_choice(self, monkeypatch):
        entity = Mock(entity_uid='pc-1')
        target = Mock(entity_uid='npc-1')

        monkeypatch.setattr(battle_module, 'action_type_to_class', lambda _name: _FakeTwoStepAction)
        monkeypatch.setattr(battle_module, 'resolve_requested_action_type', lambda *args, **kwargs: 'two_weapon_attack')
        monkeypatch.setattr(battle_module, 'get_game_session', lambda: object())

        preview = battle_module._entity_target_preview(
            entity,
            target,
            'TwoWeaponAttackAction',
            {},
            Mock(),
            Mock(),
        )

        assert preview is None

    def test_entity_target_preview_uses_resolved_action_state_for_valid_targets(self, monkeypatch):
        entity = Mock(entity_uid='pc-1')
        target = Mock(entity_uid='npc-1')
        battle_map = Mock()

        seen_using = {'value': None}

        def _valid_targets_for(_entity, action):
            seen_using['value'] = getattr(action, 'using', None)
            if seen_using['value'] is None:
                raise Exception('attack range cannot be None')
            return [target]

        battle = Mock()
        battle.valid_targets_for.side_effect = _valid_targets_for

        monkeypatch.setattr(battle_module, 'action_type_to_class', lambda _name: _FakeActionWithStateCarrier)
        monkeypatch.setattr(battle_module, 'resolve_requested_action_type', lambda *args, **kwargs: 'two_weapon_attack')
        monkeypatch.setattr(battle_module, 'get_game_session', lambda: object())

        preview = battle_module._entity_target_preview(
            entity,
            target,
            'TwoWeaponAttackAction',
            {'using': 'shortsword'},
            battle_map,
            battle,
        )

        assert preview == {'valid_target': True}
        assert seen_using['value'] == 'shortsword'

    def test_get_target_returns_invalid_target_when_preview_unavailable(self, monkeypatch):
        app = Flask(__name__)
        payload = {
            'id': 'pc-1',
            'x': 6,
            'y': 21,
            'action_info': 'TwoWeaponAttackAction',
            'opts': {'using': 'shortsword', 'action_type': 'two_weapon_attack'},
        }

        entity = Mock(entity_uid='pc-1')
        target = Mock(entity_uid='npc-1')
        battle_map = Mock()
        battle_map.map_stack = object()

        game = Mock()
        game.get_current_battle.return_value = None
        game.get_entity_by_uid.return_value = entity
        game.get_map_for_entity.return_value = battle_map

        monkeypatch.setattr(battle_module, 'get_current_game', lambda: game)
        monkeypatch.setattr(battle_module, '_entity_target_preview', lambda *args, **kwargs: None)

        import natural20.map_stack_targeting as stack_targeting
        monkeypatch.setattr(
            stack_targeting,
            'entity_at_stack_position',
            lambda _stack, _map, x, y: (target, battle_map, x, y),
        )

        with app.test_request_context(f"/target?payload={json.dumps(payload)}"):
            response = battle_module.get_target()

        data = response.get_json()
        assert response.status_code == 200
        assert data['valid_target'] is False
        assert 'primary_error' in data
