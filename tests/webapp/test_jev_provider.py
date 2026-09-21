"""Tests for the JevProvider (TypeSafe "System One" decision model).

Covers:
  * LLMProvider ABC conformance and registration in LLMHandler.
  * initialize() graceful degradation (missing key / missing SDK).
  * decide_action() / decide_bool() native decision API against a fake SDK.
  * env wiring for the DM and NPC ``jev`` branches in llm_init.
"""

import os
import types
import unittest
from unittest.mock import MagicMock, patch


class TestJevProviderABCAndRegistration(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import LLMHandler, JevProvider, LLMProvider
        self.LLMHandler = LLMHandler
        self.JevProvider = JevProvider
        self.LLMProvider = LLMProvider

    def test_is_concrete_subclass(self):
        self.assertTrue(issubclass(self.JevProvider, self.LLMProvider))
        self.assertEqual(self.JevProvider.__abstractmethods__, frozenset())

    def test_registered_in_handler(self):
        handler = self.LLMHandler({})
        self.assertIn('jev', handler.providers)
        self.assertIsInstance(handler.providers['jev'], self.JevProvider)

    def test_instantiate_provider_returns_jev(self):
        inst = self.LLMHandler._instantiate_provider('jev')
        self.assertIsInstance(inst, self.JevProvider)


class TestJevProviderInit(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import JevProvider
        self.JevProvider = JevProvider

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ('TYPESAFE_API_KEY', 'TYPESAFE_BASE_URL', 'TYPESAFE_MODEL',
                      'TYPESAFE_DEFAULT_MODEL', 'JEV_TIMEOUT'):
                os.environ.pop(k, None)
            p = self.JevProvider({})
        self.assertEqual(p.base_url, self.JevProvider.DEFAULT_BASE_URL)
        self.assertEqual(p.current_model, self.JevProvider.DEFAULT_MODEL)
        self.assertIsNone(p.client)
        self.assertFalse(p.is_available)

    def test_initialize_no_key_returns_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TYPESAFE_API_KEY', None)
            p = self.JevProvider({})
            self.assertFalse(p.initialize({}))
        self.assertFalse(p.is_available)

    def test_initialize_missing_sdk_returns_false(self):
        # API key present but typesafe_sdk is not importable.
        p = self.JevProvider({'api_key': 'secret'})
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'secret'}):
            with patch('webapp.llm_handler.JevProvider._import_sdk',
                       side_effect=ImportError("No module named 'typesafe_sdk'")):
                self.assertFalse(p.initialize({'api_key': 'secret'}))
        self.assertFalse(p.is_available)

    def test_initialize_with_sdk_returns_true(self):
        p = self.JevProvider({'api_key': 'secret', 'model': 'jev-latest'})

        fake_client = MagicMock()
        fake_client_cls = MagicMock(return_value=fake_client)

        def fake_import_sdk():
            return fake_client_cls, MagicMock(), MagicMock()

        with patch('webapp.llm_handler.JevProvider._import_sdk', side_effect=fake_import_sdk):
            self.assertTrue(p.initialize({'api_key': 'secret'}))
        self.assertTrue(p.is_available)
        self.assertIs(p.client, fake_client)
        # base_url/model/timeout forwarded to the client constructor
        fake_client_cls.assert_called_once_with(
            api_key='secret',
            model='jev-latest',
            base_url=self.JevProvider.DEFAULT_BASE_URL,
            timeout=p.request_timeout,
        )


def _fake_sdk():
    """Build a fake typesafe_sdk (TypeSafeClient, Choice, Noul) triple."""

    class FakeChoice:
        def __init__(self, instructions=None, criteria=None):
            self.instructions = instructions
            self.criteria = criteria or {}

    class FakeNoul:
        def __init__(self, instructions=None):
            self.instructions = instructions

    class FakeClient:
        def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
            self.api_key = api_key
            self.model = model
            self.base_url = base_url
            self.timeout = timeout
            self.calls = []
            # Configurable answers
            self.choice_answer = '0'
            self.confidence = 0.9
            self.probabilities = {'0': 0.9, '1': 0.1}
            self.noul_answer = 0.75
            self.noul_confidence = 0.8

        def system_one(self, state, questions, **kw):
            self.calls.append({'state': state, 'questions': questions})
            qname, q = next(iter(questions.items()))
            if isinstance(q, FakeNoul):
                resp = MagicMock()
                resp.nouls = {qname: types.SimpleNamespace(
                    noul=self.noul_answer, confidence=self.noul_confidence)}
                return resp
            # default: Choice
            resp = MagicMock()
            resp.choices = {qname: types.SimpleNamespace(
                choice=self.choice_answer,
                confidence=self.confidence,
                probabilities=self.probabilities,
            )}
            return resp

    return FakeClient, FakeChoice, FakeNoul


