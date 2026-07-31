"""Tests for LLM context budget resolution and compaction."""

import os
from unittest.mock import MagicMock, patch

import pytest

from webapp.llm_context_budget import (
    compact_history_messages,
    compact_messages_for_budget,
    compute_prompt_budget,
    maybe_compact_messages,
    resolve_context_limit_from_env,
    resolve_provider_context_limit,
)


class _FakeProvider:
    def __init__(self, **kwargs):
        self.base_url = kwargs.get('base_url')
        self.current_model = kwargs.get('model')
        self.context_window = kwargs.get('context_window')
        self.max_tokens = kwargs.get('max_tokens', 320)

    def set_context_window(self, value):
        self.context_window = int(value)
        return True


def test_resolve_context_limit_from_env_prefers_npc_specific(monkeypatch):
    monkeypatch.setenv('NPC_LLM_CONTEXT_SIZE', '32000')
    monkeypatch.setenv('N20_LLM_CONTEXT_SIZE', '8000')
    assert resolve_context_limit_from_env('npc') == 32000


def test_compute_prompt_budget_reserves_output_and_margin():
    budget = compute_prompt_budget(32768, max_output_tokens=320, safety_margin=512)
    assert budget == 32768 - 320 - 512


def test_compact_history_drops_overheard_first():
    history = [
        {'role': 'system', 'content': 'you overhear Bob talk to Carol (in common): hello'},
        {'role': 'user', 'content': 'They said: hi ' + ('x' * 200)},
        {'role': 'assistant', 'content': 'You said: welcome ' + ('y' * 200)},
    ]
    compacted, report = compact_history_messages(history, budget_tokens=80, keep_recent=2)
    assert report['dropped_overheard'] == 1
    assert all('overhear' not in msg.get('content', '').lower() for msg in compacted)


def test_compact_history_summarizes_old_turns():
    history = []
    for idx in range(12):
        history.append({'role': 'user', 'content': f'question {idx} ' + ('x' * 80)})
        history.append({'role': 'assistant', 'content': f'answer {idx} ' + ('y' * 80)})

    compacted, report = compact_history_messages(history, budget_tokens=400, keep_recent=4)
    assert report['summarized_messages'] > 0
    assert compacted[0]['role'] == 'system'
    assert 'Earlier in this conversation' in compacted[0]['content']
    assert len(compacted) <= 5


def test_compact_messages_preserves_primary_system_prompt():
    system_prompt = 'You are Mara.' + (' context ' * 200)
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': 'hello'},
        {'role': 'assistant', 'content': 'hi there'},
    ]
    for idx in range(10):
        messages.append({'role': 'user', 'content': f'line {idx} ' + ('z' * 120)})
        messages.append({'role': 'assistant', 'content': f'reply {idx} ' + ('q' * 120)})

    compacted, report = compact_messages_for_budget(messages, budget_tokens=800, keep_recent=4)
    assert compacted[0]['content'].startswith('You are Mara.')
    assert report['summarized_messages'] > 0


def test_maybe_compact_messages_uses_env_limit(monkeypatch):
    monkeypatch.setenv('NPC_LLM_CONTEXT_SIZE', '1000')
    monkeypatch.delenv('N20_LLM_CONTEXT_AUTO_DETECT', raising=False)

    provider = _FakeProvider(max_tokens=100)
    messages = [
        {'role': 'system', 'content': 'system ' * 300},
        {'role': 'user', 'content': 'hello ' * 200},
        {'role': 'assistant', 'content': 'world ' * 200},
    ]
    compacted, report = maybe_compact_messages(messages, provider=provider, role='npc')
    assert report['context_limit'] == 1000
    assert report['compacted'] is True
    assert len(compacted) <= len(messages)


@patch('webapp.llm_context_budget.detect_llama_cpp_context', return_value=4096)
def test_resolve_provider_context_limit_auto_detect_llama(mock_detect):
    class LlamaCppProviderStub(_FakeProvider):
        pass

    provider = LlamaCppProviderStub(base_url='http://localhost:8011')
    with patch.dict(os.environ, {'N20_LLM_CONTEXT_AUTO_DETECT': '1'}, clear=False):
        for key in ('NPC_LLM_CONTEXT_SIZE', 'N20_NPC_CONTEXT_SIZE', 'N20_LLM_CONTEXT_SIZE'):
            os.environ.pop(key, None)
        limit, source = resolve_provider_context_limit(provider, role='npc', auto_detect=True)
    assert limit == 4096
    assert source == 'auto_detect'
    mock_detect.assert_called_once()
