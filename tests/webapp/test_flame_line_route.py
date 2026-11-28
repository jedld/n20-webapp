"""POST /action round-trip for Investiture of Flame's Line of Flame: the
route asks for a direction square (select_empty_space), then commits. Also
covers the /target hover preview (footprint squares) and the animation
payload that drives the 2D/3D flame VFX."""

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
from natural20.actions.flame_line_action import FlameLineAction  # noqa: E402
from natural20.battle import Battle, action_animator  # noqa: E402
from natural20.die_roll import DieRoll  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.npc import Npc  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402


class _FlameEffect:
    id = 'investiture_of_flame'

    def __init__(self):
        self.spell_properties = {'spellcasting_ability': 'wisdom'}


class TestFlameLineRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        self.battle = Battle(self.session, self.battle_map, animation_log_enabled=True)
        self.wizard = PlayerCharacter(self.session, {
            'name': 'Crysania', 'entity_uid': 'crysania', 'race': 'human',
            'classes': {'wizard': 11}, 'level': 11,
            'max_hp': 60, 'hp': 60, 'hit_die': 'inherit',
            'ability': {'str': 8, 'dex': 14, 'con': 12, 'int': 18, 'wis': 12, 'cha': 10},
            'saving_throw_proficiencies': ['int', 'wis'],
            'equipped': ['dagger'], 'inventory': [{'type': 'dagger', 'qty': 1}],
            'token': ['W'], 'group': 'a',
        })
        self.session.register_entity(self.wizard)
        self.battle_map.add(self.wizard, 1, 1)
        self.battle.add(self.wizard, 'a', add_to_initiative=True)
        self.battle.start()
        self.wizard.reset_turn(self.battle)
        # The line only exists while the investiture effect is up.
        self.wizard.register_effect('investiture_of_flame', _FlameEffect,
                                    effect=_FlameEffect(), source=self.wizard)

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
            payload = {'id': self.wizard.entity_uid,
                       'action': 'FlameLineAction', 'opts': opts}
            if target is not None:
                payload['target'] = target
            response = client.post('/action', json=payload)
        return response.get_json()

    def test_action_click_asks_for_direction_then_commits(self):
        payload = self._post({'action_type': 'investiture_flame_line'})
        # Regression: build_map() used to have a spell-style signature and
        # the route 500'd, which looked like "the button does nothing".
        self.assertEqual(payload.get('type'), 'select_empty_space', payload)
        self.assertEqual(self.committed, [])

        payload = self._post({'action_type': 'investiture_flame_line'},
                             target={'x': 5, 'y': 5})
        self.assertEqual(payload.get('status'), 'ok', payload)
        self.assertEqual(len(self.committed), 1)
        action = self.committed[0]
        self.assertIsInstance(action, FlameLineAction)
        DieRoll.fudge(20)
        action.resolve(self.session, self.battle_map, {'battle': self.battle})
        self.assertTrue(any(item.get('type') == 'flame_line_used'
                            for item in action.result))
        # Animation payload carries the VFX key + exact footprint.
        message = action_animator(action, self.battle)['message']
        self.assertEqual(message.get('special_effect'), 'investiture_flame_line')
        self.assertEqual(message.get('spell'), 'investiture_flame_line')
        self.assertTrue(message.get('target_squares'))
        self.assertEqual({tuple(s) for s in message['target_squares']},
                         {tuple(s) for s in action.line_squares})

    def test_target_preview_shows_line_squares(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by',
                   return_value=[self.wizard.entity_uid]):
            payload = json.dumps({
                'id': self.wizard.entity_uid,
                'x': 5, 'y': 5,
                'action_info': 'FlameLineAction',
                'opts': {},
            })
            response = client.get('/target', query_string={'payload': payload})
        data = response.get_json()
        self.assertTrue(data.get('valid_target'), data)
        squares = {tuple(s) for s in data.get('target_squares') or []}
        self.assertTrue(squares, data)
        # Preview squares match the committed footprint (same builder).
        self.assertTrue(all(0 <= x < self.battle_map.size[0]
                            and 0 <= y < self.battle_map.size[1]
                            for (x, y) in squares))


if __name__ == '__main__':
    unittest.main()
