"""Regression tests for POST /action UseItemAction and battle music."""

import os
import sys
import unittest
from unittest.mock import patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

import webapp.app as appmod  # noqa: E402
from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402
from webapp.blueprints.helpers.action_utils import resolve_use_item_name  # noqa: E402


class TestUseItemRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        self.battle = Battle(self.session, self.battle_map)
        self.fighter = PlayerCharacter.load(self.session, 'high_elf_fighter.yml')
        self.battle.add(self.fighter, 'a', position='spawn_point_1')
        self.battle.start()
        self.fighter.take_damage(10, session=self.session)
        self.hp_before = self.fighter.hp()
        self.fx, self.fy = self.battle_map.position_of(self.fighter)

        self.item_opts = {
            'consumable': True,
            'image': 'healing_potion',
            'name': 'healing_potion',
            'qty': 1,
            'item': {
                'consumable': True,
                'equippable': False,
                'hp_regained': '2d4+2',
                'item_class': 'HealingPotion',
                'name': 'Potion of Healing',
                'type': 'potion',
                'usable': True,
            },
            'label': 'Potion of Healing',
        }

        self._orig_get_entity = appmod.current_game.get_entity_by_uid
        self._orig_get_battle = appmod.current_game.get_current_battle
        self._orig_get_map = appmod.current_game.get_map_for_entity
        self._orig_commit = appmod.current_game.commit_and_update

        appmod.current_game.get_entity_by_uid = self._get_entity
        appmod.current_game.get_current_battle = lambda: self.battle
        appmod.current_game.get_map_for_entity = lambda ent: self.battle_map
        appmod.current_game.commit_and_update = self._commit

    def tearDown(self):
        appmod.current_game.get_entity_by_uid = self._orig_get_entity
        appmod.current_game.get_current_battle = self._orig_get_battle
        appmod.current_game.get_map_for_entity = self._orig_get_map
        appmod.current_game.commit_and_update = self._orig_commit

    def _get_entity(self, uid):
        if uid == self.fighter.entity_uid:
            return self.fighter
        return self._orig_get_entity(uid)

    def _commit(self, username, action, pov):
        action.source = appmod.current_game.canonical_map_entity(action.source)
        target = getattr(action, 'target', None)
        if hasattr(target, 'entity_uid'):
            action.target = appmod.current_game.canonical_map_entity(target)
        self.battle.action(action)
        self.battle.commit(action)
        return {'status': 'ok'}

    def test_resolve_use_item_name_falls_back_to_image_slug(self):
        self.assertEqual(
            resolve_use_item_name({'image': 'healing_potion'}),
            'healing_potion',
        )

    def test_use_item_heals_in_combat(self):
        client = appmod.app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'

        with patch('webapp.app.entities_controlled_by', return_value=[self.fighter]):
            response = client.post(
                '/action',
                json={
                    'id': self.fighter.entity_uid,
                    'action': 'UseItemAction',
                    'opts': self.item_opts,
                    'target': {'x': self.fx, 'y': self.fy},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('status'), 'ok')
        self.assertGreater(self.fighter.hp(), self.hp_before)
        self.assertEqual(self.fighter.item_count('healing_potion'), 0)

    def test_play_battle_music_uses_campaign_track(self):
        game = appmod.current_game
        game.soundtracks = [{'name': 'battle', 'file': 'sounds/combat.mp3', 'volume': 20}]
        with patch.object(game, 'play_soundtrack') as play_mock:
            self.assertTrue(game.play_battle_music(self.battle))
        play_mock.assert_called_once_with('battle')


if __name__ == '__main__':
    unittest.main()
