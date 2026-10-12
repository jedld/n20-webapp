from unittest.mock import MagicMock

from webapp.utils import GameManagement


def _game(animation_log):
    battle = MagicMock()
    battle.get_animation_logs.return_value = animation_log
    gm = GameManagement.__new__(GameManagement)
    gm.socketio = MagicMock()
    gm._render_epoch = 0
    gm._render_epoch_by_user = {}
    gm.get_current_battle = MagicMock(return_value=battle)
    return gm, battle


def test_legendary_move_animates_without_an_early_map_refresh():
    path = [[35, 10], [34, 11], [33, 12]]
    gm, battle = _game([['npc_strahd', path, {'type': 'move'}]])

    gm.push_end_of_turn_animations(battle)

    emitted = [call.args[1] for call in gm.socketio.emit.call_args_list]
    assert [m['type'] for m in emitted] == ['move']
    assert emitted[0]['message']['animation_log'][0][1] == path
    battle.clear_animation_logs.assert_called_once()


def test_end_of_turn_invalidates_cached_map_renders():
    gm, battle = _game([])
    before = gm.render_epoch_for_user('player1')

    gm.push_end_of_turn_animations(battle)

    assert gm.render_epoch_for_user('player1') > before
    gm.socketio.emit.assert_not_called()
