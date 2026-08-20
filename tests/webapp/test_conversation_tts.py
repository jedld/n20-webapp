from types import SimpleNamespace
from unittest.mock import Mock

from natural20.npc import Npc


class _FakeTTSManager:
    def __init__(self):
        self.calls = []

    def is_enabled(self):
        return True

    def list_locked_voices(self):
        return []

    def register_voice_prompt(self, *args, **kwargs):
        return None

    def create_voice(self, *args, **kwargs):
        return None

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return f'/tmp/fake_{len(self.calls)}.wav'

    def lookup_cached_line(self, synthesis_key):
        return None


def _make_service(listener, understands: bool, roles=None):
    from webapp.conversation_service import ConversationService

    service = SimpleNamespace()
    service.game_session = Mock()
    service._tts_manager = _FakeTTSManager()
    service.conversation_listener_for_username = lambda username: listener
    service.listener_understands_language = lambda l, lang: understands
    service.roles_for_username = lambda username: roles or []
    service._npc_voice_accent = lambda entity: None
    service._npc_voice_language = lambda entity: None
    service._npc_voice_prompt = lambda entity: 'sheep voice'
    service._npc_reference_audio = lambda entity: None
    service.logger = Mock()
    service.TTS_EMOTION_PATTERNS = ConversationService.TTS_EMOTION_PATTERNS
    service._detect_emotion_from_text = ConversationService._detect_emotion_from_text.__get__(service, ConversationService)
    service._ensure_tts_audio = ConversationService._ensure_tts_audio.__get__(service, ConversationService)
    service._beast_speech_tts_style = ConversationService._beast_speech_tts_style.__get__(service, ConversationService)
    service._listener_cannot_hear = ConversationService._listener_cannot_hear.__get__(service, ConversationService)
    service._listener_speech_reachability = ConversationService._listener_speech_reachability.__get__(service, ConversationService)
    service._tts_gain_for_reachability = ConversationService._tts_gain_for_reachability.__get__(service, ConversationService)
    service._tts_synthesis_cache_key = ConversationService._tts_synthesis_cache_key.__get__(service, ConversationService)
    service._attach_per_listener_tts = ConversationService._attach_per_listener_tts.__get__(service, ConversationService)
    return service


def test_per_listener_tts_uses_rendered_gibberish_for_sheep():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.session = Mock()
    service = _make_service(listener, understands=False)

    source = Mock(spec=Npc)
    source.entity_uid = 'finethir'
    source.label = lambda: 'Finethir'
    source.properties = {}

    rendered = {'message': "Ban'baa braemoo", 'language': 'sheep'}
    base = {
        'message': 'Please help me!',
        'language': 'sheep',
        'tts_pending': True,
        'volume': 'normal',
    }

    result = service._attach_per_listener_tts(rendered, base, source, 'aldric')

    assert result['tts_generated'] is True
    assert service._tts_manager.calls[0]['text'] == "Ban'baa braemoo"
    assert 'bleating' in service._tts_manager.calls[0]['instruct'].lower()


def test_per_listener_tts_uses_clear_text_after_comprehension():
    listener = Mock()
    listener.languages = lambda: ['common', 'beast']
    listener.session = Mock()
    service = _make_service(listener, understands=True)

    source = Mock(spec=Npc)
    source.entity_uid = 'finethir'
    source.label = lambda: 'Finethir'
    source.properties = {}

    rendered = {'message': 'Please help me!', 'language': 'sheep'}
    base = {
        'message': 'Please help me!',
        'language': 'sheep',
        'tts_pending': True,
        'volume': 'normal',
    }

    result = service._attach_per_listener_tts(rendered, base, source, 'aldric')

    assert result.get('tts_generated') is True, service._tts_manager.calls
    assert service._tts_manager.calls[0]['text'] == 'Please help me!'
    assert 'bleating' not in (service._tts_manager.calls[0].get('instruct') or '').lower()


def test_tts_deduplicates_identical_speech_for_multiple_listeners():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.entity_uid = 'pc1'
    listener.session = Mock()

    service = _make_service(listener, understands=True)
    service._listener_speech_reachability = lambda *args, **kwargs: {
        'reachable_now': True,
        'distance_ft': 5,
        'adjusted_distance_ft': 5,
        'effective_distance_ft': 30,
        'status': 'reachable',
    }

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Welcome to the tavern!'}
    base = {'message': 'Welcome to the tavern!', 'language': 'common', 'volume': 'normal'}
    cache = {}

    first = service._attach_per_listener_tts(rendered.copy(), base, source, 'player1', synthesis_cache=cache)
    second = service._attach_per_listener_tts(rendered.copy(), base, source, 'player2', synthesis_cache=cache)

    assert first['tts_generated'] is True
    assert second['tts_generated'] is True
    assert first['audio_url'] == second['audio_url']
    assert len(service._tts_manager.calls) == 1


