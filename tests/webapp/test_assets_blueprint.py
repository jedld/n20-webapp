"""Smoke tests for the assets blueprint routes."""
import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402
from webapp.blueprints.assets import (  # noqa: E402
    _resolve_map_background_path,
    _resolve_map_preview_path,
)


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
            os.path.join(os.path.dirname(__file__), '..', '..', 'user_levels', 'wild_sheep_chase')
        )

        class FakeSession:
            root_path = campaign_root

        full_path, _directory, resolved_name = _resolve_map_background_path(
            FakeSession(),
            'map_woodland_path_bg.jpg',
        )
        assert full_path
        assert resolved_name == 'map_woodland_path_bg.jpg'

    def test_resolve_map_preview_uses_map_background_image(self):
        campaign_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'user_levels', 'wild_sheep_chase')
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
