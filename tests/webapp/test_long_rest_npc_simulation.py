from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from webapp.long_rest_npc_simulation import (
    RealtimeUiSuppressor,
    _mentions_other,
    build_long_rest_system_context,
    commit_long_rest_core_memory,
    execute_long_rest_turn,
    find_related_npcs_in_range,
    iter_dialog_npcs,
    load_long_rest_sim_config,
    run_long_rest_npc_simulation,
    schedule_long_rest_npc_simulation,
    summarize_conversation_buffer,
)


class _Npc:
    is_npc = staticmethod(lambda: True)

    def __init__(self, uid, *, dialog=True, backstory='', journal=None):
        self.entity_uid = uid
        self.dialog = dialog
        self.properties = {'journal': journal or ''}
        self._backstory = backstory
        self.conversation_buffer = []

    def backstory(self):
        return self._backstory

    def description(self):
        return ''

    def clear_conversation_buffer(self):
        self.conversation_buffer = []


def test_load_long_rest_sim_config_defaults():
    session = SimpleNamespace(game_properties={})
    cfg = load_long_rest_sim_config(session)
    assert cfg['enabled'] is True
    assert cfg['llm_enabled'] is True
    assert cfg['loops_per_npc'] == 3
    assert cfg['conversation_range_ft'] == 60


def test_load_long_rest_sim_config_respects_background_llm_env(monkeypatch):
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    session = SimpleNamespace(game_properties={})
    cfg = load_long_rest_sim_config(session)
    assert cfg['llm_enabled'] is False


def test_load_long_rest_sim_config_from_game_yml():
    session = SimpleNamespace(game_properties={
        'long_rest_npc_simulation': {
            'enabled': True,
            'loops_per_npc': 5,
            'conversation_range_ft': 90,
        },
    })
    cfg = load_long_rest_sim_config(session)
    assert cfg['loops_per_npc'] == 5
    assert cfg['conversation_range_ft'] == 90


def test_mentions_other_detects_name_in_backstory():
    mara = _Npc('mara', backstory='Works closely with Sergeant Corin Vale on market patrols.')
    corin = _Npc('corin_vale_guard', backstory='Reports to Captain Thalen.')
    assert _mentions_other(mara.backstory().lower(), corin) is True


def test_find_related_npcs_in_range_filters_by_reachability():
    mara = _Npc('mara', backstory='Often talks with Garret about shipments.')
    garret = _Npc('garret', backstory='Mara and I share rumors from the docks.')
    stranger = _Npc('stranger')

    rag = MagicMock()
    rag.get_nearby_entities.return_value = [
        {'entity': garret, 'reachable_now': True},
        {'entity': stranger, 'reachable_now': True},
    ]

    related = find_related_npcs_in_range(mara, [mara, garret, stranger], rag, range_ft=60)
    assert garret in related
    assert stranger not in related


def test_iter_dialog_npcs_collects_unique_dialog_entities():
    npc = _Npc('tavern_keeper')
    quiet = _Npc('prop', dialog=False)
    battle_map = SimpleNamespace(entities=[npc, quiet, npc])
    game = SimpleNamespace(maps={'town': battle_map})
    assert iter_dialog_npcs(game) == [npc]


def test_realtime_ui_suppressor_is_reentrant():
    game = SimpleNamespace()
    assert RealtimeUiSuppressor.suppressed(game) is False
    with RealtimeUiSuppressor.suppress(game):
        assert RealtimeUiSuppressor.suppressed(game) is True
        with RealtimeUiSuppressor.suppress(game):
            assert RealtimeUiSuppressor.suppressed(game) is True
        assert RealtimeUiSuppressor.suppressed(game) is True
    assert RealtimeUiSuppressor.suppressed(game) is False


def test_advance_world_time_skips_emit_when_suppressed():
    import threading

    from webapp.utils import GameManagement

    gm = GameManagement.__new__(GameManagement)
    gm.game_session = SimpleNamespace(game_time=100, increment_game_time=MagicMock())
    gm.socketio = MagicMock()
    gm.autosave = False
    gm.game_state_lock = threading.Lock()
    gm.get_current_battle = MagicMock(return_value=None)
    gm.loop_environment = MagicMock()

    with RealtimeUiSuppressor.suppress(gm):
        gm.advance_world_time(seconds=6)
    gm.socketio.emit.assert_not_called()


def test_commit_long_rest_core_memory_heuristic_fallback():
    entity = _Npc('mara')
    rag = MagicMock()
    store = MagicMock()
    store.create.return_value = {'id': 'mem1'}
    rag._npc_memory_store.return_value = store

    created = commit_long_rest_core_memory(
        rag,
        entity,
        ['Mara closed the tavern.', 'Mara spoke with Garret about wool prices.'],
        game_time=3600,
        llm_handler=None,
    )
    assert created == {'id': 'mem1'}
    store.create.assert_called_once()
    kwargs = store.create.call_args.kwargs
    assert kwargs['source'] == 'long_rest'
    assert 'core_memory' in kwargs['tags']


