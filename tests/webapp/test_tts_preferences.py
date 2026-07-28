import pytest

from webapp.tts.preferences import (
    available_delivery_modes,
    campaign_delivery_mode_from_game,
    default_delivery_mode,
    get_delivery_mode,
    normalize_delivery_mode,
    refresh_campaign_delivery_mode,
)


@pytest.fixture(autouse=True)
def _reset_campaign_delivery_mode():
    refresh_campaign_delivery_mode({})
    yield
    refresh_campaign_delivery_mode({})


def test_default_delivery_mode_async(monkeypatch):
    monkeypatch.delenv('TTS_DELIVERY', raising=False)
    refresh_campaign_delivery_mode({})
    assert default_delivery_mode() == 'async'


def test_default_delivery_mode_stream(monkeypatch):
    monkeypatch.setenv('TTS_DELIVERY', 'stream')
    refresh_campaign_delivery_mode({})
    assert default_delivery_mode() == 'stream'


def test_campaign_delivery_mode_from_game_yml():
    assert campaign_delivery_mode_from_game({'tts_delivery': 'stream'}) == 'stream'
    assert campaign_delivery_mode_from_game({'tts': {'delivery': 'manual'}}) == 'manual'


def test_get_delivery_mode_uses_campaign_yaml(monkeypatch):
    monkeypatch.delenv('TTS_DELIVERY', raising=False)
    refresh_campaign_delivery_mode({'tts_delivery': 'stream'})
    assert get_delivery_mode('player1') == 'stream'
    assert get_delivery_mode('dm') == 'stream'


def test_normalize_delivery_mode_aliases():
    assert normalize_delivery_mode('streaming') == 'stream'
    assert normalize_delivery_mode('click') == 'manual'
    assert normalize_delivery_mode('bogus') is None


def test_available_modes():
    assert available_delivery_modes() == ['async', 'stream', 'manual']
