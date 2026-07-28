from types import SimpleNamespace

from webapp.tts.model_config import (
    get_model_config,
    resolve_model_profile,
    MODEL_PROFILES,
)
from webapp.tts.cosyvoice_provider import MockCosyVoiceProvider


def test_resolve_cosyvoice_v3_profile():
    engine = SimpleNamespace(
        _fallback_mode=False,
        _model_version='v3',
        model_profile_key=lambda: 'cosyvoice_v3',
    )
    assert resolve_model_profile(engine, 'cosyvoice') == 'cosyvoice_v3'


def test_resolve_cosyvoice_openvoice_fallback():
    engine = SimpleNamespace(
        _fallback_mode=True,
        _model_version='v3',
        model_profile_key=lambda: 'cosyvoice_openvoice_fallback',
    )
    assert resolve_model_profile(engine, 'cosyvoice') == 'cosyvoice_openvoice_fallback'


def test_get_model_config_uses_profile_defaults():
    engine = MockCosyVoiceProvider(cache_dir='/tmp/tts_test_profiles')
    cfg = get_model_config(engine, 'mock_cosyvoice')
    assert cfg.profile == 'mock_cosyvoice'
    assert cfg.max_chars == MODEL_PROFILES['mock_cosyvoice'].max_chars


def test_get_model_config_openvoice_gtts_longer_chunks():
    engine = SimpleNamespace(
        _fallback_mode=True,
        model_profile_key=lambda: 'openvoice_gtts',
    )
    cfg = get_model_config(engine, 'openvoice')
    assert cfg.max_chars == MODEL_PROFILES['openvoice_gtts'].max_chars
    assert cfg.max_chars > MODEL_PROFILES['cosyvoice_v3'].max_chars


def test_profile_specific_env_override(monkeypatch):
    engine = MockCosyVoiceProvider(cache_dir='/tmp/tts_test_profiles_env')
    monkeypatch.setenv('N20_TTS_MAX_CHARS_MOCK_COSYVOICE', '180')
    cfg = get_model_config(engine, 'mock_cosyvoice')
    assert cfg.max_chars == 180


def test_global_env_override(monkeypatch):
    engine = MockCosyVoiceProvider(cache_dir='/tmp/tts_test_profiles_global')
    monkeypatch.setenv('N20_TTS_MAX_CHARS', '160')
    cfg = get_model_config(engine, 'mock_cosyvoice')
    assert cfg.max_chars == 160
