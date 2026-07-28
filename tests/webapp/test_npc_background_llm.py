from types import SimpleNamespace

from webapp.npc_background_llm import (
    npc_background_llm_enabled,
    resolve_background_llm_enabled,
)


def test_npc_background_llm_enabled_defaults_true():
    assert npc_background_llm_enabled() is True


def test_npc_background_llm_enabled_respects_env(monkeypatch):
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    assert npc_background_llm_enabled() is False


def test_npc_background_llm_enabled_respects_game_yml():
    session = SimpleNamespace(game_properties={'npc_background_llm': {'enabled': False}})
    assert npc_background_llm_enabled(session) is False


def test_resolve_background_llm_enabled_prefers_feature_block():
    session = SimpleNamespace(game_properties={'npc_background_llm': {'enabled': True}})
    assert resolve_background_llm_enabled(session, config_block={'llm_enabled': False}) is False
