import wave

from webapp.tts.text_chunking import split_tts_text
from webapp.tts.model_config import get_model_config, MODEL_PROFILES
from webapp.tts.streaming import concat_wav_files, silence_pcm_bytes
from webapp.tts.cosyvoice_provider import MockCosyVoiceProvider


def test_split_short_text_unchanged():
    text = 'Hello there, adventurer.'
    assert split_tts_text(text, max_chars=220) == [text]


def test_split_long_text_at_sentences():
    text = (
        'Welcome to the market square. '
        'Merchants hawk their wares from colorful stalls. '
        'The smell of fresh bread drifts through the air. '
        'A bard tunes his lute near the fountain.'
    )
    chunks = split_tts_text(text, max_chars=90)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 90 for chunk in chunks)
    # Full message preserved when rejoined (modulo spaces).
    assert ''.join(chunks).replace(' ', '') == text.replace(' ', '')


def test_split_keeps_stage_direction_with_line():
    text = '[laughs] You really think you can best me in combat?'
    chunks = split_tts_text(text, max_chars=220)
    assert chunks == [text]


def test_split_oversized_sentence_by_clauses():
    text = (
        'If you seek the old wizard, travel north past the bridge, '
        'follow the river until you see the broken tower, and then '
        'knock three times on the oak door.'
    )
    chunks = split_tts_text(text, max_chars=70)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 70 for chunk in chunks)


def test_max_tts_chunk_chars_per_profile_env(monkeypatch):
    from webapp.tts.cosyvoice_provider import MockCosyVoiceProvider

    engine = MockCosyVoiceProvider(cache_dir='/tmp/tts_chunk_env')
    monkeypatch.setenv('N20_TTS_MAX_CHARS_MOCK_COSYVOICE', '150')
    cfg = get_model_config(engine, 'mock_cosyvoice')
    assert cfg.max_chars == 150
    chunks = split_tts_text('word ' * 80, config=cfg)
    assert all(len(chunk) <= 150 for chunk in chunks)


def test_manager_concatenates_long_mock_speech(tmp_path):
    provider = MockCosyVoiceProvider(cache_dir=str(tmp_path / 'cache'))
    provider.initialize()
    provider.create_voice('npc1', 'merchant voice')

    from webapp.tts.manager import TTSManager

    manager = TTSManager(cache_dir=str(tmp_path / 'cache'))
    manager._engine = provider
    manager._provider_name = 'mock_cosyvoice'
    manager._auto_create_on_first_speech = True

    long_text = '. '.join([f'This is spoken line number {i}' for i in range(12)]) + '.'
    cfg = get_model_config(provider, 'mock_cosyvoice')
    chunks = split_tts_text(long_text, config=cfg)
    assert len(chunks) > 1

    output = manager.generate(long_text, 'npc1')
    assert output
    with wave.open(output, 'rb') as wf:
        frames_multi = wf.getnframes()

    single = manager.generate(chunks[0], 'npc1')
    with wave.open(single, 'rb') as wf:
        frames_single = wf.getnframes()

    assert frames_multi > frames_single


def test_concat_wav_files_adds_pause(tmp_path):
    from webapp.tts.cosyvoice_provider import MockCosyVoiceProvider

    provider = MockCosyVoiceProvider(cache_dir=str(tmp_path))
    path_a = str(tmp_path / 'a.wav')
    path_b = str(tmp_path / 'b.wav')
    provider._create_placeholder_wav(path_a)
    provider._create_placeholder_wav(path_b)
    out = str(tmp_path / 'out.wav')
    concat_wav_files([path_a, path_b], out, pause_ms=100)

    with wave.open(path_a, 'rb') as a, wave.open(out, 'rb') as merged:
        assert merged.getnframes() > a.getnframes()


def test_silence_pcm_bytes_length():
    pcm = silence_pcm_bytes(22050, 100)
    assert len(pcm) == 22050 * 2 // 10  # 100ms mono int16
