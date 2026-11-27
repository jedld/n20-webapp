"""GET /reaction and POST /end_turn around pending async reaction windows.

A 500 from GET /reaction left the reaction modal unrendered while combat
waited forever for an input nobody was ever shown (End Turn "does nothing").
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


class TestReactionEndpoint(unittest.TestCase):
    def setUp(self):
        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = None
        self.current_game.dm_turn_hold = None
        self.current_game.waiting_for_reaction = False
        self.current_game.waiting_for_reaction_input.return_value = False

    def _client(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        return client

    def test_get_reaction_without_pending_returns_empty_200(self):
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game):
            resp = self._client().get('/reaction')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'')

    def test_get_reaction_with_stale_bool_marker_returns_empty_200(self):
        # clear_reaction_input leaves a truthy-cleared False marker behind in
        # some flows; indexing it used to raise TypeError.
        self.current_game.waiting_for_reaction_input.return_value = True
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game):
            resp = self._client().get('/reaction')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'')

    def test_end_turn_rejected_while_reaction_pending(self):
        self.current_game.waiting_for_reaction = [Mock(), Mock(), None, []]
        with patch('webapp.blueprints.battle.get_current_game', return_value=self.current_game):
            resp = self._client().post('/end_turn', json={})
        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json()
        self.assertEqual(payload.get('status'), 'error')
        self.assertIn('reaction', payload.get('message', '').lower())


if __name__ == '__main__':
    unittest.main()
