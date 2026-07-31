"""Context window resolution and prompt compaction for LLM providers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from webapp.llm_handler import estimate_llm_tokens, summarize_llm_messages

logger = logging.getLogger(__name__)

_OVERHEAR_PREFIXES = (
    'you overhear',
    ' says (in ',
    'said to no one in particular',
)
_ENTITY_CONTEXT_PREFIX = 'you are responding as'


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get('text') or part.get('content') or ''))
        return ''.join(parts)
    return str(content or '')


def context_limit_env_names(role: str = 'npc') -> Tuple[str, ...]:
    if role == 'npc':
        return (
            'NPC_LLM_CONTEXT_SIZE',
            'N20_NPC_CONTEXT_SIZE',
            'LLAMA_CPP_CONTEXT_SIZE',
            'N20_LLM_CONTEXT_SIZE',
        )
    return ('N20_LLM_CONTEXT_SIZE', 'LLAMA_CPP_CONTEXT_SIZE')


def resolve_context_limit_from_env(role: str = 'npc') -> Optional[int]:
    for env_name in context_limit_env_names(role):
        value = _env_int(env_name)
        if value:
            return value
    return None


def detect_llama_cpp_context(base_url: str, *, timeout: int = 5) -> Optional[int]:
    url = f"{str(base_url).rstrip('/')}/props"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.debug('[llm_context_budget] llama.cpp /props failed for %s: %s', base_url, exc)
        return None

    settings = data.get('default_generation_settings') or {}
    for key in ('n_ctx', 'context_size'):
        raw = settings.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
    raw = data.get('n_ctx')
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


def detect_ollama_context(base_url: str, model: str, *, timeout: int = 5) -> Optional[int]:
    if not model:
        return None
    url = f"{str(base_url).rstrip('/')}/api/show"
    try:
        response = requests.post(url, json={'name': model}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.debug('[llm_context_budget] Ollama /api/show failed for %s: %s', model, exc)
        return None

    for container in (data.get('model_info') or {}, data.get('details') or {}, data):
        if not isinstance(container, dict):
            continue
        for key in (
            'gemma.context_length',
            'llama.context_length',
            'qwen2.context_length',
            'context_length',
            'n_ctx_train',
        ):
            raw = container.get(key)
            if isinstance(raw, int) and raw > 0:
                return raw
    parameters = data.get('parameters') or ''
    if isinstance(parameters, str):
        match = re.search(r'context_length\s+(\d+)', parameters)
        if match:
            return int(match.group(1))
    return None


def default_context_limit_for_provider(provider: Any) -> int:
    provider_name = type(provider).__name__.lower()
    if 'ollama' in provider_name:
        return _env_int('N20_OLLAMA_DEFAULT_CONTEXT_SIZE', 8192) or 8192
    if 'llama' in provider_name:
        return _env_int('N20_LLAMA_CPP_DEFAULT_CONTEXT_SIZE', 32768) or 32768
    return _env_int('N20_LLM_DEFAULT_CONTEXT_SIZE', 8192) or 8192


def resolve_provider_context_limit(
    provider: Any,
    *,
    role: str = 'npc',
    auto_detect: Optional[bool] = None,
) -> Tuple[int, str]:
    """Return (context_limit_tokens, source_label)."""
    env_limit = resolve_context_limit_from_env(role)
    if env_limit:
        return env_limit, 'env'

    provider_limit = getattr(provider, 'context_window', None)
    if isinstance(provider_limit, int) and provider_limit > 0:
        return provider_limit, 'provider'

    if auto_detect is None:
        auto_detect = str(os.environ.get('N20_LLM_CONTEXT_AUTO_DETECT', '1')).strip().lower() not in {
            '0', 'false', 'no', 'off', 'disabled',
        }

    if auto_detect and provider is not None:
        base_url = getattr(provider, 'base_url', None)
        model = (
            getattr(provider, 'current_model', None)
            or getattr(provider, 'model', None)
        )
        provider_name = type(provider).__name__.lower()
        detected = None
        if base_url and 'llama' in provider_name:
            detected = detect_llama_cpp_context(base_url)
        elif base_url and 'ollama' in provider_name:
            detected = detect_ollama_context(base_url, str(model or ''))
        if detected:
            return detected, 'auto_detect'

    return default_context_limit_for_provider(provider), 'default'


def apply_context_limit_to_provider(
    provider: Any,
    *,
    role: str = 'npc',
    auto_detect: Optional[bool] = None,
) -> Tuple[int, str]:
    """Resolve context limit and store it on providers that support context_window."""
    limit, source = resolve_provider_context_limit(provider, role=role, auto_detect=auto_detect)
    if hasattr(provider, 'set_context_window'):
        provider.set_context_window(limit)
    elif not getattr(provider, 'context_window', None):
        provider.context_window = limit
    return limit, source


def max_output_tokens_for_provider(provider: Any, *, role: str = 'npc') -> int:
    if role == 'npc':
        env_value = _env_int('NPC_LLM_MAX_TOKENS')
        if env_value:
            return env_value
    for attr in ('max_tokens', 'num_predict'):
        raw = getattr(provider, attr, None)
        if isinstance(raw, int) and raw > 0:
            return raw
    return 320 if role == 'npc' else 1000


def compute_prompt_budget(
    context_limit: int,
    max_output_tokens: int,
    *,
    safety_margin: Optional[int] = None,
) -> int:
    margin = safety_margin
    if margin is None:
        margin = _env_int('N20_LLM_CONTEXT_SAFETY_MARGIN', 512) or 512
    budget = int(context_limit) - int(max_output_tokens) - int(margin)
    return max(budget, int(context_limit * 0.5))


def compact_threshold_pct() -> float:
    raw = os.environ.get('N20_LLM_CONTEXT_COMPACT_PCT', '85')
    try:
        return float(raw)
    except ValueError:
        return 85.0


def keep_recent_turns() -> int:
    return _env_int('N20_NPC_CONTEXT_KEEP_RECENT_TURNS', 6) or 6


def _is_overheard_message(message: Dict[str, Any]) -> bool:
    if str(message.get('role') or '') != 'system':
        return False
    text = _message_text(message).strip().lower()
    return any(text.startswith(prefix) for prefix in _OVERHEAR_PREFIXES)


def _is_entity_context_message(message: Dict[str, Any]) -> bool:
    if str(message.get('role') or '') != 'system':
        return False
    return _message_text(message).strip().lower().startswith(_ENTITY_CONTEXT_PREFIX)


def _split_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not messages:
        return [], []
    protected: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(messages) and str(messages[idx].get('role') or '') == 'system':
        protected.append(messages[idx])
        idx += 1
        if idx < len(messages) and str(messages[idx].get('role') or '') != 'system':
            break
        if idx >= 2 and not _is_entity_context_message(messages[idx]):
            break
    return protected, messages[idx:]


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(estimate_llm_tokens(_message_text(msg)) for msg in messages)


def _heuristic_recap(messages: List[Dict[str, Any]], *, max_chars: int = 1200) -> str:
    lines: List[str] = []
    for msg in messages:
        role = str(msg.get('role') or 'unknown')
        text = _message_text(msg).strip()
        if not text:
            continue
        if role == 'assistant':
            lines.append(f'You said: {text}')
        elif role == 'user':
            lines.append(f'They said: {text}')
        else:
            lines.append(text)
    recap = ' '.join(lines)
    if len(recap) > max_chars:
        recap = recap[: max_chars - 3].rstrip() + '...'
    return recap


def compact_history_messages(
    history: List[Dict[str, Any]],
    budget_tokens: int,
    *,
    keep_recent: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compact user/assistant (and overhear) history to fit a token budget."""
    report: Dict[str, Any] = {
        'actions': [],
        'dropped_overheard': 0,
        'summarized_messages': 0,
        'budget_tokens': budget_tokens,
    }
    if not history:
        return history, report

    working = list(history)
    recent_keep = keep_recent if keep_recent is not None else keep_recent_turns()

    def fits(msgs: List[Dict[str, Any]]) -> bool:
        return _estimate_messages_tokens(msgs) <= budget_tokens

    if fits(working):
        return working, report

    overheard = [msg for msg in working if _is_overheard_message(msg)]
    if overheard:
        working = [msg for msg in working if not _is_overheard_message(msg)]
        report['dropped_overheard'] = len(overheard)
        report['actions'].append(f'dropped_overheard:{len(overheard)}')
        if fits(working):
            return working, report

    if len(working) <= recent_keep:
        while working and not fits(working):
            removed = working.pop(0)
            report['actions'].append(f'dropped_oldest:{removed.get("role")}')
        return working, report

    older = working[:-recent_keep]
    recent = working[-recent_keep:]
    recap_text = _heuristic_recap(older)
    recap_message = {
        'role': 'system',
        'content': (
            'Earlier in this conversation (compressed recap; details may be incomplete): '
            + recap_text
        ),
    }
    compacted = [recap_message] + recent
    report['summarized_messages'] = len(older)
    report['actions'].append(f'summarized_turns:{len(older)}')

    while compacted and not fits(compacted):
        if len(compacted) > 1:
            removed = compacted.pop(1)
            report['actions'].append(f'dropped_after_recap:{removed.get("role")}')
            continue
        content = _message_text(compacted[0])
        compacted[0] = {
            'role': 'system',
            'content': content[: max(200, len(content) // 2)],
        }
        report['actions'].append('truncated_recap')

    return compacted, report


def compact_messages_for_budget(
    messages: List[Dict[str, Any]],
    budget_tokens: int,
    *,
    keep_recent: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compact a full chat payload (system + history) to fit ``budget_tokens``."""
    protected, history = _split_messages(messages)
    protected_tokens = _estimate_messages_tokens(protected)
    history_budget = max(budget_tokens - protected_tokens, budget_tokens // 4)

    compacted_history, history_report = compact_history_messages(
        history,
        history_budget,
        keep_recent=keep_recent,
    )
    result = protected + compacted_history

    if _estimate_messages_tokens(result) > budget_tokens and len(protected) > 1:
        while len(protected) > 1 and _estimate_messages_tokens(protected + compacted_history) > budget_tokens:
            removed = protected.pop()
            history_report['actions'].append('truncated_secondary_system')
        result = protected + compacted_history

    summary_before = summarize_llm_messages(messages)
    summary_after = summarize_llm_messages(result)
    report = {
        **history_report,
        'before_est_prompt_tokens': summary_before.get('est_prompt_tokens'),
        'after_est_prompt_tokens': summary_after.get('est_prompt_tokens'),
        'protected_system_messages': len(protected),
    }
    return result, report


def maybe_compact_messages(
    messages: List[Dict[str, Any]],
    *,
    provider: Any,
    role: str = 'npc',
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Resolve limits and compact when estimated usage exceeds the budget."""
    context_limit, source = resolve_provider_context_limit(provider, role=role)
    max_output = max_output_tokens_for_provider(provider, role=role)
    budget = compute_prompt_budget(context_limit, max_output)
    summary = summarize_llm_messages(messages, context_limit=context_limit)
    est_tokens = int(summary.get('est_prompt_tokens') or 0)
    usage_pct = summary.get('context_usage_pct')
    threshold = compact_threshold_pct()
    should_compact = est_tokens > budget or (
        usage_pct is not None and float(usage_pct) >= threshold
    )

    report: Dict[str, Any] = {
        'context_limit': context_limit,
        'context_limit_source': source,
        'prompt_budget_tokens': budget,
        'max_output_tokens': max_output,
        'est_prompt_tokens': est_tokens,
        'context_usage_pct': usage_pct,
        'compacted': False,
    }

    if not should_compact:
        return messages, report

    compacted, compact_report = compact_messages_for_budget(messages, budget)
    report.update(compact_report)
    report['compacted'] = True
    logger.info(
        '[llm_context_budget] compacted %s context est_tokens=~%s->~%s budget=%s actions=%s',
        role,
        report.get('before_est_prompt_tokens'),
        report.get('after_est_prompt_tokens'),
        budget,
        report.get('actions'),
    )
    return compacted, report
