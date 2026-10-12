"""Layered AI configuration (``.ai.yaml``) for providers, combat, conversation and tasks.

Resolution order (first hit wins):

1. ``<campaign>/.ai.yaml``      — campaign specific (default save target)
2. legacy campaign settings     — ``game.yml`` blocks / ``index.json`` (only where they existed)
3. ``webapp/.ai.yaml``          — installation-wide defaults (``N20_AI_CONFIG_PATH`` overrides the path)
4. environment variables        — ``LLM_PROVIDER``, ``NPC_*``, ``BATTLE_*``, ``N20_NPC_*`` …

Per-entity settings (``npcs``) are keyed by ``entity_uid`` or NPC type and fall
back to the ``sidekicks`` block (in-party sidekicks only) and then to the
general ``combat`` / ``conversation`` / ``tasks`` defaults. See ``docs/AI_CONFIG.md``.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

AI_CONFIG_FILENAME = '.ai.yaml'
CONFIG_VERSION = 1
SECRET_MASK = '********'

PROVIDER_NAMES = ('llama_cpp', 'ollama', 'openai', 'anthropic', 'jev', 'mock')
RESERVED_PROFILES = ('dm', 'npc', 'battle')
CONTROLLER_KINDS = ('ai', 'llm', 'manual')
SCOPES = ('campaign', 'webapp')

_PROFILE_STR_KEYS = ('provider', 'base_url', 'model', 'api_key', 'api_key_env', 'label')
_PROFILE_INT_KEYS = ('context_window', 'timeout', 'max_tokens')
_ENTITY_SECTIONS = {
    'combat': {'controller': 'controller', 'provider': 'profile'},
    'conversation': {'provider': 'profile'},
    'tasks': {'enabled': 'bool', 'provider': 'profile'},
}
_GLOBAL_SECTIONS = {
    'combat': {'npc_controller': 'controller', 'llm_provider': 'profile', 'force_llm': 'bool'},
    'conversation': {'enabled': 'bool', 'provider': 'profile'},
    'tasks': {
        'provider': 'profile',
        'autonomous_loops': 'bool',
        'background_llm': 'bool',
        'environment_ticks': 'bool',
        'goal_turn_seconds': 'int',
    },
}
# Per-entity key -> general default it falls back to.
_ENTITY_FALLBACK = {
    ('combat', 'controller'): ('combat', 'npc_controller'),
    ('combat', 'provider'): ('combat', 'llm_provider'),
    ('conversation', 'provider'): ('conversation', 'provider'),
    ('tasks', 'provider'): ('tasks', 'provider'),
}

_ENV_PROVIDER_VARS = {
    'llama_cpp': ('LLAMA_CPP_BASE_URL', 'LLAMA_CPP_MODEL', 'LLAMA_CPP_API_KEY'),
    'ollama': ('OLLAMA_BASE_URL', 'OLLAMA_MODEL', None),
    'openai': ('OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENAI_API_KEY'),
    'anthropic': (None, 'ANTHROPIC_MODEL', 'ANTHROPIC_API_KEY'),
    'jev': ('TYPESAFE_BASE_URL', 'TYPESAFE_MODEL', 'TYPESAFE_API_KEY'),
    'mock': (None, None, None),
}
_DISABLED = frozenset({'0', 'false', 'no', 'off', 'disabled'})


def normalize_provider_name(name: Any) -> Optional[str]:
    value = str(name or '').strip().lower().replace('.', '_').replace('-', '_')
    if value in ('llamacpp',):
        value = 'llama_cpp'
    return value or None


def webapp_config_path() -> Path:
    override = os.environ.get('N20_AI_CONFIG_PATH')
    if override and override.strip():
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / AI_CONFIG_FILENAME


def campaign_config_path(campaign_root: Optional[str]) -> Optional[Path]:
    if not campaign_root:
        return None
    return Path(campaign_root) / AI_CONFIG_FILENAME


def _load_yaml(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        with path.open(encoding='utf-8') as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("[AI config] Failed to read %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _dig(data: Dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _coerce(kind: str, value: Any) -> Any:
    if value is None or value == '':
        return None
    if kind == 'bool':
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in _DISABLED
    if kind == 'int':
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if kind == 'controller':
        value = str(value).strip().lower()
        return value if value in CONTROLLER_KINDS else None
    if kind == 'profile':
        value = str(value).strip()
        return value or None
    return value


def _sanitize_profile(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in _PROFILE_STR_KEYS:
        value = raw.get(key)
        if value is None or str(value).strip() == '':
            continue
        out[key] = str(value).strip()
    if 'provider' in out:
        out['provider'] = normalize_provider_name(out['provider'])
        if out['provider'] not in PROVIDER_NAMES:
            raise ValueError(f"Unknown provider {raw.get('provider')!r}; expected one of {', '.join(PROVIDER_NAMES)}")
    for key in _PROFILE_INT_KEYS:
        value = _coerce('int', raw.get(key))
        if value is not None:
            out[key] = value
    if 'enabled' in raw and raw.get('enabled') is not None:
        out['enabled'] = _coerce('bool', raw.get('enabled'))
    return out


def _sanitize_section(raw: Any, schema: Dict[str, str]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, kind in schema.items():
        value = _coerce(kind, raw.get(key))
        if value is not None:
            out[key] = value
    return out


def _sanitize_entity(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for section, schema in _ENTITY_SECTIONS.items():
        cleaned = _sanitize_section(raw.get(section), schema)
        if cleaned:
            out[section] = cleaned
    return out


def sanitize_layer(raw: Any) -> Dict[str, Any]:
    """Validate a layer payload; unknown keys are dropped, bad provider names raise."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {'version': CONFIG_VERSION}
    providers = {}
    for name, profile in (raw.get('providers') or {}).items():
        name = str(name or '').strip()
        cleaned = _sanitize_profile(profile)
        if name and cleaned:
            providers[name] = cleaned
    if providers:
        out['providers'] = providers
    for section, schema in _GLOBAL_SECTIONS.items():
        cleaned = _sanitize_section(raw.get(section), schema)
        if cleaned:
            out[section] = cleaned
    sidekicks = _sanitize_entity(raw.get('sidekicks'))
    if sidekicks:
        out['sidekicks'] = sidekicks
    npcs = {}
    for key, settings in (raw.get('npcs') or {}).items():
        key = str(key or '').strip()
        cleaned = _sanitize_entity(settings)
        if key and cleaned:
            npcs[key] = cleaned
    if npcs:
        out['npcs'] = npcs
    return out


