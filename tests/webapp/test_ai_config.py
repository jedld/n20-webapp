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


# ---------------------------------------------------------------------- #
# Named agent bundles (``agents:``)                                      #
# ---------------------------------------------------------------------- #

AGENT_YAML = {
    'providers': {
        'jev_fast': {'provider': 'jev', 'base_url': 'http://jev:8901'},
        'story': {'provider': 'llama_cpp', 'model': 'big-model'},
    },
    'agents': {
        'brute': {'label': 'Brute', 'controller': 'hybrid', 'provider': 'jev_fast',
                  'strategist_provider': 'story',
                  'directives': 'Focus archers.\nNever pursue.'},
        'skirmish': {'controller': 'llm', 'provider': 'story'},
    },
}


def test_sanitize_agents(layers):
    config = layers(dict(AGENT_YAML))
    assert config.agent_names() == ['brute', 'skirmish']
    assert config.agent('brute')['controller'] == 'hybrid'
    assert config.agent('brute')['directives'] == 'Focus archers.\nNever pursue.'
    assert config.agent('missing') is None
    with pytest.raises(ValueError):
        ai_config.sanitize_layer({'agents': {'x': {'controller': 'magic-model'}}})
    # agent:<name> is a valid per-NPC controller value
    layer = ai_config.sanitize_layer({'npcs': {'g': {'combat': {'controller': 'agent:brute'}}}})
    assert layer['npcs']['g']['combat']['controller'] == 'agent:brute'


def test_agent_for_entity_precedence(layers):
    config = layers({
        **AGENT_YAML,
        'combat': {'npc_agent': 'brute'},
        'sidekicks': {'combat': {'agent': 'skirmish'}},
        'npcs': {'boss_1': {'combat': {'agent': 'skirmish'}}},
    }, session=MagicMock())
    boss = _Npc(entity_uid='boss_1', npc_type='goblin')
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    sidekick = _Npc(entity_uid='sk_1', npc_type='commoner')
    plain = _Npc(entity_uid='v_1', npc_type='treeraptor')
    with patch('natural20.sidekick.is_in_party_sidekick', side_effect=lambda _s, e: e is sidekick):
        name, definition, source = config.agent_for_entity(boss)
        assert (name, source) == ('skirmish', 'npc:boss_1')
        assert definition['provider'] == 'story'
        name, _definition, source = config.agent_for_entity(grunt)
        assert (name, source) == ('brute', 'default')
        name, _definition, source = config.agent_for_entity(sidekick)
        assert (name, source) == ('skirmish', 'sidekicks')
        # no fallback configured for this NPC kind chain
        config.layers['campaign']['combat'].pop('npc_agent')
        config.merged = ai_config.deep_merge(config.layers['webapp'], config.layers['campaign'])
        name, definition, _source = config.agent_for_entity(plain)
        assert name is None and definition is None


def test_entity_combat_controller_uses_agent_kind(layers):
    config = layers({
        'combat': {'npc_controller': 'ai'},
        'agents': {'brute': {'controller': 'hybrid'}},
        'npcs': {'goblin': {'combat': {'agent': 'brute'}}},
    })
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    assert ai_config.entity_combat_controller(grunt) == 'hybrid'
    # explicit per-NPC controller beats the agent bundle's kind
    config.layers['campaign']['npcs']['goblin']['combat']['controller'] = 'llm'
    config.merged = ai_config.deep_merge(config.layers['webapp'], config.layers['campaign'])
    assert ai_config.entity_combat_controller(grunt) == 'llm'


def test_agent_provider_beats_global_default(layers):
    config = layers(dict(AGENT_YAML))
    config.layers['campaign']['combat'] = {'npc_agent': 'brute',
                                           'llm_provider': 'plain_default',
                                           'strategist_provider': 'plain_default'}
    config.merged = ai_config.deep_merge(config.layers['webapp'], config.layers['campaign'])
    handler = _handler()
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    with patch('webapp.ai_config.provider_for_profile') as pfp, \
            patch('natural20.sidekick.is_in_party_sidekick', return_value=False):
        pfp.return_value = 'agent-provider'
        assert ai_config.combat_llm_provider_for(handler, grunt) == 'agent-provider'
        pfp.assert_called_once_with(handler, 'jev_fast')
        pfp.reset_mock()
        pfp.return_value = 'strategist-provider'
        assert ai_config.strategist_llm_provider_for(handler, grunt) == 'strategist-provider'
        pfp.assert_called_once_with(handler, 'story')


def test_controller_spec_parsing(layers):
    layers(dict(AGENT_YAML))
    assert ai_config.parse_controller_spec('agent:Brute') == ('agent', 'brute')
    assert ai_config.parse_controller_spec('LLM') == ('kind', 'llm')
    assert ai_config.is_controller_spec('hybrid')
    assert ai_config.is_controller_spec('agent:brute')
    assert not ai_config.is_controller_spec('agent:nope')
    assert not ai_config.is_controller_spec('magic')
    assert not ai_config.is_controller_spec(None)


