"""POST /action support for Dispel Magic targeting persistent spell zones.

Coordinate clicks are legal for select_target spells marked ``allow_effects``
(DispelMagicSpell); overlapping zones return a ``multiple_effects`` picker
list and the caster re-posts with ``opts.effect_id``.
"""

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
from natural20.spell.extensions.persistent_zone import PersistentAoEZone  # noqa: E402
from natural20.spell.spike_growth_spell import SpikeGrowthZone  # noqa: E402

CENTER = (5, 5)


class TestDispelZoneRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(root_path='tests/fixtures', event_manager=EventManager())

    def setUp(self):
        self.battle_map = Map(self.session, 'battle_sim')
        # The fixture map spawns its own NPCs; clear them so target
        # resolution on the zone squares stays coordinate-based.
        from natural20.npc import Npc
        for entity in list(self.battle_map.entities):
            if isinstance(entity, Npc):
                self.battle_map.remove(entity)
        self.battle = Battle(self.session, self.battle_map)
        self.wizard = PlayerCharacter(self.session, {
            'name': 'Disputer', 'entity_uid': 'disputer', 'race': 'human',
            'classes': {'wizard': 11}, 'level': 11,
            'max_hp': 60, 'hp': 60, 'hit_die': 'inherit',
            'ability': {'str': 8, 'dex': 14, 'con': 12, 'int': 18, 'wis': 12, 'cha': 10},
            'saving_throw_proficiencies': ['int', 'wis'],
            'prepared_spells': ['dispel_magic'], 'spellbook': ['dispel_magic'],
            'equipped': ['dagger'], 'inventory': [{'type': 'dagger', 'qty': 1}],
            'token': ['W'], 'group': 'a',
        })
        self.session.register_entity(self.wizard)
        self.battle.add(self.wizard, 'a', add_to_initiative=True)
        self.battle_map.move_to(self.wizard, 2, 5, self.battle)
        self.battle.start()

        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = self.battle
        self.current_game.get_entity_by_uid = lambda uid: {
            self.wizard.entity_uid: self.wizard,
        }.get(uid)
        self.current_game.get_map_for_entity.return_value = self.battle_map
        self.current_game.game_session = self.session
        self.current_game.canonical_map_entity.side_effect = lambda entity: entity
        self.committed = []

        def commit(_username, action, _pov):
            self.committed.append(action)
            return {'status': 'ok'}

        self.current_game.commit_and_update.side_effect = commit

        # A live Spike Growth zone covering CENTER.
        self.zone = SpikeGrowthZone(
            self.wizard, self.battle, self.battle_map,
            [CENTER, (5, 6)], {'label': 'Spike Growth', 'level': 2},
        )
        self.battle.register_zone(self.zone)

    def _post(self, extra_opts=None):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        opts = {'spell': 'dispel_magic', 'at_level': 3, 'action_type': 'cast'}
        if extra_opts:
            opts.update(extra_opts)
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game), \
             patch('webapp.blueprints.battle.get_game_session', return_value=self.session), \
             patch('webapp.blueprints.battle.entities_controlled_by',
                   return_value=[self.wizard.entity_uid]):
            response = client.post('/action', json={
                'id': self.wizard.entity_uid,
                'action': 'SpellAction',
                'opts': opts,
                'target': {'x': CENTER[0], 'y': CENTER[1]},
            })
        return response

    def test_coordinate_click_on_zone_casts(self):
        """One zone over the square: auto-select it and carry effect_id."""
        response = self._post()
        payload = response.get_json()
        self.assertEqual(payload.get('status'), 'ok', payload)
        self.assertEqual(len(self.committed), 1)
        action = self.committed[0]
        self.assertIsInstance(action, SpellAction)
        self.assertEqual(list(action.target), list(CENTER))
        self.assertEqual(str(getattr(action, 'effect_id', None)), str(self.zone.id))

    def test_overlapping_zones_return_multiple_effects(self):
        other = PersistentAoEZone(self.wizard, self.battle, self.battle_map,
                                  [CENTER], name='web',
                                  spell={'label': 'Web', 'level': 2})
        self.battle.register_zone(other)

        payload = self._post().get_json()
        self.assertEqual(payload.get('status'), 'multiple_effects', payload)
        self.assertEqual(payload.get('target'), list(CENTER))
        ids = {effect['id'] for effect in payload['effects']}
        self.assertEqual(ids, {self.zone.id, other.id})
        labels = {effect['label'] for effect in payload['effects']}
        self.assertEqual(labels, {'Spike Growth', 'Web'})
        self.assertEqual(self.committed, [])

    def test_multiple_effects_pick_round_trip(self):
        other = PersistentAoEZone(self.wizard, self.battle, self.battle_map,
                                  [CENTER], name='web',
                                  spell={'label': 'Web', 'level': 2})
        self.battle.register_zone(other)

        payload = self._post(extra_opts={'effect_id': other.id}).get_json()
        self.assertEqual(payload.get('status'), 'ok', payload)
        self.assertEqual(len(self.committed), 1)
        self.assertEqual(str(self.committed[0].effect_id), str(other.id))

    def test_empty_square_without_zones_still_rejected(self):
        self.battle.active_zones = []
        payload = self._post().get_json()
        self.assertEqual(payload.get('status'), 'error')
        self.assertEqual(self.committed, [])


if __name__ == '__main__':
    unittest.main()