def mask_layer(layer: Dict[str, Any]) -> Dict[str, Any]:
    masked = copy.deepcopy(layer or {})
    for profile in (masked.get('providers') or {}).values():
        if isinstance(profile, dict) and profile.get('api_key'):
            profile['api_key'] = SECRET_MASK
    return masked


def _restore_masked_secrets(new_layer: Dict[str, Any], old_layer: Dict[str, Any]) -> None:
    old_providers = (old_layer or {}).get('providers') or {}
    for name, profile in (new_layer.get('providers') or {}).items():
        if profile.get('api_key') == SECRET_MASK:
            previous = (old_providers.get(name) or {}).get('api_key')
            if previous:
                profile['api_key'] = previous
            else:
                profile.pop('api_key', None)


def _env_enabled(name: str, default: str = '1') -> bool:
    return os.environ.get(name, default).strip().lower() not in _DISABLED


def _env_profile(provider: Optional[str], *, prefix: str = '') -> Dict[str, Any]:
    provider = normalize_provider_name(provider) or 'llama_cpp'
    url_var, model_var, key_var = _ENV_PROVIDER_VARS.get(provider, (None, None, None))

    def first(*names):
        for name in names:
            if name and os.environ.get(name):
                return os.environ.get(name)
        return None

    up = provider.upper()
    base_url = first(f'{prefix}BASE_URL' if prefix else None, f'{prefix}{up}_BASE_URL' if prefix else None, url_var)
    model = first(f'{prefix}MODEL' if prefix else None, f'{prefix}{up}_MODEL' if prefix else None, model_var,
                  'TYPESAFE_DEFAULT_MODEL' if provider == 'jev' else None)
    api_key = first(f'{prefix}API_KEY' if prefix else None, f'{prefix}{up}_API_KEY' if prefix else None, key_var)
    profile = {'provider': provider}
    if base_url:
        profile['base_url'] = base_url
    if model:
        profile['model'] = model
    profile['api_key_set'] = bool(api_key)
    return profile


