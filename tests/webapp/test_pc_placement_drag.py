"""PC drag-and-drop placement tests (DM, not in edit mode).

Dragging a PC from the party tray onto the VTT map when the PC has no token
on any map must *spawn* it, not 404. Regression test for the ``/move_entity``
"Entity not found" failure (the tray lists every campaign character sheet,
but only placed PCs are registered entities).
"""

import os
import sys
from unittest.mock import Mock, patch

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

ENGINE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
template_root = os.path.join(ENGINE_ROOT, 'templates')
os.environ.setdefault('TEMPLATE_DIR', template_root)

FIXTURES = os.path.join(ENGINE_ROOT, 'tests', 'fixtures')

from webapp.app import app  # noqa: E402
from webapp.blueprints.helpers import runtime_state  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.session import Session  # noqa: E402

PC_UID = 'high_elf_mage'


@pytest.fixture
def world():
    """A real Session + Map with no PC token placed yet, plus a mock game."""
    session = Session(root_path=FIXTURES, event_manager=EventManager())
    battle_map = Map(session, 'battle_sim.yml')
    # Make sure the PC is not on the map.
    assert battle_map.entity_by_uid(PC_UID) is None

    game = Mock()
    game.get_map_for_user.return_value = battle_map
    game.deferred_players = {}

    def _entity_by_uid(uid):
        for ent in battle_map.entities:
            if str(getattr(ent, 'entity_uid', '')) == str(uid):
                return ent
        return None

    game.get_entity_by_uid.side_effect = _entity_by_uid
    return session, game, battle_map


def _dm_client():
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'dm'
    return client


def _patches(session, game):
    socketio = Mock()
    return [
        patch.dict(runtime_state._globals, {
            'current_game': game,
            'game_session': session,
            'socketio': socketio,
        }),
        patch('webapp.blueprints.dm.get_socketio', return_value=socketio),
        patch('webapp.blueprints.dm.user_role', new=lambda: ['dm']),
        # Keep the selectable-character materializer out of the sheet path.
        patch('webapp.blueprints.helpers.pvp.ensure_character_entity_loaded',
              return_value=None),
    ]


def test_drag_spawns_pc_with_no_token(world):
    session, game, battle_map = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        response = client.post('/move_entity',
                               json={'entity_uid': PC_UID, 'x': 1, 'y': 2})
        assert response.status_code == 200, response.get_json()
        assert response.get_json().get('spawned') is True
        placed = battle_map.entity_at(1, 2)
        assert placed is not None
        assert str(placed.entity_uid) == PC_UID
        # Registered for later lookups (second drag is a plain move).
        assert game.get_entity_by_uid(PC_UID) is placed
    finally:
        for p in patches:
            p.stop()


def test_second_drag_moves_the_same_token(world):
    session, game, battle_map = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        assert client.post('/move_entity',
                           json={'entity_uid': PC_UID, 'x': 1, 'y': 2}).status_code == 200
        response = client.post('/move_entity',
                               json={'entity_uid': PC_UID, 'x': 4, 'y': 2})
        assert response.status_code == 200, response.get_json()
        assert response.get_json().get('spawned') is None
        assert battle_map.entity_at(1, 2) is None
        moved = battle_map.entity_at(4, 2)
        assert moved is not None and str(moved.entity_uid) == PC_UID
    finally:
        for p in patches:
            p.stop()


def test_drag_unknown_uid_rejected(world):
    session, game, battle_map = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        response = client.post('/move_entity',
                               json={'entity_uid': 'who_is_this', 'x': 1, 'y': 2})
        assert response.status_code == 400
        assert 'not found' in (response.get_json() or {}).get('error', '').lower()
        assert battle_map.entity_at(1, 2) is None
    finally:
        for p in patches:
            p.stop()


def test_spawn_npc_bumps_render_epoch(world):
    """Placements must advance the /update render-cache epoch.

    Without the bump the server keeps serving the pre-spawn cached map
    HTML after refresh_map: the new token never renders, and later
    refreshes re-render the stale snapshot and visually drop tokens that
    were added at runtime.
    """
    session, game, battle_map = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        response = client.post('/spawn_npc',
                               json={'npc_type': 'boar', 'x': 1, 'y': 0})
        assert response.status_code == 200, response.get_json()
        game.bump_render_epoch.assert_called()
        assert battle_map.entity_at(1, 0) is not None
    finally:
        for p in patches:
            p.stop()


def test_spawned_token_survives_update_render_cache(world):
    """End-to-end: spawn two NPCs and rebuild the /update cache each time.

    Reproduces the reported symptom where dropping a second NPC made the
    first one vanish: the cached HTML under the old epoch had neither.
    """
    from webapp.blueprints.navigation import _UPDATE_RESPONSE_CACHE

    session, game, battle_map = world
    client = _dm_client()
    patches = _patches(session, game)
    for p in patches:
        p.start()
    try:
        epoch = [0]
        game.render_epoch_for_user.side_effect = lambda user=None: epoch[0]

        def _bump(*a, **k):
            epoch[0] += 1
        game.bump_render_epoch.side_effect = _bump

        assert client.post('/spawn_npc',
                           json={'npc_type': 'boar', 'x': 1, 'y': 0}).status_code == 200
        assert epoch[0] == 1
        assert client.post('/spawn_npc',
                           json={'npc_type': 'bat', 'x': 1, 'y': 1}).status_code == 200
        assert epoch[0] == 2
        # Both tokens are on the server map and survive the cache-key change.
        assert battle_map.entity_at(1, 0) is not None
        assert battle_map.entity_at(1, 1) is not None
        _UPDATE_RESPONSE_CACHE.clear()
    finally:
        for p in patches:
            p.stop()
