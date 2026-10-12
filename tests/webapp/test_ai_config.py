"""Layered .ai.yaml configuration: precedence, secrets, per-NPC resolution, wiring."""
import os
import stat
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp import ai_config  # noqa: E402
from webapp.ai_config import AiConfig, SECRET_MASK  # noqa: E402


def _write(path, data):
    path.write_text(yaml.safe_dump(data))


@pytest.fixture
def layers(tmp_path):
    campaign = tmp_path / 'campaign'
    campaign.mkdir()
    webapp_file = tmp_path / 'webapp.ai.yaml'

    def make(campaign_data=None, webapp_data=None, session=None):
        if campaign_data is not None:
            _write(campaign / '.ai.yaml', campaign_data)
        if webapp_data is not None:
            _write(webapp_file, webapp_data)
        config = AiConfig(str(campaign), webapp_path=webapp_file, game_session=session)
        ai_config.set_ai_config(config)
        return config

    yield make
    ai_config.set_ai_config(None)


class _Npc(SimpleNamespace):
    pass


def test_resolve_precedence_campaign_legacy_webapp_env(layers):
    config = layers({'tasks': {'goal_turn_seconds': 3}}, {'tasks': {'goal_turn_seconds': 9, 'background_llm': False}})
    assert config.resolve('tasks.goal_turn_seconds', legacy=lambda: 5) == (3, 'campaign')
    assert config.resolve('tasks.background_llm', legacy=lambda: True) == (True, 'legacy')
    assert config.resolve('tasks.background_llm', legacy=lambda: None) == (False, 'webapp')
    assert config.resolve('tasks.environment_ticks', env=lambda: False) == (False, 'env')


def test_sanitize_rejects_unknown_provider(layers):
    config = layers()
    with pytest.raises(ValueError):
        config.save_layer('campaign', {'providers': {'dm': {'provider': 'gpt-magic'}}})


def test_save_masks_and_preserves_api_key(layers):
    config = layers()
    config.save_layer('campaign', {'providers': {'dm': {'provider': 'llamacpp', 'api_key': 'secret-1'}}})
    path = config.paths['campaign']
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert config.masked()['campaign']['providers']['dm']['api_key'] == SECRET_MASK
    assert config.profile('dm')['provider'] == 'llama_cpp'

    config.save_layer('campaign', {'providers': {'dm': {'provider': 'llama_cpp', 'api_key': SECRET_MASK,
                                                        'model': 'm2'}}})
    assert yaml.safe_load(path.read_text())['providers']['dm'] == {
        'provider': 'llama_cpp', 'model': 'm2', 'api_key': 'secret-1'}


def test_api_key_env_reference(layers, monkeypatch):
    monkeypatch.setenv('MY_KEY', 'from-env')
    config = layers({'providers': {'fast': {'provider': 'openai', 'api_key_env': 'MY_KEY'}}})
    assert config.profile('fast')['api_key'] == 'from-env'


def test_entity_setting_order(layers):
    session = MagicMock()
    config = layers(
        {
            'combat': {'npc_controller': 'ai'},
            'npcs': {'goblin': {'combat': {'controller': 'manual'}},
                     'boss_1': {'combat': {'controller': 'llm'}}},
        },
        session=session,
    )
    boss = _Npc(entity_uid='boss_1', npc_type='goblin')
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    villager = _Npc(entity_uid='v_1', npc_type='commoner')
    sidekick = _Npc(entity_uid='sk_1', npc_type='commoner')
    config.layers['campaign']['sidekicks'] = {'combat': {'controller': 'llm'}}
    config.merged = ai_config.deep_merge(config.layers['webapp'], config.layers['campaign'])
    with patch('natural20.sidekick.is_in_party_sidekick', side_effect=lambda _s, e: e is sidekick):
        assert config.entity_setting(boss, 'combat', 'controller') == ('llm', 'npc:boss_1')
        assert config.entity_setting(grunt, 'combat', 'controller') == ('manual', 'npc:goblin')
        assert config.entity_setting(sidekick, 'combat', 'controller') == ('llm', 'sidekicks')
        assert config.entity_setting(villager, 'combat', 'controller') == ('ai', 'default')
        assert config.entity_setting(villager, 'tasks', 'enabled') == (None, 'unset')