def test_build_combat_controller_from_agent(layers):
    from natural20.llm_controller import HybridStrategistController, LlmMcpController
    from natural20.generic_controller import GenericController

    layers({**AGENT_YAML,
            'agents': {**AGENT_YAML['agents'],
                       'guard': {'controller': 'ai'},
                       'handler': {'controller': 'manual'}}})
    handler = _handler()
    brute = ai_config.build_combat_controller_from_agent(None, handler, None, 'brute')
    assert isinstance(brute, HybridStrategistController)
    assert brute.agent_name == 'brute'
    assert brute.agent_persona == 'Focus archers.\nNever pursue.'
    assert brute.agent_profile == 'jev_fast'
    assert brute.agent_strategist_profile == 'story'

    skirmish = ai_config.build_combat_controller_from_agent(None, handler, None, 'skirmish')
    assert isinstance(skirmish, LlmMcpController)
    assert skirmish.agent_name == 'skirmish'

    guard = ai_config.build_combat_controller_from_agent(None, handler, None, 'guard')
    assert isinstance(guard, GenericController)

    # unknown / manual bundles defer to the caller's manual handling
    assert ai_config.build_combat_controller_from_agent(None, handler, None, 'nope') is None

    assert ai_config.build_combat_controller_from_agent(None, handler, None, 'handler') is None

    # unified spec dispatcher
    spec = ai_config.build_combat_controller_for_spec(None, handler, None, 'agent:skirmish')
    assert isinstance(spec, LlmMcpController) and spec.agent_name == 'skirmish'
    assert ai_config.build_combat_controller_for_spec(None, handler, None, 'manual') is None
    llm = ai_config.build_combat_controller_for_spec(None, handler, None, 'llm')
    assert isinstance(llm, LlmMcpController) and llm.agent_name is None


# ---------------------------------------------------------------------- #
# ``combat.strategist``: hybrid planner mode (follows INT by default)     #
# ---------------------------------------------------------------------- #

def test_strategist_mode_precedence(layers):
    layers({'combat': {'strategist': 'always'},
            'npcs': {'goblin': {'combat': {'strategist': 'off'}}}})
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    other = _Npc(entity_uid='v_1', npc_type='treeraptor')
    assert ai_config.strategist_mode_for() == 'always'
    assert ai_config.strategist_mode_for(grunt) == 'off'
    assert ai_config.strategist_mode_for(other) == 'always'


def test_strategist_mode_defaults_to_auto(layers):
    layers({'combat': {'strategist': 'daydream'}})   # unknown value is dropped
    assert ai_config.strategist_mode_for() == 'auto'
    assert ai_config.strategist_mode_for(_Npc(entity_uid='x', npc_type='goblin')) == 'auto'


def test_agent_bundle_can_set_the_strategist_mode(layers):
    layers({**AGENT_YAML,
            'agents': {**AGENT_YAML['agents'],
                       'planner': {'controller': 'hybrid', 'strategist': 'always'}}})
    handler = _handler()
    controller = ai_config.build_combat_controller_from_agent(None, handler, None, 'planner')
    assert controller.strategist_mode == 'always'


def test_hybrid_controller_gets_the_configured_mode(layers):
    layers({'combat': {'strategist': 'off'},
            'npcs': {'goblin': {'combat': {'strategist': 'always'}}}})
    handler = _handler()
    grunt = _Npc(entity_uid='g_2', npc_type='goblin')
    with patch('natural20.sidekick.is_in_party_sidekick', return_value=False):
        hybrid = ai_config.build_llm_combat_controller(None, handler, grunt, kind='hybrid')
    assert hybrid.strategist_mode == 'always'
    # plain llm controllers have no planner to configure
    plain = ai_config.build_llm_combat_controller(None, handler, None, kind='llm')
    assert not hasattr(plain, 'strategist_mode')


def test_registry_builds_agent_spec(layers):
    from webapp.game_management_components import GameControllerRegistry
    from natural20.llm_controller import HybridStrategistController

    layers(dict(AGENT_YAML))
    manager = SimpleNamespace(game_session=None, web_controllers={},
                              force_llm_npc_combat=False, npc_controller='agent:brute')
    registry = GameControllerRegistry(manager)
    grunt = _Npc(entity_uid='g_2', npc_type='goblin', familiar=lambda: False)
    with patch('natural20.sidekick.is_in_party_sidekick', return_value=False):
        assert registry.effective_npc_combat_controller(grunt) == 'agent:brute'
        controller = registry.build_combat_controller_for_entity(grunt)
    assert isinstance(controller, HybridStrategistController)
    assert controller.agent_name == 'brute'
    assert controller.agent_persona == 'Focus archers.\nNever pursue.'


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