def env_summary() -> Dict[str, Any]:
    """Read-only view of what environment variables configure (shown as fallbacks in the UI)."""
    providers = {'dm': _env_profile(os.environ.get('LLM_PROVIDER', 'llama_cpp'))}
    if _env_enabled('NPC_LLM_ENABLED'):
        providers['npc'] = _env_profile(os.environ.get('NPC_LLM_PROVIDER', 'ollama'), prefix='NPC_')
    battle = os.environ.get('BATTLE_LLM_PROVIDER', '').strip()
    if battle:
        providers['battle'] = _env_profile(battle, prefix='BATTLE_TYPESAFE_')
    return {
        'providers': providers,
        'combat': {
            'force_llm': _env_enabled('NPC_LLM_COMBAT_ENABLED', '0'),
        },
        'conversation': {'enabled': _env_enabled('NPC_LLM_ENABLED')},
        'tasks': {
            'autonomous_loops': _env_enabled('N20_NPC_LOOPS'),
            'background_llm': _env_enabled('N20_NPC_BACKGROUND_LLM'),
            'environment_ticks': _env_enabled('N20_NPC_ENV_TICKS'),
            'goal_turn_seconds': int(os.environ.get('NPC_GOAL_TURN_SECONDS', '6') or 6),
        },
    }


class AiConfig:
    """Campaign + webapp ``.ai.yaml`` layers with merge, lookup and save helpers."""

    def __init__(self, campaign_root: Optional[str] = None, *, webapp_path: Optional[Path] = None,
                 game_session: Any = None):
        self.campaign_root = campaign_root
        self.game_session = game_session
        self.paths = {
            'campaign': campaign_config_path(campaign_root),
            'webapp': Path(webapp_path) if webapp_path else webapp_config_path(),
        }
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self.layers = {scope: _load_yaml(path) for scope, path in self.paths.items()}
            self.merged = deep_merge(self.layers['webapp'], self.layers['campaign'])

    # -- generic lookup -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        value = _dig(self.merged, dotted)
        return default if value is None else value

    def source_of(self, dotted: str) -> Optional[str]:
        for scope in ('campaign', 'webapp'):
            if _dig(self.layers.get(scope) or {}, dotted) is not None:
                return scope
        return None

    def resolve(self, dotted: str, *, legacy: Optional[Callable[[], Any]] = None,
                env: Optional[Callable[[], Any]] = None, default: Any = None) -> Tuple[Any, str]:
        """campaign .ai.yaml -> legacy campaign setting -> webapp .ai.yaml -> env -> default."""
        value = _dig(self.layers.get('campaign') or {}, dotted)
        if value is not None:
            return value, 'campaign'
        if legacy is not None:
            value = legacy()
            if value is not None:
                return value, 'legacy'
        value = _dig(self.layers.get('webapp') or {}, dotted)
        if value is not None:
            return value, 'webapp'
        if env is not None:
            value = env()
            if value is not None:
                return value, 'env'
        return default, 'default'

    # -- provider profiles ---------------------------------------------
    def profile(self, name: str) -> Optional[Dict[str, Any]]:
        profile = (self.merged.get('providers') or {}).get(name)
        if not isinstance(profile, dict) or not profile.get('provider'):
            return None
        profile = dict(profile)
        env_name = profile.get('api_key_env')
        if env_name and not profile.get('api_key') and os.environ.get(env_name):
            profile['api_key'] = os.environ.get(env_name)
        return profile

    def profile_names(self) -> list:
        names = list(RESERVED_PROFILES)
        for name in (self.merged.get('providers') or {}):
            if name not in names:
                names.append(name)
        return names

    # -- per-entity settings -------------------------------------------
    def _entity_keys(self, entity) -> Iterable[str]:
        uid = getattr(entity, 'entity_uid', None)
        if uid:
            yield str(uid)
        npc_type = getattr(entity, 'npc_type', None)
        if npc_type:
            yield str(npc_type)

    def _is_sidekick(self, entity) -> bool:
        if self.game_session is None:
            return False
        try:
            from natural20.sidekick import is_in_party_sidekick
            return bool(is_in_party_sidekick(self.game_session, entity))
        except Exception:
            return False

    def entity_setting(self, entity, section: str, key: str) -> Tuple[Any, str]:
        """Per-NPC value -> sidekick defaults -> general default. Returns ``(value, source)``."""
        npcs = self.merged.get('npcs') or {}
        for entity_key in self._entity_keys(entity):
            value = _dig(npcs.get(entity_key) or {}, f'{section}.{key}')
            if value is not None:
                return value, f'npc:{entity_key}'
        if entity is not None and self._is_sidekick(entity):
            value = _dig(self.merged.get('sidekicks') or {}, f'{section}.{key}')
            if value is not None:
                return value, 'sidekicks'
        fallback = _ENTITY_FALLBACK.get((section, key))
        if fallback:
            value = _dig(self.merged, '.'.join(fallback))
            if value is not None:
                return value, 'default'
        return None, 'unset'

    # -- persistence ----------------------------------------------------
    def masked(self) -> Dict[str, Dict[str, Any]]:
        return {scope: mask_layer(layer) for scope, layer in self.layers.items()}

    def save_layer(self, scope: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        path = self.paths.get(scope)
        if path is None:
            raise ValueError('No campaign is loaded; save to the webapp scope instead')
        layer = sanitize_layer(raw)
        with self._lock:
            _restore_masked_secrets(layer, self.layers.get(scope) or {})
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + '.tmp')
            with tmp.open('w', encoding='utf-8') as fh:
                fh.write('# Natural20 AI configuration (managed by the DM Console > AI Settings panel).\n')
                fh.write('# May contain API keys: keep it out of version control or use api_key_env.\n')
                yaml.safe_dump(layer, fh, sort_keys=False, default_flow_style=False)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            self.reload()
        return layer


