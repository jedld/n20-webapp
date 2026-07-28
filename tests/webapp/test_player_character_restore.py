"""Restore player character assignment after in-memory controller state is lost."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from webapp.blueprints.helpers import pvp as pvp_helpers
from webapp.blueprints.helpers import template_globals


class FakeEntity:
    def __init__(self, entity_uid):
        self.entity_uid = entity_uid
        self.name = entity_uid


def test_character_uid_for_username_matches_selectable_name():
    index = {
        'selectable_characters': [
            {'name': 'aldric', 'sheet': 'characters/aldric_fighter.yml'},
        ],
    }
    with patch.object(pvp_helpers, 'get_index_data', return_value=index):
        assert pvp_helpers.character_uid_for_username('aldric') == 'aldric'
        assert pvp_helpers.character_uid_for_username('Aldric') == 'aldric'
        assert pvp_helpers.character_uid_for_username('mira') is None


def test_restore_player_character_for_user_rebuilds_controllers():
    controllers = []
    entity = FakeEntity('aldric')
    current_game = MagicMock()
    current_game.ensure_user_entities_spawned.side_effect = [None, [entity]]
    current_game.get_entity_by_uid.return_value = entity
    current_game.get_pov_entity_for_user.return_value = None
    current_game.get_map_for_entity.return_value = SimpleNamespace(name='town_market')

    index = {
        'selectable_characters': [
            {
                'name': 'aldric',
                'sheet': 'characters/aldric_fighter.yml',
                'overrides': {'entity_uid': 'aldric'},
            },
        ],
    }

    with patch.object(pvp_helpers, 'get_controllers', return_value=controllers), \
         patch.object(pvp_helpers, 'get_index_data', return_value=index), \
         patch.object(pvp_helpers, 'get_current_game', return_value=current_game), \
         patch.object(template_globals, 'entities_controlled_by', return_value=[]), \
         patch.object(pvp_helpers, 'ensure_character_entity_loaded', return_value=entity):
        restored = pvp_helpers.restore_player_character_for_user('aldric')

    assert restored is True
    assert controllers == [{'entity_uid': 'aldric', 'controllers': ['aldric']}]
    current_game._setup_controllers.assert_called_once()
    current_game.set_pov_entity_for_user.assert_called_once_with('aldric', entity)
    current_game.switch_map_for_user.assert_called_once_with('aldric', 'town_market')
