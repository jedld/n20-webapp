"""Tests for the dedicated battle-decision LLM provider wiring.

Covers:
  * ``LLMHandler.initialize_battle_provider`` / ``battle_llm_provider`` /
    ``get_battle_provider_info``.
  * ``configure_battle_provider_from_environment`` (``BATTLE_LLM_PROVIDER``)
    env resolution, overrides, and unsupported-provider handling.
  * ``JevProvider`` request/response logging for fine-tuning datasets.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

_TYPESAFE_VARS = (
    'BATTLE_LLM_PROVIDER',
    'TYPESAFE_API_KEY',
    'BATTLE_TYPESAFE_API_KEY',
    'TYPESAFE_BASE_URL',
    'BATTLE_TYPESAFE_BASE_URL',
    'TYPESAFE_MODEL',
    'BATTLE_TYPESAFE_MODEL',
    'TYPESAFE_DEFAULT_MODEL',
)


def _save_and_clean_env(overrides, saved_stack):
    """Pop all battle/typesafe vars, then apply ``overrides``.

    Appends the saved values to ``saved_stack`` for restoration.
    """
    saved = {}
    for key in _TYPESAFE_VARS:
        saved[key] = os.environ.pop(key, None)
    for key, value in overrides.items():
        os.environ[key] = value
    saved_stack.append(saved)


def _restore_env(saved_stack):
    while saved_stack:
        saved = saved_stack.pop()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestBattleProviderHandlerSlots(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import LLMHandler, JevProvider
        self.LLMHandler = LLMHandler
        self.JevProvider = JevProvider

    def test_battle_provider_defaults_none(self):
        handler = self.LLMHandler()
        self.assertIsNone(handler.battle_provider)
        self.assertFalse(handler.get_battle_provider_info().get('initialized'))

    def test_battle_llm_provider_falls_back_to_dm(self):
        handler = self.LLMHandler()
        handler.current_provider = MagicMock()
        handler.battle_provider = None
        self.assertIs(handler.battle_llm_provider(), handler.current_provider)

    def test_battle_llm_provider_prefers_battle(self):
        handler = self.LLMHandler()
        handler.current_provider = MagicMock()
        handler.battle_provider = MagicMock()
        self.assertIs(handler.battle_llm_provider(), handler.battle_provider)

    def test_initialize_battle_provider_sets_separate_instance(self):
        handler = self.LLMHandler()
        handler.current_provider = MagicMock(name='dm')
        fake = MagicMock(name='battle')
        fake.initialize = MagicMock(return_value=True)
        fake.current_model = 'jev-latest'
        fake.base_url = 'https://api.typesafe.ai'
        with patch.object(self.LLMHandler, '_instantiate_provider',
                          return_value=fake):
            ok = handler.initialize_battle_provider('jev', {'api_key': 'secret'})
        self.assertTrue(ok)
        self.assertIsNotNone(handler.battle_provider)
        self.assertIsNot(handler.battle_provider, handler.current_provider)
        fake.initialize.assert_called_once_with({'api_key': 'secret'})

    def test_initialize_battle_provider_unknown_name(self):
        handler = self.LLMHandler()
        self.assertFalse(handler.initialize_battle_provider('bogus', {}))
        self.assertIsNone(handler.battle_provider)

    def test_get_battle_provider_info_populated(self):
        handler = self.LLMHandler()
        provider = self.JevProvider({'model': 'jev-latest'})
        handler.battle_provider = provider
        info = handler.get_battle_provider_info()
        self.assertTrue(info['initialized'])
        self.assertEqual(info['provider_type'], 'JevProvider')
        self.assertEqual(info['current_model'], 'jev-latest')
        self.assertEqual(info['base_url'], provider.base_url)


class TestBattleProviderEnvWiring(unittest.TestCase):
    def setUp(self):
        from webapp.blueprints.helpers import llm_init as mod
        self.mod = mod
        self._saved_stack = []

    def tearDown(self):
        _restore_env(self._saved_stack)

    def _configure(self, **overrides):
        _save_and_clean_env(overrides, self._saved_stack)
        handler = MagicMock()
        result = self.mod.configure_battle_provider_from_environment(handler)
        return handler, result

    def test_unset_returns_false(self):
        handler, result = self._configure()
        self.assertFalse(result)
        handler.initialize_battle_provider.assert_not_called()

    def test_jev_from_shared_typesafe(self):
        handler, result = self._configure(
            BATTLE_LLM_PROVIDER='jev',
            TYPESAFE_API_KEY='ts_key',
            TYPESAFE_BASE_URL='https://api.typesafe.ai',
            TYPESAFE_MODEL='jev-latest',
        )
        self.assertTrue(result)
        handler.initialize_battle_provider.assert_called_once_with('jev', {
            'api_key': 'ts_key',
            'base_url': 'https://api.typesafe.ai',
            'model': 'jev-latest',
        })

    def test_jev_battle_overrides_take_precedence(self):
        handler, _ = self._configure(
            BATTLE_LLM_PROVIDER='jev',
            TYPESAFE_API_KEY='shared',
            TYPESAFE_MODEL='shared-model',
            BATTLE_TYPESAFE_API_KEY='battle',
            BATTLE_TYPESAFE_MODEL='battle-model',
        )
        _, config = handler.initialize_battle_provider.call_args[0]
        self.assertEqual(config['api_key'], 'battle')
        self.assertEqual(config['model'], 'battle-model')

    def test_unsupported_provider_returns_false(self):
        handler, result = self._configure(BATTLE_LLM_PROVIDER='ollama')
        self.assertFalse(result)
        handler.initialize_battle_provider.assert_not_called()

    def test_jev_no_key_still_attempts_with_empty_config(self):
        _save_and_clean_env({'BATTLE_LLM_PROVIDER': 'jev'}, self._saved_stack)
        handler = MagicMock()
        handler.initialize_battle_provider.return_value = False
        result = self.mod.configure_battle_provider_from_environment(handler)
        self.assertFalse(result)
        handler.initialize_battle_provider.assert_called_once_with('jev', {})

    def test_case_insensitive_and_stripped(self):
        handler, result = self._configure(
            BATTLE_LLM_PROVIDER='  JEV ',
            TYPESAFE_API_KEY='k',
        )
        self.assertTrue(result)
        handler.initialize_battle_provider.assert_called_once_with(
            'jev', {'api_key': 'k'})


class TestJevDefaultBaseUrl(unittest.TestCase):
    """Without a configured base URL, Jev talks to the public typesafe.ai API."""

    def setUp(self):
        from webapp.llm_handler import JevProvider
        self.JevProvider = JevProvider
        self._saved_stack = []

    def tearDown(self):
        _restore_env(self._saved_stack)

    def test_defaults_when_nothing_configured(self):
        _save_and_clean_env({}, self._saved_stack)
        provider = self.JevProvider({})
        self.assertEqual(provider.base_url, 'https://api.typesafe.ai')
        provider.initialize({})  # no API key -> disabled, but URL still resolved
        self.assertEqual(provider.base_url, 'https://api.typesafe.ai')

    def test_blank_values_fall_back_to_default(self):
        _save_and_clean_env({'TYPESAFE_BASE_URL': '   '}, self._saved_stack)
        provider = self.JevProvider({'base_url': ''})
        provider.initialize({'base_url': ' '})
        self.assertEqual(provider.base_url, 'https://api.typesafe.ai')

    def test_ai_yaml_profile_without_base_url_uses_default(self):
        from webapp.ai_config import provider_config_from_profile
        _save_and_clean_env({}, self._saved_stack)
        name, config = provider_config_from_profile({'provider': 'jev', 'api_key': 'k'}, role='battle')
        provider = self.JevProvider({})
        with patch.object(self.JevProvider, '_import_sdk', side_effect=ImportError):
            provider.initialize(config)
        self.assertEqual(name, 'jev')
        self.assertEqual(provider.base_url, 'https://api.typesafe.ai')

    def test_explicit_base_url_wins(self):
        _save_and_clean_env({'TYPESAFE_BASE_URL': 'https://env.example'}, self._saved_stack)
        provider = self.JevProvider({})
        provider.initialize({'base_url': 'http://localhost:8901/'})
        self.assertEqual(provider.base_url, 'http://localhost:8901')


def _resolve_battle_provider(handler):
    """Mirror the resolution used by the 'llm' controller build sites."""
    battle_provider_fn = getattr(handler, 'battle_llm_provider', None)
    if callable(battle_provider_fn):
        return battle_provider_fn()
    return getattr(handler, 'current_provider', None)


class TestBattleProviderRouting(unittest.TestCase):
    """The 'llm' controller build sites resolve the battle provider first."""

    def test_prefers_battle_provider(self):
        handler = MagicMock()
        handler.battle_llm_provider = MagicMock(return_value='battle-provider')
        self.assertEqual(_resolve_battle_provider(handler), 'battle-provider')

    def test_falls_back_to_dm_provider(self):
        handler = MagicMock(spec=['current_provider'])
        self.assertIs(_resolve_battle_provider(handler), handler.current_provider)

    def test_none_when_no_handler(self):
        self.assertIsNone(_resolve_battle_provider(None))

    def test_registry_llm_controller_uses_runtime_battle_provider(self):
        """Auto-battle controllers must use the live handler, not re-import webapp.app."""
        from types import SimpleNamespace
        from webapp.blueprints.helpers import runtime_state
        from webapp.game_management_components import GameControllerRegistry

        handler = MagicMock()
        handler.battle_llm_provider = MagicMock(return_value='jev-provider')
        captured = {}

        class _FakeLlmController:
            def __init__(self, session, llm_provider=None):
                captured['provider'] = llm_provider

        registry = GameControllerRegistry(SimpleNamespace(game_session=None))
        with patch.dict(runtime_state._globals, {'llm_handler': handler}), \
                patch('webapp.game_management_components.LlmMcpController', _FakeLlmController), \
                patch.dict(sys.modules, {'webapp.utils': None}):
            registry._build_controller_by_kind('llm')
        self.assertEqual(captured['provider'], 'jev-provider')


class TestJevRequestLogging(unittest.TestCase):
    """JevProvider records decision requests/responses for fine-tuning datasets."""

    def setUp(self):
        from types import SimpleNamespace
        from webapp.llm_handler import JevProvider
        self.SimpleNamespace = SimpleNamespace
        self.JevProvider = JevProvider

    def _provider(self, records):
        provider = self.JevProvider({}, request_logger=records.append)
        provider.client = MagicMock()
        return provider

    def test_decide_action_records_request_and_response(self):
        records = []
        provider = self._provider(records)
        result = self.SimpleNamespace(
            choices={
                'action': self.SimpleNamespace(
                    choice='1',
                    confidence=0.7,
                    probabilities={'0': 0.3, '1': 0.7},
                )
            }
        )
        provider.client.system_one.return_value = result

        with patch.object(self.JevProvider, '_import_sdk',
                          return_value=(None, MagicMock(), None)):
            parsed = provider.decide_action('state A', ['alpha', 'beta'])

        self.assertEqual(parsed['index'], 1)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['v'], 1)
        self.assertEqual(record['model'], 'jev-latest')
        self.assertEqual(record['task'], 'action')
        self.assertEqual(record['request']['state'], 'state A')
        self.assertEqual(record['request']['options'], ['alpha', 'beta'])
        self.assertEqual(record['response']['parsed']['index'], 1)
        self.assertEqual(record['response']['raw']['choice'], '1')
        self.assertIsInstance(record['latency_ms'], float)

    def test_decide_bool_records_request_and_response(self):
        records = []
        provider = self._provider(records)
        result = self.SimpleNamespace(
            nouls={'answer': self.SimpleNamespace(noul=0.35, confidence=0.9)}
        )
        provider.client.system_one.return_value = result

        with patch.object(self.JevProvider, '_import_sdk',
                          return_value=(None, MagicMock(), MagicMock())):
            parsed = provider.decide_bool('state B', 'Should it cast?')

        self.assertEqual(parsed, {'noul': 0.35, 'confidence': 0.9})
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['task'], 'bool')
        self.assertEqual(record['request']['state'], 'state B')
        self.assertEqual(record['request']['instructions'], 'Should it cast?')
        self.assertEqual(record['response']['parsed'], {'noul': 0.35, 'confidence': 0.9})
        self.assertEqual(record['response']['raw']['noul'], 0.35)

    def test_failed_request_records_error(self):
        records = []
        provider = self._provider(records)
        provider.client.system_one.side_effect = RuntimeError('boom')

        with patch.object(self.JevProvider, '_import_sdk',
                          return_value=(None, MagicMock(), None)):
            parsed = provider.decide_action('state C', ['alpha'])

        self.assertIsNone(parsed)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['task'], 'action')
        self.assertIsNone(record.get('response'))
        self.assertEqual(record['error'], 'boom')
        self.assertIsInstance(record['latency_ms'], float)


if __name__ == '__main__':
    unittest.main()
