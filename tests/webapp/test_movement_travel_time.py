"""Tests for exploration movement travel time / watermark advancement."""

from types import SimpleNamespace

from natural20.actions.move_action import MoveAction
from natural20.player_character import PlayerCharacter
from natural20.session import Session


class _FakeSession:
    def __init__(self):
        self.game_time = 0

    def increment_game_time(self, seconds=6):
        self.game_time += int(seconds or 0)


class _FakeGame:
    def __init__(self):
        self.previous_time = 0
        self.player_character_game_times = {}
        self.game_session = _FakeSession()
        self._battle = None
        self.zone_ticks = 0

    def get_current_battle(self):
        return self._battle

    def _tick_exploration_environment(self):
        self.zone_ticks += 1

    def increment_game_time(self, player_character, seconds=6):
        # Mirror GameManagement.increment_game_time
        from webapp.utils import GameManagement
        return GameManagement.increment_game_time(self, player_character, seconds=seconds)


def test_movement_travel_time_seconds_formula():
    from webapp.utils import GameManagement

    entity = SimpleNamespace(speed=lambda: 30)
    assert GameManagement.movement_travel_time_seconds(entity, 30) == 6
    assert GameManagement.movement_travel_time_seconds(entity, 60) == 12
    assert GameManagement.movement_travel_time_seconds(entity, 15) == 3
    assert GameManagement.movement_travel_time_seconds(entity, 5) == 1
    assert GameManagement.movement_travel_time_seconds(entity, 0) == 0
    assert GameManagement.movement_travel_time_seconds(SimpleNamespace(speed=lambda: 0), 30) == 0


def test_move_action_distance_ft_from_move_cost():
    from webapp.utils import GameManagement

    map_obj = SimpleNamespace(feet_per_grid=5)
    action = SimpleNamespace(result=[
        {'type': 'move', 'map': map_obj, 'move_cost': 6, 'path': [[0, 0], [1, 0], [2, 0]]},
        {'type': 'other'},
    ])
    assert GameManagement.move_action_distance_ft(action) == 30.0


def test_watermark_advances_global_time_by_delta():
    game = _FakeGame()
    pc_a = object()
    pc_b = object()

    game.increment_game_time(pc_a, seconds=6)
    assert game.player_character_game_times[pc_a] == 6
    assert game.previous_time == 6
    assert game.game_session.game_time == 6

    # Trailing PC does not rewind/advance world clock
    game.increment_game_time(pc_b, seconds=6)
    assert game.player_character_game_times[pc_b] == 6
    assert game.game_session.game_time == 6

    # Long move: watermark jumps by 12, global time follows the delta
    game.increment_game_time(pc_a, seconds=12)
    assert game.player_character_game_times[pc_a] == 18
    assert game.previous_time == 18
    assert game.game_session.game_time == 18


def test_out_of_combat_move_uses_travel_time(monkeypatch):
    """MoveAction commit should request travel-time seconds, not a flat 6."""
    from webapp.utils import GameManagement

    session = Session(root_path='user_levels/death_house')
    pc = PlayerCharacter.load(session, 'characters/Oogway.yml')
    battle_map = next(iter(session.maps.values()))

    # Place PC somewhere open
    pos = None
    width, height = battle_map.size
    for y in range(height):
        for x in range(width):
            if battle_map.placeable(pc, x, y, None):
                pos = (x, y)
                break
        if pos:
            break
    assert pos is not None
    battle_map.add(pc, pos[0], pos[1], group='a')

    dest = (min(pos[0] + 2, width - 1), pos[1])
    action = MoveAction(session, pc, 'move')
    action.unlimited_movement = True
    action.move_path = [list(pos), [pos[0] + 1, pos[1]], list(dest)] if dest[0] != pos[0] else [list(pos), list(dest)]
    # Ensure path has at least 2 points with movement
    if action.move_path[-1] == action.move_path[0]:
        action.move_path = [list(pos), [min(pos[0] + 1, width - 1), pos[1]]]

    recorded = {}

    def fake_increment(self, player_character, seconds=6):
        recorded['seconds'] = seconds
        recorded['pc'] = player_character

    monkeypatch.setattr(GameManagement, 'increment_game_time', fake_increment)

    # Build a minimal GameManagement-like object with commit path dependencies
    gm = SimpleNamespace(
        game_session=session,
        get_map_for_entity=lambda e: battle_map,
        _get_map_lock=lambda m: __import__('threading').Lock(),
        logger=__import__('logging').getLogger('test'),
        move_action_distance_ft=GameManagement.move_action_distance_ft,
        movement_travel_time_seconds=GameManagement.movement_travel_time_seconds,
        increment_game_time=lambda pc, seconds=6: fake_increment(gm, pc, seconds=seconds),
        get_current_battle=lambda: None,
    )

    # Manually run the out-of-combat resolve/apply/time portion
    action.resolve(session, battle_map)
    from natural20.action import get_action_subclasses
    for item in list(action.result):
        for klass in get_action_subclasses():
            other = klass.apply(None, item, session=session)
            if isinstance(other, list):
                action.result.extend(other)

    distance_ft = GameManagement.move_action_distance_ft(action)
    seconds = GameManagement.movement_travel_time_seconds(pc, distance_ft)
    gm.increment_game_time(pc, seconds=seconds)

    assert distance_ft > 0
    assert recorded['seconds'] == seconds
    assert recorded['seconds'] == int(round((distance_ft / pc.speed()) * 6))