class TestJevDecideAction(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import JevProvider
        self.JevProvider = JevProvider

    def _provider_with_client(self, client):
        p = self.JevProvider({'api_key': 'secret'})
        p.client = client
        return p

    def test_returns_none_without_client(self):
        p = self.JevProvider({})
        self.assertIsNone(p.decide_action('state', ['a', 'b']))

    def test_returns_none_when_options_empty(self):
        p = self._provider_with_client(_fake_sdk()[0]())
        self.assertIsNone(p.decide_action('state', []))

    def test_rejects_too_many_options(self):
        p = self._provider_with_client(_fake_sdk()[0]())
        many = [f"opt{i}" for i in range(256)]
        self.assertIsNone(p.decide_action('state', many))

    def test_decide_action_success(self):
        FakeClient, FakeChoice, FakeNoul = _fake_sdk()
        client = FakeClient()
        client.choice_answer = '2'
        client.confidence = 0.85
        client.probabilities = {'0': 0.1, '1': 0.2, '2': 0.7}
        p = self._provider_with_client(client)

        with patch('webapp.llm_handler.JevProvider._import_sdk',
                   return_value=(FakeClient, FakeChoice, FakeNoul)):
            result = p.decide_action('STATE', ['a', 'b', 'c'])

        self.assertIsNotNone(result)
        self.assertEqual(result['index'], 2)
        self.assertEqual(result['choice'], '2')
        self.assertAlmostEqual(result['confidence'], 0.85)
        self.assertEqual(result['probabilities'], {'0': 0.1, '1': 0.2, '2': 0.7})
        # The prompt/state is passed through and criteria map to indices.
        self.assertEqual(client.calls[-1]['state'], 'STATE')
        sent = client.calls[-1]['questions']['action']
        self.assertEqual(sent.criteria, {'0': 'a', '1': 'b', '2': 'c'})

    def test_out_of_range_choice_returns_none(self):
        FakeClient, FakeChoice, FakeNoul = _fake_sdk()
        client = FakeClient()
        client.choice_answer = '5'  # out of range for 3 options
        p = self._provider_with_client(client)

        with patch('webapp.llm_handler.JevProvider._import_sdk',
                   return_value=(FakeClient, FakeChoice, FakeNoul)):
            self.assertIsNone(p.decide_action('STATE', ['a', 'b', 'c']))

    def test_non_integer_choice_returns_none(self):
        FakeClient, FakeChoice, FakeNoul = _fake_sdk()
        client = FakeClient()
        client.choice_answer = 'banana'
        p = self._provider_with_client(client)

        with patch('webapp.llm_handler.JevProvider._import_sdk',
                   return_value=(FakeClient, FakeChoice, FakeNoul)):
            self.assertIsNone(p.decide_action('STATE', ['a', 'b', 'c']))

    def test_sdk_exception_returns_none(self):
        FakeClient, FakeChoice, FakeNoul = _fake_sdk()
        client = FakeClient()

        def boom(state, questions, **kw):
            raise RuntimeError('network down')
        client.system_one = boom
        p = self._provider_with_client(client)

        with patch('webapp.llm_handler.JevProvider._import_sdk',
                   return_value=(FakeClient, FakeChoice, FakeNoul)):
            self.assertIsNone(p.decide_action('STATE', ['a', 'b']))


class TestJevDecideBool(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import JevProvider
        self.JevProvider = JevProvider

    def test_returns_none_without_client(self):
        p = self.JevProvider({})
        self.assertIsNone(p.decide_bool('state', 'Is it safe?'))

    def test_decide_bool_success(self):
        FakeClient, FakeChoice, FakeNoul = _fake_sdk()
        client = FakeClient()
        client.noul_answer = 0.75
        client.noul_confidence = 0.8
        p = self.JevProvider({'api_key': 'secret'})
        p.client = client

        with patch('webapp.llm_handler.JevProvider._import_sdk',
                   return_value=(FakeClient, FakeChoice, FakeNoul)):
            result = p.decide_bool('STATE', 'Is it safe?')

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['noul'], 0.75)
        self.assertAlmostEqual(result['confidence'], 0.8)


class TestJevProviderMisc(unittest.TestCase):
    def setUp(self):
        from webapp.llm_handler import JevProvider
        self.JevProvider = JevProvider

    def test_send_message_returns_note(self):
        p = self.JevProvider({})
        out = p.send_message([{'role': 'user', 'content': 'hi'}])
        self.assertIn('Jev', out)
        self.assertIn('decision', out.lower())

    def test_get_available_models_no_client(self):
        p = self.JevProvider({'model': 'jev-latest'})
        self.assertEqual(p.get_available_models(), ['jev-latest'])

    def test_get_available_models_parses_client(self):
        p = self.JevProvider({'model': 'jev-latest'})
        fake = MagicMock()
        fake.models.list.return_value = types.SimpleNamespace(
            models=['jev-latest', 'jev-1.13.0'])
        p.client = fake
        self.assertEqual(p.get_available_models(), ['jev-latest', 'jev-1.13.0'])

    def test_set_model_updates_client(self):
        p = self.JevProvider({'model': 'jev-latest'})
        fake = MagicMock()
        p.client = fake
        self.assertTrue(p.set_model('jev-1.13.0'))
        self.assertEqual(p.current_model, 'jev-1.13.0')
        self.assertEqual(fake.model, 'jev-1.13.0')

    def test_set_model_empty_false(self):
        p = self.JevProvider({'model': 'jev-latest'})
        self.assertFalse(p.set_model(''))

    def test_context_window_noop(self):
        p = self.JevProvider({})
        self.assertFalse(p.set_context_window(4096))
        self.assertIsNone(p.get_context_window())


class TestJevEnvWiring(unittest.TestCase):
    def setUp(self):
        from webapp.blueprints.helpers import llm_init as mod
        self.mod = mod

    @patch.dict(os.environ, {'LLM_PROVIDER': 'jev', 'TYPESAFE_API_KEY': 'ts_key'})
    def test_dm_jev_with_key(self):
        handler = MagicMock()
        handler.initialize_provider.return_value = True
        result = self.mod.configure_llm_handler_from_environment(handler)
        self.assertTrue(result)
        handler.initialize_provider.assert_called_once_with('jev', {'api_key': 'ts_key'})

    @patch.dict(os.environ, {'LLM_PROVIDER': 'jev', 'TYPESAFE_API_KEY': 'ts_key',
                             'TYPESAFE_BASE_URL': 'https://api.typesafe.ai',
                             'TYPESAFE_MODEL': 'jev-latest'})
    def test_dm_jev_full_config(self):
        handler = MagicMock()
        handler.initialize_provider.return_value = True
        self.mod.configure_llm_handler_from_environment(handler)
        handler.initialize_provider.assert_called_once_with('jev', {
            'api_key': 'ts_key',
            'base_url': 'https://api.typesafe.ai',
            'model': 'jev-latest',
        })

    @patch.dict(os.environ, {'LLM_PROVIDER': 'jev'})
    def test_dm_jev_no_key_still_attempts(self):
        handler = MagicMock()
        handler.initialize_provider.return_value = False
        result = self.mod.configure_llm_handler_from_environment(handler)
        self.assertFalse(result)
        handler.initialize_provider.assert_called_once_with('jev', {})

    @patch.dict(os.environ, {'LLM_PROVIDER': 'jev', 'TYPESAFE_DEFAULT_MODEL': 'jev-1.13.0'})
    def test_dm_jev_default_model_env(self):
        handler = MagicMock()
        handler.initialize_provider.return_value = True
        self.mod.configure_llm_handler_from_environment(handler)
        _, config = handler.initialize_provider.call_args[0]
        self.assertEqual(config.get('model'), 'jev-1.13.0')

    @patch.dict(os.environ, {'NPC_LLM_PROVIDER': 'jev', 'NPC_JEV_API_KEY': 'npc_key'})
    def test_npc_jev_branch(self):
        handler = MagicMock()
        handler.initialize_npc_provider.return_value = True
        result = self.mod.configure_npc_provider_from_environment(handler)
        self.assertTrue(result)
        name, config = handler.initialize_npc_provider.call_args[0]
        self.assertEqual(name, 'jev')
        self.assertEqual(config['api_key'], 'npc_key')
        self.assertIn('timeout', config)

    @patch.dict(os.environ, {'NPC_LLM_PROVIDER': 'jev', 'TYPESAFE_API_KEY': 'shared'})
    def test_npc_jev_falls_back_to_dm_key(self):
        handler = MagicMock()
        handler.initialize_npc_provider.return_value = True
        self.mod.configure_npc_provider_from_environment(handler)
        _, config = handler.initialize_npc_provider.call_args[0]
        self.assertEqual(config.get('api_key'), 'shared')


if __name__ == '__main__':
    unittest.main()