def test_per_listener_tts_strips_emphasis_markers_for_synthesis():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.session = Mock()
    service = _make_service(listener, understands=True)

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Oh, you want the *real* news?'}
    base = {
        'message': rendered['message'],
        'language': 'common',
        'tts_pending': True,
        'volume': 'normal',
    }

    result = service._attach_per_listener_tts(rendered, base, source, 'player1')

    assert result['message'] == 'Oh, you want the *real* news?'
    assert result['tts_text'] == 'Oh, you want the real news?'
    assert service._tts_manager.calls[0]['text'] == 'Oh, you want the real news?'


def test_tts_skipped_for_deaf_listener():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = ['deafened']
    listener.session = Mock()
    service = _make_service(listener, understands=True)

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Can you hear me?'}
    base = {'message': 'Can you hear me?', 'language': 'common', 'volume': 'normal'}

    result = service._attach_per_listener_tts(rendered, base, source, 'player1')

    assert result.get('tts_generated') is False
    assert result.get('tts_skipped') == 'deaf_or_unconscious'
    assert len(service._tts_manager.calls) == 0


def test_tts_skipped_for_non_speech_placeholder():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.session = Mock()
    service = _make_service(listener, understands=True)

    source = Mock(spec=Npc)
    source.entity_uid = 'cala'
    source.label = lambda: 'Sister Cala'
    source.properties = {}

    rendered = {'message': '...'}
    base = {'message': '...', 'language': 'common', 'volume': 'normal'}

    result = service._attach_per_listener_tts(rendered, base, source, 'player1')

    assert result.get('tts_generated') is False
    assert result.get('tts_skipped') == 'non_speech'
    assert len(service._tts_manager.calls) == 0


def test_tts_skipped_when_listener_too_far():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.session = Mock()
    service = _make_service(listener, understands=True)
    service._listener_speech_reachability = lambda *args, **kwargs: {
        'reachable_now': False,
        'distance_ft': 120,
        'adjusted_distance_ft': 120,
        'effective_distance_ft': 30,
        'status': 'too_far',
    }

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Hello out there!'}
    base = {'message': 'Hello out there!', 'language': 'common', 'volume': 'normal'}

    result = service._attach_per_listener_tts(rendered, base, source, 'player1')

    assert result.get('tts_generated') is False
    assert result.get('tts_skipped') == 'too_far'
    assert len(service._tts_manager.calls) == 0


def test_tts_gain_attenuates_with_distance():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.session = Mock()
    service = _make_service(listener, understands=True)
    service._listener_speech_reachability = lambda *args, **kwargs: {
        'reachable_now': True,
        'distance_ft': 20,
        'adjusted_distance_ft': 20,
        'effective_distance_ft': 30,
        'status': 'reachable',
    }

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Come closer.'}
    base = {'message': 'Come closer.', 'language': 'common', 'volume': 'normal'}

    result = service._attach_per_listener_tts(rendered, base, source, 'player1')

    assert result['tts_generated'] is True
    assert 0.15 < result['tts_gain'] < 1.0


def test_tts_gain_for_reachability_edge_cases():
    from webapp.conversation_service import ConversationService

    service = SimpleNamespace()
    fn = ConversationService._tts_gain_for_reachability.__get__(service, ConversationService)

    assert fn(None, volume='normal') == 0.0
    assert fn({'reachable_now': False, 'status': 'too_far'}, volume='normal') == 0.0
    assert fn({'reachable_now': True, 'status': 'self'}, volume='normal') == 1.0
    assert fn({
        'reachable_now': True,
        'adjusted_distance_ft': 0,
        'effective_distance_ft': 30,
        'status': 'reachable',
    }, volume='normal') == 1.0


def test_tts_delivery_mode_defaults_to_async(monkeypatch):
    from webapp.conversation_service import ConversationService

    monkeypatch.delenv('TTS_DELIVERY', raising=False)
    service = ConversationService(
        current_game_getter=lambda: None,
        game_session=Mock(),
        socketio=Mock(),
        entity_rag_handler_getter=lambda: None,
        llm_conversation_handler_getter=lambda: None,
        roles_for_username_getter=lambda u: [],
        entity_owners_getter=lambda: {},
        entities_controlled_by_getter=lambda u: [],
        logins_getter=lambda: [],
        logger=Mock(),
    )
    assert service._tts_delivery_mode() == 'async'