# ---------------------------------------------------------------------------
# Active config + runtime application
# ---------------------------------------------------------------------------

_active: Optional[AiConfig] = None
_profile_cache: Dict[str, Tuple[Dict[str, Any], Any]] = {}
_cache_lock = threading.Lock()


def get_ai_config() -> Optional[AiConfig]:
    return _active


def set_ai_config(config: Optional[AiConfig]) -> None:
    global _active
    _active = config
    with _cache_lock:
        _profile_cache.clear()


def provider_config_from_profile(profile: Dict[str, Any], *, role: str) -> Tuple[str, Dict[str, Any]]:
    provider = normalize_provider_name(profile.get('provider'))
    config: Dict[str, Any] = {}
    for key in ('base_url', 'model', 'api_key', 'context_window', 'timeout', 'max_tokens'):
        if profile.get(key) not in (None, ''):
            config[key] = profile[key]
    if provider == 'llama_cpp':
        config.setdefault('api_key', 'llama-cpp')
        if role in ('npc', 'task') and 'max_tokens' not in config:
            config['max_tokens'] = 320
    if role in ('npc', 'task') and 'timeout' not in config:
        from webapp.blueprints.helpers.llm_init import npc_llm_request_timeout
        config['timeout'] = npc_llm_request_timeout(provider)
    return provider, config


def _apply_context_limit(provider, role: str) -> None:
    try:
        from webapp.llm_context_budget import apply_context_limit_to_provider
        apply_context_limit_to_provider(provider, role=role)
    except Exception:
        logger.debug("[AI config] context limit not applied for %s", role, exc_info=True)


