"""Tests for disk-backed TTS line cache."""

from unittest.mock import Mock

from webapp.tts.manager import TTSManager
from webapp.tts.synthesis_cache import cached_line_path, synthesis_cache_key


def test_manager_reuses_cached_line_on_disk(tmp_path):
    manager = TTSManager(device="cpu", cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3")
    manager.create_voice("npc-1", "Test voice")

    key = synthesis_cache_key("npc-1", "Hello there.", "calm", None, "normal")
    out = cached_line_path(manager.cache_dir, key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"RIFFcached")

    path = manager.generate(
        "Hello there.",
        "npc-1",
        emotion="calm",
        volume="normal",
        synthesis_key=key,
    )
    assert path == str(out)


def test_emit_sync_reuses_audio_for_multiple_listeners():
    from webapp.conversation_service import ConversationService

    service = Mock(spec=ConversationService)
    service.current_game = Mock(realtime_ui_suppressed=lambda: False)
    service.socketio = Mock()
    service.socketio.emit = Mock()
    service.socketio.sleep = Mock()
    service.socketio.start_background_task = Mock()
    service._normalize_username = ConversationService._normalize_username.__get__(service, ConversationService)
    service._sids_for_username = lambda username: [f"sid-{username}"]
    service._tts_delivery_mode = lambda username=None: "sync"
    service._mark_tts_delivery_status = ConversationService._mark_tts_delivery_status.__get__(service, ConversationService)
    service._schedule_async_tts_delivery = Mock()
    service._schedule_stream_tts_delivery = Mock()
    service.render_conversation_payload_for_username = lambda username, source, payload: dict(payload)

    calls = []

    def fake_attach(rendered_payload, base_payload, source_entity, username, synthesis_cache=None, generate_audio=True):
        calls.append(username)
        key = "shared"
        if synthesis_cache is not None and synthesis_cache.get(key):
            rendered_payload = dict(rendered_payload)
            rendered_payload["audio_url"] = synthesis_cache[key]
            rendered_payload["tts_generated"] = True
            return rendered_payload
        if synthesis_cache is not None:
            synthesis_cache[key] = "/tts/shared.wav"
        rendered_payload = dict(rendered_payload)
        rendered_payload["audio_url"] = "/tts/shared.wav"
        rendered_payload["tts_generated"] = True
        return rendered_payload

    service._attach_per_listener_tts = fake_attach

    source = Mock()
    source.entity_uid = "pip"
    payload = {"message": "Welcome!", "tts_pending": True, "language": "common", "volume": "normal"}

    emitted = ConversationService.emit_conversation_to_usernames(
        service, {"player1", "player2"}, payload, source_entity=source,
    )

    assert set(calls) == {"player1", "player2"}
    assert emitted["player1"]["audio_url"] == "/tts/shared.wav"
    assert emitted["player2"]["audio_url"] == "/tts/shared.wav"