def test_emit_conversation_async_does_not_block_on_tts(monkeypatch):
    from webapp.conversation_service import ConversationService
    from webapp.tts.preferences import refresh_campaign_delivery_mode

    monkeypatch.setenv('TTS_DELIVERY', 'async')
    refresh_campaign_delivery_mode({})
    socketio = Mock()
    service = SimpleNamespace()
    service.current_game = Mock(realtime_ui_suppressed=lambda: False)
    service._tts_manager = _FakeTTSManager()
    service.socketio = socketio
    service.logger = Mock()
    service._sids_for_username = lambda username: ['sid-1']
    service._normalize_username = lambda username: str(username).lower()
    service.render_conversation_payload_for_username = lambda username, source, payload: dict(payload)
    service._tts_delivery_mode = ConversationService._tts_delivery_mode.__get__(service, ConversationService)
    service._tts_should_generate = ConversationService._tts_should_generate.__get__(service, ConversationService)
    service._mark_tts_delivery_status = ConversationService._mark_tts_delivery_status.__get__(
        service, ConversationService
    )
    scheduled_jobs = []
    service._schedule_async_tts_delivery = lambda jobs, base_payload, source_entity: scheduled_jobs.append(jobs)

    def fake_attach(rendered_payload, base_payload, source_entity, username, synthesis_cache=None, generate_audio=True):
        rendered_payload = dict(rendered_payload)
        if generate_audio:
            service._tts_manager.generate(text=rendered_payload.get('message'), npc_uid='pip')
            rendered_payload['audio_url'] = '/tts/fake.wav'
        else:
            rendered_payload['tts_status'] = 'pending'
        return rendered_payload

    service._attach_per_listener_tts = fake_attach
    service.emit_conversation_to_usernames = ConversationService.emit_conversation_to_usernames.__get__(
        service, ConversationService
    )

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'

    payload = {
        'reply_id': 'reply-abc',
        'entity_id': 'pip',
        'message': 'Hello there!',
        'language': 'common',
        'volume': 'normal',
        'tts_pending': True,
    }

    emitted = service.emit_conversation_to_usernames({'player1'}, payload, source_entity=source)

    assert emitted['player1']['tts_status'] == 'pending'
    assert 'audio_url' not in emitted['player1']
    assert len(service._tts_manager.calls) == 0
    socketio.emit.assert_called()
    assert len(scheduled_jobs) == 1
    assert scheduled_jobs[0][0]['username'] == 'player1'


def test_attach_per_listener_tts_skips_generation_when_requested():
    listener = Mock()
    listener.languages = lambda: ['common']
    listener.statuses = []
    listener.session = Mock()
    service = _make_service(listener, understands=True)

    source = Mock(spec=Npc)
    source.entity_uid = 'pip'
    source.label = lambda: 'Pip'
    source.properties = {}

    rendered = {'message': 'Welcome!'}
    base = {'message': 'Welcome!', 'language': 'common', 'volume': 'normal'}

    result = service._attach_per_listener_tts(
        rendered, base, source, 'player1', generate_audio=False
    )

    assert result.get('tts_emotion') == 'calm'
    assert result.get('tts_generated') is False
    assert 'audio_url' not in result
    assert len(service._tts_manager.calls) == 0


def test_stream_tts_delivery_queues_consecutive_jobs():
    from webapp.conversation_service import ConversationService

    service = ConversationService(
        current_game_getter=lambda: Mock(),
        game_session=Mock(),
        socketio=Mock(),
        entity_rag_handler_getter=lambda: Mock(),
        llm_conversation_handler_getter=lambda: Mock(),
        roles_for_username_getter=lambda username: [],
        entity_owners_getter=lambda: {},
        entities_controlled_by_getter=lambda username: [],
        logins_getter=lambda: {},
        logger=Mock(),
    )
    service._deliver_stream_tts_jobs = Mock()
    service.socketio.start_background_task = Mock(side_effect=lambda fn: fn())

    service._schedule_stream_tts_delivery([{'username': 'pc', 'reply_id': 'a'}], {}, Mock())
    service._schedule_stream_tts_delivery([{'username': 'pc', 'reply_id': 'b'}], {}, Mock())

    assert service._deliver_stream_tts_jobs.call_count == 2
    assert service._stream_tts_queue == []
    assert service._stream_tts_worker_running is False