def apply_provider_role(handler, config: Optional[AiConfig], role: str) -> bool:
    """(Re)initialize one handler slot from ``.ai.yaml`` or, when unset, from env vars."""
    from webapp.blueprints.helpers import llm_init

    profile = config.profile(role) if config is not None else None
    if role == 'dm':
        if profile is not None:
            name, provider_config = provider_config_from_profile(profile, role='dm')
            if handler.initialize_provider(name, provider_config):
                _apply_context_limit(handler.current_provider, 'dm')
                return True
            logger.warning("[AI config] dm profile (%s) failed; falling back to env vars", name)
        return bool(llm_init.configure_llm_handler_from_environment(handler))
    if role == 'npc':
        handler.npc_provider = None
        if profile is not None:
            if profile.get('enabled') is False:
                return False
            name, provider_config = provider_config_from_profile(profile, role='npc')
            if handler.initialize_npc_provider(name, provider_config):
                if handler.npc_provider is not None:
                    _apply_context_limit(handler.npc_provider, 'npc')
                return True
            logger.warning("[AI config] npc profile (%s) failed; falling back to env vars", name)
        elif config is not None and config.get('conversation.enabled') is False:
            return False
        return bool(llm_init.configure_npc_provider_from_environment(handler))
    if role == 'battle':
        handler.battle_provider = None
        if profile is not None:
            if profile.get('enabled') is False:
                return False
            name, provider_config = provider_config_from_profile(profile, role='battle')
            if handler.initialize_battle_provider(name, provider_config):
                return True
            logger.warning("[AI config] battle profile (%s) failed; falling back to env vars", name)
        return bool(llm_init.configure_battle_provider_from_environment(handler))
    raise ValueError(f'Unknown provider role {role!r}')


def provider_for_profile(handler, name: Optional[str]):
    """Provider instance for a profile name (reserved slots or a cached custom profile)."""
    if handler is None or not name:
        return None
    if name == 'dm':
        return getattr(handler, 'current_provider', None)
    if name == 'npc':
        return getattr(handler, 'npc_provider', None) or getattr(handler, 'current_provider', None)
    if name == 'battle':
        fn = getattr(handler, 'battle_llm_provider', None)
        return fn() if callable(fn) else getattr(handler, 'current_provider', None)
    config = get_ai_config()
    profile = config.profile(name) if config is not None else None
    if profile is None or profile.get('enabled') is False:
        return None
    with _cache_lock:
        cached = _profile_cache.get(name)
        if cached is not None and cached[0] == profile:
            return cached[1]
    provider_name, provider_config = provider_config_from_profile(profile, role='task')
    try:
        provider = handler._instantiate_provider(provider_name)
        if not provider.initialize(provider_config):
            logger.error("[AI config] Profile %r (%s) failed to initialize", name, provider_name)
            return None
    except Exception as exc:
        logger.error("[AI config] Profile %r could not be created: %s", name, exc)
        return None
    with _cache_lock:
        _profile_cache[name] = (profile, provider)
    return provider


def entity_combat_controller(entity) -> Optional[str]:
    config = get_ai_config()
    if config is None or entity is None:
        return None
    value, _source = config.entity_setting(entity, 'combat', 'controller')
    return value if value in CONTROLLER_KINDS else None


def combat_llm_provider_for(handler, entity=None):
    """Provider for an ``llm`` combat controller: per-NPC profile -> combat default -> battle slot."""
    config = get_ai_config()
    if config is not None and entity is not None:
        profile, _source = config.entity_setting(entity, 'combat', 'provider')
    else:
        profile = config.get('combat.llm_provider') if config is not None else None
    provider = provider_for_profile(handler, profile) if profile else None
    if provider is not None:
        return provider
    fn = getattr(handler, 'battle_llm_provider', None)
    if callable(fn):
        return fn()
    return getattr(handler, 'current_provider', None)


def entity_tasks_enabled(entity) -> bool:
    config = get_ai_config()
    if config is None or entity is None:
        return True
    value, _source = config.entity_setting(entity, 'tasks', 'enabled')
    return value is not False


def make_npc_provider_resolver(handler, game) -> Callable[[Dict[str, Any]], Any]:
    """Resolver installed on ``LLMHandler`` to pick per-NPC conversation/task providers."""

    def resolve(context: Dict[str, Any]):
        config = get_ai_config()
        uid = (context or {}).get('entity_uid')
        if config is None or not uid:
            return None
        try:
            entity = game.get_entity_by_uid(uid)
        except Exception:
            entity = None
        if entity is None:
            return None
        profile = None
        if (context or {}).get('ai_role') == 'task':
            profile, _source = config.entity_setting(entity, 'tasks', 'provider')
        if not profile:
            profile, _source = config.entity_setting(entity, 'conversation', 'provider')
        return provider_for_profile(handler, profile) if profile else None

    return resolve


