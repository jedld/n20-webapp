"""Tests that equipping/unequipping a light source refreshes the map.

Regression: after a player equipped a torch in a dark room, the map did not
re-render with the new light source until a full page reload.  The root cause
was that ``/equip`` and ``/unequip`` mutated entity equipment without bumping
the ``/update`` render epoch, so the versioned render cache kept serving
pre-equip tile HTML even though the server emitted ``refresh_map``.

These tests assert that the equip/unequip routes (and the supporting
``recalc_lights_for_user`` helper) invalidate the render cache so the client's
subsequent ``/update`` request fetches fresh tile HTML.
"""
import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402


def _make_entity(uid='pc-1'):
    entity = Mock()
    entity.entity_uid = uid
    entity.label.return_value = 'Torch-Bearer'
    return entity


class TestRecalcLightsForUser:
    """Unit tests for GameManagement.recalc_lights_for_user."""

    def test_recomputes_lights_and_bumps_epoch(self):
        from webapp.utils import GameManagement

        game = GameManagement.__new__(GameManagement)
        game.maps = {
            'map_a': _map_stub(compute_side_effect=None),
            'map_b': _map_stub(compute_side_effect=None),
        }
        game._render_epoch = 0
        game._render_epoch_by_user = {}
        game.logger = Mock()
        calls = {'bump': 0}

        def _bump(username=None):
            calls['bump'] += 1
            # Mimic the real bump: advance the global epoch.
            game._render_epoch = game._render_epoch + 1
            if username is not None:
                game._render_epoch_by_user[str(username)] = max(
                    game._render_epoch, game._render_epoch_by_user.get(str(username), 0)
                )

        game.bump_render_epoch = _bump

        before = game.render_epoch_for_user('pc-1')
        game.recalc_lights_for_user('pc-1')
        after = game.render_epoch_for_user('pc-1')

        # Both maps must have had their light grid recomputed.
        assert game.maps['map_a']._compute_lights.called
        assert game.maps['map_b']._compute_lights.called
        # The epoch must have advanced for the user so /update re-renders.
        assert calls['bump'] == 1
        assert after > before

    def test_recalc_is_noop_when_maps_empty(self):
        from webapp.utils import GameManagement

        game = GameManagement.__new__(GameManagement)
        game.maps = {}
        game._render_epoch = 0
        game._render_epoch_by_user = {}
        game.logger = Mock()
        calls = {'bump': 0}
        game.bump_render_epoch = lambda username=None: calls.__setitem__('bump', calls['bump'] + 1)

        game.recalc_lights_for_user('pc-1')
        # Still bumps the epoch even when there are no maps to recompute.
        assert calls['bump'] == 1

    def test_recalc_survives_compute_exception(self):
        from webapp.utils import GameManagement

        game = GameManagement.__new__(GameManagement)
        game.maps = {'map_a': _map_stub(compute_side_effect=RuntimeError('boom'))}
        game._render_epoch = 0
        game._render_epoch_by_user = {}
        game.logger = Mock()
        calls = {'bump': 0}
        game.bump_render_epoch = lambda username=None: calls.__setitem__('bump', calls['bump'] + 1)

        # Must not raise even if a map's recompute fails.
        game.recalc_lights_for_user('pc-1')
        assert calls['bump'] == 1


def _map_stub(compute_side_effect=None):
    m = Mock()
    m.name = 'stub_map'
    side = compute_side_effect
    if side is None:
        m._compute_lights = Mock()
    else:
        m._compute_lights = Mock(side_effect=side)
    return m


class TestEquipUnequipRoutes:
    """Route-level tests that /equip and /unequip bump the render epoch."""

    @patch('webapp.blueprints.dm.get_socketio')
    @patch('webapp.blueprints.dm.get_current_game')
    def test_equip_bumps_epoch_and_refreshes(self, mock_current_game, mock_socketio):
        entity = _make_entity()
        battle_map = Mock()
        battle_map.entity_by_uid.return_value = entity
        game = Mock()
        game.get_map_for_user.return_value = battle_map
        mock_current_game.return_value = game

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'player1'
        response = client.post('/equip', data={
            'id': 'pc-1',
            'item_id': 'torch',
        })

        assert response.status_code == 200
        assert response.get_json() == {'status': 'ok'}
        entity.equip.assert_called_once_with('torch')
        # The render cache must be invalidated for the acting user.
        game.recalc_lights_for_user.assert_called_once_with('player1')
        # And a refresh_map event must be emitted to all clients.
        emitted_refresh = False
        for call in mock_socketio.return_value.emit.call_args_list:
            args = call[0]
            if len(args) >= 2 and args[0] == 'message' and isinstance(args[1], dict) and args[1].get('type') == 'refresh_map':
                emitted_refresh = True
                break
        assert emitted_refresh, 'expected a refresh_map message emit'

    @patch('webapp.blueprints.dm.get_socketio')
    @patch('webapp.blueprints.dm.get_current_game')
    def test_unequip_bumps_epoch_and_refreshes(self, mock_current_game, mock_socketio):
        entity = _make_entity()
        battle_map = Mock()
        battle_map.entity_by_uid.return_value = entity
        game = Mock()
        game.get_map_for_user.return_value = battle_map
        mock_current_game.return_value = game

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'player1'
        response = client.post('/unequip', data={
            'id': 'pc-1',
            'item_id': 'torch',
        })

        assert response.status_code == 200
        assert response.get_json() == {'status': 'ok'}
        entity.unequip.assert_called_once_with('torch')
        game.recalc_lights_for_user.assert_called_once_with('player1')

    @patch('webapp.blueprints.dm.get_socketio')
    @patch('webapp.blueprints.dm.get_current_game')
    def test_equip_unknown_entity_404(self, mock_current_game, mock_socketio):
        battle_map = Mock()
        battle_map.entity_by_uid.return_value = None
        game = Mock()
        game.get_map_for_user.return_value = battle_map
        mock_current_game.return_value = game

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'player1'
        response = client.post('/equip', data={'id': 'ghost', 'item_id': 'torch'})

        assert response.status_code == 404
        # No recalc / refresh should occur for a missing entity.
        game.recalc_lights_for_user.assert_not_called()
