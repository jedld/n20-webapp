"""Smoke tests for the assets blueprint routes."""
import io
import os
import shutil
import sys
import tempfile

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from PIL import Image  # noqa: E402
import yaml  # noqa: E402

from webapp.app import app  # noqa: E402
from webapp.blueprints.assets import (  # noqa: E402
    _resolve_map_background_path,
    _resolve_map_preview_path,
)
from webapp.blueprints.helpers import runtime_state  # noqa: E402
from natural20.map_set import MapSetRegistry  # noqa: E402

import pytest


@pytest.fixture(autouse=True)
def _restore_runtime_globals():
    """These tests replace the process-wide runtime_state globals (current_game
    etc.) with fakes via register_globals; restore them afterwards so later
    test modules (any that hit real routes) are not polluted."""
    snapshot = dict(runtime_state._globals)
    yield
    runtime_state._globals.clear()
    runtime_state._globals.update(snapshot)


class _FakeMap:
    def __init__(self, background):
        self._background = background

    def background_image(self):
        return self._background


class TestAssetsBlueprint:
    def test_create_map_requires_auth(self):
        client = app.test_client()
        response = client.post('/create_map', json={})
        assert response.status_code in (400, 401, 403, 302)

    def test_delete_map_requires_auth(self):
        client = app.test_client()
        response = client.post('/delete_map', json={'map_name': 'missing'})
        assert response.status_code in (401, 403, 302)

    def test_serve_map_image_not_found(self):
        client = app.test_client()
        response = client.get('/assets/maps/does-not-exist.png')
        assert response.status_code == 404

    def test_resolve_map_background_path_falls_back_to_assets_root(self):
        campaign_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', '..', 'user_levels', 'wild_sheep_chase')
        )

        class FakeSession:
            root_path = campaign_root

        full_path, _directory, resolved_name = _resolve_map_background_path(
            FakeSession(),
            'map_town_market_bg.jpg',
        )
        assert full_path
        assert resolved_name == 'map_town_market_bg.jpg'

    def test_resolve_map_preview_uses_map_background_image(self):
        campaign_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', '..', 'user_levels', 'wild_sheep_chase')
        )

        class FakeSession:
            root_path = campaign_root
            maps = {
                'town_market': _FakeMap('map_town_market_bg.jpg'),
            }

        full_path, _directory, resolved_name = _resolve_map_preview_path(
            FakeSession(),
            'town_market.png',
        )
        assert full_path
        assert resolved_name == 'map_town_market_bg.jpg'


