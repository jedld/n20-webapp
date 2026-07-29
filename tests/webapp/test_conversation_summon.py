import os
import sys
from unittest.mock import Mock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.conversation_service import ConversationService


class FakeReceiver:
    def __init__(self, entity_uid='pip_barmaid'):
        self.entity_uid = entity_uid

    def label(self):
        return 'Pip'


class FakeSpeaker:
    def __init__(self, entity_uid='aldric'):
        self.entity_uid = entity_uid


def _service_with_mocks(*, distance_squares=8):
    service = ConversationService.__new__(ConversationService)
    service.logger = Mock()
    rag_handler = Mock()
    rag_handler.apply_response_plan_directives.return_value = {'executed_actions': ['approach']}
    service._entity_rag_handler_getter = lambda: rag_handler
    battle_map = Mock()
    battle_map.distance.return_value = distance_squares
    battle_map.feet_per_grid = 5
    current_game = Mock()
    current_game.get_current_battle.return_value = None
    current_game.get_map_for_entity.return_value = battle_map
    service._current_game_getter = lambda: current_game
    service._rag_handler = rag_handler
    service._battle_map = battle_map
    return service


def test_message_summons_npc_detects_common_phrases():
    assert ConversationService.message_summons_npc('@pip-barmaid hi pip come over here!')
    assert ConversationService.message_summons_npc('come here please')
    assert ConversationService.message_summons_npc('GET OVER HERE')
    assert not ConversationService.message_summons_npc('I would like an ale please')


def test_message_summons_npc_ignores_relay_instructions():
    assert not ConversationService.message_summons_npc(
        'Go over to Henrik, tell him to come over',
    )
    assert not ConversationService.message_summons_npc('@pip-barmaid tell him to come here')


def test_message_requests_npc_relay():
    assert ConversationService.message_requests_npc_relay(
        'Go over to Henrik, tell him to come over',
    )
    assert ConversationService.message_requests_npc_relay('@pip ask Garret for help')


def test_select_npc_chain_responders_prefers_name_in_spoken_line():
    service = ConversationService.__new__(ConversationService)
    pip = FakeReceiver('pip_barmaid')
    henrik = Mock()
    henrik.entity_uid = 'henrik_patron'
    henrik.label = Mock(return_value='Henrik (Regular)')
    henrik.is_npc = Mock(return_value=True)
    henrik.dialog = True
    aldric = FakeSpeaker('aldric')

    battle_map = Mock()
    battle_map.entities = [pip, henrik, aldric]
    current_game = Mock()
    current_game.get_map_for_entity.return_value = battle_map
    service._current_game_getter = lambda: current_game

    responders = service.select_npc_chain_responders(
        [(henrik, 'Henrik! Come here.', [aldric])],
        pip,
        {
            'message': 'Henrik! Aldric wants a word with you. Get over to his table, would you?',
            'targets': [aldric],
        },
        player_speaker=aldric,
    )
    assert responders == [henrik]


def test_select_npc_chain_responders_includes_explicit_reply_targets():
    service = ConversationService.__new__(ConversationService)
    pip = FakeReceiver('pip_barmaid')
    sheep = Mock()
    sheep.entity_uid = 'finethir_shinebright'
    sheep.label = Mock(return_value='Finethir (Sheep)')
    sheep.is_npc = Mock(return_value=True)
    sheep.dialog = True
    aldric = FakeSpeaker('aldric')

    battle_map = Mock()
    battle_map.entities = [pip, sheep, aldric]
    current_game = Mock()
    current_game.get_map_for_entity.return_value = battle_map
    service._current_game_getter = lambda: current_game

    responders = service.select_npc_chain_responders(
        [],
        pip,
        {
            'message': 'Um... excuse me, Mr. Sheep? What is it you want?',
            'targets': [sheep],
        },
        player_speaker=aldric,
    )
    assert responders == [sheep]


def test_message_invites_listener_approach():
    assert ConversationService.message_invites_listener_approach(
        'by the way tell henrik to come over here will ya',
    )
    assert not ConversationService.message_invites_listener_approach('bring me an ale')


def test_maybe_auto_approach_summoned_npc_moves_when_addressed():
    service = _service_with_mocks()
    receiver = FakeReceiver()
    speaker = FakeSpeaker()

    moved = service.maybe_auto_approach_summoned_npc(
        receiver,
        speaker,
        '@pip-barmaid come here',
        delivered_targets=[receiver],
        mentioned_targets=[receiver],
    )

    assert moved is True
    service._rag_handler.apply_response_plan_directives.assert_called_once()
    plan = service._rag_handler.apply_response_plan_directives.call_args[0][0]
    assert plan['approach']['target_info']['target'] is speaker


def test_maybe_auto_approach_summoned_npc_skips_when_already_adjacent():
    service = _service_with_mocks(distance_squares=1)
    receiver = FakeReceiver()
    speaker = FakeSpeaker()

    moved = service.maybe_auto_approach_summoned_npc(
        receiver,
        speaker,
        'come over here',
        delivered_targets=[receiver],
    )

    assert moved is False
    service._rag_handler.apply_response_plan_directives.assert_not_called()