def apply_runtime_settings(game, config: Optional[AiConfig]) -> None:
    """Push non-provider settings (combat defaults, goal cadence) onto the game manager."""
    if game is None or config is None:
        return
    # Values the game booted with (index.json / env), restored when a setting is removed.
    baseline = getattr(game, '_ai_baseline', None)
    if baseline is None:
        baseline = {
            'npc_controller': getattr(game, 'npc_controller', None) or 'ai',
            'force_llm': bool(getattr(game, 'force_llm_npc_combat', False)),
            'goal_turn_seconds': getattr(game, 'goal_turn_seconds', 6),
        }
        game._ai_baseline = baseline
    controller, _source = config.resolve(
        'combat.npc_controller',
        legacy=lambda: getattr(game, 'ai_legacy_npc_controller', None),
        default=baseline['npc_controller'],
    )
    if controller in CONTROLLER_KINDS:
        game.npc_controller = controller
    force_llm, _source = config.resolve('combat.force_llm', default=baseline['force_llm'])
    game.force_llm_npc_combat = bool(force_llm)
    goal_seconds, _source = config.resolve('tasks.goal_turn_seconds', default=baseline['goal_turn_seconds'])
    if isinstance(goal_seconds, int) and goal_seconds > 0:
        game.goal_turn_seconds = goal_seconds


def configure_ai(handler, game=None, game_session=None, *, roles: Iterable[str] = RESERVED_PROFILES) -> AiConfig:
    """Load ``.ai.yaml`` layers, initialize provider slots, and install runtime hooks."""
    session = game_session or getattr(game, 'game_session', None)
    campaign_root = getattr(session, 'root_path', None) if session is not None else None
    config = AiConfig(campaign_root, game_session=session)
    set_ai_config(config)
    for role in roles:
        try:
            apply_provider_role(handler, config, role)
        except Exception:
            logger.exception("[AI config] Failed to apply %s provider", role)
    if game is not None:
        handler.npc_provider_resolver = make_npc_provider_resolver(handler, game)
        apply_runtime_settings(game, config)
    return config


def list_npcs(game) -> list:
    """Non-PC creatures on loaded maps, with their resolved AI settings (for the DM panel)."""
    from natural20.player_character import PlayerCharacter

    config = get_ai_config()
    seen = set()
    rows = []
    for map_name, battle_map in (getattr(game, 'maps', None) or {}).items():
        try:
            entities = list(battle_map.entities)
        except Exception:
            continue
        for entity in entities:
            uid = str(getattr(entity, 'entity_uid', '') or '')
            if not uid or uid in seen or isinstance(entity, PlayerCharacter):
                continue
            seen.add(uid)
            try:
                label = entity.label()
            except Exception:
                label = getattr(entity, 'name', uid)
            row = {
                'entity_uid': uid,
                'label': str(label),
                'npc_type': getattr(entity, 'npc_type', None),
                'map': map_name,
                'dialog': bool(getattr(entity, 'dialog', False)),
                'sidekick': bool(config._is_sidekick(entity)) if config is not None else False,
                'resolved': {},
            }
            if config is not None:
                for section, keys in _ENTITY_SECTIONS.items():
                    for key in keys:
                        value, source = config.entity_setting(entity, section, key)
                        row['resolved'][f'{section}.{key}'] = {'value': value, 'source': source}
            rows.append(row)
    rows.sort(key=lambda r: (not r['sidekick'], r['label'].lower()))
    return rows


