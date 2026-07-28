import os
import sys

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.blueprints.helpers.llm_init import npc_llm_request_timeout
from webapp.llm_handler import (
    LLMHandler,
    LlamaCppProvider,
    format_llm_context_log,
    summarize_llm_messages,
)


class _RecordingProvider(LlamaCppProvider):
    last_timeout = None

    def send_message(self, messages):
        return "ok"

    def initialize(self, config):
        self.base_url = config.get('base_url', 'http://npc.example')
        self.current_model = config.get('model', 'npc-model')
        if config.get('timeout') is not None:
            self.request_timeout = int(config['timeout'])
        return True


def test_npc_llm_request_timeout_defaults_to_180(monkeypatch):
    monkeypatch.delenv('NPC_LLM_TIMEOUT', raising=False)
    assert npc_llm_request_timeout('llama_cpp') == 180


def test_npc_llm_request_timeout_honors_env(monkeypatch):
    monkeypatch.setenv('NPC_LLM_TIMEOUT', '240')
    assert npc_llm_request_timeout('llama_cpp') == 240


def test_initialize_npc_provider_uses_separate_instance(monkeypatch):
    monkeypatch.setattr(LLMHandler, '_instantiate_provider', lambda _self, name: _RecordingProvider())

    handler = LLMHandler()
    handler.initialize_provider('llama_cpp', {
        'base_url': 'http://dm.example',
        'model': 'dm-model',
        'timeout': 60,
    })
    handler.initialize_npc_provider('llama_cpp', {
        'base_url': 'http://npc.example',
        'model': 'npc-model',
        'timeout': 180,
    })

    assert handler.current_provider is not handler.npc_provider
    assert handler.current_provider.base_url == 'http://dm.example'
    assert handler.npc_provider.base_url == 'http://npc.example'
    assert handler.npc_provider.request_timeout == 180


def test_send_message_conversation_mode_uses_npc_provider(monkeypatch):
    class _StubProvider:
        def __init__(self, label):
            self.label = label

        def send_message(self, messages):
            return self.label

        def initialize(self, config):
            return True

        def get_available_models(self):
            return []

    handler = LLMHandler()
    handler.current_provider = _StubProvider('dm')
    handler.npc_provider = _StubProvider('npc')

    response = handler.send_message(
        [{'role': 'user', 'content': 'hello'}],
        context={'response_mode': 'conversation', 'skip_continuation': True},
    )
    assert response == 'npc'


def test_llama_cpp_provider_uses_configured_timeout(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen['timeout'] = timeout
        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {'choices': [{'message': {'content': 'hi'}}]}

        return Resp()

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr(
        LlamaCppProvider,
        'get_available_models',
        lambda self: ['local-model'],
    )

    provider = LlamaCppProvider()
    provider.initialize({'base_url': 'http://localhost:8011', 'timeout': 200})
    provider.send_message([{'role': 'user', 'content': 'hello'}])
    assert seen['timeout'] == 200


def test_summarize_llm_messages_counts_roles_and_tokens(monkeypatch):
    monkeypatch.setenv('N20_LLM_CHARS_PER_TOKEN', '4')
    summary = summarize_llm_messages([
        {'role': 'system', 'content': 'abcdefghijklmnop'},
        {'role': 'user', 'content': 'abcd'},
    ], context_limit=100)
    assert summary['message_count'] == 2
    assert summary['total_chars'] == 20
    assert summary['est_prompt_tokens'] == 5
    assert summary['context_limit'] == 100
    assert summary['context_usage_pct'] == 5.0
    assert summary['roles']['system']['messages'] == 1
    assert summary['largest_message']['role'] == 'system'
    log_line = format_llm_context_log(summary)
    assert 'est_prompt_tokens=~5' in log_line
    assert 'context_usage=5.0%' in log_line
