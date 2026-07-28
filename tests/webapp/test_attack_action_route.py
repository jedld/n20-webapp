"""Regression tests for POST /action attack targeting."""

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


class TestAttackActionRoute(unittest.TestCase):
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
        self.current_game.get_entity_by_uid = lambda uid: {
            self.fighter.entity_uid: self.fighter,
            self.wolf.entity_uid: self.wolf,
        }.get(uid)
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.commit_and_update.return_value = {'status': 'ok'}
        self.current_game.game_session = self.session

    def test_attack_with_coords_uses_entity_at_fallback(self):
        """AttackAction must not IndexError when entities_at is empty but a token exists."""
        client = app.test_client()
        wx, wy = self.battle_map.position_of(self.wolf)

        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by', return_value=[self.fighter.entity_uid]):
            with patch.object(self.battle_map, 'entities_at', return_value=[]):
                response = client.post(
                    '/action',
                    json={
                        'id': self.fighter.entity_uid,
                        'action': 'AttackAction',
                        'opts': {'using': 'rapier', 'action_type': 'attack'},
                        'target': {'x': wx, 'y': wy},
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.current_game.commit_and_update.assert_called_once()
        payload = response.get_json()
        self.assertNotEqual(payload.get('status'), 'error')


if __name__ == '__main__':
    unittest.main()
