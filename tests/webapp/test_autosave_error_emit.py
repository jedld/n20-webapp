"""Tests for DM-facing autosave error toasters.

When autosave is enabled and a background save fails, the webapp must emit
an ``autosave_error`` SocketIO message (carrying full error details) only to
logged-in DMs, so the VTT can show an error toaster to the DM.
"""

import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webapp.blueprints.helpers import runtime_state
from webapp.utils import GameManagement


@pytest.fixture
def _restore_runtime_logins():
    """Snapshot/restore the runtime_state LOGINS global across a test."""
    saved = runtime_state._globals.get('LOGINS', 'MISSING')
    yield
    if saved == 'MISSING':
        runtime_state._globals.pop('LOGINS', None)
    else:
        runtime_state._globals['LOGINS'] = saved


def _make_minimal_gm():
    """Construct a GameManagement-like shell without running __init__."""
    gm = GameManagement.__new__(GameManagement)
    gm.logger = MagicMock()
    gm.socketio = MagicMock()
    gm.autosave = True
    gm.save_dir = '/tmp/saves'
    gm.username_to_sid = {
        'dm': ['sid-dm-1'],
        'alice': ['sid-alice-1', 'sid-alice-2'],
    }
    return gm


def test_emit_autosave_error_only_to_logged_in_dm(_restore_runtime_logins):
    runtime_state.register_globals(LOGINS=[
        {'name': 'dm', 'role': ['dm']},
        {'name': 'alice', 'role': ['player']},
    ])
    gm = _make_minimal_gm()

    gm._emit_autosave_error(ValueError('disk full'), name='autosave')

    assert gm.socketio.emit.call_count == 1
    call = gm.socketio.emit.call_args
    event_name, payload = call.args[0], call.args[1]
    assert event_name == 'message'
    assert payload['type'] == 'autosave_error'
    msg = payload['message']
    assert msg['title'] == 'Autosave failed'
    assert msg['error_type'] == 'ValueError'
    assert msg['error'] == 'disk full'
    assert msg['target_file'] == 'autosave'
    assert 'ValueError: disk full' in msg['traceback']
    assert call.kwargs.get('to') == ['sid-dm-1']


def test_emit_autosave_error_no_dm_logged_in(_restore_runtime_logins):
    runtime_state.register_globals(LOGINS=[
        {'name': 'alice', 'role': ['player']},
    ])
    gm = _make_minimal_gm()

    gm._emit_autosave_error(RuntimeError('boom'))

    gm.socketio.emit.assert_not_called()


def test_emit_autosave_error_no_dm_socket_connected(_restore_runtime_logins):
    runtime_state.register_globals(LOGINS=[
        {'name': 'dm', 'role': ['dm']},
    ])
    gm = _make_minimal_gm()
    gm.username_to_sid = {'alice': ['sid-alice-1']}  # DM logged in elsewhere/absent

    gm._emit_autosave_error(RuntimeError('boom'))

    gm.socketio.emit.assert_not_called()


def test_dm_usernames_fallback_prefix(_restore_runtime_logins):
    # No LOGINS registered (e.g., standalone/tests): fall back to the
    # conventional 'dm' username prefix present in username_to_sid.
    runtime_state._globals.pop('LOGINS', None)
    gm = _make_minimal_gm()

    assert gm._dm_usernames() == ['dm']


def test_save_worker_emits_autosave_error_on_failure(_restore_runtime_logins):
    runtime_state.register_globals(LOGINS=[
        {'name': 'dm', 'role': ['dm']},
    ])
    gm = _make_minimal_gm()
    gm._save_queue = queue.Queue(maxsize=50)
    gm._SAVE_SENTINEL = object()
    gm._prepare_save = MagicMock(side_effect=OSError('cannot serialize state'))

    gm._save_queue.put('autosave')
    gm._save_queue.put(gm._SAVE_SENTINEL)

    gm._save_worker()  # processes the request, fails, emits, then stops at sentinel

    gm.logger.exception.assert_called()
    assert gm.socketio.emit.call_count == 1
    event_name, payload = gm.socketio.emit.call_args.args[0], gm.socketio.emit.call_args.args[1]
    assert event_name == 'message'
    assert payload['type'] == 'autosave_error'
    assert payload['message']['error_type'] == 'OSError'
    assert payload['message']['error'] == 'cannot serialize state'
    assert 'OSError: cannot serialize state' in payload['message']['traceback']
    assert gm.socketio.emit.call_args.kwargs.get('to') == ['sid-dm-1']


def test_save_game_async_fallback_failure_emits_and_raises(_restore_runtime_logins):
    runtime_state.register_globals(LOGINS=[
        {'name': 'dm', 'role': ['dm']},
    ])
    gm = _make_minimal_gm()
    # Queueing fails (simulate a broken queue) ...
    gm._save_queue = SimpleNamespace(full=lambda: False,
                                     get_nowait=MagicMock(side_effect=RuntimeError('queue down')),
                                     put_nowait=MagicMock(side_effect=RuntimeError('queue down')))
    # ... and the synchronous fallback also fails.
    gm.save_game = MagicMock(side_effect=OSError('sync save failed'))

    with pytest.raises(OSError):
        gm.save_game_async()

    assert gm.socketio.emit.call_count == 1
    payload = gm.socketio.emit.call_args.args[1]
    assert payload['type'] == 'autosave_error'
    assert payload['message']['error_type'] == 'OSError'
    assert payload['message']['error'] == 'sync save failed'
    assert gm.socketio.emit.call_args.kwargs.get('to') == ['sid-dm-1']