class TestCreateMapBlueprint:
    """Cover the unified create-map form: name, size, description, illumination, image, map set."""

    def _build_state(self, tmpdir):
        othermaps = {}

        class FakeSession:
            def __init__(self):
                self.root_path = tmpdir
                self.maps = {}
                self.other_maps = {}
                self.game_properties = {'maps': {}}
                self.map_sets = MapSetRegistry()
                self.active_map_set = 'root'

            # Mirrors Session.register_map for the string ref case
            def register_map(self, name, map_or_map_file, map_set=None):
                if isinstance(map_or_map_file, str):
                    self.maps[name] = map_or_map_file
                else:
                    self.maps[name] = map_or_map_file
                set_id = map_set or getattr(self, 'active_map_set', None) or 'root'
                self.map_sets.assign_map(name, set_id)
                self._link_maps_within_sets()
                return self.maps[name]

            def _link_maps_within_sets(self):
                # No-op: map objects are string refs in this test
                pass

        class FakeGame:
            def __init__(self):
                self.maps = {}
                self.other_maps = {}

        session = FakeSession()
        game = FakeGame()
        runtime_state.register_globals(
            LOGINS=[{'name': 'dm', 'role': ['dm']}],
            CONTROLLERS=[],
            OTHERMAPS=othermaps,
            current_game=game,
            game_session=session,
            LEVEL=tmpdir,
        )
        return session, game, othermaps

    def test_create_map_with_image_description_and_illumination(self):
        tmpdir = tempfile.mkdtemp()
        try:
            session, game, othermaps = self._build_state(tmpdir)
            png = io.BytesIO()
            Image.new('RGBA', (32, 16), (120, 200, 90, 255)).save(png, format='PNG')
            png.seek(0)

            client = app.test_client()
            with client.session_transaction() as sess:
                sess['username'] = 'dm'
            response = client.post(
                '/create_map',
                data={
                    'name': 'test_map_1',
                    'width': '6',
                    'height': '4',
                    'description': 'A test map',
                    'illumination': '0.5',
                    'image': (png, 'test_map_1.png'),
                },
                content_type='multipart/form-data',
            )
            assert response.status_code == 200, response.data
            assert response.get_json() == {'status': 'ok', 'name': 'test_map_1'}

            # YAML written with the requested attributes
            with open(os.path.join(tmpdir, 'maps', 'test_map_1.yml')) as handle:
                props = yaml.safe_load(handle)
            assert props['name'] == 'test_map_1'
            assert props['description'] == 'A test map'
            assert props['background_image'] == 'test_map_1.png'
            assert props['map']['illumination'] == 0.5
            assert len(props['map']['base']) == 4
            assert all(len(row) == 6 for row in props['map']['base'])

            # Uploaded image stored as PNG (not the placeholder)
            png_path = os.path.join(tmpdir, 'assets', 'maps', 'test_map_1.png')
            assert os.path.exists(png_path)
            with Image.open(png_path) as img:
                assert img.size == (32, 16)
                assert img.mode == 'RGBA'

            # Registered in session + other_maps
            assert 'test_map_1' in session.maps
            assert othermaps['test_map_1'] == 'maps/test_map_1'
            assert game.other_maps['test_map_1'] == 'maps/test_map_1'

            # Persisted to game.yml
            with open(os.path.join(tmpdir, 'game.yml')) as handle:
                game_props = yaml.safe_load(handle)
            assert game_props['maps']['test_map_1'] == 'maps/test_map_1'
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_create_map_placeholder_when_no_image(self):
        tmpdir = tempfile.mkdtemp()
        try:
            session, _game, _othermaps = self._build_state(tmpdir)
            client = app.test_client()
            with client.session_transaction() as sess:
                sess['username'] = 'dm'
            response = client.post(
                '/create_map',
                data={'name': 'bare_map', 'width': '5', 'height': '3'},
                content_type='multipart/form-data',
            )
            assert response.status_code == 200, response.data

            with open(os.path.join(tmpdir, 'maps', 'bare_map.yml')) as handle:
                props = yaml.safe_load(handle)
            assert props['description'] == 'Empty map bare_map'
            assert props['map']['illumination'] == 1.0
            assert 'background_image' not in props

            png_path = os.path.join(tmpdir, 'assets', 'maps', 'bare_map.png')
            assert os.path.exists(png_path)
            with Image.open(png_path) as img:
                assert img.size == (max(160, 5 * 10), max(100, 3 * 10))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_create_map_assigns_new_map_set(self):
        tmpdir = tempfile.mkdtemp()
        try:
            session, _game, _othermaps = self._build_state(tmpdir)
            client = app.test_client()
            with client.session_transaction() as sess:
                sess['username'] = 'dm'
            response = client.post(
                '/create_map',
                data={
                    'name': 'dungeon_2f',
                    'map_set': 'dungeon_2f',
                    'map_set_label': 'Dungeon 2F',
                },
                content_type='multipart/form-data',
            )
            assert response.status_code == 200, response.data

            # New set created with the label, map assigned to it
            map_set = session.map_sets.get('dungeon_2f')
            assert map_set is not None
            assert map_set.label == 'Dungeon 2F'
            assert 'dungeon_2f' in map_set.maps

            with open(os.path.join(tmpdir, 'game.yml')) as handle:
                game_props = yaml.safe_load(handle)
            assert game_props['map_sets']['dungeon_2f']['label'] == 'Dungeon 2F'
            assert 'dungeon_2f' in game_props['map_sets']['dungeon_2f']['maps']
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_create_map_rejects_invalid_name_and_existing_map(self):
        tmpdir = tempfile.mkdtemp()
        try:
            session, _game, _othermaps = self._build_state(tmpdir)
            client = app.test_client()
            with client.session_transaction() as sess:
                sess['username'] = 'dm'

            response = client.post(
                '/create_map',
                data={'name': 'bad name!'},
                content_type='multipart/form-data',
            )
            assert response.status_code == 400

            # First create succeeds, second is rejected as existing
            assert client.post(
                '/create_map',
                data={'name': 'dup_map'},
                content_type='multipart/form-data',
            ).status_code == 200
            response = client.post(
                '/create_map',
                data={'name': 'dup_map'},
                content_type='multipart/form-data',
            )
            assert response.status_code == 400
            assert 'exists' in response.get_json().get('error', '').lower()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