def set_entity_override(config: AiConfig, scope: str, key: str, settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Upsert (or delete when ``settings`` is falsy) one ``npcs`` entry in a layer."""
    key = str(key or '').strip()
    if not key:
        raise ValueError('key (entity_uid or npc type) is required')
    layer = copy.deepcopy(config.layers.get(scope) or {})
    npcs = dict(layer.get('npcs') or {})
    cleaned = _sanitize_entity(settings or {})
    if cleaned:
        npcs[key] = cleaned
    else:
        npcs.pop(key, None)
    layer['npcs'] = npcs
    return config.save_layer(scope, layer)


def config_payload(game=None, handler=None) -> Dict[str, Any]:
    """Everything the AI Settings panel needs (secrets masked)."""
    config = get_ai_config()
    payload: Dict[str, Any] = {
        'provider_names': list(PROVIDER_NAMES),
        'controller_kinds': list(CONTROLLER_KINDS),
        'reserved_profiles': list(RESERVED_PROFILES),
        'secret_mask': SECRET_MASK,
        'env': env_summary(),
    }
    if config is not None:
        payload.update({
            'paths': {scope: (str(path) if path else None) for scope, path in config.paths.items()},
            'layers': config.masked(),
            'effective': mask_layer(config.merged),
            'profiles': config.profile_names(),
        })
    if handler is not None:
        status = {}
        for role, getter in (('dm', 'get_provider_info'), ('npc', 'get_npc_provider_info'),
                             ('battle', 'get_battle_provider_info')):
            fn = getattr(handler, getter, None)
            try:
                status[role] = fn() if callable(fn) else {}
            except Exception as exc:
                status[role] = {'error': str(exc)}
        payload['status'] = status
    if game is not None:
        payload['npcs'] = list_npcs(game)
        payload['runtime'] = {
            'npc_controller': getattr(game, 'npc_controller', None),
            'force_llm': bool(getattr(game, 'force_llm_npc_combat', False)),
            'goal_turn_seconds': getattr(game, 'goal_turn_seconds', None),
        }
    return payload


def save_and_apply(handler, game, scope: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a layer, then re-initialize only the provider slots whose settings changed."""
    config = get_ai_config()
    if config is None:
        raise ValueError('AI configuration is not initialized')
    before = copy.deepcopy(config.merged)
    config.save_layer(scope, raw)
    return reapply(handler, game, before)


def reapply(handler, game, before: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = get_ai_config()
    roles = list(RESERVED_PROFILES) if before is None else changed_provider_roles(before, config.merged)
    with _cache_lock:
        _profile_cache.clear()
    results = {}
    for role in roles:
        try:
            results[role] = apply_provider_role(handler, config, role)
        except Exception as exc:
            logger.exception("[AI config] Failed to apply %s provider", role)
            results[role] = False
            results[f'{role}_error'] = str(exc)
    if game is not None:
        apply_runtime_settings(game, config)
    return results


def test_profile(handler, profile: Dict[str, Any], *, name: Optional[str] = None) -> Dict[str, Any]:
    """Instantiate a provider from a (possibly unsaved) profile and try to initialize it."""
    cleaned = _sanitize_profile(profile)
    if not cleaned.get('provider'):
        raise ValueError('provider is required')
    if cleaned.get('api_key') in (None, SECRET_MASK) and name:
        config = get_ai_config()
        saved = config.profile(name) if config is not None else None
        if saved and saved.get('api_key'):
            cleaned['api_key'] = saved['api_key']
    if cleaned.get('api_key') == SECRET_MASK:
        cleaned.pop('api_key')
    if not cleaned.get('api_key') and cleaned.get('api_key_env'):
        cleaned['api_key'] = os.environ.get(cleaned['api_key_env']) or None
    provider_name, provider_config = provider_config_from_profile(cleaned, role='test')
    provider = handler._instantiate_provider(provider_name)
    ok = bool(provider.initialize(provider_config))
    result = {
        'success': ok,
        'provider': provider_name,
        'model': getattr(provider, 'current_model', None) or getattr(provider, 'model', None),
    }
    models = getattr(provider, 'available_models', None)
    if isinstance(models, (list, tuple)):
        result['available_models'] = list(models)[:50]
    return result


def changed_provider_roles(before: Dict[str, Any], after: Dict[str, Any]) -> list:
    before_p = (before or {}).get('providers') or {}
    after_p = (after or {}).get('providers') or {}
    roles = [role for role in RESERVED_PROFILES if before_p.get(role) != after_p.get(role)]
    if (before or {}).get('conversation', {}).get('enabled') != (after or {}).get('conversation', {}).get('enabled'):
        if 'npc' not in roles:
            roles.append('npc')
    return roles
