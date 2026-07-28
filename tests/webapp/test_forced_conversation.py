"""Tests for proximity-triggered forced conversations."""

from unittest.mock import Mock, patch

from webapp.forced_conversation import (
    ForcedConversationManager,
    build_detection_notes,
    build_session_state_notes,
    forced_conversation_config,
    resolve_effective_config,
    resolve_forced_conversation_setting,
)


class _StubNpc:
    def __init__(self, uid, label='NPC', *, group='b', config=None):
        self.entity_uid = uid
        self._label = label
        self.group = group
        self.properties = {'forced_conversation': config} if config else {}

    def label(self):
        return self._label

    def update_state(self, _state):
        return None


class _StubPc:
    def __init__(self, uid):
        self.entity_uid = uid


def _build_manager():
    game_manager = Mock()
    game_manager.get_current_battle.return_value = None
    game_manager.get_entity_by_uid.side_effect = lambda uid: _StubPc(uid)
    game_manager.entity_owners.return_value = ['player1']
    game_manager.game_session = Mock()
    game_manager.game_session.session_state = {}
    game_manager.game_session.game_properties = {
        'forced_conversation_defaults': {'volume': 'normal', 'distance_ft': 30},
        'forced_conversations': {
            'guz': {
                'resolution': {
                    'hostile_keywords': ['refuse'],
                    'friendly_keywords': ['hand over the sheep'],
                    'friendly_group': 'c',
                },
            },
        },
    }
    game_manager.game_session.update_state = lambda state: game_manager.game_session.session_state.update(state)

    conversation_service = Mock()
    conversation_service.entity_rag_handler = Mock()
    conversation_service.entity_rag_handler.get_nearby_entities.return_value = [
        {'entity': _StubPc('pc-1'), 'reachable_now': True},
    ]
    conversation_service._sids_for_username.return_value = ['sid-1']
    conversation_service.socketio = Mock()

    manager = ForcedConversationManager(
        game_manager,
        conversation_service_getter=lambda: conversation_service,
    )
    return manager, game_manager, conversation_service


def test_forced_conversation_config_requires_mapping():
    assert forced_conversation_config(_StubNpc('x')) is None
    assert forced_conversation_config(_StubNpc('x', config={'enabled': True})) is not None
    assert forced_conversation_config(_StubNpc('x', config={'enabled': False})) is None


def test_check_after_move_starts_forced_conversation():
    manager, game_manager, conversation_service = _build_manager()
    npc = _StubNpc(
        'guz',
        config={
            'enabled': True,
            'once_session_key': 'guz_scene',
            'opening': 'initial_statement',
            'initial_statement': 'Hand over the sheep!',
        },
    )
    pc = _StubPc('pc-1')
    conversation_service.entity_rag_handler.get_nearby_entities.return_value = [
        {'entity': pc, 'reachable_now': True},
    ]
    battle_map = Mock()
    battle_map.name = 'town_market'
    battle_map.entities = {npc: [4, 22]}
    battle_map.can_see.return_value = True
    game_manager.get_map_for_entity.return_value = battle_map

    with patch('webapp.forced_conversation.is_player_character', return_value=True), patch.object(manager, 'start') as start_mock:
        manager.check_after_move(pc)

    start_mock.assert_called_once()


def test_block_action_prevents_locked_pc_movement():
    manager, game_manager, _conversation_service = _build_manager()
    manager.session.session_state['forced_conversation'] = {
        'active': True,
        'participant_pc_uids': ['pc-1'],
    }
    action = Mock()
    action.action_type = 'move'
    action.source = _StubPc('pc-1')
    assert manager.block_action(action) == 'Movement is restricted during this conversation.'


