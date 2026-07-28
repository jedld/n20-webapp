import os
import sys
from unittest.mock import Mock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.conversation_service import ConversationService


class _FakeNpc:
    def __init__(self, uid, name):
        self.entity_uid = uid
        self._name = name

    def label(self):
        return self._name

    def is_npc(self):
        return True

    def send_conversation(self, message, distance_ft=30, targets=None, language=None, volume=None, narrative=None, segments=None):
        return [(targets[0], message, targets)] if targets else []


class _FakeGame:
    def __init__(self, entities):
        self._entities = entities
        self.username_to_sid = {'player1': ['sid-player1']}

    def get_entity_by_uid(self, uid):
        return self._entities.get(uid)


def _build_service(game, rag_handler):
    service = ConversationService(
        current_game_getter=lambda: game,
        game_session=Mock(),
        socketio=Mock(),
        entity_rag_handler_getter=lambda: rag_handler,
        llm_conversation_handler_getter=Mock,
        roles_for_username_getter=Mock(return_value=[]),
        entity_owners_getter=Mock(return_value={}),
        entities_controlled_by_getter=Mock(return_value={}),
        logins_getter=Mock(return_value={}),
        logger=Mock(),
    )
    service.emit_conversation_to_usernames = Mock(return_value={'player1'})
    service.conversation_audience_usernames = Mock(return_value={'player1'})
    service.conversation_visible_whisper_usernames = Mock(return_value=set())
    service._append_http_reply_for_requester = Mock()
    service._persist_conversation_line = Mock()
    return service


def test_talk_continue_emits_next_beat():
    speaker = _FakeNpc('aldric', 'Aldric')
    npc = _FakeNpc('finethir_shinebright', 'Finethir')
    game = _FakeGame({'finethir_shinebright': npc, 'aldric': speaker})

    rag_handler = Mock()
    rag_handler.pop_next_reply_beat.return_value = (
        [{'type': 'speech', 'text': 'Now listen closely.'}],
        [],
    )
    rag_handler._apply_narrative_segment_policies.return_value = (
        [{'type': 'speech', 'text': 'Now listen closely.'}],
        [],
        'Now listen closely.',
        None,
    )
    rag_handler.count_speech_beats.return_value = 0

    service = _build_service(game, rag_handler)
    service._reply_continuations['cont-1'] = {
        'receiver_uid': 'finethir_shinebright',
        'speaker_uid': 'aldric',
        'pending_segments': [{'type': 'speech', 'text': 'Now listen closely.'}],
        'targets': ['aldric'],
        'volume': 'whisper',
        'language': 'sheep',
        'tts_emotion': 'whisper',
        'tts_instruct': None,
        'local_chat_only': False,
    }

    payload, status = service.talk_continue_response({'continuation_id': 'cont-1'}, requesting_username='player1')

    assert status == 200
    assert payload['success'] is True
    assert len(payload['replies']) == 0
    assert 'cont-1' not in service._reply_continuations
    rag_handler.apply_conversation_keywords.assert_called_once()


def test_split_segments_first_beat_defers_follow_up_lines():
    from webapp.entity_rag_handler import EntityRAGHandler

    session = Mock()
    session.game_time = 0
    session.load_state.return_value = {}
    session.game_properties = {}
    handler = EntityRAGHandler(session, Mock())
    segments = [
        {'type': 'speech', 'text': 'First line.'},
        {'type': 'aside', 'text': 'They glance away.'},
        {'type': 'speech', 'text': 'Second line.'},
    ]
    first, pending = handler._split_segments_first_beat(segments)
    assert [segment['type'] for segment in first] == ['speech', 'aside']
    assert [segment['text'] for segment in pending] == ['Second line.']
    assert handler.count_speech_beats(pending) == 1


def test_talk_continue_missing_returns_404():
    service = _build_service(_FakeGame({}), Mock())
    payload, status = service.talk_continue_response({'continuation_id': 'missing'})
    assert status == 404
    assert payload['success'] is False


def test_select_conversation_responders_honors_explicit_target_not_in_overhear_list():
    speaker = _FakeNpc('aldric', 'Aldric')
    mara = _FakeNpc('mara_bartender', 'Mara')
    mara.dialog = True
    bystander = _FakeNpc('finethir_shinebright', 'Finethir')
    bystander.dialog = True

    rag_handler = Mock()
    service = _build_service(_FakeGame({
        'aldric': speaker,
        'mara_bartender': mara,
        'finethir_shinebright': bystander,
    }), rag_handler)

    overheard_only = [
        (bystander, 'any ale?', [mara]),
    ]
    responders = service.select_conversation_responders(
        overheard_only,
        speaker,
        targeted_entities=[mara],
        latest_message='any ale?',
    )

    assert [npc.entity_uid for npc in responders] == ['mara_bartender']
