from unittest.mock import MagicMock

from natural20.web.web_controller import ManualControl
from webapp.utils import GameManagement


def _combatant(dead=False, unconscious=False):
    entity = MagicMock()
    entity.dead.return_value = dead
    entity.unconscious.return_value = unconscious
    # Must be falsy: a truthy MagicMock would route the turn through the
    # incapacitated-skip branch instead of ai_loop.
    entity.incapacitated.return_value = False
    return entity


def _game(battle):
    gm = GameManagement.__new__(GameManagement)
    gm.logger = MagicMock()
    gm.socketio = MagicMock()
    gm._render_epoch = 0
    gm._render_epoch_by_user = {}
    gm.battle = battle
    gm.auto_battle = False
    gm.end_turn_state = False
    gm.get_current_battle = MagicMock(return_value=battle)
    gm.end_current_battle = MagicMock()
    gm.push_end_of_turn_animations = MagicMock()
    gm.begin_legendary_phase = MagicMock(return_value=False)
    return gm


def _battle(current):
    battle = MagicMock()
    battle.legendary_phase = None
    battle.current_turn.return_value = current
    battle.current_turn_index = 0
    battle.battle_ends.return_value = False
    return battle


def test_reaction_during_end_of_turn_moves_on_without_replaying_the_turn():
    dead = _combatant(dead=True)
    battle = _battle(dead)
    gm = _game(battle)
    gm.end_turn_state = True
    gm.ai_loop = MagicMock()
    gm._run_turns = MagicMock()

    gm.resume_after_reaction()

    gm.ai_loop.assert_not_called()
    battle.start_turn.assert_not_called()
    battle.end_turn.assert_not_called()
    battle.next_turn.assert_called_once()
    gm._run_turns.assert_called_once()
    assert gm.end_turn_state is False


def test_reaction_that_ends_the_battle_ends_it():
    battle = _battle(_combatant(dead=True))
    battle.battle_ends.return_value = True
    gm = _game(battle)
    gm.end_turn_state = True
    gm.ai_loop = MagicMock()

    gm.resume_after_reaction()

    gm.end_current_battle.assert_called_once()
    gm.ai_loop.assert_not_called()
    battle.next_turn.assert_not_called()


def test_mid_turn_reaction_lets_the_ai_finish_then_ends_that_turn_once():
    current = _combatant()
    battle = _battle(current)
    gm = _game(battle)
    gm.ai_loop = MagicMock()
    gm._run_turns = MagicMock()

    gm.resume_after_reaction()

    gm.ai_loop.assert_called_once()
    battle.start_turn.assert_not_called()
    current.reset_turn.assert_not_called()
    battle.end_turn.assert_called_once()
    battle.next_turn.assert_called_once()
    gm._run_turns.assert_called_once()


def test_mid_turn_reaction_hands_control_back_to_a_human():
    battle = _battle(_combatant())
    gm = _game(battle)
    gm.ai_loop = MagicMock(side_effect=ManualControl())

    gm.resume_after_reaction()

    battle.end_turn.assert_not_called()
    gm.logger.info.assert_called_with('waiting for user to end turn.')


def test_game_loop_skips_dead_combatants_before_starting_a_turn():
    current = _combatant()
    battle = _battle(current)
    gm = _game(battle)
    gm.ai_loop = MagicMock(side_effect=ManualControl())

    gm.game_loop()

    battle.skip_dead_turns.assert_called_once()
    battle.start_turn.assert_called_once()
