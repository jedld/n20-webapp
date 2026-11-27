"""Tests for per-map display settings: GET/POST /map_settings and the
shared helper in webapp.blueprints.helpers.map_settings."""
import os
import shutil
import sys
import tempfile

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

import yaml  # noqa: E402

from webapp.app import app  # noqa: E402
from webapp.blueprints.helpers import runtime_state, map_settings as map_settings_helper  # noqa: E402

import pytest


@pytest.fixture(autouse=True)
def _restore_runtime_globals():
    snapshot = dict(runtime_state._globals)
    yield
    runtime_state._globals.clear()
    runtime_state._globals.update(snapshot)


class _FakeMap:
    """Live Map stand-in with the real background_stretch semantics."""

    def __init__(self, name):
        self.name = name
        self.properties = {}

    def background_stretch(self):
        value = self.properties.get('background_stretch', False)
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)


class _EmittingSocket:
    def __init__(self):
        self.emitted = []

    def emit(self, event, payload):
        self.emitted.append((event, payload))


def _build_state(tmpdir):
    session_dir = os.path.join(tmpdir, 'maps')
    os.makedirs(session_dir, exist_ok=True)
    yml_path = os.path.join(session_dir, 'battle_map.yml')
    with open(yml_path, 'w') as handle:
        yaml.safe_dump({'name': 'battle_map', 'map': {'base': ['..']}}, handle)

    class FakeSession:
        def __init__(self):
            self.root_path = tmpdir
            self.maps = {'battle_map': _FakeMap('battle_map')}
            self.game_properties = {'maps': {'battle_map': 'maps/battle_map'}}

    class FakeGame:
        pass

    session = FakeSession()
    socket = _EmittingSocket()
    runtime_state.register_globals(
        LOGINS=[{'name': 'dm', 'role': ['dm']}, {'name': 'player', 'role': ['user']}],
        CONTROLLERS=[],
        OTHERMAPS={},
        current_game=FakeGame(),
        game_session=session,
        socketio=socket,
        LEVEL=tmpdir,
    )
    return session, socket, yml_path


class TestMapSettingsRoutes:
    def test_requires_auth(self):
        client = app.test_client()
        response = client.get('/map_settings', query_string={'map': 'battle_map'})
        assert response.status_code in (401, 403, 302)

    def test_non_dm_forbidden(self):
        _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'player'
        response = client.get('/map_settings', query_string={'map': 'battle_map'})
        assert response.status_code == 403

    def test_get_defaults_for_dm(self):
        _session, _socket, _yml = _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        response = client.get('/map_settings', query_string={'map': 'battle_map'})
        assert response.status_code == 200, response.data
        assert response.get_json()['settings'] == {'background_stretch': False}

    def test_get_unknown_map_404(self):
        _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        response = client.get('/map_settings', query_string={'map': 'nope'})
        assert response.status_code == 404

    def test_post_set_persists_and_broadcasts(self):
        session, socket, yml_path = _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        response = client.post('/map_settings', json={
            'map': 'battle_map',
            'settings': {'background_stretch': True},
        })
        assert response.status_code == 200, response.data
        assert response.get_json()['settings'] == {'background_stretch': True}
        assert session.maps['battle_map'].background_stretch() is True
        with open(yml_path) as handle:
            assert yaml.safe_load(handle).get('background_stretch') is True
        assert socket.emitted and socket.emitted[-1][0] == 'message'
        payload = socket.emitted[-1][1]
        assert payload['type'] == 'map_settings'
        assert payload['message'] == {'map': 'battle_map', 'settings': {'background_stretch': True}}

    def test_post_reset_removes_yaml_key(self):
        _session, _socket, yml_path = _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        client.post('/map_settings', json={
            'map': 'battle_map', 'settings': {'background_stretch': True},
        })
        response = client.post('/map_settings', json={
            'map': 'battle_map', 'settings': {'background_stretch': False},
        })
        assert response.status_code == 200
        assert response.get_json()['settings'] == {'background_stretch': False}
        with open(yml_path) as handle:
            assert 'background_stretch' not in yaml.safe_load(handle)

    def test_post_unknown_key_400(self):
        _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        response = client.post('/map_settings', json={
            'map': 'battle_map', 'settings': {'mystery': 1},
        })
        assert response.status_code == 400
        assert 'mystery' in response.get_json()['error']

    def test_post_unknown_map_404(self):
        _build_state(tempfile.mkdtemp())
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        response = client.post('/map_settings', json={
            'map': 'missing', 'settings': {'background_stretch': True},
        })
        assert response.status_code == 404


class TestMapSettingsHelper:
    def test_string_coercion(self):
        m = _FakeMap('m')
        for raw in ('yes', 'TRUE', '1', True):
            assert map_settings_helper.coerce_map_setting('background_stretch', raw) is True
        for raw in ('no', '0', False, None):
            assert map_settings_helper.coerce_map_setting('background_stretch', raw) is False

    def test_apply_unknown_key_raises(self):
        import tempfile as tf
        tmpdir = tf.mkdtemp()
        try:
            session, _socket, _yml = _build_state(tmpdir)
            with pytest.raises(KeyError):
                map_settings_helper.apply_map_settings(session, 'battle_map', {'bogus': True})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
