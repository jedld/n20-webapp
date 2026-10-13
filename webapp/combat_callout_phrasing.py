"""Voice AI combat callouts in character with the NPC conversation LLM.

The engine decides *what* a callout means (``kind``, ``target``, ``intent`` —
see ``natural20.utils.combat_comms`` / ``combat_strategy``); this module only
turns that into a brief spoken line that fits the speaker's persona
(``description``, ``backstory``, ``voice`` YAML). The structured callout is
unchanged, so listeners act on the same information either way.

Campaign switch: ``game.yml`` → ``combat_communication.llm_phrasing: false``.
Per creature: ``callout_phrasing: false``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from natural20.utils import combat_comms

logger = logging.getLogger(__name__)

_PERSONA_KEYS = ('personality', 'personality_traits', 'backstory', 'description')
_PERSONA_CHARS = 700

_KIND_HINTS = {
    'focus': 'urge allies to attack {target}',
    'heal': 'call for healing',
    'help': 'ask allies for help against {target}',
    'warn': 'warn allies about {target}',
    'retreat': 'order the group to fall back',
    'protect': 'tell allies to protect {target}',
    'taunt': 'taunt {target} to draw its attention',
    'revive': 'urge allies to get the fallen {target} back on their feet',
    'regroup': 'tell allies to regroup and cover the fallen',
}


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value if v)
    return ' '.join(str(value or '').split())


def _persona_lines(speaker) -> List[str]:
    props = getattr(speaker, 'properties', None) or {}
    if not isinstance(props, dict):
        props = {}
    lines = []
    try:
        label = speaker.label()
    except Exception:
        label = getattr(speaker, 'name', None) or 'a combatant'
    kind = props.get('kind') or props.get('race') or getattr(speaker, 'npc_type', None)
    lines.append(f"You are {label}" + (f" ({_text(kind)})." if kind else "."))
    for key in _PERSONA_KEYS:
        val = _text(props.get(key))
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val[:_PERSONA_CHARS]}")
            break
    voice = props.get('voice')
    if isinstance(voice, dict):
        vp = _text(voice.get('prompt'))
        if vp:
            lines.append(f"Voice: {vp[:300]}")
        traits = _text(voice.get('traits'))
        if traits:
            lines.append(f"Manner: {traits}")
    return lines


def build_prompts(request: Dict[str, Any]) -> tuple:
    speaker = request.get('speaker')
    max_words = int(request.get('max_words') or 12)
    target = request.get('target_name') or 'them'
    hint = _KIND_HINTS.get(request.get('kind') or '', 'speak to allies').format(target=target)
    system = '\n'.join(_persona_lines(speaker) + [
        "You are in the middle of a fast, dangerous D&D battle and get one brief "
        "utterance on your turn.",
        f"Write ONE in-character line of at most {max_words} words.",
        "Convey only the intent given. Do not invent plans, facts, numbers, or names "
        "that are not given. No narration, stage directions, quotes, or speaker labels.",
        "Output only the spoken words.",
    ])
    lines = [f"Intent: {request.get('intent') or hint}", f"Purpose: {hint}"]
    if request.get('target_unconscious'):
        lines.append(f"{target} is unconscious and cannot hear you: speak to your "
                     "conscious allies, not to them.")
    recent = [str(r) for r in request.get('recent_lines') or [] if r]
    if recent:
        lines.append("You or your allies just said these; do not repeat them or reuse their wording: "
                     + " | ".join(recent))
    if request.get('telepathic'):
        lines.append("This is a silent telepathic message to your allies.")
    lang = request.get('language')
    if lang and lang not in ('common', 'telepathy'):
        lines.append(f"You are speaking {lang}; write the line in English.")
    return system, '\n'.join(lines)


def _llm_handler():
    try:
        from webapp.blueprints.helpers.runtime_state import get_llm_handler
        return get_llm_handler()
    except Exception:
        return None


def phrase_callout(request: Dict[str, Any], llm_handler=None) -> Optional[str]:
    """Phraser installed into ``combat_comms``; returns ``None`` to keep the template."""
    handler = llm_handler or _llm_handler()
    if handler is None:
        return None
    if getattr(handler, 'current_provider', None) is None and getattr(handler, 'npc_provider', None) is None:
        return None
    speaker = request.get('speaker')
    system, user = build_prompts(request)
    try:
        response = handler.send_message(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            {
                'response_mode': 'conversation',
                'log_label': f"combat_callout:{getattr(speaker, 'entity_uid', '?')}",
                'skip_continuation': True,
                'entity_uid': getattr(speaker, 'entity_uid', None),
            },
        )
    except Exception:
        logger.debug('combat callout phrasing failed', exc_info=True)
        return None
    try:
        from webapp.conversation_service import is_npc_llm_failure_response
        if is_npc_llm_failure_response(response):
            return None
    except Exception:
        if not str(response or '').strip():
            return None
    return str(response)


def install_callout_phraser() -> None:
    combat_comms.set_callout_phraser(phrase_callout)
