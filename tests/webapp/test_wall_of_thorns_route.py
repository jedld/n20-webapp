"""POST /action round-trip for Wall of Thorns: the route asks for the shape
(select_choice), then the anchor square (select_square), and the cast commits
a zone whose squares are centered on the clicked point."""

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
from natural20.actions.spell_action import SpellAction  # noqa: E402
from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402


class TestWallOfThornsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        self.battle = Battle(self.session, self.battle_map)
        self.wizard = PlayerCharacter(self.session, {
            'name': 'Crysania', 'entity_uid': 'crysania', 'race': 'human',
            'classes': {'wizard': 11}, 'level': 11,
            'max_hp': 60, 'hp': 60, 'hit_die': 'inherit',
            'ability': {'str': 8, 'dex': 14, 'con': 12, 'int': 18, 'wis': 12, 'cha': 10},
            'saving_throw_proficiencies': ['int', 'wis'],
            'prepared_spells': ['wall_of_thorns'], 'spellbook': ['wall_of_thorns'],
            'equipped': ['dagger'], 'inventory': [{'type': 'dagger', 'qty': 1}],
            'token': ['W'], 'group': 'a',
        })
        self.session.register_entity(self.wizard)
        self.battle_map.add(self.wizard, 1, 1)
        self.battle.add(self.wizard, 'a', add_to_initiative=True)
        self.battle.start()
        self.wizard.reset_turn(self.battle)
        self.wizard.spell_slots.setdefault('wizard', {})[6] = 1
        self.wizard.properties.setdefault('casted_level_spells', {})

        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        self.current_game.get_entity_by_uid = lambda uid: (
            self.wizard if uid == self.wizard.entity_uid else None)
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.game_session = self.session
        self.current_game.canonical_map_entity.side_effect = lambda entity: entity
        self.committed = []

        def commit(_username, action, _pov):
            self.committed.append(action)
            return {'status': 'ok'}

        self.current_game.commit_and_update.side_effect = commit

    def _post(self, opts, target=None):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by',
                   return_value=[self.wizard.entity_uid]):
            payload = {'id': self.wizard.entity_uid, 'action': 'SpellAction', 'opts': opts}
            if target is not None:
                payload['target'] = target
            response = client.post('/action', json=payload)
        return response.get_json()

    def test_route_asks_for_shape_then_anchor(self):
        payload = self._post({'spell': 'wall_of_thorns', 'at_level': 6,
                              'action_type': 'cast'})
        self.assertEqual(payload.get('type'), 'select_choice', payload)
        self.assertEqual(self.committed, [])

        payload = self._post({'spell': 'wall_of_thorns', 'at_level': 6,
                              'action_type': 'cast', 'choice': 'wall'})
        self.assertEqual(payload.get('type'), 'select_square', payload)
        self.assertEqual(self.committed, [])

        payload = self._post({'spell': 'wall_of_thorns', 'at_level': 6,
                              'action_type': 'cast', 'choice': 'wall'},
                             target={'x': 3, 'y': 3})
        self.assertEqual(payload.get('status'), 'ok', payload)
        self.assertEqual(len(self.committed), 1)
        action = self.committed[0]
        self.assertIsInstance(action, SpellAction)
        # The route defers resolve() to the real commit (mocked here), so
        # resolve once more to inspect the emitted zone item.
        action.resolve(self.session, self.battle_map, {'battle': self.battle})
        result = next(item for item in action.result
                      if isinstance(item, dict) and item.get('type') == 'wall_of_thorns')
        # Centered on the clicked square (clipped to the fixture map).
        squares = {tuple(s) for s in result['squares']}
        self.assertIn((3, 3), squares)
        self.assertTrue(all(y == 3 for (_x, y) in squares))

    def test_target_preview_centers_wall_squares(self):
        import json as _json
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by',
                   return_value=[self.wizard.entity_uid]):
            payload = _json.dumps({
                'id': self.wizard.entity_uid,
                'x': 3, 'y': 3,
                'action_info': 'SpellAction',
                'opts': {'spell': 'wall_of_thorns', 'at_level': 6,
                         'action_type': 'cast', 'choice': 'wall'},
            })
            response = client.get('/target', query_string={'payload': payload})
        data = response.get_json()
        self.assertTrue(data.get('valid_target'), data)
        squares = {tuple(s) for s in data.get('target_squares') or []}
        # 12-wide centered preview clipped to the 6x7 fixture map.
        self.assertTrue(squares and all(y == 3 for (_x, y) in squares))


if __name__ == '__main__':
    unittest.main()
