"""Tests for out-of-combat NPC environment ticks."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from webapp.npc_environment_ticks import (
    _npc_eligible,
    load_npc_environment_tick_config,
    run_npc_environment_tick,
    run_npc_environment_ticks,
    schedule_npc_environment_ticks,
)


class _Npc:
    is_npc = staticmethod(lambda: True)

    def __init__(self, uid, *, dialog=True):
        self.entity_uid = uid
        self.dialog = dialog
        self.conversation_buffer = []

    def conscious(self):
        return True

    def dead(self):
        return False


def test_load_npc_environment_tick_config_defaults():
    session = SimpleNamespace(game_properties={})
    cfg = load_npc_environment_tick_config(session)
    assert cfg['enabled'] is True
    assert cfg['llm_enabled'] is True


def test_load_npc_environment_tick_config_respects_background_llm_env(monkeypatch):
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    session = SimpleNamespace(game_properties={})
    cfg = load_npc_environment_tick_config(session)
    assert cfg['llm_enabled'] is False


def test_run_npc_environment_ticks_skips_when_background_llm_disabled(monkeypatch):
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    game = MagicMock()
    game.get_current_battle.return_value = None
    game.game_session = SimpleNamespace(game_properties={})

    result = run_npc_environment_ticks(game)

    assert result == {'skipped': True, 'reason': 'background_llm_disabled'}


def test_npc_eligible_requires_dialog():
    assert _npc_eligible(_Npc('a')) is True
    assert _npc_eligible(_Npc('b', dialog=False)) is False


def test_run_npc_environment_tick_prefers_movement():
    npc = _Npc('mara')
    game = MagicMock()
    game.get_npc_movement_task.return_value = {'status': 'active'}
    game.get_short_term_goal.return_value = None
    rag = MagicMock()
    rag.execute_scheduled_movement.return_value = {'executed_actions': ['move_to']}
    llm = MagicMock()

    outcome = run_npc_environment_tick(game, npc, rag_handler=rag, llm_handler=llm)
    assert outcome['kind'] == 'movement'
    rag.execute_scheduled_movement.assert_called_once_with(
        'mara',
        llm,
        advance_time=False,
    )


def test_run_npc_environment_tick_runs_goal_when_no_movement():
    npc = _Npc('mara')
    game = MagicMock()
    game.get_npc_movement_task.return_value = None
    game.get_short_term_goal.return_value = {'status': 'active'}
    rag = MagicMock()
    rag.execute_scheduled_goal.return_value = {'executed_actions': []}
    llm = MagicMock()

    outcome = run_npc_environment_tick(game, npc, rag_handler=rag, llm_handler=llm)
    assert outcome['kind'] == 'goal'
    rag.execute_scheduled_goal.assert_called_once_with(
        'mara',
        llm,
        advance_time=False,
    )


@patch('webapp.npc_environment_ticks.execute_npc_ambient_tick')
def test_run_npc_environment_tick_ambient_fallback(mock_ambient):
    npc = _Npc('mara')
    game = MagicMock()
    game.get_npc_movement_task.return_value = None
    game.get_short_term_goal.return_value = None
    mock_ambient.return_value = {'directive_result': {}}
    rag = MagicMock()

    outcome = run_npc_environment_tick(game, npc, rag_handler=rag, llm_handler=MagicMock())
    assert outcome['kind'] == 'ambient'
    mock_ambient.assert_called_once()


def test_run_npc_environment_ticks_skips_during_battle():
    game = MagicMock()
    game.get_current_battle.return_value = object()
    game.game_session = SimpleNamespace(game_properties={})
    result = run_npc_environment_ticks(game)
    assert result['skipped'] is True
    assert result['reason'] == 'battle_active'


def test_schedule_npc_environment_ticks_skips_when_master_switch_off(monkeypatch):
    monkeypatch.setenv('N20_NPC_LOOPS', '0')
    game = SimpleNamespace(
        game_session=SimpleNamespace(game_properties={}),
        get_current_battle=lambda: None,
        realtime_ui_suppressed=lambda: False,
    )
    assert schedule_npc_environment_ticks(game) is False


@patch('webapp.npc_environment_ticks.run_npc_environment_ticks')
def test_schedule_npc_environment_ticks_queues_background(mock_run):
    game = MagicMock()
    game.get_current_battle.return_value = None
    game.realtime_ui_suppressed.return_value = False
    game.game_session = SimpleNamespace(game_properties={})
    game.socketio.start_background_task.side_effect = lambda fn: fn()

    assert schedule_npc_environment_ticks(game) is True
    mock_run.assert_called_once_with(game)