def test_summarize_conversation_buffer_clears_short_buffers():
    entity = _Npc('mara')
    entity.conversation_buffer = [{'source': entity, 'message': 'hello'}]
    rag = MagicMock()
    rag._npc_memory_store.return_value = MagicMock()

    summarize_conversation_buffer(entity, rag, game_time=10, llm_handler=None)
    assert entity.conversation_buffer == []


def test_run_long_rest_npc_simulation_skips_during_battle():
    game = MagicMock()
    game.get_current_battle.return_value = object()
    game.game_session = SimpleNamespace(game_properties={})

    result = run_long_rest_npc_simulation(
        game,
        game_time=100,
        conversation_service=MagicMock(),
        rag_handler=MagicMock(),
        llm_handler=MagicMock(),
    )
    assert result['skipped'] is True
    assert result['reason'] == 'battle_active'


def test_run_long_rest_npc_simulation_skips_when_background_llm_disabled(monkeypatch):
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    game = MagicMock()
    game.get_current_battle.return_value = None
    game.game_session = SimpleNamespace(game_properties={})

    result = run_long_rest_npc_simulation(
        game,
        game_time=100,
        conversation_service=MagicMock(),
        rag_handler=MagicMock(),
        llm_handler=MagicMock(),
    )
    assert result == {'skipped': True, 'reason': 'background_llm_disabled'}


def test_schedule_long_rest_npc_simulation_skips_when_master_switch_off(monkeypatch):
    monkeypatch.setenv('N20_NPC_LOOPS', '0')
    game = MagicMock()
    game.game_session = SimpleNamespace(game_properties={})
    game.get_current_battle.return_value = None
    assert schedule_long_rest_npc_simulation(game, force=True) is False


@patch('webapp.long_rest_npc_simulation.iter_dialog_npcs', return_value=[])
def test_run_long_rest_npc_simulation_empty_map(_iter):
    game = MagicMock()
    game.get_current_battle.return_value = None
    game.game_session = SimpleNamespace(game_properties={'long_rest_npc_simulation': {'enabled': True}})

    result = run_long_rest_npc_simulation(
        game,
        game_time=100,
        conversation_service=MagicMock(),
        rag_handler=MagicMock(),
        llm_handler=MagicMock(),
    )
    assert result['npcs'] == 0


def test_execute_long_rest_turn_includes_identity_and_buffer_history():
    entity = _Npc('mara', backstory='Tavern keeper of the Rusty Flagon.')
    entity.conversation_buffer = [
        {'source': entity, 'message': 'Good evening.', 'language': 'common', 'target': None},
    ]
    entity.ability_scores = {'str': 10}
    entity.alignment = MagicMock(return_value='neutral_good')
    entity.languages = MagicMock(return_value=['common'])
    entity.label = MagicMock(return_value='Mara')

    rag = MagicMock()
    rag.goal_execution_prompt.return_value = 'Goal prompt block'
    rag.goal_turn_snapshot.return_value = 'Turn snapshot'
    rag.npc_recap_context_block.return_value = '\nMemories and logs recap\n'
    rag.build_conversation_response_plan.return_value = {'message': '', 'skip': True}
    rag.apply_response_plan_directives.return_value = {'executed_actions': []}

    conversation_service = MagicMock()
    conversation_service.build_npc_system_prompt.return_value = 'NPC identity and rules\n'

    llm = MagicMock()
    llm.conversations = {}
    llm.generate_response.return_value = '[NO_RESPONSE]'

    execute_long_rest_turn(
        rag,
        entity,
        partner=None,
        loop_index=0,
        llm_handler=llm,
        conversation_service=conversation_service,
        npc_prompt_template='template',
    )

    conversation_service.build_npc_system_prompt.assert_called_once()
    llm.create_conversation.assert_called_once()
    system_prompt = llm.create_conversation.call_args[0][1]
    assert 'LONG REST BACKGROUND SIMULATION' in system_prompt
    assert 'NPC identity and rules' in system_prompt
    assert 'Memories and logs recap' in system_prompt
    assert 'Goal prompt block' in system_prompt
    llm.update_conversation_history.assert_called_once_with(
        'mara:long_rest',
        entity.conversation_buffer,
    )


def test_build_long_rest_system_context_without_conversation_rules():
    entity = _Npc('mara')
    rag = MagicMock()
    rag.npc_recap_context_block.return_value = 'recap'
    conversation_service = MagicMock()
    conversation_service.build_npc_identity_prompt.return_value = 'identity'

    text = build_long_rest_system_context(
        conversation_service,
        rag,
        entity,
        prompt_template='tmpl',
        include_conversation_rules=False,
    )
    assert text == 'identityrecap'
    conversation_service.build_npc_identity_prompt.assert_called_once()
    conversation_service.build_npc_system_prompt.assert_not_called()
