"""Tests for stack-aware path source resolution."""
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
WEBAPP_DIR = os.path.join(REPO_ROOT, 'n20-webapp', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from natural20.event_manager import EventManager
from natural20.session import Session
from webapp.blueprints.navigation import _resolve_path_source


class TestStackPathSourceResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(
            root_path=os.path.join(REPO_ROOT, 'user_levels', 'wild_sheep_chase'),
            event_manager=EventManager(),
        )
        cls.upstairs = cls.session.maps['tavern_2nd_floor']
        cls.npc = cls.session.entity_by_uid('pip_barmaid')

    def test_world_coords_resolve_to_overlay_entity(self):
        self.upstairs.place((4, 0), self.npc)
        entity, lx, ly = _resolve_path_source(self.upstairs, None, 'test', 13, 16)
        self.assertIs(entity, self.npc)
        self.assertEqual((lx, ly), (4, 0))


if __name__ == '__main__':
    unittest.main()
