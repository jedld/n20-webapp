"""Tests for DM user management (logins + PC assignment)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from webapp.blueprints.helpers import runtime_state
from webapp.user_management import (
    UserManagementError,
    assign_character,
    create_user,
    delete_user,
    list_users,
    unassign_character,
    update_user,
)


class TestUserManagement(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.index_path = os.path.join(self.tmpdir, 'index.json')
        self.index_data = {
            'title': 'Test Campaign',
            'tile_size': 70,
            'login_background': 'bg.png',
            'map': 'maps/test',
            'soundtracks': [],
            'logins': [
                {'name': 'dm', 'password': 'admin', 'role': ['dm']},
                {'name': 'alice', 'password': 'secret', 'role': ['player']},
            ],
            'default_controllers': [],
            'selectable_characters': [
                {
                    'name': 'gomerin',
                    'file': 'characters/gomerin.png',
                    'description': 'Fighter',
                    'sheet': 'characters/high_elf_fighter.yml',
                },
            ],
        }
        with open(self.index_path, 'w', encoding='utf-8') as handle:
            json.dump(self.index_data, handle)

        self.logins = list(self.index_data['logins'])
        self.controllers = list(self.index_data['default_controllers'])
        self.mock_game = Mock()
        self.mock_game.username_to_sid = {'alice': ['sid-1']}
        self.mock_game.deferred_players = {}
        self.mock_game.web_controllers = {}
        self.mock_game._setup_controllers = Mock()
        self.mock_game.spawn_player_for_user = Mock(return_value=[])
        self.mock_game.ensure_user_entities_spawned = Mock(return_value=[])
        self.mock_game.get_entity_by_uid = Mock(return_value=None)
        self.mock_game.get_pov_entity_for_user = Mock(return_value=None)
        self.mock_game.get_map_for_entity = Mock(return_value=None)

        self.mock_session = Mock()
        self.mock_session.root_path = self.tmpdir

        runtime_state.register_globals(
            LOGINS=self.logins,
            CONTROLLERS=self.controllers,
            index_data=self.index_data,
            current_game=self.mock_game,
            game_session=self.mock_session,
            LEVEL=self.tmpdir,
        )

        self.pvp_index_patch = patch(
            'webapp.blueprints.helpers.pvp.get_index_data',
            Mock(return_value=self.index_data),
        )
        self.pvp_index_patch.start()

        self.ensure_patch = patch(
            'webapp.user_management.ensure_character_entity_loaded',
            return_value=Mock(entity_uid='gomerin'),
        )
        self.ensure_patch.start()

    def tearDown(self):
        self.pvp_index_patch.stop()
        self.ensure_patch.stop()

    def test_list_users_redacts_passwords(self):
        result = list_users()
        self.assertEqual(len(result['users']), 2)
        alice = next(user for user in result['users'] if user['username'] == 'alice')
        self.assertTrue(alice['connected'])
        self.assertEqual(alice['assigned_characters'], [])
        self.assertNotIn('password', alice)

    def test_create_and_update_user_persists_index(self):
        created = create_user('bob', 'hunter2', roles=['player'])
        self.assertEqual(created['username'], 'bob')
        self.assertTrue(any(login['name'] == 'bob' for login in self.logins))

        with open(self.index_path, encoding='utf-8') as handle:
            saved = json.load(handle)
        self.assertTrue(any(login['name'] == 'bob' for login in saved['logins']))

        update_user('bob', password='newpass', roles=['player', 'dm'])
        with open(self.index_path, encoding='utf-8') as handle:
            saved = json.load(handle)
        bob = next(login for login in saved['logins'] if login['name'] == 'bob')
        self.assertEqual(bob['password'], 'newpass')
        self.assertEqual(bob['role'], ['player', 'dm'])

    def test_cannot_delete_last_dm(self):
        with self.assertRaises(UserManagementError):
            delete_user('dm')

    def test_assign_and_unassign_character(self):
        result = assign_character('alice', 'gomerin', spawn=False)
        self.assertEqual(result['character_uid'], 'gomerin')
        self.assertIn('gomerin', result['assigned_characters'])

        with open(self.index_path, encoding='utf-8') as handle:
            saved = json.load(handle)
        entry = next(item for item in saved['default_controllers'] if item['entity_uid'] == 'gomerin')
        self.assertEqual(entry['controllers'], ['alice'])

        removed = unassign_character('alice', 'gomerin')
        self.assertEqual(removed['removed_characters'], ['gomerin'])
        self.assertEqual(removed['assigned_characters'], [])

    def test_assign_rejects_taken_character(self):
        self.controllers.append({'entity_uid': 'gomerin', 'controllers': ['bob']})
        self.logins.append({'name': 'bob', 'password': 'bob', 'role': ['player']})
        with self.assertRaises(UserManagementError):
            assign_character('alice', 'gomerin')


if __name__ == '__main__':
    unittest.main()
