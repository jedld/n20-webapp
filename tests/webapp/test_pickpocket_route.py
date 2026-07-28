"""Regression tests for pickpocket action HTTP flow."""

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
from natural20.session import Session  # noqa: E402


class TestPickpocketRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        self.battle = Battle(self.session, self.battle_map)
        self.thief = self.session.npc('goblin')
        self.victim = self.session.npc('goblin')
        self.battle.add(self.thief, 'a', position='spawn_point_1')
        self.battle.add(self.victim, 'b', position='spawn_point_2')
        self.battle_map.move_to(self.thief, 0, 0, self.battle)
        self.battle_map.move_to(self.victim, 1, 0, self.battle)
        self.battle.start()

        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        uid_map = {
            str(self.thief.entity_uid): self.thief,
            str(self.victim.entity_uid): self.victim,
        }
        self.current_game.get_entity_by_uid = lambda uid: uid_map.get(str(uid))
        self.current_game.canonical_map_entity = lambda entity: entity
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.commit_and_update.return_value = {'status': 'ok'}
        self.current_game.game_session = self.session

    def _post_action(self, client, payload):
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by', return_value=[self.thief.entity_uid]):
            return client.post('/action', json=payload)

    def test_pickpocket_target_then_item_selection(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        first = self._post_action(client, {
            'id': str(self.thief.entity_uid),
            'action': 'PickpocketAction',
            'opts': {'action_type': 'pickpocket'},
        })
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        self.assertEqual(first_payload.get('type'), 'select_target')

        vx, vy = self.battle_map.position_of(self.victim)
        second = self._post_action(client, {
            'id': str(self.thief.entity_uid),
            'action': 'PickpocketAction',
            'opts': {'action_type': 'pickpocket'},
            'target': {'x': vx, 'y': vy},
        })
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertEqual(second_payload.get('type'), 'select_pickpocket_item')
        self.assertEqual(second_payload.get('target_uid'), self.victim.entity_uid)
        self.assertIn('pickpocket_attempt', second_payload)
        self.assertTrue(second_payload['pickpocket_attempt'].get('success'))

        third = self._post_action(client, {
            'id': str(self.thief.entity_uid),
            'action': 'PickpocketAction',
            'opts': {
                'action_type': 'pickpocket',
                'item_name': 'arrows',
                'pickpocket_attempt': second_payload['pickpocket_attempt'],
            },
            'target': str(self.victim.entity_uid),
        })
        self.assertEqual(third.status_code, 200)
        self.assertEqual(third.get_json().get('status'), 'ok')
        self.current_game.commit_and_update.assert_called_once()

    def test_pickpocket_items_shows_roll_summary(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        attempt = {
            'success': True,
            'roll_total': 18,
            'roll_breakdown': '1d20 (15) + 3',
            'passive_insight': 12,
            'message': "Thief's sleight of hand succeeds.",
        }
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session):
            response = client.get(
                '/pickpocket_items',
                query_string={
                    'id': str(self.thief.entity_uid),
                    'target': str(self.victim.entity_uid),
                    'pickpocket_attempt': __import__('json').dumps(attempt),
                },
            )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Sleight of Hand succeeded', html)
        self.assertIn('18', html)
        self.assertIn('passive Insight', html)
        self.assertIn('12', html)
        self.assertIn('1d20 (15) + 3', html)


if __name__ == '__main__':
    unittest.main()
