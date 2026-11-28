"""DM-only /dm/teleport_destinations route: the panel list payload, the
add/update/set_familiarity/delete ops, map/anchor validation, and the
empty-database cast block through the shared helper (docs/TELEPORT_DESTINATIONS.md)."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

import yaml  # noqa: E402

os.environ.setdefault('N20_SKIP_STARTUP_THREADS', '1')

from webapp.app import app  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402
from natural20.utils.teleport_destinations import list_destinations  # noqa: E402


def _campaign(tmpdir):
    os.makedirs(os.path.join(tmpdir, 'maps'), exist_ok=True)
    rows = ['.' * 8 for _ in range(8)]
    for name in ('field', 'tower'):
        with open(os.path.join(tmpdir, 'maps', f'{name}.yml'), 'w') as fh:
            yaml.safe_dump({'name': name.title(),
                            'map': {'base': rows, 'meta': rows, 'illumination': 1.0},
                            'legend': {}}, fh)
    with open(os.path.join(tmpdir, 'game.yml'), 'w') as fh:
        yaml.safe_dump({'name': 'TP fixture', 'ruleset': '5e-2014',
                        'starting_map': 'maps/field'}, fh)
    with open(os.path.join(tmpdir, 'index.json'), 'w') as fh:
        json.dump({'title': 't', 'logins': [{'name': 'dm', 'password': 'x',
                                             'role': ['dm']}],
                   'default_controllers': []}, fh)
    return Session(root_path=tmpdir, event_manager=EventManager())


class TestTeleportDestinationsRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.session = _campaign(self._tmp.name)
        self.wizard = PlayerCharacter(self.session, {
            'name': 'Wizard', 'entity_uid': 'wizard', 'race': 'human',
            'classes': {'wizard': 7}, 'level': 7,
            'max_hp': 40, 'hp': 40,
            'ability': {'int': 18},
            'prepared_spells': ['teleport'], 'spellbook': ['teleport'],
            'token': ['W'], 'group': 'a',
        })
        self.session.register_entity(self.wizard)
        self.wizard.spell_slots.setdefault('wizard', {})[7] = 1
        field_map = self.session.register_map('field', 'maps/field')
        field_map.add(self.wizard, 1, 1, 'a')
        if 'tower' not in (self.session.maps or {}):
            self.session.register_map('tower', 'maps/tower')

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, username='dm'):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = username
        return client

    def _post(self, client, payload):
        return client.post('/dm/teleport_destinations', json=payload)

    def test_get_requires_login(self):
        client = app.test_client()
        response = client.get('/dm/teleport_destinations')
        # the app's global login gate redirects anonymous visitors
        self.assertIn(response.status_code, (302, 401))

    def test_get_panel_payload_for_caster(self):
        client = self._client()
        with patch('webapp.blueprints.dm.get_game_session', return_value=self.session):
            response = client.get('/dm/teleport_destinations',
                                  query_string={'entity_uid': 'wizard'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([c['entity_uid'] for c in payload['casters']], ['wizard'])
        self.assertTrue(payload['casters'][0]['knows_teleport'])
        self.assertIn('field', [m['name'] for m in payload['maps']])
        self.assertEqual(payload['destinations'], [])

    def test_add_update_familiarity_delete(self):
        client = self._client()
        with patch('webapp.blueprints.dm.get_game_session', return_value=self.session):
            added = self._post(client, {
                'op': 'add', 'entity_uid': 'wizard', 'name': 'Tower Library',
                'map': 'tower', 'x': 3, 'y': 4, 'familiarity': 'seen casually',
            })
            self.assertEqual(added.status_code, 200)
            dest_id = added.get_json()['destination']['id']
            self.assertEqual(dest_id, 'tower-library')
            self.assertTrue(list_destinations(self.wizard))

            rated = self._post(client, {
                'op': 'set_familiarity', 'entity_uid': 'wizard',
                'dest_id': dest_id, 'familiarity': 'very familiar',
            })
            self.assertEqual(rated.get_json()['destinations'][0]['familiarity'],
                             'very_familiar')

            deleted = self._post(client, {'op': 'delete', 'entity_uid': 'wizard',
                                          'dest_id': dest_id})
            self.assertEqual(deleted.get_json()['destinations'], [])
            self.assertEqual(list_destinations(self.wizard), [])

    def test_add_validates_map_and_anchor(self):
        client = self._client()
        with patch('webapp.blueprints.dm.get_game_session', return_value=self.session):
            bad_map = self._post(client, {
                'op': 'add', 'entity_uid': 'wizard', 'name': 'Nowhere',
                'map': 'not_a_map', 'x': 1, 'y': 1,
            })
            self.assertEqual(bad_map.status_code, 400)
            bad_anchor = self._post(client, {
                'op': 'add', 'entity_uid': 'wizard', 'name': 'Ghost',
                'kind': 'anchor', 'anchor_entity_uid': 'ghost-uid',
            })
            self.assertEqual(bad_anchor.status_code, 400)

    def test_empty_database_blocks_teleport(self):
        from natural20.actions.spell_action import SpellAction
        self.assertEqual(list_destinations(self.wizard), [])
        self.assertFalse(SpellAction.can_cast(self.wizard, None, 'teleport'))
        client = self._client()
        with patch('webapp.blueprints.dm.get_game_session', return_value=self.session):
            self._post(client, {
                'op': 'add', 'entity_uid': 'wizard', 'name': 'Tower',
                'map': 'tower', 'x': 1, 'y': 1, 'familiarity': 'very familiar',
            })
        self.assertTrue(SpellAction.can_cast(self.wizard, None, 'teleport'))


if __name__ == '__main__':
    unittest.main()
