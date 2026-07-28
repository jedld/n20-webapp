"""Tests for entity registry UID resolution."""

import unittest

from natural20.event_manager import EventManager
from natural20.map import Map
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from webapp.game_management_components import GameEntityRegistry


class TestEntityRegistryLookup(unittest.TestCase):
    def setUp(self):
        self.session = Session(root_path='tests/fixtures', event_manager=EventManager())
        self.game_map = Map(self.session, 'battle_sim')
        self.manager = type('Mgr', (), {'maps': {'battle_sim': self.game_map}, 'deferred_players': {}})()
        self.registry = GameEntityRegistry(self.manager)
        self.fighter = PlayerCharacter.load(self.session, 'high_elf_fighter.yml')
        self.game_map.add(self.fighter, 2, 3)

    def test_get_entity_by_uid_prefers_live_map_token(self):
        stale = PlayerCharacter.load(self.session, 'high_elf_fighter.yml')
        stale.entity_uid = self.fighter.entity_uid

        resolved = self.registry.get_entity_by_uid(self.fighter.entity_uid)
        self.assertIs(resolved, self.fighter)
        self.assertIsNot(resolved, stale)


if __name__ == '__main__':
    unittest.main()
