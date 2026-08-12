"""Path preview cost accounting must match exploration MoveAction budgets."""

from natural20.map import Map
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from natural20.utils.movement import compute_actual_moves

from webapp.blueprints.navigation import (
    _exploration_move_budget_squares,
    _path_response_movement_cost,
)


def test_exploration_budget_matches_move_action_formula():
    session = Session(root_path='tests/fixtures')
    battle_map = Map(session, 'path_finding_test')
    mx, my = battle_map.size
    assert _exploration_move_budget_squares(battle_map) == (mx + my) * 4


def test_exploration_long_path_cost_stays_non_negative():
    """Hover cost must not go negative for paths longer than combat speed.

    Previously /path used battle_map.movement_cost() which budgets by speed(),
    so a long exploration path reported budget<0 and the UI dropped the
    indicator even though MoveAction(unlimited_movement) would commit it.
    """
    session = Session(root_path='tests/fixtures')
    battle_map = Map(session, 'large_map')
    pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    battle_map.add(pc, 0, 0)

    # Straight path well beyond a typical 30-ft combat move (6 squares).
    path = [[x, 0] for x in range(0, 25)]
    assert len(path) - 1 > pc.speed() // battle_map.feet_per_grid

    legacy = battle_map.movement_cost(pc, path, battle=None)
    assert legacy.budget < 0

    cost = _path_response_movement_cost(
        battle_map, pc, path, battle=None, can_move=True, exploration=True
    )
    assert cost.budget >= 0
    assert cost.cost == len(path) - 1

    move_budget = _exploration_move_budget_squares(battle_map)
    expected = compute_actual_moves(
        pc, path, battle_map, None, move_budget, test_placement=False
    )
    assert cost.budget == expected.budget
    assert cost.original_budget == expected.original_budget


def test_cannot_move_reports_zero_budget():
    session = Session(root_path='tests/fixtures')
    battle_map = Map(session, 'path_finding_test')
    pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    battle_map.add(pc, 0, 0)
    path = [[0, 0], [1, 0], [2, 0]]
    cost = _path_response_movement_cost(
        battle_map, pc, path, battle=None, can_move=False, exploration=True
    )
    assert cost.budget == 0
    assert cost.original_budget == 0
    assert cost.impediment == 'cannot_move'
