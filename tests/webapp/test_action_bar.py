"""Tests for action bar rendering and batch loading."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402
from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402


class TestActionBarRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        self.battle = Battle(self.session, self.battle_map)
        self.fighter = PlayerCharacter.load(self.session, 'high_elf_fighter.yml')
        self.wolf = self.session.npc('wolf')
        self.battle.add(self.fighter, 'a', position='spawn_point_1')
        self.battle.add(self.wolf, 'b', position='spawn_point_2')
        self.battle_map.move_to(self.fighter, 0, 0, self.battle)
        self.battle_map.move_to(self.wolf, 1, 0, self.battle)
        self.battle.start()

        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        self.current_game.get_map_for_user.return_value = self.battle_map
        self.current_game.get_entity_by_uid = lambda uid: {
            self.fighter.entity_uid: self.fighter,
            self.wolf.entity_uid: self.wolf,
        }.get(uid)
        self.current_game.get_map_for_entity.return_value = self.battle_map

    def test_actions_batch_returns_sections_for_multiple_entities(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        with patch('webapp.blueprints.helpers.action_bar.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.helpers.action_bar.entity_owners', return_value=['dm']):
            response = client.get(
                '/actions/batch',
                query_string={
                    'ids': f'{self.fighter.entity_uid},{self.wolf.entity_uid}',
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('sections', payload)
        self.assertEqual(len(payload['sections']), 2)
        for section in payload['sections']:
            self.assertIn('id', section)
            self.assertTrue(section.get('html') or section.get('error'))

    def test_actions_helper_computes_interact_and_other_actions_once(self):
        from webapp.blueprints.helpers.action_bar import _action_template_context

        with patch.object(self.fighter, 'available_actions', wraps=self.fighter.available_actions) as available_actions:
            _action_template_context(self.fighter, self.battle, self.battle_map, is_dm=True)
            self.assertEqual(available_actions.call_count, 2)
            interact_call = available_actions.call_args_list[0].kwargs
            other_call = available_actions.call_args_list[1].kwargs
            self.assertTrue(interact_call.get('interact_only'))
            self.assertTrue(other_call.get('except_interact'))

    def test_action_template_context_skips_spells_for_ground_objects(self):
        from natural20.item_library.common import Ground
        from webapp.blueprints.helpers.action_bar import _action_template_context

        ground = Ground(self.session, self.battle_map, {
            'name': 'loot_pile',
            'entity_uid': 'ground-loot-1',
            'type': 'ground',
        })

        with patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session):
            ctx = _action_template_context(ground, None, self.battle_map, is_dm=True)

        self.assertEqual(ctx['castable_spells_by_level'], {})
        self.assertEqual(ctx['spell_tab_groups'], [])

    def test_action_template_context_handles_null_inventory_objects(self):
        from natural20.item_library.door_object import DoorObject
        from webapp.blueprints.helpers.action_bar import _action_template_context

        door = DoorObject(self.session, self.battle_map, {
            'name': 'tavern_suite_door',
            'entity_uid': 'tavern_suite_door',
            'type': 'door',
        })

        with patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session):
            ctx = _action_template_context(door, None, self.battle_map, is_dm=True)

        self.assertEqual(ctx['usable_items'], [])
        self.assertEqual(ctx['other_items'], [])

    def test_actions_batch_handles_null_inventory_objects(self):
        from natural20.item_library.door_object import DoorObject

        door = DoorObject(self.session, self.battle_map, {
            'name': 'tavern_suite_door',
            'entity_uid': 'tavern_suite_door',
            'type': 'door',
        })

        current_game = Mock()
        current_game.get_current_battle.return_value = None
        current_game.get_map_for_user.return_value = self.battle_map
        current_game.get_entity_by_uid = lambda uid: door if uid == 'tavern_suite_door' else None
        current_game.get_map_for_entity.return_value = self.battle_map

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        with patch('webapp.blueprints.helpers.action_bar.get_current_game', return_value=current_game), \
             patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.helpers.action_bar.entity_owners', return_value=['dm']):
            response = client.get(
                '/actions/batch',
                query_string={'ids': 'tavern_suite_door'},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['sections']), 1)
        self.assertTrue(payload['sections'][0].get('html'))

    def test_fighter_action_bar_includes_knockout_toggle(self):
        from webapp.blueprints.helpers.action_bar import _action_template_context

        self.battle.set_current_turn(self.fighter)
        self.fighter.reset_turn(self.battle)

        with patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session):
            ctx = _action_template_context(self.fighter, self.battle, self.battle_map, is_dm=True)

        self.assertTrue(
            ctx['has_melee_knockout'],
            'expected a melee attack eligible for knockout on the fighter action bar',
        )

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        with patch('webapp.blueprints.helpers.action_bar.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.helpers.action_bar.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.helpers.action_bar.entity_owners', return_value=['dm']):
            response = client.get(
                '/actions/batch',
                query_string={'ids': self.fighter.entity_uid},
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_json()['sections'][0]['html']
        self.assertIn('knockout-toggle', html)
        self.assertIn('data-can-knock-unconscious="1"', html)


if __name__ == '__main__':
    unittest.main()