def _handler():
    handler = MagicMock()
    handler.npc_provider = None
    handler.battle_provider = None
    handler.initialize_provider.return_value = True
    handler.initialize_npc_provider.return_value = True
    handler.initialize_battle_provider.return_value = True
    return handler


def test_apply_provider_role_prefers_yaml_profile(layers):
    config = layers({'providers': {'battle': {'provider': 'jev', 'base_url': 'http://jev:8901'}}})
    handler = _handler()
    with patch('webapp.blueprints.helpers.llm_init.configure_battle_provider_from_environment') as env:
        assert ai_config.apply_provider_role(handler, config, 'battle') is True
    env.assert_not_called()
    handler.initialize_battle_provider.assert_called_once_with('jev', {'base_url': 'http://jev:8901'})


def test_apply_provider_role_falls_back_to_env(layers):
    config = layers({'providers': {'dm': {'provider': 'openai', 'api_key': 'x'}}})
    handler = _handler()
    handler.initialize_provider.return_value = False
    with patch('webapp.blueprints.helpers.llm_init.configure_llm_handler_from_environment',
               return_value=True) as env:
        assert ai_config.apply_provider_role(handler, config, 'dm') is True
    env.assert_called_once_with(handler)

    empty = layers({})
    with patch('webapp.blueprints.helpers.llm_init.configure_npc_provider_from_environment',
               return_value=True) as env_npc:
        ai_config.apply_provider_role(handler, empty, 'npc')
    env_npc.assert_called_once_with(handler)


def test_resolver_picks_conversation_and_task_profiles(layers):
    config = layers({
        'providers': {'chatty': {'provider': 'mock'}, 'planner': {'provider': 'mock'}},
        'npcs': {'npc_1': {'conversation': {'provider': 'chatty'}, 'tasks': {'provider': 'planner'}}},
    })
    npc = _Npc(entity_uid='npc_1')
    game = SimpleNamespace(get_entity_by_uid=lambda uid: npc if uid == 'npc_1' else None)
    handler = _handler()
    chatty, planner = object(), object()
    providers = {'chatty': chatty, 'planner': planner}
    with patch.object(ai_config, 'provider_for_profile', side_effect=lambda _h, name: providers.get(name)):
        resolve = ai_config.make_npc_provider_resolver(handler, game)
        assert resolve({'entity_uid': 'npc_1', 'ai_role': 'conversation'}) is chatty
        assert resolve({'entity_uid': 'npc_1', 'ai_role': 'task'}) is planner
        assert resolve({'entity_uid': 'other', 'ai_role': 'task'}) is None
    assert config.profile('chatty') == {'provider': 'mock'}


def test_handler_uses_resolver_for_npc_conversation():
    from webapp.llm_handler import LLMHandler, MockProvider

    handler = LLMHandler()
    handler.initialize_provider('mock', {})
    special = MockProvider()
    special.initialize({})
    special.send_message = MagicMock(return_value='per-npc reply')
    handler.npc_provider_resolver = lambda ctx: special if ctx.get('entity_uid') == 'npc_1' else None
    reply = handler._send_message_impl(
        [{'role': 'system', 'content': 'You are an NPC.'}, {'role': 'user', 'content': 'hi'}],
        {'response_mode': 'conversation', 'skip_continuation': True, 'entity_uid': 'npc_1'},
    )
    assert 'per-npc reply' in reply
    special.send_message.assert_called()


def test_conversation_controller_tags_task_ids():
    from webapp.llm_conversation_controller import LLMConversationController

    handler = MagicMock()
    handler.npc_provider = None
    handler.current_provider = None
    handler.send_message.return_value = 'ok'
    controller = LLMConversationController(handler)
    controller.create_conversation('npc_1:goal', 'sys')
    controller.add_message('npc_1:goal', 'user', 'go')
    controller.generate_response('npc_1:goal')
    context = handler.send_message.call_args[0][1]
    assert context['entity_uid'] == 'npc_1'
    assert context['ai_role'] == 'task'


