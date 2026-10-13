"""Attacking unseen squares through /target (hover preview) and /action."""

import json
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
from natural20.actions.attack_action import AttackAction  # noqa: E402
from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402

FIGHTER_POS = (1, 1)
GOBLIN_POS = (2, 1)
EMPTY_POS = (2, 2)


class TestUnseenTargetAttackRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'tests/fixtures/battle_sim.yml')
        self.battle = Battle(self.session, self.battle_map)
        self.fighter = PlayerCharacter.load(self.session, 'high_elf_fighter.yml')
        self.goblin = self.session.npc('goblin', {'name': 'Gruk'})
        self.battle.add(self.fighter, 'a', position=FIGHTER_POS, token='F')
        self.battle.add(self.goblin, 'b', position=GOBLIN_POS, token='g')
        for e in (self.fighter, self.goblin):
            e.reset_turn(self.battle)
        self.battle.start()
        self.battle.set_current_turn(self.fighter)
        self.goblin.statuses.append('invisible')
        self.weapon = next(
            item for item in self.fighter.properties.get('equipped', [])
            if (self.session.load_weapon(item) or {}).get('type') == 'melee_attack'
        )

        self.committed = []
        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        uid_map = {str(e.entity_uid): e for e in (self.fighter, self.goblin)}
        self.current_game.get_entity_by_uid = lambda uid: uid_map.get(str(uid))
        self.current_game.canonical_map_entity = lambda entity: entity
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.game_session = self.session

        def _commit(_user, action, _pov):
            self.committed.append(action)
            return {'status': 'ok'}
        self.current_game.commit_and_update.side_effect = _commit

    def _patches(self):
        return (
            patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game),
            patch('webapp.blueprints.battle.get_game_session', return_value=self.session),
            patch('webapp.blueprints.battle.entities_controlled_by',
                  return_value=[self.fighter.entity_uid]),
        )

    def _client(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        return client

    def _preview(self, x, y):
        payload = {
            'id': self.fighter.entity_uid, 'x': x, 'y': y,
            'action_info': 'AttackAction', 'opts': {'using': self.weapon},
        }
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            response = self._client().get('/target', query_string={'payload': json.dumps(payload)})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def _attack(self, x, y):
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            response = self._client().post('/action', json={
                'id': self.fighter.entity_uid,
                'action': 'AttackAction',
                'opts': {'using': self.weapon},
                'target': {'x': x, 'y': y},
            })
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_preview_is_identical_for_occupied_and_empty_unseen_squares(self):
        occupied = self._preview(*GOBLIN_POS)
        empty = self._preview(*EMPTY_POS)
        self.assertTrue(occupied['valid_target'])
        self.assertTrue(occupied['blind_attack'])
        self.assertEqual(occupied['adv_info'], [[], ['unseen_target']])
        self.assertIn('blind_hint', occupied)
        self.assertNotIn('Gruk', json.dumps(occupied))
        occupied.pop('sensed')
        empty.pop('sensed')
        self.assertEqual(occupied, empty)

    def test_preview_flags_heard_square_as_sensed(self):
        self.assertTrue(self._preview(*GOBLIN_POS)['sensed'])
        self.assertFalse(self._preview(*EMPTY_POS)['sensed'])

    def test_preview_rejects_out_of_range_square(self):
        data = self._preview(5, 6)
        self.assertFalse(data['valid_target'])
        self.assertTrue(data['primary_error'])

    def test_action_on_coordinates_becomes_blind_attack(self):
        self._attack(*GOBLIN_POS)
        self.assertEqual(len(self.committed), 1)
        action = self.committed[0]
        self.assertIsInstance(action, AttackAction)
        self.assertEqual(action.target_square, GOBLIN_POS)

    def test_action_on_empty_square_is_blind_attack_too(self):
        self._attack(*EMPTY_POS)
        self.assertEqual(self.committed[0].target_square, EMPTY_POS)
        self.assertIsNone(self.committed[0].target)

    def test_select_target_step_lists_sensed_squares(self):
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            response = self._client().post('/action', json={
                'id': self.fighter.entity_uid,
                'action': 'AttackAction',
                'opts': {'using': self.weapon},
            })
        data = response.get_json()
        self.assertEqual(data['type'], 'select_target')
        self.assertTrue(data['blind_attacks'])
        self.assertEqual(data['unseen_squares'], [list(GOBLIN_POS)])
        self.assertNotIn(self.goblin.entity_uid, data['valid_targets'])


if __name__ == '__main__':
    unittest.main()