def test_maybe_resolve_hostile_ends_session():
    manager, game_manager, conversation_service = _build_manager()
    npc = _StubNpc('guz')
    manager.session.session_state['forced_conversation'] = {
        'active': True,
        'npc_uid': 'guz',
        'participant_pc_uids': ['pc-1'],
        'config': {
            'resolution': {
                'hostile_keywords': ['refuse'],
            },
        },
    }
    conversation_service.entity_rag_handler._handle_hostile_state_change = Mock()

    assert manager.maybe_resolve_after_npc_reply(npc, 'I refuse!', speaker=_StubPc('pc-1')) is True
    assert 'forced_conversation' not in manager.session.session_state
    conversation_service.entity_rag_handler._handle_hostile_state_change.assert_called_once_with(npc)


def test_resolve_public_setting_from_map():
    game_session = Mock()
    game_session.game_properties = {
        'forced_conversations': {
            'guz': {
                'settings': {
                    'public': {'context': 'public demand'},
                    'private': {'context': 'door hammer'},
                },
            },
        },
    }
    battle_map = Mock()
    battle_map.name = 'town_market'
    battle_map.properties = {'forced_conversation_setting': 'public'}
    npc = _StubNpc('guz', config={'enabled': True})
    effective = resolve_effective_config(npc.properties['forced_conversation'], battle_map, game_session, npc)
    assert effective['context'] == 'public demand'
    assert effective['_setting'] == 'public'


def test_build_detection_notes_when_finethir_present():
    sheep = _StubNpc('finethir_shinebright')
    sheep.properties = {'sub_type': 'finethir_sheep'}
    battle_map = Mock()
    battle_map.entities = {sheep: [11, 22]}
    config = {
        'detect_targets': [
            {
                'entity_uid': 'finethir_shinebright',
                'presence_note': 'The sheep is here.',
            },
        ],
    }
    note = build_detection_notes(config, battle_map, Mock())
    assert 'sheep is here' in note


def test_after_npc_reply_escalates_when_impatient():
    manager, game_manager, conversation_service = _build_manager()
    npc = _StubNpc('guz')
    manager.session.session_state['forced_conversation'] = {
        'active': True,
        'npc_uid': 'guz',
        'participant_pc_uids': ['pc-1'],
        'player_reply_count': 5,
        'config': {
            'impatience': {'max_player_replies': 5},
            'resolution': {'hostile_group': 'b'},
        },
    }
    conversation_service.entity_rag_handler._handle_hostile_state_change = Mock()
    with patch.object(manager, '_apply_hostile_reveals'):
        manager.after_npc_reply(npc)
    assert 'forced_conversation' not in manager.session.session_state


def test_build_session_state_notes_when_flag_set():
    session = Mock()
    session.session_state = {'wild_sheep_guz_killed': True}
    note = build_session_state_notes({
        'session_state_notes': [{
            'key': 'wild_sheep_guz_killed',
            'note': 'Guz is dead.',
        }],
    }, session)
    assert 'Guz is dead' in note


def test_build_session_state_notes_absent_flag():
    session = Mock()
    session.session_state = {}
    note = build_session_state_notes({
        'session_state_notes': [{
            'key': 'wild_sheep_guz_killed',
            'note': 'Guz is dead.',
        }],
    }, session)
    assert note == ''


def test_initial_statement_injects_merged_context():
    manager, game_manager, conversation_service = _build_manager()
    npc = _StubNpc(
        'ahmed_noke',
        config={
            'enabled': True,
            'opening': 'initial_statement',
            'initial_statement': 'Return the sheep.',
            'context': 'Demand the sheep.',
        },
    )
    pc = _StubPc('pc-1')
    battle_map = Mock()
    battle_map.name = 'treehouse_interior'
    battle_map.entities = {npc: [6, 4]}
    battle_map.can_see.return_value = True
    conversation_service.inject_npc_llm_system_note = Mock()

    with patch.object(manager, '_speak_initial_statement'), patch.object(manager, '_open_dialog_for_participants'):
        manager.start(npc, pc, battle_map, npc.properties['forced_conversation'])

    conversation_service.inject_npc_llm_system_note.assert_called_once()
    assert 'Demand the sheep' in conversation_service.inject_npc_llm_system_note.call_args[0][1]