def test_registry_uses_per_npc_controller(layers):
    from webapp.game_management_components import GameControllerRegistry
    from natural20.generic_controller import GenericController

    layers({'npcs': {'boss': {'combat': {'controller': 'llm', 'provider': 'battle'}}}})
    manager = SimpleNamespace(game_session=None, web_controllers={}, force_llm_npc_combat=False,
                              npc_controller='ai')
    registry = GameControllerRegistry(manager)
    boss = _Npc(entity_uid='boss', familiar=lambda: False)
    grunt = _Npc(entity_uid='grunt', familiar=lambda: False)
    assert registry.effective_npc_combat_controller(boss) == 'llm'
    assert registry.effective_npc_combat_controller(grunt) == 'ai'

    built = {}

    class _FakeLlm:
        def __init__(self, session, llm_provider=None):
            built['provider'] = llm_provider

    handler = MagicMock()
    handler.battle_llm_provider.return_value = 'jev-battle'
    with patch('webapp.game_management_components.LlmMcpController', _FakeLlm), \
            patch.dict(sys.modules, {'webapp.utils': None}), \
            patch('webapp.blueprints.helpers.runtime_state._globals', {'llm_handler': handler}):
        controller = registry.build_combat_controller_for_entity(boss)
        assert isinstance(controller, _FakeLlm)
        assert built['provider'] == 'jev-battle'
        assert isinstance(registry.build_combat_controller_for_entity(grunt), GenericController)


def test_background_toggles(layers, monkeypatch):
    from webapp import npc_background_llm as bg

    session = SimpleNamespace(game_properties={'npc_autonomous_loops': {'enabled': True}})
    monkeypatch.setenv('N20_NPC_LOOPS', '0')
    layers({})
    assert bg.npc_autonomous_loops_enabled(session) is False  # env kill switch beats game.yml
    layers({'tasks': {'autonomous_loops': True}})
    assert bg.npc_autonomous_loops_enabled(session) is True  # .ai.yaml beats env

    monkeypatch.setenv('N20_NPC_LOOPS', '1')
    monkeypatch.setenv('N20_NPC_BACKGROUND_LLM', '0')
    layers({}, {})
    assert bg.npc_background_llm_enabled(SimpleNamespace(game_properties={})) is False
    layers({}, {'tasks': {'background_llm': True}})
    assert bg.npc_background_llm_enabled(SimpleNamespace(game_properties={})) is True


def test_apply_runtime_settings_respects_legacy_and_restores(layers):
    game = SimpleNamespace(npc_controller='ai', force_llm_npc_combat=False, goal_turn_seconds=6,
                           ai_legacy_npc_controller=None)
    config = layers({'combat': {'npc_controller': 'llm'}, 'tasks': {'goal_turn_seconds': 2}})
    ai_config.apply_runtime_settings(game, config)
    assert (game.npc_controller, game.goal_turn_seconds) == ('llm', 2)
    config = layers({})
    ai_config.apply_runtime_settings(game, config)
    assert (game.npc_controller, game.goal_turn_seconds) == ('ai', 6)


class TestAiConfigRoutes:
    @pytest.fixture
    def client(self, layers):
        os.environ.setdefault('TEMPLATE_DIR', os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'templates')))
        from webapp.app import app
        layers({})
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'dm'
        return client

    def test_get_and_save_roundtrip(self, client):
        res = client.get('/ai/config')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] and 'env' in body and 'layers' in body

        with patch.object(ai_config, 'apply_provider_role', return_value=True):
            res = client.post('/ai/config', json={
                'scope': 'campaign',
                'config': {'combat': {'npc_controller': 'llm'},
                           'npcs': {'goblin': {'tasks': {'enabled': False}}}},
            })
        body = res.get_json()
        assert res.status_code == 200, body
        assert body['layers']['campaign']['combat']['npc_controller'] == 'llm'
        assert body['applied'] == {}

        res = client.post('/ai/config/npc', json={'key': 'goblin', 'settings': None})
        assert 'goblin' not in (res.get_json()['layers']['campaign'].get('npcs') or {})

    def test_save_rejects_bad_provider(self, client):
        res = client.post('/ai/config', json={'config': {'providers': {'dm': {'provider': 'nope'}}}})
        assert res.status_code == 400

    def test_requires_dm(self, client):
        with client.session_transaction() as sess:
            sess['username'] = 'not-a-dm'
        assert client.get('/ai/config').status_code == 403
