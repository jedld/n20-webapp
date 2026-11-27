from unittest.mock import MagicMock

from webapp.utils import GameManagement


def _minimal_game_management():
    gm = GameManagement.__new__(GameManagement)
    gm.logger = MagicMock()
    gm.output_logger = MagicMock()
    gm.socketio = MagicMock()
    gm._game_loop_task_guard = __import__("threading").Lock()
    gm._game_loop_task_running = False
    gm._game_loop_task_token = 0
    gm._game_loop_task_started = 0.0
    gm.dm_turn_hold = None
    gm.waiting_for_reaction = False
    gm.get_current_battle = MagicMock(return_value=None)
    gm.game_state_lock = __import__("threading").Lock()
    gm._run_game_loop_once = MagicMock()
    gm._game_loop_background = MagicMock()
    return gm


def test_schedule_game_loop_skips_when_already_running():
    gm = _minimal_game_management()
    gm._game_loop_task_running = True

    assert gm.schedule_game_loop() is False
    gm.socketio.start_background_task.assert_not_called()


def test_schedule_game_loop_starts_background_task():
    gm = _minimal_game_management()

    assert gm.schedule_game_loop(battle_start=True) is True
    gm.socketio.start_background_task.assert_called_once()
    assert gm._game_loop_task_running is True


def test_execute_game_loop_blocking_runs_inline():
    gm = _minimal_game_management()
    gm._run_game_loop_once = MagicMock()

    assert gm.execute_game_loop(blocking=True) is True
    gm._run_game_loop_once.assert_called_once_with(battle_start=True)
    gm.socketio.start_background_task.assert_not_called()


def test_stalled_loop_task_is_revived():
    """A loop task that stopped advancing with no legitimate waiting state is
    replaced instead of blocking every later schedule forever."""
    gm = _minimal_game_management()
    gm._game_loop_task_running = True
    gm._game_loop_task_token = 7
    gm._game_loop_task_started = __import__("time").monotonic() - 10_000  # ancient (fresh CI runners have small monotonic clocks)
    gm.get_current_battle = MagicMock(return_value=MagicMock())

    assert gm.schedule_game_loop() is True
    gm.socketio.start_background_task.assert_called_once()
    kwargs = gm.socketio.start_background_task.call_args.kwargs
    assert kwargs["token"] == 8
    gm.logger.error.assert_called_once()

    # The wedged original task finishing late must not clear the new owner.
    gm._end_game_loop_task(7)
    assert gm._game_loop_task_running is True
    gm._end_game_loop_task(8)
    assert gm._game_loop_task_running is False


def test_stale_check_respects_legitimate_waiting_states():
    gm = _minimal_game_management()
    gm._game_loop_task_running = True
    gm._game_loop_task_started = __import__("time").monotonic() - 10_000
    gm.get_current_battle = MagicMock(return_value=MagicMock())

    gm.waiting_for_reaction = [MagicMock(), MagicMock(), None, []]
    assert gm.schedule_game_loop() is False
    gm.waiting_for_reaction = False

    gm.dm_turn_hold = "some-uid"
    assert gm.schedule_game_loop() is False
    gm.dm_turn_hold = None


def test_fresh_loop_task_is_not_stale():
    gm = _minimal_game_management()
    gm._game_loop_task_running = True
    gm._game_loop_task_started = __import__("time").monotonic()
    gm.get_current_battle = MagicMock(return_value=MagicMock())

    assert gm.schedule_game_loop() is False
    gm.socketio.start_background_task.assert_not_called()
