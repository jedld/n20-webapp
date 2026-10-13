"""Battle pacing: AI combatants wait for the DM to end their turn."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from natural20.web.web_controller import WebController
from webapp.battle_management import battle_admin
from webapp.utils import GameManagement


def _combatant(uid='orc-1'):
    entity = MagicMock()
    entity.entity_uid = uid
    entity.name = uid
    entity.label.return_value = uid.title()
    entity.dead.return_value = False
    entity.unconscious.return_value = False
    return entity


def _battle(current, controller=None):
    battle = MagicMock()
    battle.legendary_phase = None
    battle.current_turn.return_value = current
    battle.current_turn_index = 0
    battle.battle_ends.return_value = False
    battle.controller_for.return_value = controller if controller is not None else MagicMock()
    return battle


def _game(battle, *, pacing=None, game_properties=None, dm_online=True):
    gm = GameManagement.__new__(GameManagement)
    gm.logger = MagicMock()
    gm.socketio = MagicMock()
    gm._render_epoch = 0
    gm._render_epoch_by_user = {}
    gm.battle = battle
    gm.auto_battle = False
    gm.end_turn_state = False
    gm.dm_turn_hold = None
    gm.game_state_lock = threading.RLock()
    gm._game_loop_task_guard = threading.Lock()
    gm._game_loop_task_running = False
    state = {}
    if pacing is not None:
        state['battle_pacing'] = {'dm_ends_ai_turns': pacing}
    gm.game_session = SimpleNamespace(session_state=state, game_properties=game_properties or {})
    gm.username_to_sid = {'dm': ['sid-1']} if dm_online else {}
    gm._dm_usernames = MagicMock(return_value=['dm'])
    gm.get_current_battle = MagicMock(return_value=battle)
    gm.end_current_battle = MagicMock()
    gm.push_end_of_turn_animations = MagicMock()
    gm.begin_legendary_phase = MagicMock(return_value=False)
    gm.loop_environment = MagicMock()
    gm.emit_post_game_loop_updates = MagicMock()
    return gm


def _emitted_types(gm):
    return [c.args[1].get('type') for c in gm.socketio.emit.call_args_list
            if len(c.args) > 1 and isinstance(c.args[1], dict)]


def test_ai_turn_is_held_for_the_dm_instead_of_ending():
    current = _combatant()
    battle = _battle(current)
    gm = _game(battle, pacing=True)
    gm.ai_loop = MagicMock()

    gm.game_loop()

    gm.ai_loop.assert_called_once()
    battle.end_turn.assert_not_called()
    battle.next_turn.assert_not_called()
    assert gm.dm_turn_hold == 'orc-1'
    assert 'dm_turn_hold' in _emitted_types(gm)


def test_no_hold_when_pacing_is_off():
    battle = _battle(_combatant())
    battle.battle_ends.side_effect = lambda: battle.end_turn.called
    gm = _game(battle, pacing=False)
    gm.ai_loop = MagicMock()

    gm.game_loop()

    assert gm.dm_turn_hold is None
    battle.end_turn.assert_called_once()


def test_turn_is_held_until_a_dm_logs_in_even_with_no_dm_online():
    battle = _battle(_combatant())
    gm = _game(battle, pacing=True, dm_online=False)
    gm.ai_loop = MagicMock()

    gm.game_loop()

    assert gm.dm_turn_hold == 'orc-1'
    battle.end_turn.assert_not_called()


def test_human_controlled_turns_are_never_held():
    current = _combatant('pc-1')
    battle = _battle(current, controller=MagicMock(spec=WebController))
    gm = _game(battle, pacing=True)

    assert gm._hold_for_dm(battle, current) is False
    assert gm.dm_turn_hold is None


def test_campaign_flag_is_the_default_and_runtime_toggle_overrides_it():
    battle = _battle(_combatant())
    gm = _game(battle, game_properties={'battle_pacing': {'dm_ends_ai_turns': True}})
    assert gm.dm_ends_ai_turns() is True

    gm.set_dm_ends_ai_turns(False)
    assert gm.dm_ends_ai_turns() is False
    assert gm.game_session.session_state['battle_pacing'] == {'dm_ends_ai_turns': False}


def test_held_game_loop_does_not_replay_the_turn():
    battle = _battle(_combatant())
    gm = _game(battle, pacing=True)
    gm.dm_turn_hold = 'orc-1'
    gm.ai_loop = MagicMock()

    gm.game_loop()
    gm.resume_after_reaction()

    gm.ai_loop.assert_not_called()
    battle.start_turn.assert_not_called()


def test_releasing_the_hold_ends_the_turn_and_continues():
    battle = _battle(_combatant())
    gm = _game(battle, pacing=True)
    gm.dm_turn_hold = 'orc-1'
    gm._run_turns = MagicMock()
    gm.socketio.start_background_task.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    assert gm.release_dm_turn_hold() is True

    assert gm.dm_turn_hold is None
    battle.end_turn.assert_called_once()
    battle.next_turn.assert_called_once()
    gm._run_turns.assert_called_once()
    assert 'dm_turn_hold_end' in _emitted_types(gm)
    assert gm.game_loop_task_running() is False


def test_turning_pacing_off_releases_a_held_turn():
    battle = _battle(_combatant())
    gm = _game(battle, pacing=True)
    gm.dm_turn_hold = 'orc-1'
    gm.release_dm_turn_hold = MagicMock()

    gm.set_dm_ends_ai_turns(False)

    gm.release_dm_turn_hold.assert_called_once()


def test_battle_admin_pacing_and_next_turn_release():
    battle = _battle(_combatant())
    gm = _game(battle, pacing=False)
    gm.release_dm_turn_hold = MagicMock(return_value=True)

    result = battle_admin(gm, gm.game_session, 'pacing', enabled=True)
    assert result['dm_ends_ai_turns'] is True

    gm.dm_turn_hold = 'orc-1'
    result = battle_admin(gm, gm.game_session, 'next_turn')
    assert result['released_held_turn'] is True
    gm.release_dm_turn_hold.assert_called_once()
    battle.next_turn.assert_not_called()
