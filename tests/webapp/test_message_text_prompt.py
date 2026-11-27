"""POST /action round-trip for the Message cantrip's text_input step: the
route asks for the whisper text after the target, then completes when the
client re-posts with opts.message (and the clicked target)."""

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

GUARD_POS = (5, 5)


class TestMessageTextPrompt(unittest.TestCase):
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
            'prepared_spells': ['message'], 'spellbook': ['message'],
            'equipped': ['dagger'], 'inventory': [{'type': 'dagger', 'qty': 1}],
            'token': ['W'], 'group': 'a',
        })
        self.session.register_entity(self.wizard)
        self.battle_map.add(self.wizard, 2, 5)
        self.battle.add(self.wizard, 'a', add_to_initiative=True)
        self.battle.start()
        # The fixture map spawns a Guard at (5, 5): the whisper target.
        self.guard = next(e for e in self.battle_map.entities
                          if getattr(e, 'name', '') == 'Guard')

        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        self.current_game.get_entity_by_uid = lambda uid: {
            self.wizard.entity_uid: self.wizard,
            self.guard.entity_uid: self.guard,
        }.get(uid)
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.game_session = self.session
        self.current_game.canonical_map_entity.side_effect = lambda entity: entity
        self.committed = []

        def commit(_username, action, _pov):
            self.committed.append(action)
            return {'status': 'ok'}

        self.current_game.commit_and_update.side_effect = commit

    def _post(self, opts):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by',
                   return_value=[self.wizard.entity_uid]):
            response = client.post('/action', json={
                'id': self.wizard.entity_uid,
                'action': 'SpellAction',
                'opts': opts,
                'target': {'x': GUARD_POS[0], 'y': GUARD_POS[1]},
            })
        return response.get_json()

    def test_route_prompts_for_the_message_text(self):
        payload = self._post({'spell': 'message', 'at_level': 0, 'action_type': 'cast'})
        self.assertEqual(payload.get('type'), 'text_input', payload)
        self.assertEqual(payload.get('field'), 'message')
        self.assertEqual(self.committed, [])

    def test_repost_with_message_completes_the_cast(self):
        payload = self._post({'spell': 'message', 'at_level': 0, 'action_type': 'cast',
                              'message': 'hold the line'})
        self.assertEqual(payload.get('status'), 'ok', payload)
        self.assertEqual(len(self.committed), 1)
        action = self.committed[0]
        self.assertIsInstance(action, SpellAction)
        self.assertEqual(action.message, 'hold the line')
        self.assertIs(action.target, self.guard)


if __name__ == '__main__':
    unittest.main()
