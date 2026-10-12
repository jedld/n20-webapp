"""Regression: edit-mode placements must invalidate the /update render cache.

Every edit endpoint rewrites the map YAML and calls
``GameManagement.reload_map_by_name``. The /update route caches rendered tile
HTML keyed by the render epoch, so without an epoch bump the first drop renders
and every later drop is served the stale cached HTML.
"""
import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)


def test_reload_map_by_name_bumps_render_epoch():
    from webapp.utils import GameManagement

    game = GameManagement.__new__(GameManagement)
    game.game_session = Mock()
    game.other_maps = {'crypt': 'maps/crypt'}
    game.maps = {'crypt': Mock(name='old_map')}
    game.battle_map = None
    game.current_map_for_user = {'dm': ('crypt', game.maps['crypt'])}
    game.entity_registry = Mock()
    game._render_epoch = 3
    game._render_epoch_by_user = {}

    new_map = Mock(name='new_map')
    with patch('webapp.utils.Map', return_value=new_map):
        game.reload_map_by_name('crypt')

    assert game.maps['crypt'] is new_map
    assert game.current_map_for_user['dm'] == ('crypt', new_map)
    assert game.render_epoch_for_user('dm') > 3
