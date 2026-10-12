"""Tests for the player-facing sidekick auto-follow toggle endpoint."""
import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402


class TestPartySidekickFollowEndpoint:
    """POST /party/sidekick_follow — owner/DM auto-follow toggle."""

    def _client(self, username=None):
        client = app.test_client()
        if username is not None:
            with client.session_transaction() as sess:
                sess['username'] = username
        return client

    @patch('webapp.blueprints.navigation.get_current_game')
    def test_anonymous_redirected_to_login(self, game_mock):
        # The app-wide require_login before_request redirects anonymous users
        # to /login before the endpoint's own 401 check can run.
        response = self._client().post('/party/sidekick_follow', json={'entity_uid': 'squire-1'})
        assert response.status_code == 302
        assert '/login' in response.headers.get('Location', '')

    @patch('webapp.blueprints.navigation.get_current_game')
    def test_requires_entity_uid(self, game_mock):
        response = self._client('gomerin').post('/party/sidekick_follow', json={})
        assert response.status_code == 400
        assert 'entity_uid' in response.get_json()['error']

    @patch('webapp.blueprints.navigation.get_current_game')
    def test_unknown_entity_returns_404(self, game_mock):
        game = Mock()
        game.get_entity_by_uid.return_value = None
        game_mock.return_value = game

        response = self._client('gomerin').post(
            '/party/sidekick_follow', json={'entity_uid': 'ghost-9'})
        assert response.status_code == 404
        assert response.get_json()['error'] == 'Entity not found'

    @patch('webapp.blueprints.navigation.get_game_session')
    @patch('webapp.blueprints.navigation.get_current_game')
    def test_non_sidekick_returns_400(self, game_mock, session_mock):
        entity = Mock(entity_uid='random-npc')
        game = Mock()
        game.get_entity_by_uid.return_value = entity
        game_mock.return_value = game

        with patch('natural20.sidekick.is_in_party_sidekick', return_value=False):
            response = self._client('gomerin').post(
                '/party/sidekick_follow', json={'entity_uid': 'random-npc'})

        assert response.status_code == 400
        assert 'not an in-party sidekick' in response.get_json()['error']

    @patch('webapp.blueprints.navigation.get_game_session')
    @patch('webapp.blueprints.navigation.get_current_game')
    def test_non_owner_player_rejected(self, game_mock, session_mock):
        entity = Mock(entity_uid='squire-1')
        game = Mock()
        game.get_entity_by_uid.return_value = entity
        game_mock.return_value = game

        with patch('natural20.sidekick.is_in_party_sidekick', return_value=True), \
             patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']):
            response = self._client('stranger').post(
                '/party/sidekick_follow', json={'entity_uid': 'squire-1'})

        assert response.status_code == 403
        assert response.get_json()['error'] == 'You do not control this companion'

    @patch('webapp.blueprints.navigation.get_socketio')
    @patch('webapp.blueprints.navigation.get_game_session')
    @patch('webapp.blueprints.navigation.get_current_game')
    def test_owner_toggle_inverts_state(self, game_mock, session_mock, socketio_mock):
        entity = Mock(entity_uid='squire-1')
        game = Mock()
        game.get_entity_by_uid.return_value = entity
        game_mock.return_value = game

        with patch('natural20.sidekick.is_in_party_sidekick', return_value=True), \
             patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']), \
             patch('natural20.sidekick.is_auto_follow_sidekick', return_value=False), \
             patch(
                 'webapp.blueprints.helpers.sidekick_control.set_auto_follow_and_control',
                 return_value={'auto_follow': True},
             ) as set_follow:
            response = self._client('Gomerin').post(
                '/party/sidekick_follow', json={'entity_uid': 'squire-1'})

        assert response.status_code == 200
        body = response.get_json()
        assert body['success'] is True
        assert body['auto_follow'] is True
        set_follow.assert_called_once_with(entity, True)
        game.bump_render_epoch.assert_called_once_with(username='Gomerin')
        # get_socketio() is patched, so the emit lands on the mock's return value.
        socketio_mock.return_value.emit.assert_called_with('message', {'type': 'refresh_map'})

    @patch('webapp.blueprints.navigation.get_game_session')
    @patch('webapp.blueprints.navigation.get_current_game')
    def test_dm_can_toggle_without_ownership(self, game_mock, session_mock):
        entity = Mock(entity_uid='squire-1')
        game = Mock()
        game.get_entity_by_uid.return_value = entity
        game_mock.return_value = game

        with patch('natural20.sidekick.is_in_party_sidekick', return_value=True), \
             patch('natural20.sidekick.sidekick_owners', return_value=[]), \
             patch('webapp.blueprints.helpers.auth_utils.roles_for_username', return_value=['dm']), \
             patch('natural20.sidekick.is_auto_follow_sidekick', return_value=True), \
             patch(
                 'webapp.blueprints.helpers.sidekick_control.set_auto_follow_and_control',
                 return_value={'auto_follow': False},
             ) as set_follow:
            response = self._client('dm').post(
                '/party/sidekick_follow', json={'entity_uid': 'squire-1'})

        assert response.status_code == 200
        body = response.get_json()
        assert body['success'] is True
        assert body['auto_follow'] is False
        set_follow.assert_called_once_with(entity, False)

    @patch('webapp.blueprints.navigation.get_game_session')
    @patch('webapp.blueprints.navigation.get_current_game')
    def test_explicit_enabled_param_respected(self, game_mock, session_mock):
        entity = Mock(entity_uid='squire-1')
        game = Mock()
        game.get_entity_by_uid.return_value = entity
        game_mock.return_value = game

        with patch('natural20.sidekick.is_in_party_sidekick', return_value=True), \
             patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']), \
             patch('natural20.sidekick.is_auto_follow_sidekick', return_value=False), \
             patch(
                 'webapp.blueprints.helpers.sidekick_control.set_auto_follow_and_control',
                 return_value={'auto_follow': False},
             ) as set_follow:
            response = self._client('gomerin').post(
                '/party/sidekick_follow',
                json={'entity_uid': 'squire-1', 'enabled': False})

        assert response.status_code == 200
        set_follow.assert_called_once_with(entity, False)
