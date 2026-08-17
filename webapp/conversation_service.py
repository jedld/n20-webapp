from flask import jsonify, request, session
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

from natural20.player_character import PlayerCharacter
from natural20.npc import Npc
from natural20.utils.conversation import (
    conversation_reachability,
    entity_label,
    format_entity_gear_for_conversation,
    mention_handle_for,
    normalize_speech_mode,
    parse_player_conversation_input,
    resolve_named_targets,
    resolve_mention_targets,
    speech_distance_for,
    unique_entities,
)
from natural20.utils.gibberish import gibberish
from natural20.utils.language_comprehension import understands_language
from natural20.utils.outward_appearance import conversation_self_appearance
from natural20.utils.animal_communication import (
    _entity_speaks_beast_language,
    animal_communication_activation_note,
)
from natural20.utils.conversation_offers import item_offer_resolution_note
from natural20.utils.conversation_witness import witness_entity_uids
from natural20.utils.npc_hit_reaction import out_of_combat_hit_reaction_note
from natural20.utils.pickpocket_notification import (
    pickpocket_attempt_note,
    pickpocket_victim_caught_note,
)
from natural20.utils.steal_notification import steal_detection_note
from natural20.spell.message_spell_link import get_message_spell_link
from natural20.utils.language_comprehension import comprehends_speech

# Timing instrumentation — shared across webapp modules.
_DEBUG_TIMING = bool(int(os.environ.get('N20_DEBUG_TIMING', '0')))


def _timing_log(msg):
    """Emit a timing log line when N20_DEBUG_TIMING=1."""
    if _DEBUG_TIMING:
        try:
            from .blueprints.helpers.runtime_state import get_logger
            logger = get_logger()
            if logger:
                logger.warning(f"[timing] {msg}")
        except Exception:
            pass


# Recognises a player's request to attempt an Insight (Wisdom) check.
# Examples that match (case-insensitive):
#   "insight check"                       "[insight check]"
#   "I want to roll insight on @Garrick"  "/insight"
#   "Insight check on the merchant about his prices"
#   "make an insight check"
_SUMMON_NPC_PHRASES = re.compile(
    r'\b(?:'
    r'come\s+(?:here|over(?:\s+here)?)'
    r'|get\s+over\s+here'
    r'|head\s+over(?:\s+here)?'
    r'|over\s+here'
    r'|come\s+to\s+me'
    r')\b',
    re.IGNORECASE,
)

_INVITE_APPROACH_PHRASES = re.compile(
    r'\b(?:'
    r'come\s+(?:here|over(?:\s+here)?)'
    r'|get\s+over\s+here'
    r'|over\s+here'
    r')\b',
    re.IGNORECASE,
)

# Lines that should get an in-character reply when the NPC was directly addressed.
_DIRECT_REPLY_MESSAGE = re.compile(
    r'^\s*(?:'
    r'hi|hello|hey|greetings|howdy|hail|yo|sup'
    r'|good\s+(?:morning|afternoon|evening|day)'
    r'|what|where|who|why|how|when|which'
    r'|is|are|am|was|were|do|does|did|can|could|would|will|have|has'
    r')\b',
    re.IGNORECASE,
)

_PASSIVE_ACK_MESSAGE = re.compile(
    r'^\s*(?:'
    r'ok(?:ay)?|k|kk|cool|thanks?|thank\s+you|thx|ty|got\s+it|understood|'
    r'will\s+do|sounds\s+good|perfect|great|alright|right|sure|yep|yeah|yes|'
    r'no\s+problem|np|cheers|lovely|wonderful|fine|good|done|bye|goodbye|'
    r'see\s+ya|see\s+you|later|farewell|good\s+night|gn'
    r')\s*[.!?,]*\s*$',
    re.IGNORECASE,
)


def is_passive_acknowledgement(message) -> bool:
    text = (message or '').strip()
    if not text:
        return False
    return bool(_PASSIVE_ACK_MESSAGE.match(text))

PLAYER_INSIGHT_PATTERNS = [
    re.compile(r'^\s*/?\s*insight(?:\s+check)?\b', re.IGNORECASE),
    re.compile(r'^\s*\[\s*insight(?:\s+check)?[^\]]*\]', re.IGNORECASE),
    re.compile(r'\b(?:make|do|roll|attempt|try|perform)\s+(?:an?\s+)?insight\s+check\b', re.IGNORECASE),
    re.compile(r'\binsight\s+check\s+(?:on|against|of|for)\b', re.IGNORECASE),
    re.compile(r'\broll(?:ing)?\s+insight\b', re.IGNORECASE),
]

# Pulls a "target" and an optional "purpose" clause out of an insight request.
# We try a few common phrasings; whichever matches first wins.
_INSIGHT_TARGET_PURPOSE_PATTERNS = [
    re.compile(
        r'insight(?:\s+check)?\s+on\s+(?P<target>@?[\w\'\- ]+?)'
        r'(?:\s+(?:about|regarding|for|to(?:\s+(?:see|determine|tell|find\s+out))?)\s+(?P<purpose>.+))?$',
        re.IGNORECASE,
    ),
    re.compile(
        r'insight(?:\s+check)?\s+against\s+(?P<target>@?[\w\'\- ]+?)'
        r'(?:\s+(?:about|regarding|for)\s+(?P<purpose>.+))?$',
        re.IGNORECASE,
    ),
]


def is_player_insight_request(message):
    """Return True when the player's message is asking to roll Insight."""
    if not message:
        return False
    text = str(message).strip()
    if not text:
        return False
    for pattern in PLAYER_INSIGHT_PATTERNS:
        if pattern.search(text):
            return True
    return False


def parse_player_insight_request(message):
    """Extract a (target_spec, purpose) tuple from the player's message.

    Either field may be ``None`` if the request is too vague.
    """
    text = (message or '').strip()
    target_spec = None
    purpose = None
    for pattern in _INSIGHT_TARGET_PURPOSE_PATTERNS:
        match = pattern.search(text)
        if match:
            target_spec = (match.group('target') or '').strip(' .,!?') or None
            purpose = (match.group('purpose') or '').strip(' .,!?') or None
            break

    # Fall back: anything after the first "about"/"regarding"/"because"
    if purpose is None:
        about_match = re.search(
            r'\b(?:about|regarding|because|to\s+see|to\s+determine|to\s+tell|to\s+find\s+out)\s+(?P<rest>.+)$',
            text,
            re.IGNORECASE,
        )
        if about_match:
            purpose = about_match.group('rest').strip(' .,!?') or None
    return target_spec, purpose


PLAYER_PERCEPTION_PATTERNS = [
    re.compile(r'^\s*/?\s*perception(?:\s+check)?\b', re.IGNORECASE),
    re.compile(r'^\s*\[\s*perception(?:\s+check)?[^\]]*\]', re.IGNORECASE),
    re.compile(r'\b(?:make|do|roll|attempt|try|perform)\s+(?:an?\s+)?perception\s+check\b', re.IGNORECASE),
    re.compile(r'\bperception\s+check\s+(?:on|against|of|for)\b', re.IGNORECASE),
    re.compile(r'\broll(?:ing)?\s+perception\b', re.IGNORECASE),
]

# Detects "describe what I see?", "what do I see?", "describe what you see?" etc.
# These are conversation-time description requests that prompt the NPC LLM to generate
# a visual aside about what the player character can see (facial expression, body language,
# clothing, injury, environment relevance).
PLAYER_DESCRIBE_SEE_PATTERNS = [
    # "describe what I see", "describe what you see", "tell me what you see"
    re.compile(
        r'\b(?:describe|tell|explain)\b[^?]*\b(?:i|me|you)\b[^?]*\bsee\b',
        re.IGNORECASE,
    ),
    # "what do I see?", "what do you see?"
    re.compile(
        r'\bwhat\s+(?:do|did)\s+(?:i|me|you)\b[^?]*\bsee\b',
        re.IGNORECASE,
    ),
    # "give me a description", "give me a description of X"
    re.compile(
        r'\bgive\s+me\s+(?:a\s+)?description\b',
        re.IGNORECASE,
    ),
    # "take a good look", "look closely", "look carefully", "look hard"
    re.compile(
        r'\b(?:take\s+a\s+good\s+look|look\s+(?:carefully|closely|hard|over|at))\b',
        re.IGNORECASE,
    ),
    # "run me down what I see"
    re.compile(
        r'\brun\s+(?:me|us)\s+down\b',
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Semantic intent detection: "describe what I see" variants.
#
# We use a fast regex pre-filter for common phrases, then fall back to the
# NPC LLM for semantic classification when the regex is inconclusive.
# This gives us both speed and robustness against natural language variation.
# ---------------------------------------------------------------------------

# Fast pre-filter patterns — matches ~90 % of real user inputs.
PLAYER_DESCRIBE_SEE_FAST_PATTERNS = [
    # "describe what I see", "describe what you see", "tell me what you see"
    re.compile(
        r'\b(?:describe|tell|explain)\b[^?]*\b(?:i|me|you)\b[^?]*\bsee\b',
        re.IGNORECASE,
    ),
    # "what do I see?", "what do you see?", "what did I see?"
    re.compile(
        r'\bwhat\s+(?:do|did)\s+(?:i|me|you)\b[^?]*\bsee\b',
        re.IGNORECASE,
    ),
    # "give me a description", "give me a description of X"
    re.compile(
        r'\bgive\s+me\s+(?:a\s+)?description\b',
        re.IGNORECASE,
    ),
    # "take a good look", "look closely", "look carefully", "look hard"
    re.compile(
        r'\b(?:take\s+a\s+good\s+look|look\s+(?:carefully|closely|hard|over|at))\b',
        re.IGNORECASE,
    ),
    # "run me down what I see"
    re.compile(
        r'\brun\s+(?:me|us)\s+down\b',
        re.IGNORECASE,
    ),
]

# Extracts an optional target or focus from describe-what-I-see messages.
_DESCRIBE_SEE_TARGET_PATTERNS = [
    # "describe what I see about @NPC focusing on his injuries"
    re.compile(
        r'(?:describe|tell|explain)\s+(?:what\s+)?(?:i|me|you)\s+see'
        r'(?:\s+(?:about|regarding|of|on)\s+(?P<target>@?[\w\'\- ]+?))?'
        r'(?:\s+(?:about|regarding|focusing?|for)\s+(?P<focus>.+))?$',
        re.IGNORECASE,
    ),
    # "what do I see about the vampire?", "what do I see about @NPC?"
    re.compile(
        r'\bwhat\s+(?:do|did)\s+(?:i|me|you)\s+see'
        r'(?:\s+(?:about|regarding|of|on)\s+(?P<target>[\w\@\'\- ]+?))?'
        r'(?:\s+(?:about|regarding|focusing?|for)\s+(?P<focus>.+))?$',
        re.IGNORECASE,
    ),
    # "give me a description of @Darlene regarding her clothing"
    re.compile(
        r'(?:describe|give\s+me)\s+(?:a\s+)?(?:description|run\??\s*down)'
        r'(?:\s+)?(?:of\s+(?P<target>@?[\w\'\- ]+?))?'
        r'(?:\s+(?:about|regarding|focusing?|for)\s+(?P<focus>.+))?$',
        re.IGNORECASE,
    ),
]

_PERCEPTION_TARGET_FOCUS_PATTERNS = [
    re.compile(
        r'perception(?:\s+check)?\s+on\s+(?P<target>@?[\w\'\- ]+?)'
        r'(?:\s+(?:about|regarding|for|to\s+(?:notice|spot|see|find))\s+(?P<focus>.+))?$',
        re.IGNORECASE,
    ),
    re.compile(
        r'perception(?:\s+check)?\s+against\s+(?P<target>@?[\w\'\- ]+?)'
        r'(?:\s+(?:about|regarding|for)\s+(?P<focus>.+))?$',
        re.IGNORECASE,
    ),
]

_DM_CHAT_COMMAND_PREFIX = re.compile(r'^\s*@dm\s+', re.IGNORECASE)


def normalize_player_dm_command(message):
    """Strip a leading @dm prefix from local-chat DM commands."""
    text = str(message or '').strip()
    if not text:
        return ''
    return _DM_CHAT_COMMAND_PREFIX.sub('', text, count=1).strip()


def is_player_perception_request(message):
    """Return True when the player's message is asking to roll Perception."""
    if not message:
        return False
    text = normalize_player_dm_command(message)
    if not text:
        return False
    if _DM_CHAT_COMMAND_PREFIX.search(str(message or '')) and re.search(
        r'\bperception\b',
        text,
        re.IGNORECASE,
    ):
        return True
    for pattern in PLAYER_PERCEPTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def parse_player_perception_request(message):
    """Extract a (target_spec, focus) tuple from the player's message."""
    text = normalize_player_dm_command(message)
    target_spec = None
    focus = None
    for pattern in _PERCEPTION_TARGET_FOCUS_PATTERNS:
        match = pattern.search(text)
        if match:
            target_spec = (match.group('target') or '').strip(' .,!?') or None
            focus = (match.group('focus') or '').strip(' .,!?') or None
            break

    if focus is None:
        about_match = re.search(
            r'\b(?:about|regarding|to\s+(?:notice|spot|see|find))\s+(?P<rest>.+)$',
            text,
            re.IGNORECASE,
        )
        if about_match:
            focus = about_match.group('rest').strip(' .,!?') or None
    return target_spec, focus


def is_player_describe_see_request(message: str, *, _llm_handler=None) -> bool:
    """Return True when the player's message is asking the NPC to describe what they see.

    Detection strategy (fast → robust):
    1. Fast regex pre-filter — matches ~90 % of real user inputs.
    2. LLM-based semantic classification — used when the regex is inconclusive
       but the caller explicitly requests it (e.g. during conversation turns).

    Examples:
        - "describe what I see"
        - "what do I see?"
        - "tell me what you see"
        - "describe what you see"
        - "what do you see here?"
        - "can you paint me a picture of the room?"  (LLM-only)
    """
    if not message:
        return False
    text = normalize_player_dm_command(message)
    if not text:
        return False
    # Fast regex pre-filter — covers the majority of cases.
    for pattern in PLAYER_DESCRIBE_SEE_FAST_PATTERNS:
        if pattern.search(text):
            return True
    # LLM-based semantic fallback for natural-language variations.
    if _llm_handler is not None:
        try:
            prompt = (
                "The player has just spoken during an NPC conversation. "
                "Determine whether they are asking the NPC to describe what they see "
                "(visual description of the NPC/environment). "
                "Reply with ONLY the word YES or NO — nothing else.\n\n"
                f"Player message: \"{message}\"\n\n"
                "Is this a request to describe what the player sees (visual scene description)?"
            )
            response = _llm_handler.send_simple_message(prompt, context={'log_label': 'describe_see_intent'})
            return response is not None and response.strip().upper().startswith('YES')
        except Exception as exc:
            logger.debug("Describe-see LLM fallback failed: %s", exc)
    return False


def parse_player_describe_see_request(message: str):
    """Extract a (target_spec, focus) tuple from the player's describe-see message.

    Returns (None, None) if no target or focus can be inferred.
    Leading '@' is stripped from target specs (e.g. '@Cecilia' → 'Cecilia').
    """
    text = normalize_player_dm_command(message)
    target_spec = None
    focus = None
    for pattern in _DESCRIBE_SEE_TARGET_PATTERNS:
        match = pattern.search(text)
        if match:
            raw_target = (match.group('target') or '').strip(' .,!?') or None
            if raw_target:
                target_spec = raw_target.lstrip('@')
            focus = (match.group('focus') or '').strip(' .,!?') or None
            # Strip leading prepositions from focus (e.g. "on his injuries" → "his injuries")
            if focus:
                focus = re.sub(
                    r'^(?:on|about|regarding|for)\s+',
                    '',
                    focus,
                    flags=re.IGNORECASE,
                ).strip(' .,!?') or None
            break
    # Fallback: if pattern matched but target was None, the text after the
    # preposition is likely the target (e.g. "what do I see about the vampire?").
    if target_spec is None and focus is not None:
        target_spec = focus.lstrip('@')
        focus = None
    return target_spec, focus


_PLAYER_GIFT_EXCLUSIONS = re.compile(
    r'\b(?:'
    r'give\s+(?:me|us)\b'
    r'|(?:can|could|would|will)\s+(?:you|u)\s+give\s+me\b'
    r')\b',
    re.IGNORECASE,
)

PLAYER_GIFT_PATTERNS = [
    re.compile(r'\bI\s+(?:give|hand|offer|pass|pay|tip|donate)\b', re.IGNORECASE),
    re.compile(r'\b(?:here(?:\'s| is)|take this)\b', re.IGNORECASE),
    re.compile(r'(?:^|\*)\s*(?:hands?|giving|passes?)\s+(?:over|to)\b', re.IGNORECASE),
    re.compile(r'\*\s*(?:hands?|gives?|passes?)\b', re.IGNORECASE),
]

_CURRENCY_GIFT_PATTERN = re.compile(
    r'\b(?:(?P<qty>\d+)\s*)?(?P<unit>silver|gold|copper|electrum|platinum|sp|gp|cp|ep|pp)\b',
    re.IGNORECASE,
)

_CURRENCY_ITEM_SLUGS = {
    'silver': 'silver_piece',
    'sp': 'silver_piece',
    'gold': 'gold_piece',
    'gp': 'gold_piece',
    'copper': 'copper_piece',
    'cp': 'copper_piece',
    'electrum': 'electronum',
    'ep': 'electronum',
    'platinum': 'platinum_piece',
    'pp': 'platinum_piece',
}


def _player_gift_offer_text(message, actions=None):
    parts = [str(message or '').strip()]
    for action in actions or []:
        text = str(action or '').strip()
        if text:
            parts.append(text)
    return ' '.join(part for part in parts if part).strip()


def is_player_gift_offer(message, actions=None):
    """Return True when the player is offering to hand something to a listener."""
    text = _player_gift_offer_text(message, actions)
    if not text:
        return False
    if is_player_insight_request(message) or is_player_perception_request(message):
        return False
    if _PLAYER_GIFT_EXCLUSIONS.search(text):
        return False
    for pattern in PLAYER_GIFT_PATTERNS:
        if pattern.search(text):
            return True
    return False


def parse_player_gift_offer(message, actions=None):
    """Extract optional currency/item hints from a player gift offer."""
    text = _player_gift_offer_text(message, actions)
    item_hint = None
    match = _CURRENCY_GIFT_PATTERN.search(text)
    if match:
        item_hint = _CURRENCY_ITEM_SLUGS.get(str(match.group('unit') or '').strip().lower())
    return {'item_hint': item_hint}


def insight_check_message_key(target_uid, statement):
    """Stable key for deduplicating insight checks on a specific NPC line."""
    target = str(target_uid or '').strip()
    text = str(statement or '').strip()
    payload = f"{target}\0{text}".encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:16]


def perception_check_message_key(target_uid, focus=None):
    """Stable key for deduplicating perception checks on a scene/NPC focus."""
    target = str(target_uid or '').strip()
    text = str(focus or '__general__').strip()
    payload = f"perception\0{target}\0{text}".encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:16]


def skill_check_reply_key(reply_id):
    """Stable key for deduplicating dialog skill-check rolls on one NPC request."""
    token = str(reply_id or '').strip()
    if not token:
        return None
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]


DIALOG_SKILL_CHECK_SKILLS = frozenset({
    'acrobatics', 'animal_handling', 'arcana', 'athletics', 'deception', 'history',
    'insight', 'intimidation', 'investigation', 'medicine', 'nature', 'perception',
    'performance', 'persuasion', 'religion', 'sleight_of_hand', 'stealth', 'survival',
})

_LLM_FAILURE_PREFIXES = (
    'error communicating with',
    "i'm having trouble responding right now.",
    'unexpected error:',
    'llama.cpp provider not initialized',
)


def is_npc_llm_failure_response(text) -> bool:
    if text is None:
        return True
    lowered = str(text).strip().lower()
    if not lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in _LLM_FAILURE_PREFIXES)


class ConversationService:
    def __init__(
        self,
        current_game_getter,
        game_session,
        socketio,
        entity_rag_handler_getter,
        llm_conversation_handler_getter,
        roles_for_username_getter,
        entity_owners_getter,
        entities_controlled_by_getter,
        logins_getter,
        logger,
    ):
        self._current_game_getter = current_game_getter
        self.game_session = game_session
        self.socketio = socketio
        self._entity_rag_handler_getter = entity_rag_handler_getter
        self._llm_conversation_handler_getter = llm_conversation_handler_getter
        self._roles_for_username_getter = roles_for_username_getter
        self._entity_owners_getter = entity_owners_getter
        self._entities_controlled_by_getter = entities_controlled_by_getter
        self._logins_getter = logins_getter
        self.logger = logger
        self._conversation_presence_cache: dict = {}
        self._CONVERSANCE_PRESENCE_CACHE_TTL = 15.0  # Reduce expensive acoustic recomputation on debounced refreshes
        self._reply_continuations: dict = {}
        self._stream_tts_queue: list = []
        self._stream_tts_worker_running = False

    @property
    def current_game(self):
        return self._current_game_getter()

    @property
    def llm_conversation_handler(self):
        return self._llm_conversation_handler_getter()

    @property
    def entity_rag_handler(self):
        return self._entity_rag_handler_getter()

    @property
    def roles_for_username(self):
        return self._roles_for_username_getter()

    @property
    def entity_owners(self):
        return self._entity_owners_getter()

    @property
    def entities_controlled_by(self):
        return self._entities_controlled_by_getter()

    def _party_membership_guidance(self, receiver, speaker) -> str:
        try:
            from natural20.sidekick import is_in_party_sidekick
            from webapp.blueprints.helpers.sidekick_control import can_invite_entity
            session = self.game_session
            if is_in_party_sidekick(session, receiver):
                return (
                    "- You are already in this adventuring party. Do not use [JOIN_PARTY] again. "
                    "If you leave them peacefully, include [LEAVE_PARTY] in your response. "
                    "If you turn on them, use [GO_HOSTILE] instead.\n"
                )
            if can_invite_entity(session, receiver, speaker):
                return (
                    "- If the speaker invites you to join their adventuring party and you accept "
                    "(you must genuinely trust them), include [JOIN_PARTY] in your response "
                    "(optional owner=@handle). If you refuse, stay in character and do not use the tag.\n"
                )
            return ""
        except Exception:
            return ""

    @property
    def logins(self):
        return self._logins_getter() or []

    # --- TTS helpers (graceful no-op when TTS is not attached) -----------------

    TTS_EMOTION_PATTERNS: dict[str, list[str]] = {
        # Avoid bare "!" — fearful whispers often use punctuation and would become "angry".
        "shouting": ["SHOUT", "SHRIEK", "scream", "roar", "bellows"],
        "angry": ["angry", "furious", "shouting", "yelling", "grr", "enraged"],
        "laughing": ["[laughs]", "laughs", "laugh", "chuckle", "giggle", "[chuckles]"],
        "whisper": ["[whisper]", "whisper", "quietly", "softly", "hushed", "[hushed]"],
        "sad": ["[sighs]", "sighs", "sigh", "sad", "mournful", "[mourns]"],
        "fearful": ["afraid", "terrified", "fearful", "scared", "trembling"],
    }

    def attach_tts_manager(self, tts_manager) -> None:
        """Attach a TTSManager so NPC replies can generate speech audio."""
        self._tts_manager = tts_manager
        if tts_manager is not None and hasattr(self, 'logger'):
            self.logger.info(
                "[TTS] Attached TTS manager (provider=%s, delivery=%s)",
                getattr(tts_manager, '_provider_name', 'unknown'),
                self._tts_delivery_mode(),
            )

    def _tts_delivery_mode(self, username=None) -> str:
        """How NPC speech audio is delivered: sync, async (default), stream, or manual."""
        from webapp.tts.preferences import get_delivery_mode

        if username:
            return get_delivery_mode(username)
        from webapp.tts.preferences import default_delivery_mode
        return default_delivery_mode()

    def _tts_should_generate(self, rendered_payload) -> bool:
        if rendered_payload.get('visual_only'):
            return False
        if rendered_payload.get('tts_skipped'):
            return False
        text = str(rendered_payload.get('message') or '').strip()
        if not text:
            return False
        if not getattr(self, '_tts_manager', None) or not self._tts_manager.is_enabled():
            return False
        return True

    def _mark_tts_delivery_status(self, rendered_payload, delivery_mode: str) -> None:
        if not self._tts_should_generate(rendered_payload):
            return
        if delivery_mode == 'async':
            rendered_payload['tts_status'] = 'pending'
        elif delivery_mode == 'stream':
            if self._stream_pcm_to_socket_enabled():
                rendered_payload['tts_status'] = 'streaming'
            else:
                rendered_payload['tts_status'] = 'pending'
        elif delivery_mode == 'manual':
            rendered_payload['tts_status'] = 'manual'

    def _stream_pcm_to_socket_enabled(self) -> bool:
        """Whether to push live PCM chunks over Socket.IO during stream delivery."""
        import os

        from webapp.tts.preferences import get_delivery_mode

        flag = os.environ.get('VLLM_OMNI_TTS_SOCKET_PCM', '').strip().lower()
        if flag in ('1', 'true', 'yes', 'on'):
            return True
        if flag in ('0', 'false', 'no', 'off'):
            return False
        mgr = getattr(self, '_tts_manager', None)
        provider = getattr(mgr, '_provider_name', '') if mgr else ''
        if provider in ('qwen3_vllm', 'mock_qwen3_vllm'):
            # Phase 1 default: live PCM when stream delivery is active (~0.76s TTFA).
            return get_delivery_mode() == 'stream'
        return True

    def _schedule_stream_tts_delivery(self, jobs, base_payload, source_entity) -> None:
        if not jobs:
            return
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        self._stream_tts_queue.append((jobs, base_payload, source_entity))
        self._ensure_stream_tts_worker()

    def _ensure_stream_tts_worker(self) -> None:
        if self._stream_tts_worker_running or not self._stream_tts_queue:
            return
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        self._stream_tts_worker_running = True
        try:
            socketio.start_background_task(self._drain_stream_tts_queue)
        except Exception as exc:
            self._stream_tts_worker_running = False
            self.logger.warning("[TTS] Failed to schedule stream delivery worker: %s", exc)

    def _drain_stream_tts_queue(self) -> None:
        try:
            while self._stream_tts_queue:
                jobs, base_payload, source_entity = self._stream_tts_queue.pop(0)
                self._deliver_stream_tts_jobs(jobs, base_payload, source_entity)
        except Exception as exc:
            self.logger.warning("[TTS] Stream delivery worker failed: %s", exc)
        finally:
            self._stream_tts_worker_running = False
            if self._stream_tts_queue:
                self._ensure_stream_tts_worker()

    def _deliver_stream_tts_jobs(self, jobs, base_payload, source_entity) -> None:
        from webapp.tts.reply_cache import store_ready_reply
        from webapp.tts.streaming import pcm_b64

        synthesis_cache: dict[str, str] = {}
        work_items: list[dict] = []

        for job in jobs:
            username = job.get('username')
            reply_id = job.get('reply_id')
            rendered_payload = dict(job.get('rendered_payload') or {})
            try:
                prepared = self._attach_per_listener_tts(
                    rendered_payload,
                    base_payload,
                    source_entity,
                    username,
                    synthesis_cache=synthesis_cache,
                    generate_audio=False,
                )
                if not self._tts_should_generate(prepared):
                    continue
                text = str(prepared.get('tts_text') or prepared.get('message') or '').strip()
                if not text:
                    continue
                emotion = prepared.get('tts_emotion')
                instruct = prepared.get('tts_instruct')
                volume = base_payload.get('volume')
                npc_uid = getattr(source_entity, 'entity_uid', None)
                cache_key = self._tts_synthesis_cache_key(
                    npc_uid, text, emotion, instruct, volume,
                )
                work_items.append({
                    'username': username,
                    'reply_id': reply_id,
                    'prepared': prepared,
                    'text': text,
                    'emotion': emotion,
                    'instruct': instruct,
                    'volume': volume,
                    'cache_key': cache_key,
                })
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Stream prepare failed for %s (reply_id=%s): %s",
                    username,
                    reply_id,
                    exc,
                )

        if not work_items:
            return

        # Prefer player clients over DM for live PCM; DM often has no socket listener.
        work_items.sort(
            key=lambda item: (
                str(item.get('username') or '').lower() == 'dm',
                str(item.get('username') or '').lower(),
            )
        )

        primary = work_items[0]
        text = primary['text']
        cache_key = primary['cache_key']
        audio_url = synthesis_cache.get(cache_key)
        if not audio_url and getattr(self, '_tts_manager', None):
            cached_path = self._tts_manager.lookup_cached_line(cache_key)
            if cached_path:
                audio_url = f"/tts/{os.path.basename(cached_path)}"
                synthesis_cache[cache_key] = audio_url
                self.logger.info(
                    "[TTS] Stream line cache hit reply_id=%s npc=%s path=%s",
                    primary.get('reply_id'),
                    getattr(source_entity, 'entity_uid', None),
                    cached_path,
                )

        provider_name = getattr(self._tts_manager, '_provider_name', '')
        sample_rate = 24000 if provider_name in ('qwen3_vllm', 'mock_qwen3_vllm') else 22050
        seq = 0
        stream_output_path = None
        stream_started = False

        if not audio_url:
            reply_id = primary.get('reply_id')
            if reply_id and getattr(self, '_tts_manager', None):
                stream_output_path = str(
                    Path(self._tts_manager.cache_dir)
                    / f"stream_{reply_id}_{uuid.uuid4().hex[:8]}.wav"
                )
            self.logger.info(
                "[TTS] Stream synthesis start reply_id=%s primary_user=%s npc=%s chars=%s listeners=%s",
                reply_id,
                primary.get('username'),
                getattr(source_entity, 'entity_uid', None),
                len(text),
                [item.get('username') for item in work_items],
            )
            prepared = primary['prepared']
            event_base = {
                'type': 'tts_stream_start',
                'reply_id': reply_id,
                'entity_id': prepared.get('entity_id'),
                'speaker_name': prepared.get('speaker_name'),
                'tts_emotion': primary.get('emotion'),
                'tts_gain': prepared.get('tts_gain'),
            }
            emit_pcm = self._stream_pcm_to_socket_enabled()
            if emit_pcm:
                self._emit_tts_stream_event(primary['username'], 'tts_stream_start', {
                    **event_base,
                    'sample_rate': sample_rate,
                })
                stream_started = True
            try:
                for rate, pcm in self._stream_tts_audio(
                    source_entity,
                    text,
                    emotion=primary.get('emotion'),
                    instruct=primary.get('instruct'),
                    volume=primary.get('volume'),
                    output_path=stream_output_path,
                ):
                    sample_rate = rate
                    if emit_pcm:
                        chunk_payload = {
                            'type': 'tts_stream_chunk',
                            'reply_id': reply_id,
                            'seq': seq,
                            'sample_rate': sample_rate,
                            'pcm_b64': pcm_b64(pcm),
                        }
                        self._emit_tts_stream_event(primary['username'], 'tts_stream_chunk', chunk_payload)
                    seq += 1
                    try:
                        self.socketio.sleep(0)
                    except Exception:
                        pass
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Stream synthesis failed (reply_id=%s): %s",
                    reply_id,
                    exc,
                )
                if stream_started and reply_id:
                    fail_payload = {
                        'type': 'tts_stream_end',
                        'reply_id': reply_id,
                        'failed': True,
                        'tts_emotion': primary.get('emotion'),
                        'tts_gain': prepared.get('tts_gain'),
                        'entity_id': prepared.get('entity_id'),
                        'speaker_name': prepared.get('speaker_name'),
                        'sample_rate': sample_rate,
                        'chunks': seq,
                    }
                    self._emit_tts_stream_event(primary['username'], 'tts_stream_end', fail_payload)
                return

            audio_path = None
            if stream_output_path and Path(stream_output_path).is_file():
                audio_path = stream_output_path
            if not audio_path:
                audio_path = getattr(self._tts_manager, '_last_stream_output_path', None)
            if not audio_path:
                engine = self._tts_manager.get_engine()
                audio_path = getattr(engine, '_last_stream_output_path', None)
            if not audio_path:
                provider_name = getattr(self._tts_manager, '_provider_name', '')
                if provider_name not in ('qwen3_vllm', 'mock_qwen3_vllm'):
                    audio_path = self._ensure_tts_audio(
                        source_entity,
                        text,
                        emotion=primary.get('emotion'),
                        instruct=primary.get('instruct'),
                        volume=primary.get('volume'),
                        synthesis_key=cache_key,
                    )
            if audio_path:
                audio_url = f"/tts/{os.path.basename(audio_path)}"
                synthesis_cache[cache_key] = audio_url

            if stream_started and reply_id:
                end_payload = {
                    'type': 'tts_stream_end',
                    'reply_id': reply_id,
                    'audio_url': audio_url,
                    'tts_emotion': primary.get('emotion'),
                    'tts_gain': prepared.get('tts_gain'),
                    'entity_id': prepared.get('entity_id'),
                    'speaker_name': prepared.get('speaker_name'),
                    'sample_rate': sample_rate,
                    'chunks': seq,
                }
                self._emit_tts_stream_event(primary['username'], 'tts_stream_end', end_payload)
            elif not emit_pcm and reply_id:
                self.logger.debug(
                    "[TTS] Stream synthesis buffered without socket PCM reply_id=%s chunks=%s",
                    reply_id,
                    seq,
                )

            self.logger.info(
                "[TTS] Stream synthesis complete reply_id=%s primary_user=%s chunks=%s audio=%s",
                reply_id,
                primary.get('username'),
                seq,
                bool(audio_url),
            )
        else:
            self.logger.info(
                "[TTS] Stream cache hit reply_id=%s listeners=%s",
                primary.get('reply_id'),
                [item.get('username') for item in work_items],
            )

        for item in work_items:
            reply_id = item.get('reply_id')
            prepared = item.get('prepared') or {}
            ready_payload = {
                'type': 'tts_ready',
                'reply_id': reply_id,
                'audio_url': audio_url,
                'tts_emotion': item.get('emotion'),
                'tts_gain': prepared.get('tts_gain'),
                'entity_id': prepared.get('entity_id'),
                'speaker_name': prepared.get('speaker_name'),
                'sample_rate': sample_rate,
                'chunks': seq,
            }
            if reply_id:
                store_ready_reply(reply_id, ready_payload)
            if audio_url:
                self._emit_tts_ready(item['username'], ready_payload)

    def _stream_tts_audio(
        self,
        entity,
        text: str,
        emotion: str = None,
        instruct: str = None,
        volume: str = None,
        output_path: str = None,
    ):
        if not getattr(self, '_tts_manager', None) or not self._tts_manager.is_enabled():
            return
        npc_uid = getattr(entity, 'entity_uid', None)
        if not npc_uid or not text:
            return
        try:
            from webapp.tts.npc_voice import ensure_npc_voice_locked

            profile = ensure_npc_voice_locked(self._tts_manager, entity)
            if profile is None:
                return ""

            resolved_volume = (volume or '').strip().lower() or None
            resolved_emotion = (emotion or '').strip() or self._detect_emotion_from_text(text)
            if resolved_volume == 'whisper' and resolved_emotion in ('shouting', 'angry', 'yelling'):
                resolved_emotion = 'whisper'

            stream_kwargs = {
                'text': text,
                'npc_uid': npc_uid,
                'emotion': resolved_emotion,
                'volume': resolved_volume,
            }
            if output_path:
                stream_kwargs['output_path'] = output_path
            if instruct:
                stream_kwargs['instruct'] = instruct
            try:
                yield from self._tts_manager.generate_stream(**stream_kwargs)
            except TypeError:
                stream_kwargs.pop('instruct', None)
                try:
                    yield from self._tts_manager.generate_stream(**stream_kwargs)
                except TypeError:
                    stream_kwargs.pop('volume', None)
                    yield from self._tts_manager.generate_stream(**stream_kwargs)
        except Exception as tts_err:
            self.logger.warning(
                "[TTS] Stream generation failed for %s: %s",
                getattr(entity, 'label', lambda: npc_uid)(),
                tts_err,
            )
            return

    def _schedule_async_tts_delivery(self, jobs, base_payload, source_entity) -> None:
        if not jobs:
            return
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        try:
            socketio.start_background_task(
                self._deliver_async_tts_jobs,
                jobs,
                base_payload,
                source_entity,
            )
        except Exception as exc:
            self.logger.warning("[TTS] Failed to schedule async delivery: %s", exc)

    def _deliver_async_tts_jobs(self, jobs, base_payload, source_entity) -> None:
        from webapp.tts.reply_cache import store_ready_reply

        synthesis_cache = {}
        prepared_jobs = []
        for job in jobs:
            username = job.get('username')
            reply_id = job.get('reply_id')
            rendered_payload = dict(job.get('rendered_payload') or {})
            try:
                final_payload = self._attach_per_listener_tts(
                    rendered_payload,
                    base_payload,
                    source_entity,
                    username,
                    synthesis_cache=synthesis_cache,
                    generate_audio=True,
                )
                prepared_jobs.append((job, final_payload))
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Async prepare failed for %s (reply_id=%s): %s",
                    username,
                    reply_id,
                    exc,
                )

        for job, final_payload in prepared_jobs:
            username = job.get('username')
            reply_id = job.get('reply_id')
            try:
                audio_url = final_payload.get('audio_url')
                if not audio_url:
                    self.logger.warning(
                        "[TTS] No audio generated for %s (reply_id=%s)",
                        username,
                        reply_id,
                    )
                    store_ready_reply(reply_id, {
                        'ready': True,
                        'failed': True,
                        'reply_id': reply_id,
                        'audio_url': None,
                        'entity_id': final_payload.get('entity_id'),
                        'speaker_name': final_payload.get('speaker_name'),
                    })
                    continue
                event_payload = {
                    'type': 'tts_ready',
                    'reply_id': reply_id,
                    'audio_url': audio_url,
                    'tts_emotion': final_payload.get('tts_emotion'),
                    'tts_gain': final_payload.get('tts_gain'),
                    'entity_id': final_payload.get('entity_id'),
                    'speaker_name': final_payload.get('speaker_name'),
                }
                self._emit_tts_ready(username, event_payload)
                try:
                    self.socketio.sleep(0)
                except Exception:
                    pass
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Async delivery failed for %s (reply_id=%s): %s",
                    username,
                    reply_id,
                    exc,
                )

    def _detect_emotion_from_text(self, text: str) -> str:
        """Detect emotion from NPC response text for TTS style control."""
        if not text:
            return "calm"
        text_lower = text.lower()
        for emotion, keywords in self.TTS_EMOTION_PATTERNS.items():
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    return emotion
        return "calm"

    def _npc_voice_prompt(self, entity) -> str:
        """Resolve a voice prompt from NPC YAML / overrides, with a safe fallback."""
        from webapp.tts.npc_voice import build_npc_voice_prompt

        try:
            label = entity.label() if hasattr(entity, 'label') else getattr(entity, 'name', 'NPC')
        except Exception:
            label = 'NPC'
        return build_npc_voice_prompt(entity, fallback_label=str(label or 'NPC'))

    def _npc_reference_audio(self, entity):
        """Optional reference audio path from NPC voice config."""
        props = getattr(entity, 'properties', None) or {}
        voice = props.get('voice') if isinstance(props, dict) else None
        if not isinstance(voice, dict):
            return None
        ref = voice.get('reference_audio')
        return str(ref) if ref else None

    def _npc_voice_accent(self, entity):
        """Optional CosyVoice accent label from NPC voice config."""
        props = getattr(entity, 'properties', None) or {}
        voice = props.get('voice') if isinstance(props, dict) else None
        if not isinstance(voice, dict):
            return None
        accent = voice.get('accent')
        if accent is None:
            return None
        return str(accent).strip() or None

    def _npc_voice_language(self, entity):
        """Optional language hint from NPC voice config (defaults to en when accent set)."""
        props = getattr(entity, 'properties', None) or {}
        voice = props.get('voice') if isinstance(props, dict) else None
        if not isinstance(voice, dict):
            return None
        language = voice.get('language')
        if language:
            return str(language).strip() or None
        accent = voice.get('accent')
        if accent and str(accent).strip().lower() not in ('zh', 'chinese', 'mandarin', 'cantonese', 'yue'):
            return 'en'
        return None

    def _ensure_tts_audio(
        self,
        entity,
        text: str,
        emotion: str = None,
        instruct: str = None,
        volume: str = None,
        synthesis_key: str = None,
    ) -> str:
        """Generate TTS audio for an NPC line; return path or empty string."""
        if not getattr(self, '_tts_manager', None) or not self._tts_manager.is_enabled():
            return ""
        npc_uid = getattr(entity, 'entity_uid', None)
        if not npc_uid or not text:
            return ""
        try:
            from webapp.tts.npc_voice import ensure_npc_voice_locked

            profile = ensure_npc_voice_locked(self._tts_manager, entity)
            if profile is None:
                return ""

            resolved_volume = (volume or '').strip().lower() or None
            resolved_emotion = (emotion or '').strip() or self._detect_emotion_from_text(text)
            # Whisper volume must stay soft even if text heuristics say angry/shouting.
            if resolved_volume == 'whisper' and resolved_emotion in ('shouting', 'angry', 'yelling'):
                resolved_emotion = 'whisper'
            if resolved_volume == 'whisper' and not resolved_emotion:
                resolved_emotion = 'whisper'

            generate_kwargs = {
                'text': text,
                'npc_uid': npc_uid,
                'emotion': resolved_emotion,
                'volume': resolved_volume,
                'synthesis_key': synthesis_key,
            }
            if instruct:
                generate_kwargs['instruct'] = instruct
            try:
                return self._tts_manager.generate(**generate_kwargs) or ""
            except TypeError:
                generate_kwargs.pop('instruct', None)
                try:
                    return self._tts_manager.generate(**generate_kwargs) or ""
                except TypeError:
                    generate_kwargs.pop('volume', None)
                    return self._tts_manager.generate(**generate_kwargs) or ""
        except Exception as tts_err:
            self.logger.warning(
                "[TTS] Generation failed for %s: %s",
                getattr(entity, 'label', lambda: npc_uid)(),
                tts_err,
            )
            return ""

    def _normalize_username(self, username):
        return str(username).strip().lower() if username else ''

    def _sids_for_username(self, username):
        if not username:
            return []
        sid_map = getattr(self.current_game, 'username_to_sid', {}) or {}
        normalized = self._normalize_username(username)
        for key in (username, normalized):
            sids = sid_map.get(key)
            if sids:
                return list(sids)
        for key, sids in sid_map.items():
            if self._normalize_username(key) == normalized and sids:
                return list(sids)
        return []

    def _emit_tts_ready(self, username, event_payload) -> None:
        """Emit tts_ready and cache for HTTP polling fallback."""
        from webapp.tts.reply_cache import store_ready_reply

        payload = dict(event_payload or {})
        reply_id = payload.get('reply_id')
        if reply_id:
            store_ready_reply(reply_id, payload)
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        for sid in self._sids_for_username(username):
            try:
                socketio.emit('tts_ready', payload, to=sid)
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Failed to emit tts_ready for %s (reply_id=%s): %s",
                    username,
                    reply_id,
                    exc,
                )

    def _emit_tts_stream_event(self, username, event_name, event_payload) -> None:
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        payload = dict(event_payload or {})
        for sid in self._sids_for_username(username):
            try:
                socketio.emit(event_name, payload, to=sid)
            except Exception as exc:
                self.logger.warning(
                    "[TTS] Failed to emit %s for %s: %s",
                    event_name,
                    username,
                    exc,
                )

    def entity_audience_usernames(self, entities, include_dm=True):
        recipients = set()
        for entity in entities or []:
            if entity is None:
                continue
            recipients.update(
                self._normalize_username(username)
                for username in (self.entity_owners(entity) or [])
                if username
            )
            entity_uid = getattr(entity, 'entity_uid', None)
            if entity_uid:
                for login in self.logins:
                    username = login.get('name')
                    if not username:
                        continue
                    listener = self.conversation_listener_for_username(username)
                    if listener is not None and getattr(listener, 'entity_uid', None) == entity_uid:
                        recipients.add(self._normalize_username(username))

        if include_dm:
            for login in self.logins:
                roles = login.get('role') or []
                if 'dm' in roles and login.get('name'):
                    recipients.add(login['name'].lower())

        return recipients

    def conversation_audience_usernames(self, source_entity, processed_conversations=None, targets=None, include_dm=True):
        audience_entities = [source_entity]
        heard_uids = set()
        for receiver, _message, directed_to in (processed_conversations or []):
            audience_entities.append(receiver)
            heard_uids.add(getattr(receiver, 'entity_uid', None))
            for target in directed_to or []:
                audience_entities.append(target)
                heard_uids.add(getattr(target, 'entity_uid', None))
        for target in targets or []:
            target_uid = getattr(target, 'entity_uid', None)
            if target_uid and target_uid in heard_uids:
                audience_entities.append(target)
        return self.entity_audience_usernames(audience_entities, include_dm=include_dm)

    def conversation_visible_whisper_usernames(self, source_entity, audible_usernames=None, targets=None):
        audible_usernames = {str(username).lower() for username in (audible_usernames or []) if username}
        target_uids = {
            getattr(target, 'entity_uid', target)
            for target in (targets or [])
            if getattr(target, 'entity_uid', target)
        }

        try:
            battle_map = self.current_game.get_map_for_entity(source_entity)
        except Exception:
            battle_map = None

        if battle_map is None:
            return set()

        visible_usernames = set()
        candidate_usernames = {
            str(login.get('name')).lower()
            for login in self.logins
            if login.get('name')
        }
        candidate_usernames.update({str(username).lower() for username in self.current_game.username_to_sid.keys() if username})

        for username in candidate_usernames:
            if username in audible_usernames:
                continue

            roles = self.roles_for_username(username)
            if roles and 'dm' in roles:
                continue

            listener = self.conversation_listener_for_username(username)
            if listener is None or listener == source_entity:
                continue

            listener_uid = getattr(listener, 'entity_uid', None)
            if listener_uid and listener_uid in target_uids:
                continue

            try:
                listener_map = self.current_game.get_map_for_entity(listener)
            except Exception:
                listener_map = None

            if listener_map is not None and listener_map != battle_map:
                cross_battle = None
                try:
                    cross_battle = self.current_game.get_current_battle()
                except Exception:
                    cross_battle = None
                from natural20.utils.conversation import _shared_stack
                if not (cross_battle and _shared_stack(battle_map, listener_map) and cross_battle.can_see(listener, source_entity)):
                    continue

            try:
                visibility_battle = None
                try:
                    visibility_battle = self.current_game.get_current_battle()
                except Exception:
                    visibility_battle = None
                if visibility_battle is not None:
                    if visibility_battle.can_see(listener, source_entity):
                        visible_usernames.add(username)
                elif battle_map.can_see(listener, source_entity):
                    visible_usernames.add(username)
            except Exception:
                continue

        return visible_usernames

    def conversation_payload(self, entity, message, targets=None, volume=None, distance_ft=None, mentioned_targets=None, language=None, visual_only_usernames=None, narrative=None, actions=None, segments=None, continuation_id=None, continuation_beats_remaining=0, message_spell=False, message_spell_link_id=None, local_chat_only=False, gallery_image_id=None):
        import uuid

        targets = unique_entities(targets or [])
        mentioned_targets = unique_entities(mentioned_targets or [])
        serialized_segments = []
        for segment in segments or []:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get('type') or '').strip().lower()
            text = str(segment.get('text') or '').strip()
            if not text:
                continue
            if kind in ('aside', 'narrative', 'stage_direction'):
                serialized_segments.append({'type': 'aside', 'text': text})
            else:
                serialized_segments.append({'type': 'speech', 'text': text})
        payload = {
            'reply_id': uuid.uuid4().hex,
            'entity_id': entity.entity_uid,
            'speaker_name': entity_label(entity),
            'message': message,
            'narrative': [str(entry).strip() for entry in (narrative or []) if str(entry).strip()],
            'segments': serialized_segments,
            'actions': [str(entry).strip() for entry in (actions or []) if str(entry).strip()],
            'language': (language or 'common').lower(),
            'targets': [target.entity_uid for target in targets],
            'target_names': [entity_label(target) for target in targets],
            'mentioned_targets': [target.entity_uid for target in mentioned_targets],
            'mentioned_handles': [mention_handle_for(target) for target in mentioned_targets],
            'volume': normalize_speech_mode(mode=volume, distance_ft=distance_ft),
            'distance_ft': speech_distance_for(mode=volume, distance_ft=distance_ft),
            'visual_only_usernames': [str(username).lower() for username in (visual_only_usernames or []) if username],
        }
        if hasattr(entity, 'is_npc') and entity.is_npc():
            payload['target_id'] = entity.entity_uid
            payload['message_key'] = insight_check_message_key(entity.entity_uid, message)
        if continuation_id:
            payload['continuation_id'] = continuation_id
            payload['continuation_beats_remaining'] = int(continuation_beats_remaining or 0)
        if message_spell:
            payload['message_spell'] = True
            payload['channel'] = 'message_spell'
        if message_spell_link_id:
            payload['message_spell_link_id'] = message_spell_link_id
        if local_chat_only:
            payload['local_chat_only'] = True
        self.attach_gallery_image(payload, entity, gallery_image_id)
        return payload

    def attach_gallery_image(self, payload, entity, gallery_image_id=None):
        gallery_image_id = str(gallery_image_id or '').strip()
        if not gallery_image_id or entity is None:
            return
        payload['gallery_image_id'] = gallery_image_id
        if hasattr(entity, 'gallery_entry'):
            entry = entity.gallery_entry(gallery_image_id)
            if entry:
                payload['gallery_image'] = entry.get('image')
                if entry.get('label'):
                    payload['gallery_label'] = entry.get('label')
                return
        if hasattr(entity, 'resolve_gallery_image'):
            payload['gallery_image'] = entity.resolve_gallery_image(gallery_image_id)

    def build_skill_check_request_payload(self, requester, request_check):
        if not request_check or request_check.get('target') is None:
            return None

        resolved_check = dict(request_check)
        self.entity_rag_handler.ensure_request_check_dc(
            requester,
            resolved_check,
            speaker=resolved_check.get('target'),
        )
        target = resolved_check['target']
        skill = str(resolved_check.get('skill') or '').strip().lower()
        if not skill:
            return None

        mod_fn = getattr(target, f'{skill}_mod', None)
        try:
            mod_val = int(mod_fn()) if callable(mod_fn) else 0
        except Exception:
            mod_val = 0

        roll_str = f'1d20+{mod_val}' if mod_val >= 0 else f'1d20{mod_val}'
        skill_label = skill.replace('_', ' ').title()
        requester_label = entity_label(requester)
        return {
            'skill': skill,
            'skill_label': skill_label,
            'actor_uid': getattr(target, 'entity_uid', None),
            'actor_name': entity_label(target),
            'requester_uid': getattr(requester, 'entity_uid', None),
            'requester_name': requester_label,
            'dc': resolved_check.get('dc'),
            'roll': roll_str,
            'description': f'{skill_label} check (requested by {requester_label})',
            'reply_id': None,
        }

    def attach_skill_check_request(self, payload, requester, reply_plan):
        skill_payload = self.build_skill_check_request_payload(
            requester,
            (reply_plan or {}).get('request_check'),
        )
        if skill_payload:
            skill_payload['reply_id'] = payload.get('reply_id')
            payload['skill_check_request'] = skill_payload
        return payload

    def emit_nonverbal_npc_reply(
        self,
        receiver,
        reply_plan,
        *,
        speaker=None,
        http_replies=None,
        normalized_requesting_username=None,
    ):
        if getattr(self.current_game, 'realtime_ui_suppressed', lambda: False)():
            return None
        if not reply_plan or not reply_plan.get('skip'):
            return None

        narrative = reply_plan.get('narrative') or []
        segments = reply_plan.get('segments') or []
        request_check = reply_plan.get('request_check')
        if not narrative and not request_check:
            return None

        targets = list(reply_plan.get('targets') or [])
        if speaker is not None:
            targets = unique_entities(targets + [speaker])

        reply_distance_ft = speech_distance_for(mode=reply_plan.get('volume'))
        try:
            response_conversations = receiver.send_conversation(
                reply_plan.get('message') or '',
                distance_ft=reply_distance_ft,
                targets=targets,
                language=reply_plan.get('language', 'common'),
                volume=reply_plan.get('volume'),
                narrative=narrative,
                segments=segments,
                gallery_image_id=reply_plan.get('gallery_image_id'),
            )
        except TypeError:
            response_conversations = receiver.send_conversation(
                reply_plan.get('message') or '',
                distance_ft=reply_distance_ft,
                targets=targets,
                language=reply_plan.get('language', 'common'),
            )

        recipient_usernames = self.conversation_audience_usernames(
            receiver,
            response_conversations,
            targets=targets,
        )
        visual_whisper_usernames = set()
        if reply_plan.get('volume') == 'whisper':
            visual_whisper_usernames = self.conversation_visible_whisper_usernames(
                receiver,
                audible_usernames=recipient_usernames,
                targets=targets,
            )

        conversation_payload = self.conversation_payload(
            receiver,
            reply_plan.get('message') or '',
            targets=targets,
            volume=reply_plan.get('volume'),
            distance_ft=reply_distance_ft,
            language=reply_plan.get('language', 'common'),
            visual_only_usernames=visual_whisper_usernames,
            narrative=narrative,
            segments=segments,
            gallery_image_id=reply_plan.get('gallery_image_id'),
        )
        self.attach_skill_check_request(conversation_payload, receiver, reply_plan)
        conversation_payload['tts_pending'] = False

        emitted_by_username = self.emit_conversation_to_usernames(
            recipient_usernames | visual_whisper_usernames,
            conversation_payload,
            source_entity=receiver,
        )
        if http_replies is not None:
            self._append_http_reply_for_requester(
                http_replies,
                normalized_requesting_username,
                emitted_by_username,
                conversation_payload,
                receiver,
            )
        self.logger.info(
            "[Talk] emitted nonverbal reply from %s to_usernames=%s narrative_count=%s request_check=%s",
            receiver.label(),
            sorted(recipient_usernames | visual_whisper_usernames),
            len(narrative),
            bool(request_check),
        )
        return conversation_payload

    def conversation_listener_for_username(self, username):
        try:
            listener = self.current_game.get_pov_entity_for_user(username)
        except Exception:
            listener = None
        if listener is not None:
            return listener

        try:
            controlled_entities = self.entities_controlled_by(username)
        except Exception:
            controlled_entities = []
        return controlled_entities[0] if controlled_entities else None

    def listener_understands_language(self, listener, language):
        return understands_language(listener, language, session=self.game_session)

    def _beast_speech_tts_style(self, language, understands: bool):
        normalized = str(language or 'common').strip().lower()
        if understands or normalized not in {'sheep', 'beast', 'animals', 'animal'}:
            return None, None
        return (
            'fearful',
            'frantic sheep bleating, worried animal sounds, no intelligible human speech',
        )

    def _listener_cannot_hear(self, listener) -> bool:
        if listener is None:
            return False
        try:
            statuses = list(getattr(listener, 'statuses', []) or [])
        except Exception:
            statuses = []
        return any(status in {'deafened', 'unconscious'} for status in statuses)

    def _listener_speech_reachability(self, listener, source_entity, volume=None):
        if listener is None or source_entity is None:
            return None
        if getattr(listener, 'entity_uid', None) == getattr(source_entity, 'entity_uid', None):
            return {
                'reachable_now': True,
                'distance_ft': 0,
                'adjusted_distance_ft': 0,
                'effective_distance_ft': speech_distance_for(mode=volume),
                'status': 'self',
            }

        try:
            battle_map = self.current_game.get_map_for_entity(source_entity)
        except Exception:
            battle_map = None
        if battle_map is None:
            return {
                'reachable_now': True,
                'distance_ft': 0,
                'adjusted_distance_ft': 0,
                'effective_distance_ft': speech_distance_for(mode=volume),
                'status': 'reachable',
            }

        try:
            listener_map = self.current_game.get_map_for_entity(listener)
        except Exception:
            listener_map = None

        battle = None
        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None

        if listener_map is not None and listener_map != battle_map:
            from natural20.utils.conversation import _shared_stack
            if not (battle and _shared_stack(battle_map, listener_map) and battle.can_see(source_entity, listener)):
                return {
                    'reachable_now': False,
                    'distance_ft': None,
                    'adjusted_distance_ft': None,
                    'effective_distance_ft': 0,
                    'status': 'too_far',
                }

        listener_uid = getattr(listener, 'entity_uid', None)
        try:
            entries = conversation_reachability(source_entity, battle_map, mode=volume, battle=battle)
        except Exception:
            return None
        for entry in entries:
            entity = entry.get('entity')
            if entity is not None and getattr(entity, 'entity_uid', None) == listener_uid:
                return entry
        return {
            'reachable_now': False,
            'distance_ft': None,
            'adjusted_distance_ft': None,
            'effective_distance_ft': 0,
            'status': 'too_far',
        }

    def _tts_gain_for_reachability(self, reachability_entry, volume=None) -> float:
        if not reachability_entry or not reachability_entry.get('reachable_now'):
            return 0.0
        if reachability_entry.get('status') == 'self':
            return 1.0

        adjusted = reachability_entry.get('adjusted_distance_ft')
        if adjusted is None:
            adjusted = reachability_entry.get('distance_ft') or 0
        effective = reachability_entry.get('effective_distance_ft')
        if effective is None or effective <= 0:
            effective = speech_distance_for(mode=volume) or 30

        ratio = min(1.0, max(0.0, float(adjusted) / float(effective)))
        gain = 1.0 - (0.75 * ratio)
        volume_mode = normalize_speech_mode(mode=volume)
        if volume_mode == 'whisper':
            gain = min(gain, 0.9)
        elif volume_mode == 'shout':
            gain = min(1.0, gain + 0.05)
        return round(max(0.15, min(1.0, gain)), 3)

    def _tts_synthesis_cache_key(self, npc_uid, text, emotion, instruct, volume):
        from webapp.tts.synthesis_cache import synthesis_cache_key

        return synthesis_cache_key(npc_uid, text, emotion, instruct, volume)

    def _attach_per_listener_tts(
        self,
        rendered_payload,
        base_payload,
        source_entity,
        username,
        synthesis_cache=None,
        generate_audio=True,
    ):
        rendered_payload['tts_generated'] = False

        if rendered_payload.get('visual_only'):
            return rendered_payload

        from natural20.utils.conversation import sanitize_spoken_text_for_tts, strip_spoken_address_prefix

        text = strip_spoken_address_prefix(str(rendered_payload.get('message') or '').strip())
        if not text:
            return rendered_payload
        rendered_payload['message'] = text
        tts_text = sanitize_spoken_text_for_tts(text)
        rendered_payload['tts_text'] = tts_text

        roles = self.roles_for_username(username) or []
        is_dm = 'dm' in roles
        listener = self.conversation_listener_for_username(username)

        if not is_dm and self._listener_cannot_hear(listener):
            rendered_payload['tts_skipped'] = 'deaf_or_unconscious'
            return rendered_payload

        volume = base_payload.get('volume')
        if not is_dm:
            reachability = self._listener_speech_reachability(listener, source_entity, volume=volume)
            tts_gain = self._tts_gain_for_reachability(reachability, volume=volume)
            if tts_gain <= 0.0:
                rendered_payload['tts_skipped'] = (reachability or {}).get('status') or 'too_far'
                return rendered_payload
            rendered_payload['tts_gain'] = tts_gain
        else:
            rendered_payload['tts_gain'] = 1.0

        language = base_payload.get('language', 'common')
        understands = self.listener_understands_language(listener, language)
        emotion = base_payload.get('tts_emotion')
        instruct = base_payload.get('tts_instruct')
        beast_emotion, beast_instruct = self._beast_speech_tts_style(language, understands)
        if beast_instruct:
            instruct = instruct or beast_instruct
            emotion = emotion or beast_emotion

        resolved_emotion = emotion or self._detect_emotion_from_text(text)
        rendered_payload['tts_emotion'] = resolved_emotion
        if instruct:
            rendered_payload['tts_instruct'] = instruct

        if not generate_audio:
            return rendered_payload

        npc_uid = getattr(source_entity, 'entity_uid', None)
        cache_key = self._tts_synthesis_cache_key(
            npc_uid, tts_text, resolved_emotion, instruct, volume,
        )
        audio_url = synthesis_cache.get(cache_key) if synthesis_cache is not None else None

        if not audio_url and getattr(self, '_tts_manager', None):
            lookup = getattr(self._tts_manager, 'lookup_cached_line', None)
            cached_path = lookup(cache_key) if callable(lookup) else None
            if cached_path:
                audio_url = f"/tts/{os.path.basename(cached_path)}"
                if synthesis_cache is not None:
                    synthesis_cache[cache_key] = audio_url

        if not audio_url:
            audio_path = self._ensure_tts_audio(
                source_entity,
                tts_text,
                emotion=resolved_emotion,
                instruct=instruct,
                volume=volume,
                synthesis_key=cache_key,
            )
            if audio_path:
                audio_url = f"/tts/{os.path.basename(audio_path)}"
                if synthesis_cache is not None:
                    synthesis_cache[cache_key] = audio_url

        if audio_url:
            rendered_payload['audio_url'] = audio_url
            rendered_payload['tts_generated'] = True
        return rendered_payload

    def _append_http_reply_for_requester(
        self,
        http_replies,
        normalized_requesting_username,
        emitted_by_username,
        conversation_payload,
        source_entity,
    ):
        """Include an HTTP fallback reply for the /talk requester (client dedupes reply_id)."""
        if not normalized_requesting_username:
            return

        rendered_for_requester = emitted_by_username.get(normalized_requesting_username)
        if rendered_for_requester is None:
            rendered_for_requester = self.render_conversation_payload_for_username(
                normalized_requesting_username,
                source_entity,
                conversation_payload,
            )
            if conversation_payload.get('tts_pending'):
                delivery_mode = self._tts_delivery_mode(normalized_requesting_username)
                rendered_for_requester = self._attach_per_listener_tts(
                    rendered_for_requester,
                    conversation_payload,
                    source_entity,
                    normalized_requesting_username,
                    generate_audio=(delivery_mode == 'sync'),
                )
                self._mark_tts_delivery_status(rendered_for_requester, delivery_mode)

        has_content = bool(str(rendered_for_requester.get('message') or '').strip())
        narrative = rendered_for_requester.get('narrative') or []
        actions = rendered_for_requester.get('actions') or []
        if not has_content and isinstance(narrative, list):
            has_content = any(str(entry).strip() for entry in narrative)
        if not has_content and isinstance(actions, list):
            has_content = any(str(entry).strip() for entry in actions)
        if not has_content and rendered_for_requester.get('skill_check_request'):
            has_content = True

        if has_content:
            http_replies.append(rendered_for_requester)

    def render_conversation_payload_for_username(self, username, source_entity, payload):
        payload_for_user = dict(payload)
        language = payload_for_user.get('language') or 'common'
        normalized_username = str(username).lower() if username else username

        if not username:
            return payload_for_user

        roles = self.roles_for_username(username)
        if roles and 'dm' in roles:
            return payload_for_user

        owner_usernames = {
            self._normalize_username(username)
            for username in (self.entity_owners(source_entity) or [])
            if username
        }
        if normalized_username in owner_usernames:
            return payload_for_user

        listener = self.conversation_listener_for_username(username)
        visual_only = normalized_username in set(payload_for_user.get('visual_only_usernames') or [])

        if listener is not None and source_entity is not None and listener != source_entity:
            reachability = self._listener_speech_reachability(
                listener,
                source_entity,
                volume=payload_for_user.get('volume'),
            )
            if not reachability or not reachability.get('reachable_now'):
                if visual_only:
                    payload_for_user['message'] = 'You can see them whispering, but cannot hear the words.'
                    payload_for_user['visual_only'] = True
                    return payload_for_user
                payload_for_user['message'] = ''
                payload_for_user['inaudible'] = True
                payload_for_user['inaudible_reason'] = (reachability or {}).get('status') or 'too_far'
                return payload_for_user

        if self.listener_understands_language(listener, language):
            return payload_for_user

        payload_for_user['message'] = gibberish(payload_for_user.get('message', ''), language=language)
        return payload_for_user

    def resolve_conversation_targets(self, source_entity, primary_targets=None, message=None):
        explicit_targets = []
        for entity_uid in primary_targets or []:
            target = self.current_game.get_entity_by_uid(entity_uid)
            if target is None:
                try:
                    target = self.game_session.entity_by_uid(entity_uid)
                except Exception:
                    target = None
            if target is not None:
                explicit_targets.append(target)

        try:
            battle_map = self.current_game.get_map_for_entity(source_entity)
        except Exception:
            battle_map = None
        candidate_entities = []
        if battle_map is not None:
            try:
                candidate_entities = [entity for entity in battle_map.entities if entity != source_entity]
            except Exception:
                candidate_entities = []

        mentioned_targets = resolve_mention_targets(message or '', candidate_entities)
        inferred_targets = []
        if not explicit_targets and not mentioned_targets:
            inferred_targets = resolve_named_targets(message or '', candidate_entities)

        return unique_entities(explicit_targets + mentioned_targets + inferred_targets), unique_entities(mentioned_targets)

    def select_conversation_responders(self, processed_conversations, source_entity, targeted_entities=None, latest_message=None, language='common', volume='normal'):
        targeted_entities = unique_entities(targeted_entities or [])
        eligible_receivers = unique_entities([
            receiver for receiver, _rendered_message, _directed_to in (processed_conversations or [])
            if receiver.entity_uid != source_entity.entity_uid and receiver.is_npc() and receiver.dialog
        ])

        for target in targeted_entities:
            if (
                target.entity_uid != source_entity.entity_uid
                and getattr(target, 'is_npc', lambda: False)()
                and getattr(target, 'dialog', False)
                and target not in eligible_receivers
            ):
                eligible_receivers.append(target)

        if not eligible_receivers:
            return []

        if targeted_entities:
            targeted_uids = {target.entity_uid for target in targeted_entities}
            targeted_receivers = [receiver for receiver in eligible_receivers if receiver.entity_uid in targeted_uids]
            if targeted_receivers:
                return targeted_receivers

        llm_conversation_handler = self.llm_conversation_handler
        if len(eligible_receivers) > 1 and llm_conversation_handler is not None and hasattr(llm_conversation_handler, 'route_conversation_responders'):
            routed_receivers = llm_conversation_handler.route_conversation_responders(
                source_entity,
                eligible_receivers,
                latest_message=latest_message or '',
                targeted_entities=targeted_entities,
                language=language,
                volume=volume,
            )
            if routed_receivers:
                return routed_receivers
            if routed_receivers is not None:
                return eligible_receivers[:1]

        return eligible_receivers[:1]

    def emit_conversation_to_usernames(self, usernames, payload, source_entity=None):
        if getattr(self.current_game, 'realtime_ui_suppressed', lambda: False)():
            return {}
        tts_pending = bool(payload.get('tts_pending'))
        npc_source = (
            source_entity is not None
            and not isinstance(source_entity, PlayerCharacter)
        )
        async_jobs = []
        stream_jobs = []
        emitted_by_username = {}
        synthesis_cache = {}
        for recipient_username in usernames:
            delivery_mode = (
                self._tts_delivery_mode(recipient_username)
                if tts_pending and npc_source
                else None
            )
            generate_audio_inline = delivery_mode == 'sync'
            rendered_payload = self.render_conversation_payload_for_username(recipient_username, source_entity, payload) if source_entity is not None else dict(payload)
            if tts_pending and npc_source:
                rendered_payload = self._attach_per_listener_tts(
                    rendered_payload,
                    payload,
                    source_entity,
                    recipient_username,
                    synthesis_cache=synthesis_cache,
                    generate_audio=generate_audio_inline,
                )
                self._mark_tts_delivery_status(rendered_payload, delivery_mode)
                if delivery_mode == 'async' and rendered_payload.get('tts_status') == 'pending':
                    async_jobs.append({
                        'username': recipient_username,
                        'reply_id': rendered_payload.get('reply_id') or payload.get('reply_id'),
                        'rendered_payload': dict(rendered_payload),
                    })
                elif delivery_mode == 'stream' and rendered_payload.get('tts_status') in ('streaming', 'pending'):
                    stream_jobs.append({
                        'username': recipient_username,
                        'reply_id': rendered_payload.get('reply_id') or payload.get('reply_id'),
                        'rendered_payload': dict(rendered_payload),
                    })
            normalized_username = self._normalize_username(recipient_username)
            emitted_by_username[normalized_username] = rendered_payload
            for sid in self._sids_for_username(recipient_username):
                self.socketio.emit('message', {'type': 'conversation', 'message': rendered_payload}, to=sid)
            try:
                # Yield so long /talk handlers can flush Socket.IO events before returning.
                self.socketio.sleep(0)
            except Exception:
                pass
        if async_jobs:
            self._schedule_async_tts_delivery(async_jobs, payload, source_entity)
        if stream_jobs:
            self._schedule_stream_tts_delivery(stream_jobs, payload, source_entity)
        return emitted_by_username

    def conversation_status_summary(self, entity):
        statuses = []
        try:
            statuses = list(getattr(entity, 'statuses', []) or [])
        except Exception:
            statuses = []
        return [str(status).strip().replace('_', ' ') for status in statuses if status]

    def conversation_effect_summary(self, entity):
        effect_names = []
        current_effects = getattr(entity, 'current_effects', None)
        if not callable(current_effects):
            return effect_names

        try:
            effects = current_effects() or []
        except Exception:
            effects = []

        for effect in effects:
            if isinstance(effect, dict):
                effect_name = effect.get('effect') or effect.get('name')
            else:
                effect_name = effect
            if effect_name:
                effect_names.append(str(effect_name).strip().replace('_', ' '))
        return effect_names

    def conversation_goal_summary(self, entity):
        try:
            get_goal = getattr(self.current_game, 'get_short_term_goal', None)
            if not callable(get_goal):
                return None
            goal_state = get_goal(entity)
        except Exception:
            goal_state = None

        if not isinstance(goal_state, dict):
            return None
        if goal_state.get('status') != 'active':
            return None
        goal_text = str(goal_state.get('goal') or '').strip()
        return goal_text or None

    def conversation_attitude_toward_speaker(self, receiver, speaker):
        if receiver is None or speaker is None:
            return 'unknown'

        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None

        if battle is not None:
            try:
                if battle.opposing(receiver, speaker):
                    return 'hostile'
            except Exception:
                pass
            try:
                if battle.allies(receiver, speaker):
                    return 'allied'
            except Exception:
                pass

        receiver_group = getattr(receiver, 'group', None)
        speaker_group = getattr(speaker, 'group', None)
        if receiver_group and speaker_group:
            if receiver_group == speaker_group:
                return 'allied'
            try:
                if self.game_session.opposing(receiver_group, speaker_group):
                    return 'hostile'
            except Exception:
                pass
            return 'wary'

        try:
            receiver_owners = set(self.entity_owners(receiver))
            speaker_owners = set(self.entity_owners(speaker))
            if receiver_owners and speaker_owners and receiver_owners.intersection(speaker_owners):
                return 'allied'
        except Exception:
            pass

        return 'guarded'

    def conversation_pressure_summary(self, receiver):
        pressures = []

        hp = getattr(receiver, 'hp', None)
        max_hp = getattr(receiver, 'max_hp', None)
        try:
            if max_hp and hp is not None and max_hp > 0 and hp <= max(1, max_hp // 3):
                pressures.append('badly hurt')
        except Exception:
            pass

        statuses = self.conversation_status_summary(receiver)
        if statuses:
            pressures.append(f"statuses: {', '.join(statuses[:4])}")

        effects = self.conversation_effect_summary(receiver)
        if effects:
            pressures.append(f"active effects: {', '.join(effects[:3])}")

        goal_text = self.conversation_goal_summary(receiver)
        if goal_text:
            pressures.append(f"active goal: {goal_text}")

        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None
        if battle is not None:
            try:
                if battle.entity_state_for(receiver) is not None:
                    pressures.append('currently in combat')
            except Exception:
                pressures.append('currently in combat')

        return '; '.join(pressures) if pressures else 'no special pressure beyond the current conversation'

    @staticmethod
    def message_expects_direct_reply(message) -> bool:
        text = (message or '').strip()
        if not text:
            return False
        if '?' in text:
            return True
        return bool(_DIRECT_REPLY_MESSAGE.match(text))

    @staticmethod
    def message_summons_npc(message) -> bool:
        text = re.sub(r'@\S+', ' ', message or '')
        if re.search(
            r'\b(?:tell|ask|have|get|send|instruct|let)\s+(?:@?\w[\w-]*|him|her|them)\s+to\b',
            text,
            re.IGNORECASE,
        ):
            return False
        if re.search(r'\b(?:go|walk|head)\s+(?:over\s+)?to\s+\w', text, re.IGNORECASE):
            return False
        return bool(_SUMMON_NPC_PHRASES.search(text))

    @staticmethod
    def message_invites_listener_approach(message) -> bool:
        return bool(_INVITE_APPROACH_PHRASES.search(message or ''))

    @staticmethod
    def message_requests_npc_relay(message) -> bool:
        text = (message or '').strip()
        if not text:
            return False
        return bool(
            re.search(
                r'\b(?:tell|ask|let|have|get|send|instruct|fetch)\s+(?:@?\w[\w-]*|him|her|them)\b',
                text,
                re.IGNORECASE,
            )
        )

    def select_npc_chain_responders(self, processed_conversations, speaker_npc, reply_plan, *, player_speaker=None):
        if speaker_npc is None:
            return []

        reply_plan = reply_plan or {}

        heard_entities = {
            listener.entity_uid: listener
            for listener, _message, _directed_to in (processed_conversations or [])
            if listener is not None and listener != speaker_npc
        }

        try:
            battle_map = self.current_game.get_map_for_entity(speaker_npc)
        except Exception:
            battle_map = None
        candidates = []
        if battle_map is not None:
            try:
                candidates = [entity for entity in battle_map.entities if entity != speaker_npc]
            except Exception:
                candidates = []

        message = (reply_plan or {}).get('message') or ''
        named_targets = unique_entities(
            resolve_named_targets(message, candidates)
            + resolve_mention_targets(message, candidates)
        )
        chain_targets = unique_entities(
            list((reply_plan or {}).get('targets') or []) + named_targets
        )
        for target in chain_targets:
            if target is None or target == speaker_npc:
                continue
            if isinstance(player_speaker, PlayerCharacter) and target == player_speaker:
                continue
            uid = getattr(target, 'entity_uid', None)
            if uid:
                heard_entities[uid] = target

        if not heard_entities:
            return []

        def _eligible_npc_responder(target):
            if target is None or target == speaker_npc:
                return False
            if isinstance(player_speaker, PlayerCharacter) and target == player_speaker:
                return False
            if not getattr(target, 'is_npc', lambda: False)():
                return False
            if not getattr(target, 'dialog', False):
                return False
            if self.npc_conversation_mode_is_manual(target):
                return False
            uid = getattr(target, 'entity_uid', None)
            return bool(uid and uid in heard_entities)

        chain_responders = []
        for target in named_targets:
            if _eligible_npc_responder(target):
                chain_responders.append(target)

        if chain_responders:
            return chain_responders[:1]

        for target in unique_entities(reply_plan.get('targets') or []):
            if _eligible_npc_responder(target):
                return [target]
        return []

    def expand_reply_targets_from_message(self, speaker_npc, reply_plan):
        if speaker_npc is None or not isinstance(reply_plan, dict):
            return reply_plan

        message = reply_plan.get('message') or ''
        try:
            battle_map = self.current_game.get_map_for_entity(speaker_npc)
        except Exception:
            battle_map = None
        candidates = []
        if battle_map is not None:
            try:
                candidates = [entity for entity in battle_map.entities if entity != speaker_npc]
            except Exception:
                candidates = []

        extras = unique_entities(resolve_mention_targets(message, candidates))
        if not extras:
            return reply_plan

        expanded = dict(reply_plan)
        expanded['targets'] = unique_entities((reply_plan.get('targets') or []) + extras)
        return expanded

    def _receiver_was_addressed(self, receiver, delivered_targets, mentioned_targets):
        addressed = unique_entities((delivered_targets or []) + (mentioned_targets or []))
        return receiver in addressed

    def _speaker_within_approach_distance(self, receiver, speaker, distance_ft: float = 5) -> bool:
        if receiver is None or speaker is None:
            return False
        try:
            battle_map = self.current_game.get_map_for_entity(receiver)
        except Exception:
            battle_map = None
        if battle_map is None:
            return False
        try:
            distance = battle_map.distance(receiver, speaker) * battle_map.feet_per_grid
            return distance <= distance_ft
        except Exception:
            return False

    def maybe_auto_approach_summoned_npc(
        self,
        receiver,
        speaker,
        message,
        *,
        delivered_targets=None,
        mentioned_targets=None,
        approach_distance_ft: int = 5,
    ) -> bool:
        if speaker is None or receiver is None:
            return False
        if not self.message_summons_npc(message):
            return False
        if not self._receiver_was_addressed(receiver, delivered_targets, mentioned_targets):
            return False
        if self._speaker_within_approach_distance(receiver, speaker, distance_ft=approach_distance_ft):
            return False
        try:
            if self.current_game.get_current_battle() is not None:
                return False
        except Exception:
            pass

        return self.ensure_npc_moves_to_speaker(
            receiver,
            speaker,
            distance_ft=approach_distance_ft,
        )

    def ensure_npc_moves_to_speaker(self, receiver, speaker, *, distance_ft: int = 5) -> bool:
        if speaker is None or receiver is None:
            return False
        if self._speaker_within_approach_distance(receiver, speaker, distance_ft=distance_ft):
            return False
        try:
            if self.current_game.get_current_battle() is not None:
                return False
        except Exception:
            pass

        target_info = {
            'kind': 'entity',
            'target': speaker,
        }
        approach_plan = {
            'approach': {
                'target_info': target_info,
                'distance_ft': distance_ft,
            },
        }
        result = self.entity_rag_handler.apply_response_plan_directives(
            approach_plan,
            receiver,
            speaker=speaker,
            only={'approach'},
        )
        if self.entity_rag_handler.movement_helper.movement_reached(
            receiver,
            target_info,
            distance_ft=distance_ft,
        ):
            return 'approach' in (result.get('executed_actions') or [])

        scheduled = self.current_game.schedule_npc_move_to(
            receiver,
            target_info,
            distance_ft=distance_ft,
            speaker=speaker,
        )
        return (
            'approach' in (result.get('executed_actions') or [])
            or 'move_to' in (result.get('executed_actions') or [])
            or scheduled is not None
        )

    def build_npc_identity_prompt(self, receiver, conversation_system_prompt, speaker=None) -> str:
        """NPC backstory, gear, and appearance without live conversation response rules."""
        attributes = receiver.ability_scores
        attributes_str = "\n".join([f"{key}: {value}" for key, value in attributes.items()])
        gear_summary = format_entity_gear_for_conversation(receiver, self.game_session)
        appearance_summary = conversation_self_appearance(receiver, session=self.game_session)
        prompt = conversation_system_prompt.format(
            backstory=receiver.backstory(),
            name=receiver.label(),
            attributes=attributes_str,
            alignment=receiver.alignment().replace("_", " "),
            languages=", ".join(receiver.languages()),
            gear=gear_summary,
            appearance=appearance_summary,
        )
        try:
            from webapp.npc_situational_context import in_party_sidekick_prompt_block
            party_block = in_party_sidekick_prompt_block(
                self.game_session, receiver, speaker=speaker,
            )
            if party_block:
                prompt += "\n" + party_block
        except Exception:
            pass
        return prompt

    def build_npc_system_prompt(
        self,
        receiver,
        conversation_system_prompt,
        *,
        speaker=None,
        player_speaker=None,
        summoned: bool = False,
        relay_instruction: bool = False,
        npc_initiated: bool = False,
        npc_chain_banter: bool = False,
        player_message: str = '',
        message_spell_private: bool = False,
    ) -> str:
        """Format the standard NPC identity + conversation rules system prompt."""
        system_prompt = self.build_npc_identity_prompt(
            receiver, conversation_system_prompt, speaker=speaker,
        )
        try:
            from webapp.blueprints.helpers.time_of_day import world_time_prompt_block
            system_prompt += world_time_prompt_block(self.current_game)
        except Exception:
            pass
        system_prompt += self.conversation_response_prompt(
            receiver,
            speaker,
            player_speaker=player_speaker,
            summoned=summoned,
            relay_instruction=relay_instruction,
            npc_initiated=npc_initiated,
            npc_chain_banter=npc_chain_banter,
            player_message=player_message,
            message_spell_private=message_spell_private,
        )
        return system_prompt

    def _ensure_latest_player_turn_in_llm_history(
        self,
        llm_conversation_handler,
        receiver,
        speaker,
        spoken_message,
        *,
        language='common',
        message_spell_private: bool = False,
    ):
        """Guarantee the current PC line is the latest user turn for NPC LLM replies."""
        spoken = str(spoken_message or '').strip()
        if not spoken or speaker is None or receiver is None or llm_conversation_handler is None:
            return

        conversation_id = receiver.entity_uid
        conv = llm_conversation_handler.conversations.get(conversation_id) or {}
        messages = conv.get('messages') or []
        last_user = ''
        for entry in reversed(messages):
            if entry.get('role') == 'user':
                last_user = str(entry.get('content') or '')
                break

        if spoken in last_user:
            return

        if message_spell_private:
            speaker_label = entity_label(speaker)
            formatted = (
                "[MESSAGE SPELL — magical whisper, not normal room speech. "
                "Only you hear this; only the caster will hear your reply. "
                "Nearby creatures hear nothing.]\n"
                f"{speaker_label} whispers to you via the Message spell (in {language}): {spoken}"
            )
        else:
            formatted = llm_conversation_handler._format_incoming_player_turn({
                'source': speaker,
                'message': spoken,
                'language': language,
                'actions': [],
            })
        if not formatted:
            return
        if formatted.strip() == last_user.strip():
            return
        llm_conversation_handler.add_message(conversation_id, 'user', formatted)

    def _run_single_npc_reply_turn(
        self,
        receiver,
        speaker,
        conversation_system_prompt,
        *,
        summoned: bool = False,
        relay_instruction: bool = False,
        llm_conversation_handler=None,
        http_replies=None,
        normalized_requesting_username=None,
        nudge_on_no_response: bool = False,
        original_player_message: str = '',
        inject_system_messages=None,
        npc_initiated: bool = False,
        npc_chain_reply: bool = False,
        open_dialog: bool = False,
        local_chat_only: bool = False,
        message_spell_private: bool = False,
        message_spell_link_id: str | None = None,
        player_speaker=None,
        player_language: str = 'common',
    ):
        if llm_conversation_handler is None:
            llm_conversation_handler = self.llm_conversation_handler

        if self.npc_conversation_mode_is_manual(receiver):
            self.logger.info(
                "[Talk] skipping LLM reply for %s (manual conversation mode)",
                receiver.label(),
            )
            return None

        receiver_started = time.monotonic()
        system_prompt = self.build_npc_system_prompt(
            receiver,
            conversation_system_prompt,
            speaker=speaker,
            player_speaker=player_speaker,
            summoned=summoned,
            relay_instruction=relay_instruction,
            npc_initiated=npc_initiated,
            npc_chain_banter=npc_chain_reply,
            player_message=original_player_message,
            message_spell_private=message_spell_private,
        )
        llm_conversation_handler.create_conversation(receiver.entity_uid, system_prompt)
        try:
            llm_conversation_handler.conversations[receiver.entity_uid]['system_prompt'] = system_prompt
        except Exception:
            pass
        llm_conversation_handler.update_conversation_history(receiver.entity_uid, receiver.conversation_buffer)
        self._ensure_latest_player_turn_in_llm_history(
            llm_conversation_handler,
            receiver,
            speaker,
            original_player_message,
            language=player_language,
            message_spell_private=message_spell_private,
        )
        for note in inject_system_messages or []:
            text = str(note or '').strip()
            if text:
                llm_conversation_handler.add_message(receiver.entity_uid, 'user', text)

        self.logger.info("[Talk] generating response for %s (%s)", receiver.label(), receiver.entity_uid)
        response = llm_conversation_handler.generate_response(receiver.entity_uid)
        if is_npc_llm_failure_response(response):
            self.logger.warning(
                "[Talk] suppressed NPC LLM failure for %s preview=%r",
                receiver.label(),
                (response or '')[:160],
            )
            try:
                conv = llm_conversation_handler.conversations.get(receiver.entity_uid) or {}
                messages = conv.get('messages') or []
                if messages and messages[-1].get('role') == 'assistant':
                    if is_npc_llm_failure_response(messages[-1].get('content')):
                        messages.pop()
            except Exception:
                pass
            return None
        self.logger.info(
            "[Talk] raw LLM response for %s preview=%r",
            receiver.label(),
            (response or '')[:160],
        )
        plan_started = time.monotonic()
        reply_plan = self.entity_rag_handler.build_conversation_response_plan(
            response,
            receiver,
            speaker,
            llm_conversation_handler,
            player_speaker=player_speaker,
            default_volume='normal' if npc_chain_reply else None,
        )
        if message_spell_private and speaker is not None:
            reply_plan['volume'] = 'whisper'
            reply_plan['targets'] = [speaker]
        self.logger.info(
            "[Talk] reply plan for %s skip=%s volume=%s targets=%s plan_ms=%.1f",
            receiver.label(),
            reply_plan.get('skip'),
            reply_plan.get('volume'),
            [getattr(t, 'entity_uid', t) for t in (reply_plan.get('targets') or [])],
            (time.monotonic() - plan_started) * 1000.0,
        )
        if (
            nudge_on_no_response
            and reply_plan['skip']
            and self.message_expects_direct_reply(original_player_message)
            and self.entity_rag_handler.parse_response_controls(response).get('no_response')
        ):
            llm_conversation_handler.add_message(
                receiver.entity_uid,
                'user',
                (
                    f"{entity_label(speaker)} addressed you directly with a greeting or question. "
                    'Reply briefly in character. Use [TO: speaker] when needed. Do not output [NO_RESPONSE].'
                ),
            )
            response = llm_conversation_handler.generate_response(receiver.entity_uid)
            self.logger.info(
                "[Talk] nudged LLM response for %s preview=%r",
                receiver.label(),
                (response or '')[:160],
            )
            reply_plan = self.entity_rag_handler.build_conversation_response_plan(
                response,
                receiver,
                speaker,
                llm_conversation_handler,
                player_speaker=player_speaker,
                default_volume='normal' if npc_chain_reply else None,
            )

        if reply_plan['skip']:
            self.logger.info(
                "[Talk] skipping outbound speech for %s llm_response=%r receiver_ms=%.1f",
                receiver.label(),
                (response or '')[:160],
                (time.monotonic() - receiver_started) * 1000.0,
            )
            try:
                goal_arrival_manager = getattr(self.current_game, 'goal_arrival_manager', None)
                if goal_arrival_manager is not None:
                    if (
                        goal_arrival_manager.active_state() is not None
                        and isinstance(speaker, PlayerCharacter)
                        and is_passive_acknowledgement(original_player_message)
                    ):
                        goal_arrival_manager.on_player_passive_ack(speaker)
                    else:
                        goal_arrival_manager.maybe_complete_after_npc_reply(receiver, response)
            except Exception as exc:
                self.logger.warning(
                    "[Talk] goal arrival resolution failed for %s: %s",
                    receiver.label(),
                    exc,
                )
            self.emit_nonverbal_npc_reply(
                receiver,
                reply_plan,
                speaker=speaker,
                http_replies=http_replies,
                normalized_requesting_username=normalized_requesting_username,
            )
            self.entity_rag_handler.apply_response_plan_directives(
                reply_plan, receiver, speaker=speaker,
            )
            return None

        self.entity_rag_handler.maybe_promote_escort_approach_to_move(
            reply_plan,
            receiver,
            speaker=speaker,
            player_message=original_player_message,
        )
        pre_speech_movement = {'approach'}
        if reply_plan.get('move_to'):
            pre_speech_movement.add('move_to')
        if reply_plan.get('follow'):
            pre_speech_movement.add('follow')
        self.entity_rag_handler.apply_response_plan_directives(
            reply_plan,
            receiver,
            speaker=speaker,
            only=pre_speech_movement,
            kick_movement=True,
        )

        reply_plan = self.expand_reply_targets_from_message(receiver, reply_plan)
        if local_chat_only and speaker is not None:
            reply_plan['targets'] = [speaker]
        if message_spell_private and speaker is not None:
            reply_plan['targets'] = [speaker]
            reply_plan['volume'] = 'whisper'
        reply_distance_ft = speech_distance_for(mode=reply_plan['volume'])
        if message_spell_private and speaker is not None:
            reply_language = reply_plan.get('language') or player_language or 'common'
            reply_message = reply_plan['message']
            if comprehends_speech(speaker, reply_language, source=receiver, session=self.current_game.game_session):
                rendered_reply = reply_message
            else:
                rendered_reply = gibberish(reply_message, language=reply_language)
            buffer_entry = {
                'source': receiver,
                'message': reply_message,
                'directed_to': [speaker],
                'targets': [speaker],
                'language': reply_language,
                'distance_ft': reply_distance_ft,
                'volume': 'whisper',
                'message_spell': True,
            }
            if message_spell_link_id:
                buffer_entry['message_spell_link_id'] = message_spell_link_id
            receiver.conversation_buffer.append(buffer_entry)
            receiver.memory_buffer.append(dict(buffer_entry))
            speaker.receive_conversation(
                receiver,
                rendered_reply,
                language=reply_language,
                directed_to=[speaker],
            )
            response_conversations = [(speaker, rendered_reply, [speaker])]
        else:
            try:
                response_conversations = receiver.send_conversation(
                    reply_plan['message'],
                    distance_ft=reply_distance_ft,
                    targets=reply_plan['targets'],
                    language=reply_plan['language'],
                    volume=reply_plan['volume'],
                    narrative=reply_plan.get('narrative') or [],
                    segments=reply_plan.get('segments') or [],
                    gallery_image_id=reply_plan.get('gallery_image_id'),
                )
            except TypeError:
                response_conversations = receiver.send_conversation(
                    reply_plan['message'],
                    distance_ft=reply_distance_ft,
                    targets=reply_plan['targets'],
                    language=reply_plan['language'],
                )
        if message_spell_private and speaker is not None:
            recipient_usernames = self.entity_audience_usernames([receiver, speaker], include_dm=True)
        else:
            recipient_usernames = self.conversation_audience_usernames(
                receiver,
                response_conversations,
                targets=reply_plan['targets'],
            )
        visual_whisper_usernames = set()
        if reply_plan['volume'] == 'whisper':
            visual_whisper_usernames = self.conversation_visible_whisper_usernames(
                receiver,
                audible_usernames=recipient_usernames,
                targets=reply_plan['targets'],
            )

        conversation_payload = self.conversation_payload(
            receiver,
            reply_plan['message'],
            targets=reply_plan['targets'],
            volume=reply_plan['volume'],
            distance_ft=reply_distance_ft,
            language=reply_plan['language'],
            visual_only_usernames=visual_whisper_usernames,
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
            gallery_image_id=reply_plan.get('gallery_image_id'),
        )
        continuation_id = self._register_reply_continuation(
            receiver,
            speaker,
            reply_plan,
            local_chat_only=local_chat_only,
        )
        if continuation_id:
            conversation_payload['continuation_id'] = continuation_id
            conversation_payload['continuation_beats_remaining'] = reply_plan.get(
                'continuation_beats_remaining', 0
            )
        if local_chat_only:
            conversation_payload['local_chat_only'] = True
        if message_spell_private:
            conversation_payload['message_spell'] = True
            conversation_payload['channel'] = 'message_spell'
            conversation_payload['local_chat_only'] = True
            if message_spell_link_id:
                conversation_payload['message_spell_link_id'] = message_spell_link_id
        self.attach_skill_check_request(conversation_payload, receiver, reply_plan)
        if not isinstance(receiver, PlayerCharacter):
            conversation_payload['tts_pending'] = True
            conversation_payload['tts_emotion'] = reply_plan.get('tts_emotion')
            conversation_payload['tts_instruct'] = reply_plan.get('tts_instruct')
        else:
            conversation_payload['tts_pending'] = False

        emitted_by_username = self.emit_conversation_to_usernames(
            recipient_usernames | visual_whisper_usernames,
            conversation_payload,
            source_entity=receiver,
        )
        self._append_http_reply_for_requester(
            http_replies,
            normalized_requesting_username,
            emitted_by_username,
            conversation_payload,
            receiver,
        )
        self.logger.info(
            "[Talk] emitted reply from %s to_usernames=%s volume=%s targets=%s message_preview=%r receiver_ms=%.1f",
            receiver.label(),
            sorted(recipient_usernames | visual_whisper_usernames),
            reply_plan['volume'],
            [getattr(t, 'entity_uid', t) for t in (reply_plan.get('targets') or [])],
            (reply_plan['message'] or '')[:160],
            (time.monotonic() - receiver_started) * 1000.0,
        )
        self._persist_conversation_line(
            receiver,
            reply_plan['message'],
            targets=reply_plan['targets'],
            volume=reply_plan['volume'],
            language=reply_plan['language'],
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
        )
        try:
            self.entity_rag_handler.apply_conversation_keywords_from_plan(
                reply_plan,
                receiver,
                speaker=speaker,
                llm_conversation_handler=llm_conversation_handler,
            )
        except Exception as exc:
            self.logger.warning(
                "[Talk] conversation keyword side effects failed for %s: %s",
                receiver.label(),
                exc,
            )
        post_speech_skip = {'approach'}
        if reply_plan.get('move_to'):
            post_speech_skip.add('move_to')
        if reply_plan.get('follow'):
            post_speech_skip.add('follow')
        self.entity_rag_handler.apply_response_plan_directives(
            reply_plan,
            receiver,
            speaker=speaker,
            skip=post_speech_skip,
            player_message=original_player_message,
        )
        try:
            forced_manager = getattr(self.current_game, 'forced_conversation_manager', None)
            if forced_manager is not None:
                forced_manager.maybe_resolve_after_npc_reply(receiver, response, speaker=speaker)
                forced_manager.after_npc_reply(receiver)
        except Exception as exc:
            self.logger.warning(
                "[Talk] forced conversation resolution failed for %s: %s",
                receiver.label(),
                exc,
            )
        try:
            goal_arrival_manager = getattr(self.current_game, 'goal_arrival_manager', None)
            if goal_arrival_manager is not None:
                goal_arrival_manager.maybe_complete_after_npc_reply(receiver, response)
        except Exception as exc:
            self.logger.warning(
                "[Talk] goal arrival resolution failed for %s: %s",
                receiver.label(),
                exc,
            )
        if open_dialog:
            dialog_targets = list(reply_plan.get('targets') or [])
            if speaker is not None and speaker not in dialog_targets:
                dialog_targets.insert(0, speaker)
            self._open_entity_dialog_for_targets(receiver, dialog_targets)
        return {
            'response': response,
            'reply_plan': reply_plan,
            'response_conversations': response_conversations,
        }

    @staticmethod
    def _npc_pronoun_guidance(receiver) -> str:
        props = getattr(receiver, 'properties', None) or {}
        voice = props.get('voice') if isinstance(props.get('voice'), dict) else {}
        gender = str(props.get('gender') or voice.get('gender') or '').strip().lower()
        if gender == 'female':
            return (
                "- In [ASIDE: ...] tags, refer to yourself in third person with she/her pronouns "
                "(never he/him).\n"
            )
        if gender == 'male':
            return (
                "- In [ASIDE: ...] tags, refer to yourself in third person with he/him pronouns "
                "(never she/her).\n"
            )
        return ''

    def conversation_response_prompt(
        self,
        receiver,
        speaker,
        *,
        player_speaker=None,
        summoned: bool = False,
        relay_instruction: bool = False,
        npc_initiated: bool = False,
        npc_chain_banter: bool = False,
        player_message: str = '',
        message_spell_private: bool = False,
    ):
        target_entities = self.entity_rag_handler.get_conversation_targets(receiver, speaker=speaker)
        perceivable_entries = self.entity_rag_handler.collect_perceivable_targets(
            receiver,
            range_ft=90,
            include_objects=False,
        )

        def _languages_for(target, *, understood=False):
            getter_name = 'languages_understood' if understood else 'languages'
            getter = getattr(target, getter_name, None)
            if not callable(getter):
                getter = getattr(target, 'languages', None)
            try:
                langs = getter() or [] if callable(getter) else []
            except Exception:
                langs = []
            return [str(lang).strip() for lang in langs if str(lang).strip()]

        target_handles = []
        seen_handles = set()
        for entry in perceivable_entries[:14]:
            handle = entry.get('mention_handle')
            if not handle or handle in seen_handles:
                continue
            seen_handles.add(handle)
            name = entry.get('name') or 'unknown'
            distance_ft = entry.get('distance_ft', 0)
            target_handles.append(f"@{handle} ({name}; {distance_ft}ft)")
        for target in target_entities:
            handle = mention_handle_for(target)
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            langs = _languages_for(target)
            lang_note = f" speaks: {', '.join(langs)}" if langs else " speaks: unknown"
            target_handles.append(f"@{handle} ({entity_label(target)};{lang_note})")

        handles_text = ', '.join(target_handles) if target_handles else 'speaker'

        receiver_languages = _languages_for(receiver)
        receiver_understood = _languages_for(receiver, understood=True)
        receiver_languages_text = ', '.join(receiver_languages) if receiver_languages else 'common'
        receiver_understood_text = ', '.join(receiver_understood) if receiver_understood else receiver_languages_text

        speaker_languages = _languages_for(speaker) if speaker is not None else []
        if speaker is not None:
            speaker_label = entity_label(speaker)
            if speaker_languages:
                speaker_language_text = (
                    f"The speaker {speaker_label} understands: {', '.join(speaker_languages)}."
                )
            else:
                speaker_language_text = (
                    f"The speaker {speaker_label}'s known languages are unknown to you."
                )
        else:
            speaker_language_text = "No specific speaker is addressing you."

        shared_with_speaker = sorted(
            {lang.lower() for lang in receiver_understood}
            & {lang.lower() for lang in speaker_languages}
        )
        if shared_with_speaker:
            shared_text = ', '.join(shared_with_speaker)
        else:
            shared_text = 'none in common'

        stance_text = self.conversation_attitude_toward_speaker(receiver, speaker)
        pressure_text = self.conversation_pressure_summary(receiver)
        gear_text = format_entity_gear_for_conversation(receiver, self.game_session)
        witnessed_text = self.entity_rag_handler.witnessed_events_summary(receiver)
        memory_text = self.entity_rag_handler.npc_memory_context_summary(receiver)
        nearby_entities = self.entity_rag_handler.get_conversation_targets(receiver, speaker=speaker) or []
        animal_comm_text = self.entity_rag_handler.animal_communication_guidance_for_conversation(
            receiver,
            speaker=speaker,
            nearby_entities=nearby_entities,
        )
        animal_comm_block = f"{animal_comm_text}\n" if animal_comm_text else ''
        landmarks_text = self.entity_rag_handler.map_landmarks_prompt_for_entity(receiver)
        landmarks_block = f"\n{landmarks_text}\n" if landmarks_text else ''
        setting_text = self.entity_rag_handler.campaign_setting_prompt()
        backstory_text = self.entity_rag_handler.npc_backstory_prompt(receiver)
        backstory_block = f"\n{backstory_text}\n" if backstory_text else ''
        setting_block = f"\n{setting_text}\n" if setting_text else ''
        container_serving_text = self.entity_rag_handler.container_serving_guidance_for_conversation(receiver)
        container_serving_block = f"{container_serving_text}\n" if container_serving_text else ''
        usable_items_text = self.entity_rag_handler.usable_items_guidance_for_entity(receiver)
        usable_items_block = f"{usable_items_text}\n" if usable_items_text else ''
        inventory_containers_text = self.entity_rag_handler.inventory_containers_guidance_for_entity(receiver)
        inventory_containers_block = f"{inventory_containers_text}\n" if inventory_containers_text else ''
        interactables_text = self.entity_rag_handler.interactable_objects_guidance_for_entity(receiver)
        interactables_block = f"{interactables_text}\n" if interactables_text else ''
        perceivable_targets_text = self.entity_rag_handler.perceivable_targets_guidance_for_entity(receiver, range_ft=90)
        perceivable_targets_block = f"{perceivable_targets_text}\n" if perceivable_targets_text else ''
        summon_block = ''
        if summoned:
            summon_block = (
                "- The speaker summoned you to come closer. The server may already have moved you toward them; "
                "if you are still out of earshot, use [APPROACH: target=speaker, distance=5] before replying.\n"
            )
        relay_block = ''
        if relay_instruction:
            relay_block = (
                "- The speaker asked you to deliver a message to someone else. Move to them if needed "
                "([APPROACH: target=@handle, distance=5]), then speak to that person with "
                "[TO: @their_handle] — not [TO: speaker]. Hail them by name in the spoken line.\n"
            )
        npc_chain_block = ''
        if npc_chain_banter:
            npc_chain_block = (
                "- This is an overheard NPC-to-NPC exchange in an ongoing conversation. "
                "Reply naturally while you are engaged; use [NO_RESPONSE] when you have "
                "nothing more to add or the topic is closed.\n"
                "- Use [VOLUME: normal] for ordinary room conversation unless you are "
                "deliberately whispering a secret.\n"
            )
        initiative_block = ''
        if npc_initiated:
            initiative_block = (
                "- You are initiating this exchange. The latest system message describes something "
                "that just happened; the other party has not spoken to you through conversation yet.\n"
                "- If you choose to speak, address them with [TO: speaker] or their @handle. "
                "Your line will appear in the local chat panel directed at them, not in a separate "
                "dialog window. Use [NO_RESPONSE] if you stay completely silent.\n"
            )
        message_spell_block = ''
        if message_spell_private:
            speaker_label = entity_label(speaker) if speaker is not None else 'the caster'
            message_spell_block = (
                "\nMessage spell channel (NOT normal speech):\n"
                "- The speaker cast the Message transmutation cantrip at you. Their words arrive as a "
                "magical whisper only you can hear — not audible speech in the room.\n"
                "- Only that caster will hear your reply. Bystanders, allies, and enemies nearby hear "
                "nothing from either side.\n"
                f"- Reply to {speaker_label} with [TO: speaker] or their @handle and [VOLUME: whisper]. "
                "Keep replies to one short whispered beat.\n"
                "- Do not shout, address the room, summon help aloud, or use movement/hostility tags "
                "([APPROACH], [GO_HOSTILE], etc.) as if this were a normal conversation.\n"
                "- You may react in character to a voice in your mind (surprise, recognition of the "
                "spell, suspicion, etc.).\n"
            )
        pronoun_block = self._npc_pronoun_guidance(receiver)
        gallery_block = self.entity_rag_handler.npc_image_gallery_guidance_for_conversation(receiver)
        return (
            "\n\nConversation response rules:\n"
            f"{message_spell_block}"
            f"{npc_chain_block}"
            f"{initiative_block}"
            f"{gallery_block}"
            "- You may choose not to speak. If you do not want to respond, output exactly [NO_RESPONSE].\n"
            "- If you do speak, stay in character and return only optional control tags plus the spoken line.\n"
            "- Keep each reply to one conversational beat: a short spoken line (or two), optional [ASIDE: ...], then stop and wait for the player to answer. Do not deliver escalating multi-paragraph monologues in a single reply.\n"
            "- The conversation is ongoing: the speaker is still present and talking to you unless their latest message explicitly says they are leaving, walking away, or ending the exchange. Do not assume they departed, are departing, or turned away.\n"
            "- Do not narrate the speaker's physical actions in [ASIDE: ...] unless those actions appear in their latest message or the conversation history. Never describe their departure while they are still actively engaged with you.\n"
            "- Do not dismiss the speaker or tell them to move along while they are mid-conversation asking questions.\n"
            f"{pronoun_block}"
            "- Keep spoken dialogue as speech only. Do not mix narration into the spoken line.\n"
            "- The speaker may also perform non-verbal physical actions (for example touching your hand) that are delivered separately from speech. React to those actions in [ASIDE: ...] and/or in your spoken reply; do not ignore visible actions.\n"
            "- If you want to include third-person storytelling, stage direction, or internal/emotive scene description, put it in one or more tags like [ASIDE: ...] after the spoken line. Markdown-style *italics* are also treated as non-spoken stage direction.\n"
            "- Any [ASIDE: ...] content must be third person only. Do not use first-person narration in asides.\n"
            "- Do not reveal private mental judgments a listener could not directly observe (for example, trustworthiness verdicts). Ask for a social check with [REQUEST_CHECK: skill=persuasion, target=speaker] or [REQUEST_CHECK: skill=intimidation, target=speaker] instead.\n"
            "- You may explicitly refuse to answer, stonewall, deflect, demand proof, threaten the speaker, or tell them to leave if that fits your background, alignment, attitude, current danger, injuries, fear, active effects, duties, or goals. If you refuse out loud, say so in character; use [NO_RESPONSE] only when you stay completely silent.\n"
            "- If the latest line was directed at you and is a greeting, question, or new topic, answer in character. Do not use [NO_RESPONSE] for hello, introductions, or questions aimed at you.\n"
            "- Use [NO_RESPONSE] only for overheard side conversations, when someone else was clearly addressed, when another speaker already fully answered, or for passive acknowledgements (for example: ok, thanks, got it) that need no reply.\n"
            "- Do not repeat the same warning or biography if you already said it and nothing materially changed. Prefer a brief new reaction or [NO_RESPONSE] instead.\n"
            "- You may direct your speech with [TO: speaker], [TO: @handle], [TO: @handle1, @handle2], or [TO: all].\n"
            "- You may choose loudness with [VOLUME: whisper], [VOLUME: normal], or [VOLUME: shout]. If omitted, the server will choose the quietest volume that reaches your chosen listeners.\n"
            "- You may set spoken delivery for text-to-speech with [EMOTION: fearful] (short mood token) and/or [TTS: trembling, barely holding back tears] (free-form acting notes). Prefer these when the line has strong feeling; they are stripped from the spoken text.\n"
            "- Useful [EMOTION: ...] tokens: calm, whisper, fearful, angry, shouting, sad, cheerful, nervous, disgusted, surprised. Use [TTS: ...] for richer delivery (pace, breathiness, urgency) that a single emotion word cannot capture.\n"
            "- You may speak a different language with [in <language>]. Pick a language your intended listeners actually understand. If a listener does not share your current language, switch to a common tongue you both know (typically [in common]) so they can reply, unless you are deliberately keeping them out of the conversation.\n"
            "- You may move toward someone or something with [APPROACH: target=@handle, distance=5]. "
            "Use distance=5 when joining a person (not 10 or 15). The server continues with [MOVE] until you arrive.\n"
            "- When agreeing to show, lead, or escort someone to a guest room, suite, cellar, or other landmark, "
            "use [MOVE: target=<landmark_id>, distance=5] — not [APPROACH: target=speaker]. "
            "[APPROACH] only closes conversational distance; it does not walk you to a destination.\n"
            "- You may keep moving toward a target with [MOVE: target=@handle, distance=5], [MOVE: target=<landmark_id>, distance=5], [MOVE: target=Front Door, distance=10], or [MOVE: x=5, y=10]. Movement continues on each 6-second tick until you arrive or emit [STOP_MOVE].\n"
            "- You may trail someone with [FOLLOW: target=@handle, distance=5]. "
            "The server keeps following until you emit [STOP_FOLLOW] or the target is lost — "
            "you move whenever they move, including upstairs or through doors.\n"
            "- You may use an object with [INTERACT: target=<name or @handle>, action=<interaction>]. "
            "Works on doors, containers, levers, and other map objects the same way as for player characters.\n"
            "- Use [TARGETS] to refresh the list of everyone and objects you can perceive with @handles, "
            "or [LOCATE: target=<name>] to resolve one target before chaining "
            "[APPROACH]/[USE_ITEM]/[INTERACT]/[OFFER_ITEM] tags.\n"
            "- You may use a consumable or charged item from your inventory with "
            "[USE_ITEM: item=<item_slug>, target=self|@handle]. Healing potions may target yourself or an ally; "
            "spell scrolls need a valid target for the spell (often self or an enemy). Items in your backpack "
            "are pulled automatically when you use them.\n"
            "- You may organize your backpack with "
            "[RETRIEVE: item=<slug>, target=backpack, qty=1] and "
            "[STORE: item=<slug>, target=backpack, qty=1] (or [STOW: ...]).\n"
            "- You may offer an item with [OFFER_ITEM: item=<item_slug>, target=speaker|@handle]. "
            "This creates an Accept Item Yes/No prompt for the target. You may offer from your inventory or from an open container within reach (for example the bar). "
            "Never use [OFFER_ITEM] in the same reply where you list multiple options and ask which they want — wait for their next message. "
            "Only offer after they clearly name the specific item they want.\n"
            "- You may pick up a ground item within reach with [PICKUP: item=<item_slug>] or [PICKUP: item=<item_slug>, qty=2].\n"
            "- You may inspect container stock with [LIST_CONTAINER: target=@handle] or [LIST_CONTAINER] for all nearby containers.\n"
            "- You may read staff-only object markings with [ANNOTATIONS: target=@handle] or [ANNOTATIONS] for nearby objects. These annotations are invisible to player characters.\n"
            "- You may take items from an open container within reach with [RETRIEVE: item=<item_slug>, target=@container, qty=1]. If the container is not adjacent, use [APPROACH: target=@container, distance=5] in the same reply before [RETRIEVE].\n"
            "- You may deposit items into an open container with [STORE: item=<item_slug>, target=@container, qty=2].\n"
            "- You may open a full item trade with [TRADE: target=speaker|@handle]. Human-controlled targets see the transfer dialog; others are adjudicated by the DM.\n"
            "- You may ask someone to hand you an item with [REQUEST_ITEM: item=<item_slug>, target=speaker|@handle]. Human-controlled targets see the transfer dialog.\n"
            "- When the speaker offers to give you an item, hands something over, or otherwise signals a gift, use [ACCEPT_GIFT: target=speaker|@handle] (optional item=<item_slug> hint) to open their give-item transfer dialog. React in character first if you like.\n"
            "- If you choose violence or to attack player characters, include [GO_HOSTILE] in your response (with or without a spoken line). The server will switch you to a hostile faction and may start combat. Only use this when your character would genuinely fight.\n"
            f"{self._party_membership_guidance(receiver, speaker)}\n"
            f"{container_serving_block}"
            f"{usable_items_block}"
            f"{inventory_containers_block}"
            f"{perceivable_targets_block}"
            f"{interactables_block}"
            f"{self.entity_rag_handler.offer_item_guidance_for_conversation(receiver, speaker, player_speaker, player_message=player_message)}\n"
            f"{animal_comm_block}"
            f"{setting_block}"
            f"{backstory_block}"
            f"{landmarks_block}"
            f"{summon_block}"
            f"{relay_block}"
            f"{witnessed_text}"
            f"{memory_text}"
            "- You may store a lasting memory with [REMEMBER: title=..., summary=..., body=...] or [REMEMBER: short note].\n"
            "- You may retrieve memory details with [RECALL: id=<memory_id>] or [RECALL: query=keyword].\n"
            "- You may privately assess whether someone seems truthful with [INSIGHT: target=speaker] or [INSIGHT: target=@handle]. The server will roll insight, use DM-only context, and regenerate your reply with the result.\n"
            "- You may ask someone to make a social check with [REQUEST_CHECK: skill=persuasion, target=speaker] or [REQUEST_CHECK: skill=intimidation, target=@handle] (optional dc=##). If you omit dc, the server assigns one from your backstory or the situation.\n"
            "- You may create a persistent short-term autonomous task with [SET_GOAL: short description].\n"
            "- During autonomous follow-up you may end that task with [GOAL_COMPLETE] or [GOAL_GIVE_UP].\n"
            "- Only describe wielding, drawing, brandishing, or threatening with weapons and items listed under your gear below. Do not invent equipment.\n"
            f"- Current stance toward the speaker: {stance_text}.\n"
            f"- Current pressures and circumstances: {pressure_text}.\n"
            f"- Languages you ({entity_label(receiver)}) speak: {receiver_languages_text}.\n"
            f"- Languages you ({entity_label(receiver)}) understand: {receiver_understood_text}.\n"
            f"- {speaker_language_text}\n"
            f"- Languages you share with the speaker: {shared_text}.\n"
            f"- Nearby handles you can target right now: {handles_text}.\n"
            "- Chain targeting: [LOCATE: target=injured guard] then [USE_ITEM: item=healing_potion, target=@<handle from locate>].\n"
            f"\nYour gear and inventory (authoritative; do not contradict):\n{gear_text}\n"
        )

    def conversation_recipient_usernames(self, entity, include_requester=True):
        recipients = set(self.entity_owners(entity))

        if include_requester:
            requester = session.get('username')
            if requester:
                recipients.add(requester)

        for login in self.logins:
            roles = login.get('role') or []
            if 'dm' in roles and login.get('name'):
                recipients.add(login['name'].lower())

        return recipients

    def effective_talk_volume(self, message, requested_volume=None, requested_distance_ft=None):
        speech_source = parse_player_conversation_input(message).get('spoken') or message
        normalized_volume = normalize_speech_mode(mode=requested_volume, distance_ft=requested_distance_ft)
        if normalized_volume == 'normal' and '!' in (speech_source or ''):
            normalized_volume = 'shout'
        return normalized_volume

    def _dialog_history_between(self, entity, entity_pov):
        """Return JRPG dialog history between an NPC and the active POV character."""
        if entity is None or entity_pov is None:
            return []

        merged = list(entity.conversation_history(entity_pov))
        seen = {
            (
                entry.get('type'),
                entry.get('source'),
                entry.get('message'),
                tuple(entry.get('narrative') or []),
            )
            for entry in merged
        }
        for entry in entity_pov.conversation_history(entity):
            key = (
                entry.get('type'),
                entry.get('source'),
                entry.get('message'),
                tuple(entry.get('narrative') or []),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
        return merged

    def dialog_history_response(self, entity_id, entity_pov_id=None):
        if not entity_id:
            return {'error': 'Entity ID is required'}, 400

        entity = self.current_game.get_entity_by_uid(entity_id)
        if not entity:
            return {'error': 'Entity not found'}, 404

        if entity_pov_id:
            entity_pov = self.current_game.get_entity_by_uid(entity_pov_id)
            history = self._dialog_history_between(entity, entity_pov)
            for entry in history:
                if entry.get('type') != 'entity':
                    continue
                gallery_image_id = entry.get('gallery_image_id')
                if gallery_image_id and hasattr(entity, 'resolve_gallery_image'):
                    entry['gallery_image'] = entity.resolve_gallery_image(gallery_image_id)
                recorded = self.get_recorded_insight_check(
                    entity_pov,
                    entity.entity_uid,
                    entry.get('message') or '',
                )
                if not recorded:
                    continue
                entry['message_key'] = recorded.get('message_key') or insight_check_message_key(
                    entity.entity_uid,
                    entry.get('message') or '',
                )
                entry['target_id'] = entity.entity_uid
                entry['insight_check'] = {
                    key: recorded.get(key)
                    for key in ('target', 'target_uid', 'roll_total', 'assessment', 'reason', 'message_key')
                    if recorded.get(key) is not None
                }
                entry['insight_checked'] = True
            return {
                'success': True,
                'history': history,
                'entity_id': entity_id,
                'entity_name': entity.label(),
                'entity_pov': entity_pov.entity_uid,
            }, 200

        return {
            'success': True,
            'history': [],
            'entity_id': entity_id,
            'entity_name': entity.label(),
            'entity_pov': False,
        }, 200

    # ── Journal chat commands ──────────────────────────────────────────
    def _emit_dm_private_message(self, speaker, message_text, extra=None):
        """Send a private DM-voiced message to the speaker (and DMs)."""
        payload = {
            'entity_id': None,
            'speaker_name': 'Dungeon Master',
            'message': message_text,
            'narrative': [],
            'language': 'common',
            'targets': [getattr(speaker, 'entity_uid', None)],
            'target_names': [entity_label(speaker)] if speaker is not None else [],
            'mentioned_targets': [],
            'mentioned_handles': [],
            'volume': 'normal',
            'distance_ft': 0,
            'visual_only_usernames': [],
            'system': True,
        }
        if isinstance(extra, dict):
            payload.update(extra)
        try:
            usernames = self.entity_audience_usernames([speaker], include_dm=True)
            self.emit_conversation_to_usernames(usernames, payload, source_entity=None)
        except Exception:
            pass
        return payload

    def _handle_journal_chat_command(self, speaker, raw_message):
        """Parse and execute a /journal slash command from local chat.

        Returns ``(dict, 200)`` so callers can short-circuit ``talk``.
        """
        # Strip leading "/journal" then read sub-command + remainder.
        body = raw_message[len('/journal'):].strip()
        if not body:
            sub = 'help'
            arg = ''
        else:
            parts = body.split(None, 1)
            sub = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ''

        if not hasattr(speaker, 'add_journal_entry'):
            self._emit_dm_private_message(
                speaker,
                "Only player characters can keep a journal.",
            )
            return {'success': True, 'journal': 'unsupported'}, 200

        if sub in ('add', 'note', 'log'):
            if not arg:
                self._emit_dm_private_message(
                    speaker,
                    "Usage: /journal add <your note>",
                )
                return {'success': True, 'journal': 'usage'}, 200
            entry = speaker.add_journal_entry(arg, kind='chat', source='chat')
            if entry:
                db = getattr(self.current_game, 'campaign_log_db', None)
                if db is not None:
                    try:
                        db.append_journal_entry(speaker.entity_uid, entry)
                    except Exception as exc:
                        self.logger.debug(f"Campaign journal log skipped: {exc}")
            self._emit_dm_private_message(
                speaker,
                f"Journal entry recorded ({len(speaker.journal)} total).",
                extra={'journal_action': 'added', 'journal_entry': entry},
            )
            return {'success': True, 'journal': 'added', 'entry': entry}, 200

        if sub in ('search', 'find'):
            if not arg:
                self._emit_dm_private_message(
                    speaker,
                    "Usage: /journal search <query>",
                )
                return {'success': True, 'journal': 'usage'}, 200
            results = speaker.search_journal(query=arg)
            if not results:
                self._emit_dm_private_message(
                    speaker,
                    f"No journal entries match \"{arg}\".",
                )
                return {'success': True, 'journal': 'empty'}, 200
            recent = results[-5:]
            preview_lines = [
                f"• {self._format_journal_preview(e)}" for e in reversed(recent)
            ]
            preview = '\n'.join(preview_lines)
            extra_count = max(0, len(results) - len(recent))
            suffix = f"\n(+{extra_count} more)" if extra_count else ''
            self._emit_dm_private_message(
                speaker,
                f"Found {len(results)} journal entries matching \"{arg}\":\n{preview}{suffix}",
                extra={'journal_action': 'search', 'journal_results': results},
            )
            return {'success': True, 'journal': 'search', 'results': results}, 200

        if sub in ('list', 'recent', 'show'):
            try:
                limit = int(arg) if arg else 5
            except ValueError:
                limit = 5
            limit = max(1, min(limit, 25))
            results = speaker.search_journal(limit=limit)
            if not results:
                self._emit_dm_private_message(
                    speaker,
                    "Your journal is empty.",
                )
                return {'success': True, 'journal': 'empty'}, 200
            preview = '\n'.join(
                f"• {self._format_journal_preview(e)}" for e in reversed(results)
            )
            self._emit_dm_private_message(
                speaker,
                f"Last {len(results)} journal entries:\n{preview}",
                extra={'journal_action': 'list', 'journal_results': results},
            )
            return {'success': True, 'journal': 'list', 'results': results}, 200

        # Help / unknown
        self._emit_dm_private_message(
            speaker,
            "Journal commands: /journal add <text>, /journal search <query>, /journal list [N]",
        )
        return {'success': True, 'journal': 'help'}, 200

    @staticmethod
    def _format_journal_preview(entry):
        text = (entry.get('text') or '').strip().replace('\n', ' ')
        if len(text) > 140:
            text = text[:137] + '...'
        ts = (entry.get('ts') or '')[:19].replace('T', ' ')
        kind = entry.get('kind') or 'note'
        return f"[{kind} {ts}] {text}"

    # ── Journal chat commands ──────────────────────────────────────────
    def _emit_dm_private_message(self, speaker, message_text, extra=None):
        """Send a private DM-voiced message to the speaker (and DMs)."""
        payload = {
            'entity_id': None,
            'speaker_name': 'Dungeon Master',
            'message': message_text,
            'narrative': [],
            'language': 'common',
            'targets': [getattr(speaker, 'entity_uid', None)],
            'target_names': [entity_label(speaker)] if speaker is not None else [],
            'mentioned_targets': [],
            'mentioned_handles': [],
            'volume': 'normal',
            'distance_ft': 0,
            'visual_only_usernames': [],
            'system': True,
        }
        if isinstance(extra, dict):
            payload.update(extra)
        try:
            usernames = self.entity_audience_usernames([speaker], include_dm=True)
            self.emit_conversation_to_usernames(usernames, payload, source_entity=None)
        except Exception:
            pass
        return payload

    def _handle_journal_chat_command(self, speaker, raw_message):
        """Parse and execute a /journal slash command from local chat.

        Returns ``(dict, 200)`` so callers can short-circuit ``talk``.
        """
        # Strip leading "/journal" then read sub-command + remainder.
        body = raw_message[len('/journal'):].strip()
        if not body:
            sub = 'help'
            arg = ''
        else:
            parts = body.split(None, 1)
            sub = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ''

        if not hasattr(speaker, 'add_journal_entry'):
            self._emit_dm_private_message(
                speaker,
                "Only player characters can keep a journal.",
            )
            return {'success': True, 'journal': 'unsupported'}, 200

        if sub in ('add', 'note', 'log'):
            if not arg:
                self._emit_dm_private_message(
                    speaker,
                    "Usage: /journal add <your note>",
                )
                return {'success': True, 'journal': 'usage'}, 200
            entry = speaker.add_journal_entry(arg, kind='chat', source='chat')
            if entry:
                db = getattr(self.current_game, 'campaign_log_db', None)
                if db is not None:
                    try:
                        db.append_journal_entry(speaker.entity_uid, entry)
                    except Exception as exc:
                        self.logger.debug(f"Campaign journal log skipped: {exc}")
            self._emit_dm_private_message(
                speaker,
                f"Journal entry recorded ({len(speaker.journal)} total).",
                extra={'journal_action': 'added', 'journal_entry': entry},
            )
            return {'success': True, 'journal': 'added', 'entry': entry}, 200

        if sub in ('search', 'find'):
            if not arg:
                self._emit_dm_private_message(
                    speaker,
                    "Usage: /journal search <query>",
                )
                return {'success': True, 'journal': 'usage'}, 200
            results = speaker.search_journal(query=arg)
            if not results:
                self._emit_dm_private_message(
                    speaker,
                    f"No journal entries match \"{arg}\".",
                )
                return {'success': True, 'journal': 'empty'}, 200
            recent = results[-5:]
            preview_lines = [
                f"• {self._format_journal_preview(e)}" for e in reversed(recent)
            ]
            preview = '\n'.join(preview_lines)
            extra_count = max(0, len(results) - len(recent))
            suffix = f"\n(+{extra_count} more)" if extra_count else ''
            self._emit_dm_private_message(
                speaker,
                f"Found {len(results)} journal entries matching \"{arg}\":\n{preview}{suffix}",
                extra={'journal_action': 'search', 'journal_results': results},
            )
            return {'success': True, 'journal': 'search', 'results': results}, 200

        if sub in ('list', 'recent', 'show'):
            try:
                limit = int(arg) if arg else 5
            except ValueError:
                limit = 5
            limit = max(1, min(limit, 25))
            results = speaker.search_journal(limit=limit)
            if not results:
                self._emit_dm_private_message(
                    speaker,
                    "Your journal is empty.",
                )
                return {'success': True, 'journal': 'empty'}, 200
            preview = '\n'.join(
                f"• {self._format_journal_preview(e)}" for e in reversed(results)
            )
            self._emit_dm_private_message(
                speaker,
                f"Last {len(results)} journal entries:\n{preview}",
                extra={'journal_action': 'list', 'journal_results': results},
            )
            return {'success': True, 'journal': 'list', 'results': results}, 200

        # Help / unknown
        self._emit_dm_private_message(
            speaker,
            "Journal commands: /journal add <text>, /journal search <query>, /journal list [N]",
        )
        return {'success': True, 'journal': 'help'}, 200

    @staticmethod
    def _format_journal_preview(entry):
        text = (entry.get('text') or '').strip().replace('\n', ' ')
        if len(text) > 140:
            text = text[:137] + '...'
        ts = (entry.get('ts') or '')[:19].replace('T', ' ')
        kind = entry.get('kind') or 'note'
        return f"[{kind} {ts}] {text}"

    def _emit_player_insight_payload(self, speaker, message_text, target=None,
                                     roll_total=None, dc=None, assessment=None,
                                     reason=None, vague=False, clarification=None,
                                     purpose=None, message_key=None):
        """Send a private insight-check result/clarification to the player + DM only."""
        # entity_id must be non-empty — the conversation event handler drops
        # payloads with a falsy entity_id. Prefer the read target; fall back to
        # the observing player so clarifications (missing target) still render.
        entity_id = (
            getattr(target, 'entity_uid', None)
            or getattr(speaker, 'entity_uid', None)
        )
        payload = {
            'entity_id': entity_id,
            'speaker_name': 'Dungeon Master',
            'message': message_text,
            'narrative': [],
            'language': 'common',
            'targets': [getattr(speaker, 'entity_uid', None)],
            'target_names': [entity_label(speaker)] if speaker is not None else [],
            'mentioned_targets': [],
            'mentioned_handles': [],
            'volume': 'normal',
            'distance_ft': 0,
            'visual_only_usernames': [],
            'system': True,
            'insight_check': {
                'observer': entity_label(speaker) if speaker is not None else None,
                'target': entity_label(target) if target is not None else None,
                'roll_total': roll_total,
                'dc': dc,
                'assessment': assessment,
                'reason': reason,
                'vague': bool(vague),
                'clarification': clarification,
                'purpose': purpose,
                'message_key': message_key,
            },
        }
        usernames = self.entity_audience_usernames([speaker], include_dm=True)
        self.emit_conversation_to_usernames(usernames, payload, source_entity=None)
        return payload

    def _resolve_insight_target(self, speaker, target_spec, explicit_targets, mentioned_targets):
        """Pick the NPC the player wants to read."""
        # 1) explicit/mentioned NPC targets (NPCs only, ignore the speaker)
        for candidate in list(mentioned_targets or []) + list(explicit_targets or []):
            if candidate is None or candidate == speaker:
                continue
            if hasattr(candidate, 'is_npc') and candidate.is_npc():
                return candidate

        # 2) explicit textual target via "insight check on <name>"
        if target_spec:
            rag_handler = self.entity_rag_handler
            if rag_handler is not None and hasattr(rag_handler, 'resolve_named_target'):
                try:
                    candidate = rag_handler.resolve_named_target(speaker, target_spec, speaker=speaker)
                except Exception:
                    candidate = None
                if candidate is not None and candidate != speaker and getattr(candidate, 'is_npc', lambda: False)():
                    return candidate

        # 3) fall back to the most recent NPC that spoke to the player
        memory = list(getattr(speaker, 'memory_buffer', []) or [])
        for entry in reversed(memory):
            source = entry.get('source') if isinstance(entry, dict) else None
            if source is None or source == speaker:
                continue
            if getattr(source, 'is_npc', lambda: False)():
                return source

        return None

    def _latest_npc_statement(self, speaker, target):
        """Return the most recent statement made by ``target`` to ``speaker``."""
        memory = list(getattr(speaker, 'memory_buffer', []) or [])
        for entry in reversed(memory):
            if not isinstance(entry, dict):
                continue
            source = entry.get('source')
            if source is target:
                text = entry.get('message') or ''
                if str(text).strip():
                    return str(text).strip()
        return ''

    @staticmethod
    def _insight_checks_store(speaker):
        if not hasattr(speaker, 'properties') or speaker.properties is None:
            return {}
        store = speaker.properties.setdefault('insight_checks', {})
        if not isinstance(store, dict):
            store = {}
            speaker.properties['insight_checks'] = store
        return store

    def get_recorded_insight_check(self, speaker, target_uid, statement):
        key = insight_check_message_key(target_uid, statement)
        recorded = self._insight_checks_store(speaker).get(key)
        return dict(recorded) if isinstance(recorded, dict) else None

    def record_insight_check(self, speaker, target_uid, statement, result):
        key = insight_check_message_key(target_uid, statement)
        payload = dict(result or {})
        payload['message_key'] = key
        payload['statement'] = str(statement or '').strip()
        self._insight_checks_store(speaker)[key] = payload
        return payload

    def _execute_player_insight_check(self, speaker, target, statement_for_check,
                                      *, purpose=None, message_key=None):
        description = (
            f"Insight check on {entity_label(target)}"
            + (f" about: {purpose}" if purpose else '')
        )

        try:
            roll = speaker.insight_check(description=description)
        except Exception as exc:
            self.logger.error(f"Insight check failed for {entity_label(speaker)}: {exc}")
            self._emit_player_insight_payload(
                speaker,
                "Your insight check could not be completed.",
                target=target,
                vague=True,
                clarification='roll_failed',
                purpose=purpose,
            )
            return {'success': False, 'error': 'insight_roll_failed'}, 500

        roll_total = None
        try:
            roll_total = roll.result()
        except Exception:
            roll_total = None

        rag_handler = self.entity_rag_handler
        llm_conversation_handler = self.llm_conversation_handler
        assessment = 'uncertain'
        reason = 'There is not enough reliable evidence to be sure.'
        if rag_handler is not None and llm_conversation_handler is not None \
                and hasattr(rag_handler, '_evaluate_insight_assessment'):
            try:
                verdict = rag_handler._evaluate_insight_assessment(
                    observer=speaker,
                    target=target,
                    statement=statement_for_check,
                    roll=roll,
                    llm_conversation_handler=llm_conversation_handler,
                )
            except Exception as exc:
                self.logger.error(f"Insight adjudication failed: {exc}")
                verdict = None
            if isinstance(verdict, dict):
                assessment = verdict.get('assessment', assessment)
                reason = verdict.get('reason', reason)

        try:
            roll_repr = str(roll) if roll is not None else ''
        except Exception:
            roll_repr = ''
        if roll_repr and roll_total is not None:
            roll_text = f"Insight check ({roll_repr}) = {roll_total}"
        elif roll_total is not None:
            roll_text = f"Insight check: {roll_total}"
        else:
            roll_text = "Insight check"
        message_text = f"{roll_text} on {entity_label(target)}."

        insight_result = {
            'target': entity_label(target),
            'target_uid': getattr(target, 'entity_uid', None),
            'roll_total': roll_total,
            'assessment': assessment,
            'reason': reason,
            'message_key': message_key or insight_check_message_key(
                getattr(target, 'entity_uid', None),
                statement_for_check,
            ),
        }
        self.record_insight_check(
            speaker,
            getattr(target, 'entity_uid', None),
            statement_for_check,
            insight_result,
        )

        insight_payload = dict(insight_result)
        insight_payload['purpose'] = purpose
        self._emit_player_insight_payload(
            speaker,
            message_text,
            target=target,
            roll_total=roll_total,
            assessment=assessment,
            reason=reason,
            purpose=purpose,
            message_key=insight_result.get('message_key'),
        )

        if rag_handler is not None and hasattr(rag_handler, '_log_social_check'):
            try:
                roll_breakdown = ''
                try:
                    roll_str = str(roll) if roll is not None else ''
                except Exception:
                    roll_str = ''
                if roll_str and roll_total is not None:
                    roll_breakdown = f"{roll_str} = {roll_total}"
                elif roll_total is not None:
                    roll_breakdown = f"total {roll_total}"
                rag_handler._log_social_check(
                    f"{entity_label(speaker)} attempts an Insight check on "
                    f"{entity_label(target)} ({roll_breakdown}): {assessment}.",
                    entities=[speaker, target],
                )
            except Exception:
                pass

        return {'success': True, 'insight': insight_result}, 200

    def handle_player_insight_button_request(self, speaker, payload):
        """Roll Insight against a specific NPC line from the JRPG dialog UI."""
        if not callable(getattr(speaker, 'insight_check', None)):
            return {'success': False, 'error': 'insight_requires_player_character'}, 400

        target_id = (payload or {}).get('target_id')
        statement = str((payload or {}).get('statement') or '').strip()
        if not target_id or not statement:
            return {'success': False, 'error': 'missing_target_or_statement'}, 400

        target = self.current_game.get_entity_by_uid(target_id)
        if target is None or not getattr(target, 'is_npc', lambda: False)():
            return {'success': False, 'error': 'invalid_target'}, 400

        existing = self.get_recorded_insight_check(speaker, target_id, statement)
        if existing:
            return {
                'success': True,
                'insight': existing,
                'already_checked': True,
            }, 200

        message_key = insight_check_message_key(target_id, statement)
        return self._execute_player_insight_check(
            speaker,
            target,
            statement,
            message_key=message_key,
        )

    def _skill_check_roll_store(self, speaker):
        store = speaker.properties.setdefault('skill_check_rolls', {})
        if not isinstance(store, dict):
            store = {}
            speaker.properties['skill_check_rolls'] = store
        return store

    def get_recorded_skill_check_roll(self, speaker, reply_id):
        key = skill_check_reply_key(reply_id)
        if not key:
            return None
        recorded = self._skill_check_roll_store(speaker).get(key)
        return dict(recorded) if isinstance(recorded, dict) else None

    def record_skill_check_roll(self, speaker, reply_id, result):
        key = skill_check_reply_key(reply_id)
        if not key:
            return
        payload = dict(result or {})
        payload['reply_id'] = reply_id
        self._skill_check_roll_store(speaker)[key] = payload

    def handle_player_skill_check_button_request(
        self,
        speaker,
        payload,
        *,
        conversation_system_prompt,
        requesting_username=None,
    ):
        """Roll a requested skill check from JRPG dialog and let the NPC react."""
        requester_uid = (payload or {}).get('requester_uid')
        skill = str((payload or {}).get('skill') or '').strip().lower()
        reply_id = (payload or {}).get('reply_id')
        dc = (payload or {}).get('dc')

        if not requester_uid or not skill:
            return {'success': False, 'error': 'missing_requester_or_skill'}, 400
        if skill not in DIALOG_SKILL_CHECK_SKILLS:
            return {'success': False, 'error': 'unsupported_skill'}, 400

        requester = self.current_game.get_entity_by_uid(requester_uid)
        if requester is None or not getattr(requester, 'is_npc', lambda: False)():
            return {'success': False, 'error': 'invalid_requester'}, 400

        if reply_id:
            existing = self.get_recorded_skill_check_roll(speaker, reply_id)
            if existing:
                return {'success': True, 'skill_check': existing, 'already_checked': True}, 200

        roll_fn = getattr(speaker, f'{skill}_check', None)
        if not callable(roll_fn):
            return {'success': False, 'error': 'skill_not_available'}, 400

        skill_label = skill.replace('_', ' ').title()
        description = f'{skill_label} check (requested by {entity_label(requester)})'
        try:
            roll = roll_fn(description=description)
        except Exception as exc:
            self.logger.error(
                "Skill check failed for %s (%s): %s",
                entity_label(speaker),
                skill,
                exc,
            )
            return {'success': False, 'error': 'skill_roll_failed'}, 500

        roll_total = None
        try:
            roll_total = roll.result()
        except Exception:
            roll_total = None

        try:
            dc_value = int(dc) if dc is not None else None
        except (TypeError, ValueError):
            dc_value = None
        if dc_value is None:
            resolved_check = {'skill': skill, 'target': speaker}
            self.entity_rag_handler.ensure_request_check_dc(
                requester,
                resolved_check,
                speaker=speaker,
            )
            dc_value = resolved_check.get('dc')

        success = None
        if dc_value is not None and roll_total is not None:
            success = roll_total >= dc_value

        skill_result = {
            'skill': skill,
            'skill_label': skill_label,
            'roll_total': roll_total,
            'requester_uid': requester_uid,
            'requester_name': entity_label(requester),
            'actor_uid': getattr(speaker, 'entity_uid', None),
            'actor_name': entity_label(speaker),
            'dc': dc_value,
            'success': success,
            'reply_id': reply_id,
        }
        if reply_id:
            self.record_skill_check_roll(speaker, reply_id, skill_result)

        rag_handler = self.entity_rag_handler
        if rag_handler is not None and hasattr(rag_handler, '_log_social_check'):
            try:
                roll_breakdown = ''
                try:
                    roll_str = str(roll) if roll is not None else ''
                except Exception:
                    roll_str = ''
                if roll_str and roll_total is not None:
                    roll_breakdown = f"{roll_str} = {roll_total}"
                elif roll_total is not None:
                    roll_breakdown = f"total {roll_total}"
                outcome = ''
                if dc_value is not None and success is not None:
                    outcome = 'success' if success else 'failure'
                rag_handler._log_social_check(
                    f"{entity_label(speaker)} rolled {skill_label} ({roll_breakdown})"
                    + (f" vs DC {dc_value} ({outcome})" if outcome else '.'),
                    entities=[speaker, requester],
                )
            except Exception:
                pass

        llm_conversation_handler = self.llm_conversation_handler
        inject_system_messages = []
        if llm_conversation_handler is not None:
            outcome_text = ''
            if dc_value is not None and success is not None:
                outcome_text = ' (success)' if success else ' (failure)'
            inject_system_messages.append(
                (
                    f"{entity_label(speaker)} completed a {skill_label} check"
                    f"{f' with a total of {roll_total}' if roll_total is not None else ''}"
                    f"{f' against DC {dc_value}{outcome_text}' if dc_value is not None else ''}. "
                    "React in character to this outcome."
                )
            )

        http_replies = []
        normalized_requesting_username = self._normalize_username(requesting_username)
        npc_turn = self._run_single_npc_reply_turn(
            requester,
            speaker,
            conversation_system_prompt,
            llm_conversation_handler=llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=inject_system_messages,
        )

        response_payload = {
            'success': True,
            'skill_check': skill_result,
        }
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def maybe_open_player_gift_transfer(self, speaker, targets, message, actions=None):
        """Open the give-item dialog when a player offers to hand something to an NPC."""
        if speaker is None or not isinstance(speaker, PlayerCharacter):
            return False
        if not is_player_gift_offer(message, actions):
            return False

        npc_targets = [target for target in unique_entities(targets or []) if isinstance(target, Npc)]
        if not npc_targets:
            return False

        gift_meta = parse_player_gift_offer(message, actions)
        item_hint = gift_meta.get('item_hint')
        rag_handler = self.entity_rag_handler
        if rag_handler is None:
            return False

        opened = False
        for npc in npc_targets[:1]:
            try:
                if rag_handler._accept_gift_from_speaker(npc, speaker, item_hint=item_hint):
                    opened = True
            except Exception:
                self.logger.exception(
                    "[Talk] failed to open gift transfer speaker=%s npc=%s",
                    getattr(speaker, 'entity_uid', speaker),
                    getattr(npc, 'entity_uid', npc),
                )
        if opened:
            self.logger.info(
                "[Talk] opened player gift transfer speaker=%s npc=%s item_hint=%s",
                getattr(speaker, 'entity_uid', speaker),
                getattr(npc_targets[0], 'entity_uid', npc_targets[0]),
                item_hint,
            )
        return opened

    def handle_npc_gift_transfer_complete(
        self,
        data,
        conversation_system_prompt,
        requesting_username=None,
    ):
        """Notify an NPC LLM after a player completes (or cancels) a gift transfer dialog."""
        from webapp.npc_item_exchange import pop_npc_gift_transfer_session, summarize_player_gift_transfer

        session_id = str((data or {}).get('transfer_session_id') or '').strip()
        if not session_id:
            return {'error': 'transfer_session_id is required'}, 400

        transfer_session = pop_npc_gift_transfer_session(session_id)
        if transfer_session is None:
            return {'error': 'Unknown or expired transfer session'}, 404

        npc = self.current_game.get_entity_by_uid(transfer_session.get('npc_uid'))
        player = self.current_game.get_entity_by_uid(transfer_session.get('player_uid'))
        if npc is None or player is None:
            return {'error': 'Transfer participants not found'}, 404

        normalized_requesting_username = self._normalize_username(requesting_username)
        player_owners = {
            str(name).strip().lower()
            for name in (self.entity_owners(player) or [])
            if str(name).strip()
        }
        if normalized_requesting_username not in player_owners:
            return {'error': 'Not authorized for this transfer'}, 403

        cancelled = bool((data or {}).get('cancelled'))
        items_payload = (data or {}).get('items')
        player_label = entity_label(player)
        if cancelled:
            system_note = (
                f"{player_label} closed the item transfer window without giving anything. "
                "React briefly in character if appropriate."
            )
        else:
            summary = summarize_player_gift_transfer(items_payload)
            if summary:
                system_note = (
                    f"{player_label} gave you: {summary}. "
                    "Acknowledge receipt in character."
                )
            else:
                system_note = (
                    f"{player_label} confirmed the transfer window without selecting items. "
                    "React briefly in character if appropriate."
                )

        http_replies = []
        npc_turn = self._run_single_npc_reply_turn(
            npc,
            player,
            conversation_system_prompt,
            llm_conversation_handler=self.llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=[system_note],
        )

        response_payload = {'success': True, 'cancelled': cancelled}
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def _npc_system_prompt_template(self):
        from webapp.llm_handler import read_npc_system_prompt
        from webapp.blueprints.helpers.runtime_state import get_level

        return read_npc_system_prompt(get_level())

    def inject_npc_llm_system_note(self, npc_uid: str, note: str) -> None:
        """Queue a system note for the NPC's next LLM conversation turn."""
        text = str(note or '').strip()
        conv_id = str(npc_uid or '').strip()
        if not text or not conv_id:
            return
        handler = self.llm_conversation_handler
        if handler is None:
            return
        try:
            handler.add_message(conv_id, 'system', text)
        except Exception:
            self.logger.warning(
                'Failed to inject NPC conversation system note for %s',
                conv_id,
                exc_info=True,
            )

    def handle_npc_item_offer_resolution(
        self,
        npc,
        target,
        item_slug: str,
        *,
        accepted: bool,
        effect_message=None,
        conversation_system_prompt=None,
        requesting_username=None,
        proactive_reply: bool = True,
    ):
        """Notify an NPC LLM after a player accepts or declines an offered item."""
        if npc is None or target is None or not item_slug:
            return {'success': False, 'error': 'missing_entities'}, 400

        game_properties = getattr(self.game_session, 'game_properties', None) or {}
        note = item_offer_resolution_note(
            npc,
            target,
            item_slug,
            accepted=accepted,
            effect_message=effect_message,
            game_properties=game_properties,
        )
        self.inject_npc_llm_system_note(getattr(npc, 'entity_uid', None), note)

        if not accepted or not proactive_reply:
            return {'success': True, 'notified': True}, 200

        # The NPC already spoke when making the offer; only generate an immediate
        # follow-up line when a special on-accept effect needs in-character narration.
        if not str(effect_message or '').strip():
            return {'success': True, 'notified': True}, 200

        prompt_template = conversation_system_prompt or self._npc_system_prompt_template()
        http_replies = []
        owners = self.entity_owners(target) or []
        normalized_requesting_username = self._normalize_username(
            requesting_username or (owners[0] if owners else None)
        )
        npc_turn = self._run_single_npc_reply_turn(
            npc,
            target,
            prompt_template,
            llm_conversation_handler=self.llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=[note],
        )

        response_payload = {'success': True, 'notified': True}
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def handle_npc_out_of_combat_hit(
        self,
        npc,
        attacker,
        damage_item,
        *,
        battle_map=None,
        conversation_system_prompt=None,
        requesting_username=None,
    ):
        """Let an NPC LLM react in character after being hit outside combat."""
        if npc is None or damage_item is None:
            return {'success': False, 'error': 'missing_entities'}, 400
        if not callable(getattr(npc, 'npc', None)) or not npc.npc():
            return {'success': False, 'error': 'not_npc'}, 400
        if callable(getattr(npc, 'dead', None)) and npc.dead():
            return {'success': False, 'error': 'npc_dead'}, 200
        if callable(getattr(npc, 'conscious', None)) and not npc.conscious():
            return {'success': False, 'error': 'npc_unconscious'}, 200

        note = out_of_combat_hit_reaction_note(
            npc,
            attacker,
            damage_item,
            battle_map=battle_map,
        )
        self.inject_npc_llm_system_note(getattr(npc, 'entity_uid', None), note)

        speaker = attacker if attacker is not None else npc
        prompt_template = conversation_system_prompt or self._npc_system_prompt_template()
        http_replies = []
        owners = self.entity_owners(speaker) or []
        normalized_requesting_username = self._normalize_username(
            requesting_username or (owners[0] if owners else None)
        )
        npc_turn = self._run_single_npc_reply_turn(
            npc,
            speaker,
            prompt_template,
            llm_conversation_handler=self.llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=[note],
            npc_initiated=True,
            local_chat_only=True,
        )

        response_payload = {'success': True, 'notified': True}
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def handle_npc_steal_detected(
        self,
        npc,
        thief,
        *,
        container_info,
        items,
        detection_type: str = 'seen',
        ownership_info=None,
        can_see: bool = True,
        battle_map=None,
        conversation_system_prompt=None,
        requesting_username=None,
    ):
        """Let an NPC LLM react in character immediately after witnessing a theft."""
        del battle_map
        if npc is None or thief is None or not items:
            return {'success': False, 'error': 'missing_entities'}, 400
        if not callable(getattr(npc, 'npc', None)) or not npc.npc():
            return {'success': False, 'error': 'not_npc'}, 400
        if callable(getattr(npc, 'dead', None)) and npc.dead():
            return {'success': False, 'error': 'npc_dead'}, 200
        if callable(getattr(npc, 'conscious', None)) and not npc.conscious():
            return {'success': False, 'error': 'npc_unconscious'}, 200

        note = steal_detection_note(
            npc,
            thief,
            container_info,
            items,
            detection_type=detection_type,
            ownership_info=ownership_info,
            can_see=can_see,
        )
        self.inject_npc_llm_system_note(getattr(npc, 'entity_uid', None), note)

        prompt_template = conversation_system_prompt or self._npc_system_prompt_template()
        http_replies = []
        owners = self.entity_owners(thief) or []
        normalized_requesting_username = self._normalize_username(
            requesting_username or (owners[0] if owners else None)
        )
        npc_turn = self._run_single_npc_reply_turn(
            npc,
            thief,
            prompt_template,
            llm_conversation_handler=self.llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=[note],
            npc_initiated=True,
            local_chat_only=True,
        )

        response_payload = {'success': True, 'notified': True}
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def handle_npc_pickpocket_detected(
        self,
        npc,
        pickpocketer,
        *,
        target,
        item_name: str,
        success: bool,
        detection_type: str = 'seen',
        can_see: bool = True,
        role: str = 'witness',
        conversation_system_prompt=None,
        requesting_username=None,
    ):
        """Let an NPC LLM react immediately after witnessing or catching a pickpocket."""
        if npc is None or pickpocketer is None:
            return {'success': False, 'error': 'missing_entities'}, 400
        if not item_name:
            item_name = 'something from their belongings'
        if not callable(getattr(npc, 'npc', None)) or not npc.npc():
            return {'success': False, 'error': 'not_npc'}, 400
        if callable(getattr(npc, 'dead', None)) and npc.dead():
            return {'success': False, 'error': 'npc_dead'}, 200
        if callable(getattr(npc, 'conscious', None)) and not npc.conscious():
            return {'success': False, 'error': 'npc_unconscious'}, 200

        if role == 'victim':
            note = pickpocket_victim_caught_note(npc, pickpocketer, item_name)
        else:
            note = pickpocket_attempt_note(
                npc,
                pickpocketer,
                target,
                item_name,
                success,
                detection_type=detection_type,
                can_see=can_see,
            )
        self.inject_npc_llm_system_note(getattr(npc, 'entity_uid', None), note)

        prompt_template = conversation_system_prompt or self._npc_system_prompt_template()
        http_replies = []
        owners = self.entity_owners(pickpocketer) or []
        normalized_requesting_username = self._normalize_username(
            requesting_username or (owners[0] if owners else None)
        )
        npc_turn = self._run_single_npc_reply_turn(
            npc,
            pickpocketer,
            prompt_template,
            llm_conversation_handler=self.llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            inject_system_messages=[note],
            npc_initiated=True,
            local_chat_only=True,
        )

        response_payload = {'success': True, 'notified': True}
        if npc_turn is None:
            response_payload['npc_reply_failed'] = True
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def notify_animal_communication_granted(self, listener, *, expiration=None) -> None:
        """Tell nearby beast-speaking NPCs that a listener can understand them."""
        if listener is None:
            return

        note = animal_communication_activation_note(listener)
        if expiration is not None:
            note = (
                f"{note} (active until game time {int(expiration)}.)"
            )

        try:
            witness_uids = set(
                witness_entity_uids(
                    self.game_session,
                    source=listener,
                    target=listener,
                    distance_ft=30,
                    volume='normal',
                )
            )
        except Exception:
            witness_uids = set()

        listener_uid = str(getattr(listener, 'entity_uid', '') or '')
        prompt_template = self._npc_system_prompt_template()

        for uid in witness_uids:
            if uid == listener_uid:
                continue
            try:
                npc = self.game_session.entity_by_uid(uid)
            except Exception:
                npc = None
            if npc is None or not _entity_speaks_beast_language(npc):
                continue
            owners = self.entity_owners(listener) or []
            self.handle_npc_item_offer_resolution(
                npc,
                listener,
                'scroll_speak_animals_modified',
                accepted=True,
                effect_message=note,
                conversation_system_prompt=prompt_template,
                requesting_username=owners[0] if owners else None,
                proactive_reply=True,
            )

    def handle_player_insight_request(self, speaker, message,
                                      explicit_targets=None,
                                      mentioned_targets=None):
        """Resolve a player's "Insight Check" request privately.

        Returns the standard ``(dict, status)`` tuple expected by ``/talk``.
        The player's message is **not** forwarded to NPCs.
        """
        target_spec, purpose = parse_player_insight_request(message)
        target = self._resolve_insight_target(speaker, target_spec,
                                              explicit_targets or [],
                                              mentioned_targets or [])

        if target is None:
            self._emit_player_insight_payload(
                speaker,
                "Whom would you like to read? Try 'Insight check on @<name> about <topic>'.",
                vague=True,
                clarification='missing_target',
            )
            return {'success': True, 'insight': 'needs_target'}, 200

        # Look up something concrete to read. If the player did not give a
        # purpose and the NPC has not actually said anything yet, ask them to
        # be more specific instead of rolling.
        latest_statement = self._latest_npc_statement(speaker, target)
        if not purpose and not latest_statement:
            self._emit_player_insight_payload(
                speaker,
                f"What about {entity_label(target)} are you trying to read? "
                "(e.g. 'are they lying about the missing caravan?')",
                target=target,
                vague=True,
                clarification='missing_purpose',
            )
            return {'success': True, 'insight': 'needs_purpose'}, 200

        statement_for_check = purpose or latest_statement
        message_key = insight_check_message_key(
            getattr(target, 'entity_uid', None),
            statement_for_check,
        )
        existing = self.get_recorded_insight_check(
            speaker,
            getattr(target, 'entity_uid', None),
            statement_for_check,
        )
        if existing:
            self._emit_player_insight_payload(
                speaker,
                f"You already read {entity_label(target)} on that line.",
                target=target,
                roll_total=existing.get('roll_total'),
                assessment=existing.get('assessment'),
                reason=existing.get('reason'),
                purpose=purpose,
            )
            return {
                'success': True,
                'insight': existing,
                'already_checked': True,
            }, 200

        return self._execute_player_insight_check(
            speaker,
            target,
            statement_for_check,
            purpose=purpose,
            message_key=message_key,
        )

    def _emit_player_perception_payload(self, speaker, message_text, target=None,
                                        roll_total=None, description=None, focus=None,
                                        vague=False, clarification=None, message_key=None):
        entity_id = (
            getattr(target, 'entity_uid', None)
            or getattr(speaker, 'entity_uid', None)
        )
        payload = {
            'entity_id': entity_id,
            'speaker_name': 'Dungeon Master',
            'message': message_text,
            'narrative': [],
            'language': 'common',
            'targets': [getattr(speaker, 'entity_uid', None)],
            'target_names': [entity_label(speaker)] if speaker is not None else [],
            'mentioned_targets': [],
            'mentioned_handles': [],
            'volume': 'normal',
            'distance_ft': 0,
            'visual_only_usernames': [],
            'system': True,
            'perception_check': {
                'observer': entity_label(speaker) if speaker is not None else None,
                'target': entity_label(target) if target is not None else None,
                'target_uid': getattr(target, 'entity_uid', None) if target is not None else None,
                'roll_total': roll_total,
                'description': description,
                'focus': focus,
                'vague': bool(vague),
                'clarification': clarification,
                'message_key': message_key,
            },
        }
        usernames = self.entity_audience_usernames([speaker], include_dm=True)
        self.emit_conversation_to_usernames(usernames, payload, source_entity=None)
        return payload

    @staticmethod
    def _perception_checks_store(speaker):
        if not hasattr(speaker, 'properties') or speaker.properties is None:
            return {}
        store = speaker.properties.setdefault('perception_checks', {})
        if not isinstance(store, dict):
            store = {}
            speaker.properties['perception_checks'] = store
        return store

    def get_recorded_perception_check(self, speaker, target_uid, focus=None):
        key = perception_check_message_key(target_uid, focus)
        recorded = self._perception_checks_store(speaker).get(key)
        return dict(recorded) if isinstance(recorded, dict) else None

    def record_perception_check(self, speaker, target_uid, focus, result):
        key = perception_check_message_key(target_uid, focus)
        payload = dict(result or {})
        payload['message_key'] = key
        payload['focus'] = str(focus or '').strip()
        self._perception_checks_store(speaker)[key] = payload
        return payload

    def _resolve_perception_target(self, speaker, target_spec, explicit_targets, mentioned_targets):
        """Pick a visible entity or object the player wants to study."""
        for candidate in unique_entities(list(mentioned_targets or []) + list(explicit_targets or [])):
            if candidate is not None and candidate != speaker:
                return candidate

        if target_spec:
            rag_handler = self.entity_rag_handler
            if rag_handler is not None and hasattr(rag_handler, 'resolve_named_target'):
                try:
                    candidate = rag_handler.resolve_named_target(
                        speaker,
                        target_spec,
                        speaker=speaker,
                        include_objects=True,
                    )
                except Exception:
                    candidate = None
                if candidate is not None and candidate != speaker:
                    return candidate

        return None

    def _player_can_perceive_target(self, speaker, target) -> bool:
        if speaker is None or target is None:
            return False
        if speaker == target:
            return True
        try:
            battle_map = self.current_game.get_map_for_entity(speaker)
        except Exception:
            battle_map = None
        if battle_map is None:
            return False
        try:
            return bool(battle_map.can_see(speaker, target))
        except Exception:
            return False

    def _emit_perception_not_visible(self, speaker, target):
        self._emit_player_perception_payload(
            speaker,
            f"You cannot get a clear look at {entity_label(target)} from where you stand.",
            target=target,
            vague=True,
            clarification='not_visible',
        )
        return {'success': True, 'perception': 'not_visible'}, 200

    def _execute_player_perception_check(
        self,
        speaker,
        target,
        focus=None,
        *,
        message_key=None,
    ):
        focus_text = str(focus or '').strip()
        description = (
            f"Perception check on {entity_label(target)}"
            + (f" ({focus_text})" if focus_text else '')
        )

        try:
            roll = speaker.perception_check(description=description)
        except Exception as exc:
            self.logger.error(f"Perception check failed for {entity_label(speaker)}: {exc}")
            self._emit_player_perception_payload(
                speaker,
                'Your perception check could not be completed.',
                target=target,
                focus=focus_text or None,
                vague=True,
                clarification='roll_failed',
            )
            return {'success': False, 'error': 'perception_roll_failed'}, 500

        roll_total = None
        try:
            roll_total = roll.result()
        except Exception:
            roll_total = None

        scene_description = (
            'You study the scene, but nothing new stands out to you right now.'
        )
        rag_handler = self.entity_rag_handler
        llm_conversation_handler = self.llm_conversation_handler
        if rag_handler is not None and llm_conversation_handler is not None \
                and hasattr(rag_handler, '_evaluate_perception_description'):
            try:
                verdict = rag_handler._evaluate_perception_description(
                    observer=speaker,
                    target=target,
                    focus=focus_text,
                    roll=roll,
                    llm_conversation_handler=llm_conversation_handler,
                )
            except Exception as exc:
                self.logger.error(f"Perception adjudication failed: {exc}")
                verdict = None
            if isinstance(verdict, dict):
                scene_description = str(
                    verdict.get('description') or scene_description
                ).strip() or scene_description

        try:
            roll_repr = str(roll) if roll is not None else ''
        except Exception:
            roll_repr = ''
        if roll_repr and roll_total is not None:
            roll_text = f"Perception check ({roll_repr}) = {roll_total}"
        elif roll_total is not None:
            roll_text = f"Perception check: {roll_total}"
        else:
            roll_text = 'Perception check'
        message_text = f"{roll_text} studying {entity_label(target)}."

        perception_result = {
            'target': entity_label(target),
            'target_uid': getattr(target, 'entity_uid', None),
            'roll_total': roll_total,
            'description': scene_description,
            'focus': focus_text or None,
            'message_key': message_key or perception_check_message_key(
                getattr(target, 'entity_uid', None),
                focus_text or None,
            ),
        }
        self.record_perception_check(
            speaker,
            getattr(target, 'entity_uid', None),
            focus_text or None,
            perception_result,
        )

        self._emit_player_perception_payload(
            speaker,
            message_text,
            target=target,
            roll_total=roll_total,
            description=scene_description,
            focus=focus_text or None,
            message_key=perception_result.get('message_key'),
        )

        if rag_handler is not None and hasattr(rag_handler, '_log_social_check'):
            try:
                roll_breakdown = ''
                try:
                    roll_str = str(roll) if roll is not None else ''
                except Exception:
                    roll_str = ''
                if roll_str and roll_total is not None:
                    roll_breakdown = f"{roll_str} = {roll_total}"
                elif roll_total is not None:
                    roll_breakdown = f"total {roll_total}"
                rag_handler._log_social_check(
                    f"{entity_label(speaker)} makes a Perception check while speaking with "
                    f"{entity_label(target)} ({roll_breakdown}).",
                    entities=[speaker, target],
                )
            except Exception:
                pass

        return {'success': True, 'perception': perception_result}, 200

    def handle_player_perception_button_request(self, speaker, payload):
        if not callable(getattr(speaker, 'perception_check', None)):
            return {'success': False, 'error': 'perception_requires_player_character'}, 400

        target_id = (payload or {}).get('target_id')
        focus = str((payload or {}).get('focus') or '').strip() or None
        if not target_id:
            return {'success': False, 'error': 'missing_target'}, 400

        target = self.current_game.get_entity_by_uid(target_id)
        if target is None:
            return {'success': False, 'error': 'invalid_target'}, 400

        if not self._player_can_perceive_target(speaker, target):
            return self._emit_perception_not_visible(speaker, target)

        existing = self.get_recorded_perception_check(speaker, target_id, focus)
        if existing:
            return {
                'success': True,
                'perception': existing,
                'already_checked': True,
            }, 200

        message_key = perception_check_message_key(target_id, focus)
        return self._execute_player_perception_check(
            speaker,
            target,
            focus=focus,
            message_key=message_key,
        )

    def handle_player_perception_request(self, speaker, message,
                                         explicit_targets=None,
                                         mentioned_targets=None):
        target_spec, focus = parse_player_perception_request(message)
        target = self._resolve_perception_target(
            speaker,
            target_spec,
            explicit_targets or [],
            mentioned_targets or [],
        )

        if target is None:
            self._emit_player_perception_payload(
                speaker,
                "Whom or what are you studying? Try '@dm perception on @<name>' or "
                "'Perception check on @<name> about <detail>'.",
                vague=True,
                clarification='missing_target',
            )
            return {'success': True, 'perception': 'needs_target'}, 200

        if not self._player_can_perceive_target(speaker, target):
            return self._emit_perception_not_visible(speaker, target)

        message_key = perception_check_message_key(
            getattr(target, 'entity_uid', None),
            focus,
        )
        existing = self.get_recorded_perception_check(
            speaker,
            getattr(target, 'entity_uid', None),
            focus,
        )
        if existing:
            self._emit_player_perception_payload(
                speaker,
                f"You already studied {entity_label(target)} on that focus.",
                target=target,
                roll_total=existing.get('roll_total'),
                description=existing.get('description'),
                focus=focus,
                message_key=existing.get('message_key'),
            )
            return {
                'success': True,
                'perception': existing,
                'already_checked': True,
            }, 200

        return self._execute_player_perception_check(
            speaker,
            target,
            focus=focus,
            message_key=message_key,
        )

    def handle_player_describe_see_request(self, speaker, message,
                                           explicit_targets=None,
                                           mentioned_targets=None):
        """Handle "describe what I see?" style requests during conversation.

        The NPC LLM generates a visual aside about what the player character can
        see from their perspective: facial expressions, body language, clothing,
        injuries, and environment details relevant to the moment.

        This is similar to a perception check but focused on narrative description
        of the NPC in conversation rather than environmental scanning.
        """
        target_spec, focus = parse_player_describe_see_request(message)

        # Resolve target: prefer explicitly mentioned NPC or first entity in conversation
        target = self._resolve_perception_target(
            speaker,
            target_spec,
            explicit_targets or [],
            mentioned_targets or [],
        )

        # If no target was resolved and we have conversation targets, use the first NPC
        if target is None:
            for candidate in unique_entities(list(mentioned_targets or []) + list(explicit_targets or [])):
                if candidate is not None and candidate != speaker:
                    target = candidate
                    break

        if target is None:
            self._emit_player_perception_payload(
                speaker,
                "Who should I describe? Try '@dm describe what I see about @<name>' or "
                "'Describe what I see here'.",
                vague=True,
                clarification='missing_target',
            )
            return {'success': True, 'describe_see': 'needs_target'}, 200

        if not self._player_can_perceive_target(speaker, target):
            return self._emit_perception_not_visible(speaker, target)

        # Check for duplicate (avoid spam during same conversation beat)
        message_key = f"describe_see:{getattr(target, 'entity_uid', None)}"
        existing = self.get_recorded_perception_check(speaker, getattr(target, 'entity_uid', None))
        if existing and existing.get('message_key') == message_key:
            self._emit_player_perception_payload(
                speaker,
                f"You're still watching {entity_label(target)}.",
                target=target,
                roll_total=existing.get('roll_total'),
                description=existing.get('description'),
                focus=focus,
                message_key=message_key,
            )
            return {
                'success': True,
                'describe_see': existing,
                'already_checked': True,
            }, 200

        # Execute the description
        return self._execute_player_describe_see(
            speaker,
            target,
            focus=focus or None,
            message_key=message_key,
        )

    def _execute_player_describe_see(
        self,
        speaker,
        target,
        focus=None,
        *,
        message_key=None,
    ):
        """Generate NPC LLM description of what the player sees."""
        focus_text = str(focus or '').strip()
        description = (
            f"Describe what {entity_label(speaker)} sees regarding {entity_label(target)}"
            + (f" ({focus_text})" if focus_text else '')
        )

        # Perform a perception check to gate detail level
        try:
            roll = speaker.perception_check(description=description)
        except Exception as exc:
            self.logger.error(f"Perception check failed for {entity_label(speaker)} during describe-see: {exc}")
            self._emit_player_perception_payload(
                speaker,
                'Your attempt to observe the scene was unsuccessful.',
                target=target,
                focus=focus_text or None,
                vague=True,
                clarification='roll_failed',
            )
            return {'success': False, 'error': 'perception_roll_failed'}, 500

        roll_total = None
        try:
            roll_total = roll.result()
        except Exception:
            roll_total = None

        # Build context for the NPC LLM description
        appearance = getattr(target, 'outward_appearance', '') or ''
        statuses = list(getattr(target, 'statuses', []) or [])
        visible_effects = []
        if hasattr(target, 'current_effects') and callable(getattr(target, 'current_effects')):
            try:
                visible_effects = [str(e.get('effect', '')) for e in target.current_effects() if e.get('effect')]
            except Exception:
                visible_effects = []

        recent_conv = []
        try:
            conv_buffer = list(getattr(target, 'conversation_buffer', None) or [])[-6:]
            recent_conv = conv_buffer
        except Exception:
            pass

        # Determine roll tier for detail level
        if roll_total is not None:
            if roll_total >= 18:
                detail_tier = 'detailed'
            elif roll_total >= 14:
                detail_tier = 'moderate'
            elif roll_total >= 10:
                detail_tier = 'basic'
            else:
                detail_tier = 'vague'
        else:
            detail_tier = 'basic'

        llm_conversation_handler = self.llm_conversation_handler
        scene_description = self._get_npc_describe_see_description(
            observer=speaker,
            target=target,
            appearance=appearance,
            statuses=statuses,
            visible_effects=visible_effects,
            recent_conv=recent_conv,
            detail_tier=detail_tier,
            llm_conversation_handler=llm_conversation_handler,
        )

        roll_text = f"Observation check: {roll_total}" if roll_total else 'Observation check'
        message_text = f"{roll_text} regarding {entity_label(target)}."

        perception_result = {
            'target': entity_label(target),
            'target_uid': getattr(target, 'entity_uid', None),
            'roll_total': roll_total,
            'description': scene_description,
            'focus': focus_text or None,
            'message_key': message_key or f"describe_see:{getattr(target, 'entity_uid', None)}",
            'detail_tier': detail_tier,
            'is_describe_see': True,
        }
        self.record_perception_check(
            speaker,
            getattr(target, 'entity_uid', None),
            focus_text or None,
            perception_result,
        )

        self._emit_player_perception_payload(
            speaker,
            message_text,
            target=target,
            roll_total=roll_total,
            description=scene_description,
            focus=focus_text or None,
            vague=detail_tier == 'vague',
            message_key=message_key,
        )

        return {'success': True, 'describe_see': perception_result}, 200

    def _get_npc_describe_see_description(
        self,
        observer,
        target,
        appearance,
        statuses,
        visible_effects,
        recent_conv,
        detail_tier,
        llm_conversation_handler,
    ):
        """Generate the NPC visual description via the NPC LLM."""
        default_description = f"You observe {entity_label(target)} standing before you."

        if llm_conversation_handler is None:
            return default_description

        llm_handler = getattr(llm_conversation_handler, 'llm_hander', None)
        if llm_handler is None:
            return default_description

        # Build context block
        dm_context = {
            'target_appearance': appearance,
            'target_statuses': statuses,
            'visible_effects': visible_effects,
            'recent_conversation': recent_conv,
            'detail_tier': detail_tier,
            'observer_label': entity_label(observer),
            'target_label': entity_label(target),
        }

        messages = [
            {
                'role': 'system',
                'content': (
                    'You are generating a visual description for what a player character sees when they ask '
                    '"describe what I see" during a conversation with an NPC. '
                    'Describe only visible, surface-level details that would be apparent to someone '
                    'talking to this NPC right now. Focus on: '
                    'facial expressions (conveying emotion, attitude, tension), '
                    'body language (posture, gestures, fidgeting, stillness), '
                    'visible clothing and appearance details (state of dress, dirt, tears, jewelry), '
                    'visible injuries or status effects (bruises, bandages, magical glows), '
                    'environmental details that frame the interaction (lighting, proximity, background figures). '
                    'Keep it grounded, vivid, and in-character. Use second-person present tense ("You notice..."). '
                    'Do NOT reveal hidden motives, thoughts, or DM-only secrets. '
                    'The detail_tier tells you how much to focus on: '
                    '"vague" = general impressions only; "basic" = obvious details; '
                    '"moderate" = include subtle cues; "detailed" = capture micro-expressions and fine details. '
                    'Return JSON only with key "description" (one or two short paragraphs).'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps(dm_context, default=str),
            },
        ]

        try:
            raw_response = llm_handler.send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'describe_see',
                },
            )
        except Exception as exc:
            logger.error(f"Error generating describe-see for {entity_label(observer)}: {exc}")
            return default_description

        # Parse the LLM response
        text = str(raw_response or '').strip()
        fence = re.search(r'```(?:json|JSON)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            text = match.group(0)
        try:
            payload = json.loads(text)
            description = str(payload.get('description') or '').strip()
        except Exception:
            description = text.strip()

        if not description or description == '':
            return default_description
        return description

    def _persist_conversation_line(
        self,
        speaker,
        message,
        *,
        targets=None,
        volume=None,
        language=None,
        narrative=None,
        segments=None,
        username=None,
    ):
        db = getattr(self.current_game, 'campaign_log_db', None)
        if db is None or not message:
            return
        target_labels = []
        for target in targets or []:
            try:
                target_labels.append(entity_label(target))
            except Exception:
                target_labels.append(getattr(target, 'entity_uid', str(target)))
        try:
            db.append_conversation(
                speaker_uid=getattr(speaker, 'entity_uid', None),
                speaker_label=entity_label(speaker),
                message=message,
                targets=[getattr(t, 'entity_uid', None) for t in (targets or [])],
                target_labels=target_labels,
                volume=volume,
                language=language,
                username=username,
                narrative=narrative,
            )
        except Exception as exc:
            self.logger.debug(f"Campaign conversation log skipped: {exc}")

    def _register_reply_continuation(self, receiver, speaker, reply_plan, *, local_chat_only=False):
        pending_segments = list(reply_plan.get('pending_segments') or [])
        if not pending_segments:
            return None
        continuation_id = uuid.uuid4().hex
        self._reply_continuations[continuation_id] = {
            'receiver_uid': receiver.entity_uid,
            'speaker_uid': getattr(speaker, 'entity_uid', None) if speaker is not None else None,
            'pending_segments': pending_segments,
            'targets': [getattr(t, 'entity_uid', t) for t in (reply_plan.get('targets') or [])],
            'volume': reply_plan.get('volume'),
            'language': reply_plan.get('language'),
            'tts_emotion': reply_plan.get('tts_emotion'),
            'tts_instruct': reply_plan.get('tts_instruct'),
            'local_chat_only': bool(local_chat_only),
        }
        return continuation_id

    def _beat_segments_to_reply_plan(self, receiver, beat_segments, base_plan):
        segments, narrative, message, _inferred = self.entity_rag_handler._apply_narrative_segment_policies(
            list(beat_segments or []),
            receiver=receiver,
            speaker=receiver,
        )
        if not message and not narrative:
            return None
        reply_plan = {
            'language': base_plan.get('language') or 'common',
            'message': message,
            'narrative': narrative,
            'segments': segments,
            'targets': list(base_plan.get('targets') or []),
            'volume': base_plan.get('volume'),
            'tts_emotion': base_plan.get('tts_emotion'),
            'tts_instruct': base_plan.get('tts_instruct'),
            'gallery_image_id': base_plan.get('gallery_image_id'),
            'skip': False,
        }
        return reply_plan

    def _emit_reply_continuation_beat(
        self,
        continuation_id,
        *,
        http_replies=None,
        normalized_requesting_username=None,
    ):
        state = self._reply_continuations.get(continuation_id)
        if not state:
            return None

        receiver = self.current_game.get_entity_by_uid(state.get('receiver_uid'))
        if receiver is None:
            self._reply_continuations.pop(continuation_id, None)
            return None

        beat_segments, pending_segments = self.entity_rag_handler.pop_next_reply_beat(
            state.get('pending_segments') or []
        )
        if not beat_segments:
            self._reply_continuations.pop(continuation_id, None)
            return None

        speaker = None
        speaker_uid = state.get('speaker_uid')
        if speaker_uid:
            speaker = self.current_game.get_entity_by_uid(speaker_uid)

        targets = []
        for target_uid in state.get('targets') or []:
            target = self.current_game.get_entity_by_uid(target_uid)
            if target is not None:
                targets.append(target)

        base_plan = {
            'language': state.get('language'),
            'targets': targets,
            'volume': state.get('volume'),
            'tts_emotion': state.get('tts_emotion'),
            'tts_instruct': state.get('tts_instruct'),
        }
        reply_plan = self._beat_segments_to_reply_plan(receiver, beat_segments, base_plan)
        if reply_plan is None:
            self._reply_continuations.pop(continuation_id, None)
            return None

        if pending_segments:
            state['pending_segments'] = pending_segments
        else:
            self._reply_continuations.pop(continuation_id, None)

        beats_remaining = self.entity_rag_handler.count_speech_beats(pending_segments)
        reply_distance_ft = speech_distance_for(mode=reply_plan['volume'])
        try:
            response_conversations = receiver.send_conversation(
                reply_plan['message'],
                distance_ft=reply_distance_ft,
                targets=reply_plan['targets'],
                language=reply_plan['language'],
                volume=reply_plan['volume'],
                narrative=reply_plan.get('narrative') or [],
                segments=reply_plan.get('segments') or [],
                gallery_image_id=reply_plan.get('gallery_image_id'),
            )
        except TypeError:
            response_conversations = receiver.send_conversation(
                reply_plan['message'],
                distance_ft=reply_distance_ft,
                targets=reply_plan['targets'],
                language=reply_plan['language'],
            )

        recipient_usernames = self.conversation_audience_usernames(
            receiver,
            response_conversations,
            targets=reply_plan['targets'],
        )
        visual_whisper_usernames = set()
        if reply_plan['volume'] == 'whisper':
            visual_whisper_usernames = self.conversation_visible_whisper_usernames(
                receiver,
                audible_usernames=recipient_usernames,
                targets=reply_plan['targets'],
            )

        active_continuation_id = continuation_id if beats_remaining > 0 else None
        conversation_payload = self.conversation_payload(
            receiver,
            reply_plan['message'],
            targets=reply_plan['targets'],
            volume=reply_plan['volume'],
            distance_ft=reply_distance_ft,
            language=reply_plan['language'],
            visual_only_usernames=visual_whisper_usernames,
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
            continuation_id=active_continuation_id,
            continuation_beats_remaining=beats_remaining,
            gallery_image_id=reply_plan.get('gallery_image_id'),
        )
        if state.get('local_chat_only'):
            conversation_payload['local_chat_only'] = True
        if not isinstance(receiver, PlayerCharacter):
            conversation_payload['tts_pending'] = True
            conversation_payload['tts_emotion'] = reply_plan.get('tts_emotion')
            conversation_payload['tts_instruct'] = reply_plan.get('tts_instruct')
        else:
            conversation_payload['tts_pending'] = False

        emitted_by_username = self.emit_conversation_to_usernames(
            recipient_usernames | visual_whisper_usernames,
            conversation_payload,
            source_entity=receiver,
        )
        self._append_http_reply_for_requester(
            http_replies,
            normalized_requesting_username,
            emitted_by_username,
            conversation_payload,
            receiver,
        )
        self._persist_conversation_line(
            receiver,
            reply_plan['message'],
            targets=reply_plan['targets'],
            volume=reply_plan['volume'],
            language=reply_plan['language'],
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
        )
        try:
            self.entity_rag_handler.apply_conversation_keywords_from_plan(
                reply_plan,
                receiver,
                speaker=speaker,
            )
        except Exception as exc:
            self.logger.warning(
                "[Talk] continuation keyword side effects failed for %s: %s",
                receiver.label(),
                exc,
            )
        self.logger.info(
            "[Talk] emitted continuation beat from %s continuation_id=%s beats_remaining=%s preview=%r",
            receiver.label(),
            continuation_id,
            beats_remaining,
            (reply_plan['message'] or '')[:160],
        )
        return {
            'conversation_payload': conversation_payload,
            'continuation_id': active_continuation_id,
            'continuation_beats_remaining': beats_remaining,
        }

    def talk_continue_response(self, data, requesting_username=None):
        continuation_id = str((data or {}).get('continuation_id') or '').strip()
        if not continuation_id:
            return {'error': 'continuation_id is required'}, 400
        normalized_requesting_username = self._normalize_username(requesting_username)
        http_replies = []
        result = self._emit_reply_continuation_beat(
            continuation_id,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
        )
        if result is None:
            return {'success': False, 'error': 'Continuation not found or complete'}, 404
        return {
            'success': True,
            'replies': http_replies,
            'continuation_id': result.get('continuation_id'),
            'continuation_beats_remaining': result.get('continuation_beats_remaining', 0),
        }, 200

    def handle_message_spell_talk(
        self,
        data,
        entity,
        conversation_system_prompt,
        normalized_requesting_username,
        http_replies,
    ):
        """Deliver a whisper on an active Message cantrip link (caster ↔ target only)."""
        link_id = data.get('message_spell_link_id')
        message = data.get('message')
        language = data.get('language') or 'common'
        if not link_id:
            return {'error': 'message_spell_link_id is required'}, 400
        if message is None or not str(message).strip():
            return {'error': 'Message is required'}, 400

        game_session = self.current_game.game_session
        battle = None
        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None

        link = get_message_spell_link(game_session, link_id)
        if link is None:
            return {'error': 'Message spell link not found'}, 404
        if not link.is_active(game_session, battle=battle):
            return {'error': 'Message spell link has expired'}, 410
        if not link.can_speak(entity):
            return {'error': 'You are not part of this Message link'}, 403

        recipient = link.other_party(entity)
        if recipient is None:
            return {'error': 'Invalid Message link participants'}, 400

        parsed_input = parse_player_conversation_input(message or '')
        spoken_message = parsed_input.get('spoken') or ''
        player_actions = parsed_input.get('actions') or []
        if not spoken_message and not player_actions:
            return {'error': 'Message or action is required'}, 400

        if isinstance(entity, PlayerCharacter):
            self.current_game.increment_game_time(entity)

        volume = 'whisper'
        distance_ft = speech_distance_for(mode=volume)
        targets = [recipient]

        if comprehends_speech(recipient, language, source=entity, session=game_session):
            rendered_message = spoken_message
        else:
            rendered_message = gibberish(spoken_message, language=language)

        buffer_entry = {
            'source': entity,
            'message': spoken_message,
            'directed_to': targets,
            'targets': targets,
            'language': language,
            'distance_ft': distance_ft,
            'volume': volume,
            'narrative': player_actions,
            'actions': player_actions,
            'message_spell_link_id': link_id,
            'message_spell': True,
        }
        entity.conversation_buffer.append(buffer_entry)
        entity.memory_buffer.append(dict(buffer_entry))
        recipient.receive_conversation(
            entity,
            rendered_message,
            language=language,
            directed_to=targets,
            actions=player_actions,
        )

        speaker_payload = self.conversation_payload(
            entity,
            spoken_message,
            targets=targets,
            volume=volume,
            distance_ft=distance_ft,
            language=language,
            actions=player_actions,
            message_spell=True,
            message_spell_link_id=link_id,
            local_chat_only=True,
        )
        audience = self.entity_audience_usernames([entity, recipient], include_dm=True)
        emitted_by_username = self.emit_conversation_to_usernames(
            audience,
            speaker_payload,
            source_entity=entity,
        )
        self._append_http_reply_for_requester(
            http_replies,
            normalized_requesting_username,
            emitted_by_username,
            speaker_payload,
            entity,
        )
        self._persist_conversation_line(
            entity,
            spoken_message,
            targets=targets,
            volume=volume,
            language=language,
            username=session.get('username'),
            narrative=player_actions,
        )

        if getattr(recipient, 'is_npc', lambda: False)() and getattr(recipient, 'dialog', False):
            if not self.npc_conversation_mode_is_manual(recipient):
                self._run_single_npc_reply_turn(
                    recipient,
                    entity,
                    conversation_system_prompt,
                    llm_conversation_handler=self.llm_conversation_handler,
                    http_replies=http_replies,
                    normalized_requesting_username=normalized_requesting_username,
                    original_player_message=spoken_message or message,
                    local_chat_only=True,
                    message_spell_private=True,
                    message_spell_link_id=link_id,
                    player_speaker=entity,
                    player_language=language,
                )

        return {'success': True, 'replies': http_replies, 'message_spell_link_id': link_id}, 200

    def talk_response(self, data, conversation_system_prompt, requesting_username=None):
        talk_started = time.monotonic()
        http_replies = []
        normalized_requesting_username = self._normalize_username(requesting_username)
        dm_impersonate = bool(data.get('dm_impersonate'))
        dm_impersonate_polish = bool(data.get('dm_impersonate_polish'))
        polish_only = bool(data.get('polish_only'))
        in_character_polish = bool(data.get('in_character_polish'))
        entity_id = data.get('entity_id')
        message = data.get('message')
        language = data.get('language')
        primary_targets = data.get('targets', [])
        volume = self.effective_talk_volume(message, requested_volume=data.get('volume'), requested_distance_ft=data.get('distance_ft'))
        distance_ft = speech_distance_for(mode=volume, distance_ft=data.get('distance_ft'))
        self.logger.info(
            "[Talk] inbound speaker=%s message=%r volume=%s distance_ft=%s targets=%s language=%s",
            entity_id,
            (message or '')[:160],
            volume,
            distance_ft,
            primary_targets,
            language,
        )
        if not entity_id:
            return {'error': 'Entity ID is required'}, 400

        entity = self.current_game.get_entity_by_uid(entity_id)
        if not entity:
            return {'error': 'Entity not found'}, 404

        if data.get('message_spell_link_id'):
            return self.handle_message_spell_talk(
                data,
                entity,
                conversation_system_prompt,
                normalized_requesting_username,
                http_replies,
            )

        if data.get('no_response'):
            if not isinstance(entity, PlayerCharacter):
                return {'error': 'No Response is only available to player characters'}, 400
            goal_arrival_manager = getattr(self.current_game, 'goal_arrival_manager', None)
            if goal_arrival_manager is not None and goal_arrival_manager.on_player_no_response(entity):
                return {'success': True, 'no_response': True, 'goal_arrival_ended': True}, 200
            return {'error': 'No active goal-arrival conversation'}, 400

        if dm_impersonate:
            if not self._requester_is_dm(normalized_requesting_username):
                return {'error': 'DM impersonation requires DM privileges'}, 403
            if not getattr(entity, 'is_npc', lambda: False)():
                return {'error': 'DM impersonation requires an NPC speaker'}, 400

        insight_payload = data.get('insight_check')
        if isinstance(insight_payload, dict):
            if not callable(getattr(entity, 'insight_check', None)):
                return {'error': 'Insight checks require a player character'}, 400
            return self.handle_player_insight_button_request(entity, insight_payload)

        perception_payload = data.get('perception_check')
        if isinstance(perception_payload, dict):
            if not callable(getattr(entity, 'perception_check', None)):
                return {'error': 'Perception checks require a player character'}, 400
            return self.handle_player_perception_button_request(entity, perception_payload)

        skill_payload = data.get('skill_check')
        if isinstance(skill_payload, dict):
            skill = str((skill_payload or {}).get('skill') or '').strip().lower()
            roll_fn = getattr(entity, f'{skill}_check', None) if skill else None
            if not callable(roll_fn):
                return {'error': 'Skill check not available for this character'}, 400
            return self.handle_player_skill_check_button_request(
                entity,
                skill_payload,
                conversation_system_prompt=conversation_system_prompt,
                requesting_username=normalized_requesting_username,
            )

        if message is None:
            return {'error': 'Entity ID and message are required'}, 400

        if polish_only and in_character_polish:
            if not isinstance(entity, PlayerCharacter):
                return {'error': 'In-character polish requires a player character'}, 400
            if not self._requester_can_control_entity(normalized_requesting_username, entity):
                return {'error': 'You may only polish lines for characters you control'}, 403
            draft = str(message or '').strip()
            if not draft:
                return {'error': 'Message is required'}, 400
            polished = self.polish_pc_in_character_line(
                entity,
                draft,
                language=language or 'common',
            )
            return {
                'success': True,
                'draft_message': draft,
                'polished_message': polished or draft,
                'in_character_polish': True,
            }, 200

        parsed_input = parse_player_conversation_input(message or '')
        spoken_message = parsed_input.get('spoken') or ''
        player_actions = parsed_input.get('actions') or []
        if (
            isinstance(entity, PlayerCharacter)
            and spoken_message
            and not player_actions
            and is_passive_acknowledgement(spoken_message)
        ):
            goal_arrival_manager = getattr(self.current_game, 'goal_arrival_manager', None)
            if goal_arrival_manager is not None and goal_arrival_manager.on_player_passive_ack(entity):
                return {'success': True, 'passive_ack': True, 'goal_arrival_ended': True}, 200
        if not spoken_message and not player_actions:
            return {'error': 'Message or action is required'}, 400

        if isinstance(entity, PlayerCharacter):
            self.current_game.increment_game_time(entity)

        # Slash-command shortcut: PCs can ask the DM to manage their personal
        # journal directly from local chat. These commands never broadcast to
        # NPCs or other players. Supported forms:
        #   /journal add <text>      → append a note
        #   /journal search <query>  → return matching entries (private)
        #   /journal list [N]        → return the last N entries (default 5)
        if isinstance(entity, PlayerCharacter) and isinstance(message, str):
            stripped = message.strip()
            if stripped.lower().startswith('/journal'):
                return self._handle_journal_chat_command(entity, stripped)

        if isinstance(entity, PlayerCharacter):
            from webapp.blueprints.helpers.journal_utils import (
                _emit_journal_update,
                _persist_journal_change,
                grant_npc_journal_hooks,
            )
            hook_uids = []
            for target_uid in primary_targets or []:
                target = self.current_game.get_entity_by_uid(target_uid)
                if isinstance(target, Npc) and grant_npc_journal_hooks(entity, target):
                    hook_uids.append(entity.entity_uid)
            if hook_uids:
                _persist_journal_change(entity)
                _emit_journal_update(hook_uids, reason='npc_hook')

        entity_targets, mentioned_targets = self.resolve_conversation_targets(entity, primary_targets=primary_targets, message=message)

        # Player-side meta requests (e.g. "Insight Check on @Garrick about his
        # story") are handled privately and never broadcast to NPCs.
        if (isinstance(entity, PlayerCharacter)
                and is_player_insight_request(message)):
            return self.handle_player_insight_request(
                entity,
                message,
                explicit_targets=entity_targets,
                mentioned_targets=mentioned_targets,
            )

        if (isinstance(entity, PlayerCharacter)
                and is_player_perception_request(message)):
            return self.handle_player_perception_request(
                entity,
                message,
                explicit_targets=entity_targets,
                mentioned_targets=mentioned_targets,
            )

        # Player describe-what-I-see: during conversation, the player can ask
        # the NPC to describe what they see (facial expressions, body language,
        # clothing, injuries, environment). This triggers a perception-style
        # description using the NPC LLM, optionally with a perception check.
        if (isinstance(entity, PlayerCharacter)
                and is_player_describe_see_request(message)):
            return self.handle_player_describe_see_request(
                entity,
                message,
                explicit_targets=entity_targets,
                mentioned_targets=mentioned_targets,
            )

        original_spoken_message = spoken_message
        polished_message = None
        if dm_impersonate and (dm_impersonate_polish or polish_only):
            polished_message = self.polish_dm_impersonation_line(
                entity,
                spoken_message,
                conversation_system_prompt=conversation_system_prompt,
                language=language,
            )
            if polished_message:
                spoken_message = polished_message
            if polish_only:
                return {
                    'success': True,
                    'draft_message': original_spoken_message,
                    'polished_message': spoken_message,
                    'dm_impersonate': True,
                }, 200

        try:
            processed_conversations = entity.send_conversation(
                spoken_message,
                distance_ft=distance_ft,
                targets=entity_targets,
                language=language,
                volume=volume,
                actions=player_actions,
            )
        except TypeError:
            processed_conversations = entity.send_conversation(
                spoken_message,
                distance_ft=distance_ft,
                targets=entity_targets,
                language=language,
            )

        delivered_targets = unique_entities([
            receiver for receiver, _processed_message, directed_to in processed_conversations
            if receiver in (directed_to or [])
        ])
        for target in entity_targets:
            if (
                getattr(target, 'is_npc', lambda: False)()
                and getattr(target, 'dialog', False)
                and target not in delivered_targets
            ):
                delivered_targets.append(target)
        speaker_payload = self.conversation_payload(
            entity,
            spoken_message,
            targets=delivered_targets,
            volume=volume,
            distance_ft=distance_ft,
            mentioned_targets=mentioned_targets,
            language=language,
            actions=player_actions,
        )
        speaker_audience = self.conversation_audience_usernames(entity, processed_conversations, targets=delivered_targets)
        visual_whisper_usernames = set()
        if speaker_payload['volume'] == 'whisper':
            visual_whisper_usernames = self.conversation_visible_whisper_usernames(
                entity,
                audible_usernames=speaker_audience,
                targets=delivered_targets,
            )
            if visual_whisper_usernames:
                speaker_payload['visual_only_usernames'] = sorted(visual_whisper_usernames)
        if not isinstance(entity, PlayerCharacter):
            speaker_payload['tts_pending'] = True
        emitted_by_username = self.emit_conversation_to_usernames(
            speaker_audience | visual_whisper_usernames,
            speaker_payload,
            source_entity=entity,
        )
        if dm_impersonate:
            self._append_http_reply_for_requester(
                http_replies,
                normalized_requesting_username,
                emitted_by_username,
                speaker_payload,
                entity,
            )
        self._persist_conversation_line(
            entity,
            spoken_message,
            targets=delivered_targets,
            volume=volume,
            language=language,
            username=session.get('username'),
            narrative=player_actions,
        )

        # Bridge the spoken message into the battle event pipeline so any
        # readied "on_command" actions (e.g. familiar drinks a healing potion
        # when its master shouts "drink!") get a chance to fire. We pass the
        # raw message text plus the explicit recipient list so triggers can
        # match on phrase substrings.
        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None
        if battle is not None and getattr(battle, 'started', False) \
                and getattr(battle, 'readied_actions', None):
            try:
                battle.trigger_event('on_command', entity, {
                    'target': entity,
                    'message': message,
                    'targets': list(delivered_targets or []),
                    'volume': volume,
                })
            except Exception:
                # Conversation must never be blocked by a downstream
                # readied-action error.
                pass

        eligible_receivers = self.select_conversation_responders(
            processed_conversations,
            entity,
            targeted_entities=entity_targets,
            latest_message=spoken_message or '; '.join(player_actions),
            language=language,
            volume=volume,
        )
        self.logger.info(
            "[Talk] delivered_to=%s eligible_responders=%s",
            [getattr(t, 'entity_uid', t) for t in delivered_targets],
            [getattr(r, 'entity_uid', r) for r in eligible_receivers],
        )

        if isinstance(entity, PlayerCharacter) and delivered_targets:
            self.maybe_open_player_gift_transfer(
                entity,
                delivered_targets,
                original_spoken_message or message,
                actions=player_actions,
            )

        llm_conversation_handler = self.llm_conversation_handler
        relay_instruction = self.message_requests_npc_relay(message)
        forced_manager = getattr(self.current_game, 'forced_conversation_manager', None)
        if isinstance(entity, PlayerCharacter) and forced_manager is not None:
            forced_manager.on_player_reply(entity)
        manual_mode_npcs = []
        for receiver in eligible_receivers:
            if self.npc_conversation_mode_is_manual(receiver):
                manual_mode_npcs.append(receiver.entity_uid)
                self.logger.info(
                    "[Talk] skipping LLM reply for %s (manual conversation mode)",
                    receiver.label(),
                )
                continue
            summoned = self.message_summons_npc(message) and self._receiver_was_addressed(
                receiver,
                delivered_targets,
                mentioned_targets,
            )
            if summoned:
                approached = self.maybe_auto_approach_summoned_npc(
                    receiver,
                    entity,
                    message,
                    delivered_targets=delivered_targets,
                    mentioned_targets=mentioned_targets,
                )
                self.logger.info(
                    "[Talk] auto-approach for %s summoned=%s approached=%s",
                    receiver.label(),
                    summoned,
                    approached,
                )
            elif (
                self.message_invites_listener_approach(message)
                and self._receiver_was_addressed(receiver, delivered_targets, mentioned_targets)
            ):
                moved = self.ensure_npc_moves_to_speaker(receiver, entity)
                self.logger.info(
                    "[Talk] auto-move for %s invited=True moved=%s",
                    receiver.label(),
                    moved,
                )

            inject_notes = None
            if forced_manager is not None:
                notes = forced_manager.impatience_notes_for_turn(receiver)
                if notes:
                    inject_notes = notes
            escort_note = self.entity_rag_handler.escort_navigation_inject_note(receiver, message)
            if escort_note:
                inject_notes = list(inject_notes or [])
                inject_notes.append(escort_note)
            turn_result = self._run_single_npc_reply_turn(
                receiver,
                entity,
                conversation_system_prompt,
                summoned=summoned,
                relay_instruction=relay_instruction,
                llm_conversation_handler=llm_conversation_handler,
                http_replies=http_replies,
                normalized_requesting_username=normalized_requesting_username,
                nudge_on_no_response=receiver in delivered_targets,
                original_player_message=message,
                inject_system_messages=inject_notes,
                player_language=language,
            )
            if turn_result is None:
                continue

            max_depth = self._conversation_chain_depth()
            chain_receivers = self.select_npc_chain_responders(
                turn_result['response_conversations'],
                receiver,
                turn_result['reply_plan'],
                player_speaker=entity,
            )
            self.logger.info(
                "[Talk] npc_chain depth=%d/%s from %s eligible=%s",
                1,
                max_depth if max_depth is not None else 'unlimited',
                receiver.label(),
                [getattr(r, 'entity_uid', r) for r in chain_receivers],
            )
            for chain_receiver in chain_receivers:
                self._run_npc_chain_reply(
                    chain_receiver,
                    receiver,
                    conversation_system_prompt,
                    depth=1,
                    max_depth=max_depth,
                    llm_conversation_handler=llm_conversation_handler,
                    http_replies=http_replies,
                    normalized_requesting_username=normalized_requesting_username,
                    player_speaker=entity,
                )

        self.logger.info(
            "[Talk] complete speaker=%s elapsed_ms=%.1f",
            entity_id,
            (time.monotonic() - talk_started) * 1000.0,
        )
        response_payload = {
            'success': True,
            'resolved_target_ids': [target.entity_uid for target in delivered_targets],
            'mentioned_target_ids': [target.entity_uid for target in mentioned_targets],
            'volume': volume,
            'distance_ft': distance_ft,
        }
        if dm_impersonate:
            response_payload['dm_impersonate'] = True
            if polished_message:
                response_payload['draft_message'] = original_spoken_message
                response_payload['polished_message'] = polished_message
        if manual_mode_npcs:
            response_payload['manual_mode_npcs'] = manual_mode_npcs
        if http_replies:
            response_payload['replies'] = http_replies
        return response_payload, 200

    def _open_entity_dialog_for_targets(
        self,
        npc,
        target_entities,
        *,
        map_name=None,
        forced: bool = False,
        goal_arrival: bool = False,
    ) -> None:
        """Open the JRPG dialog UI for human-controlled owners of ``target_entities``."""
        from webapp.npc_item_exchange import human_controlled_usernames

        target_entities = [entity for entity in (target_entities or []) if entity is not None]
        if npc is None or not target_entities:
            return

        if map_name is None:
            try:
                battle_map = self.current_game.get_map_for_entity(npc)
                map_name = getattr(battle_map, 'name', None)
            except Exception:
                map_name = None

        seen_usernames = set()
        for target in target_entities:
            for username in human_controlled_usernames(self.current_game, target):
                if username in seen_usernames:
                    continue
                seen_usernames.add(username)
                for sid in self._sids_for_username(username):
                    payload = {
                        'type': 'open_entity_dialog',
                        'entity_id': npc.entity_uid,
                        'entity_name': npc.label(),
                        'map_name': map_name,
                        'forced': forced,
                    }
                    if goal_arrival:
                        payload['goal_arrival'] = True
                    self.socketio.emit('message', payload, to=sid)

    def initiate_npc_address(
        self,
        npc,
        target_entities,
        *,
        context=None,
        open_dialog=False,
        map_name=None,
        conversation_system_prompt=None,
        forced: bool = False,
        goal_arrival: bool = False,
    ) -> bool:
        """Have an NPC speak to player character(s) without a player initiating /talk."""
        from webapp.llm_handler import read_npc_system_prompt
        from webapp.blueprints.helpers.runtime_state import get_level

        target_entities = [entity for entity in (target_entities or []) if entity is not None]
        if not target_entities or npc is None:
            return False

        if self.npc_conversation_mode_is_manual(npc):
            if open_dialog:
                self._open_entity_dialog_for_targets(
                    npc,
                    target_entities,
                    map_name=map_name,
                    forced=forced,
                    goal_arrival=goal_arrival,
                )
            self.logger.info(
                "[Talk] skipping NPC initiative LLM for %s (manual conversation mode)",
                npc.label(),
            )
            return bool(open_dialog)

        if conversation_system_prompt is None:
            conversation_system_prompt = read_npc_system_prompt(get_level())

        llm_conversation_handler = self.llm_conversation_handler
        attributes = npc.ability_scores
        attributes_str = "\n".join([f"{key}: {value}" for key, value in attributes.items()])
        gear_summary = format_entity_gear_for_conversation(npc, self.game_session)
        appearance_summary = conversation_self_appearance(npc, session=self.game_session)
        system_prompt = conversation_system_prompt.format(
            backstory=npc.backstory(),
            name=npc.label(),
            attributes=attributes_str,
            alignment=npc.alignment().replace("_", " "),
            languages=", ".join(npc.languages()),
            gear=gear_summary,
            appearance=appearance_summary,
        )
        llm_conversation_handler.create_conversation(npc.entity_uid, system_prompt)
        try:
            llm_conversation_handler.conversations[npc.entity_uid]['system_prompt'] = system_prompt
        except Exception:
            pass
        llm_conversation_handler.update_conversation_history(npc.entity_uid, npc.conversation_buffer)
        if context:
            llm_conversation_handler.add_message(npc.entity_uid, 'system', str(context))

        if open_dialog:
            self._open_entity_dialog_for_targets(
                npc,
                target_entities,
                map_name=map_name,
                forced=forced,
                goal_arrival=goal_arrival,
            )

        speaker_pc = target_entities[0]
        response = llm_conversation_handler.generate_response(npc.entity_uid)
        reply_plan = self.entity_rag_handler.build_conversation_response_plan(
            response,
            npc,
            speaker_pc,
            llm_conversation_handler,
        )
        if reply_plan.get('skip'):
            self.logger.info(
                "[Talk] skipping NPC initiative from %s (plan marked skip)",
                npc.label(),
            )
            return False

        reply_distance_ft = speech_distance_for(mode=reply_plan['volume'])
        try:
            response_conversations = npc.send_conversation(
                reply_plan['message'],
                distance_ft=reply_distance_ft,
                targets=reply_plan['targets'] or target_entities,
                language=reply_plan['language'],
                volume=reply_plan['volume'],
                narrative=reply_plan.get('narrative') or [],
                segments=reply_plan.get('segments') or [],
                gallery_image_id=reply_plan.get('gallery_image_id'),
            )
        except TypeError:
            response_conversations = npc.send_conversation(
                reply_plan['message'],
                distance_ft=reply_distance_ft,
                targets=reply_plan['targets'] or target_entities,
                language=reply_plan['language'],
            )

        recipient_usernames = self.conversation_audience_usernames(
            npc,
            response_conversations,
            targets=reply_plan['targets'] or target_entities,
        )
        visual_whisper_usernames = set()
        if reply_plan['volume'] == 'whisper':
            visual_whisper_usernames = self.conversation_visible_whisper_usernames(
                npc,
                audible_usernames=recipient_usernames,
                targets=reply_plan['targets'] or target_entities,
            )

        conversation_payload = self.conversation_payload(
            npc,
            reply_plan['message'],
            targets=reply_plan['targets'] or target_entities,
            volume=reply_plan['volume'],
            distance_ft=reply_distance_ft,
            language=reply_plan['language'],
            visual_only_usernames=visual_whisper_usernames,
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
            gallery_image_id=reply_plan.get('gallery_image_id'),
        )
        conversation_payload['tts_pending'] = True
        conversation_payload['tts_emotion'] = reply_plan.get('tts_emotion')
        conversation_payload['tts_instruct'] = reply_plan.get('tts_instruct')

        self.emit_conversation_to_usernames(
            recipient_usernames | visual_whisper_usernames,
            conversation_payload,
            source_entity=npc,
        )
        self._persist_conversation_line(
            npc,
            reply_plan['message'],
            targets=reply_plan['targets'] or target_entities,
            volume=reply_plan['volume'],
            language=reply_plan['language'],
            narrative=reply_plan.get('narrative') or [],
            segments=reply_plan.get('segments') or [],
        )
        try:
            self.entity_rag_handler.apply_conversation_keywords_from_plan(
                reply_plan,
                npc,
                speaker=speaker_pc,
                llm_conversation_handler=llm_conversation_handler,
                raw_response=response,
            )
        except Exception as exc:
            self.logger.warning(
                "[Talk] NPC initiative keyword side effects failed for %s: %s",
                npc.label(),
                exc,
            )
        self.entity_rag_handler.apply_response_plan_directives(
            reply_plan, npc, speaker=speaker_pc,
        )
        try:
            forced_manager = getattr(self.current_game, 'forced_conversation_manager', None)
            if forced_manager is not None:
                forced_manager.maybe_resolve_after_npc_reply(npc, response, speaker=speaker_pc)
        except Exception as exc:
            self.logger.warning(
                "[Talk] forced conversation resolution failed for %s: %s",
                npc.label(),
                exc,
            )
        try:
            goal_arrival_manager = getattr(self.current_game, 'goal_arrival_manager', None)
            if goal_arrival_manager is not None:
                goal_arrival_manager.maybe_complete_after_npc_reply(npc, response)
        except Exception as exc:
            self.logger.warning(
                "[Talk] goal arrival resolution failed for %s: %s",
                npc.label(),
                exc,
            )

        self.logger.info(
            "[Talk] NPC initiative from %s to %s open_dialog=%s",
            npc.label(),
            [getattr(target, 'entity_uid', target) for target in target_entities],
            open_dialog,
        )
        return True

    def _requester_roles(self, username):
        if not username:
            return []
        roles_fn = self._roles_for_username_getter()
        if not callable(roles_fn):
            return []
        return roles_fn(username) or []

    def _requester_is_dm(self, username):
        return 'dm' in self._requester_roles(username)

    def _requester_can_control_entity(self, username, entity):
        if not username or entity is None:
            return False
        if self._requester_is_dm(username):
            return True
        entity_uid = getattr(entity, 'entity_uid', None)
        if not entity_uid:
            return False
        try:
            pov = self.current_game.get_pov_entity_for_user(username)
            if pov is not None and getattr(pov, 'entity_uid', None) == entity_uid:
                return True
        except Exception:
            pass
        try:
            for controlled in self.entities_controlled_by(username) or []:
                if getattr(controlled, 'entity_uid', None) == entity_uid:
                    return True
        except Exception:
            pass
        return False

    def _conversation_chain_depth(self):
        """Return the campaign-configured NPC chain reply depth.

        Reads ``conversation_chain_depth`` from ``game_properties`` (``game.yml``).
        Default is ``1`` (single NPC-to-NPC reply hop only).  Campaigns like
        *KickStart* may set ``0`` for unlimited banter until an NPC emits
        ``[NO_RESPONSE]``.
        """
        props = getattr(self.game_session, 'game_properties', None) or {}
        value = props.get('conversation_chain_depth')
        if value is None:
            value = os.environ.get('N20_CONVERSATION_CHAIN_DEPTH')
        try:
            depth = int(value) if value is not None else 1
        except (TypeError, ValueError):
            depth = 1
        if depth <= 0:
            return None
        return max(1, depth)

    def _run_npc_chain_reply(
        self,
        chain_receiver,
        previous_speaker,
        conversation_system_prompt,
        *,
        depth: int,
        max_depth: int,
        llm_conversation_handler,
        http_replies,
        normalized_requesting_username,
        player_speaker,
    ) -> None:
        """Run an NPC chain reply and recursively process further hops.

        Parameters
        ----------
        chain_receiver : Entity
            The NPC that will reply.
        previous_speaker : Entity
            The entity that prompted this reply (player or another NPC).
        conversation_system_prompt : str
            Reusable system prompt template.
        depth : int
            Current recursion depth (1 = first hop from player).
        max_depth : int
            Configured maximum chain depth.
        llm_conversation_handler : LLMConversationHandler
            Shared handler instance.
        http_replies : list | None
            Accumulator for HTTP reply payloads.
        normalized_requesting_username : str | None
        player_speaker : Entity
            Original player speaker (preserved for context).
        """
        if max_depth is not None and depth >= max_depth:
            self.logger.info(
                "[Talk] npc_chain depth limit reached at depth=%d (max=%d) — "
                "skipping further hops from %s",
                depth,
                max_depth,
                getattr(chain_receiver, 'entity_uid', chain_receiver),
            )
            return

        if self.npc_conversation_mode_is_manual(chain_receiver):
            self.logger.info(
                "[Talk] npc_chain skipping %s (manual conversation mode)",
                chain_receiver.label(),
            )
            return

        turn_result = self._run_single_npc_reply_turn(
            chain_receiver,
            previous_speaker,
            conversation_system_prompt,
            llm_conversation_handler=llm_conversation_handler,
            http_replies=http_replies,
            normalized_requesting_username=normalized_requesting_username,
            player_speaker=player_speaker,
            npc_chain_reply=True,
        )
        if turn_result is None:
            return

        next_speaker = chain_receiver
        chain_receivers = self.select_npc_chain_responders(
            turn_result['response_conversations'],
            next_speaker,
            turn_result['reply_plan'],
            player_speaker=player_speaker,
        )
        self.logger.info(
            "[Talk] npc_chain depth=%d from %s eligible=%s",
            depth + 1,
            next_speaker.label(),
            [getattr(r, 'entity_uid', r) for r in chain_receivers],
        )
        for next_chain_receiver in chain_receivers:
            self._run_npc_chain_reply(
                next_chain_receiver,
                next_speaker,
                conversation_system_prompt,
                depth=depth + 1,
                max_depth=max_depth,
                llm_conversation_handler=llm_conversation_handler,
                http_replies=http_replies,
                normalized_requesting_username=normalized_requesting_username,
                player_speaker=player_speaker,
            )

    def npc_conversation_mode_is_manual(self, entity):
        from webapp.npc_conversation_mode import is_npc_manual_conversation
        game_session = getattr(self, 'game_session', None)
        if game_session is None:
            return False
        return is_npc_manual_conversation(game_session, entity)

    def _impersonatable_npcs(self, anchor_entity=None):
        battle_map = None
        if anchor_entity is not None:
            try:
                battle_map = self.current_game.get_map_for_entity(anchor_entity)
            except Exception:
                battle_map = None
        if battle_map is None:
            try:
                battle_map = self.current_game.battle_map
            except Exception:
                battle_map = None
        if battle_map is None:
            return []

        npcs = []
        for ent in getattr(battle_map, 'entities', []) or []:
            if not callable(getattr(ent, 'is_npc', None)) or not ent.is_npc():
                continue
            npcs.append({
                'id': ent.entity_uid,
                'name': entity_label(ent),
            })
        npcs.sort(key=lambda entry: (entry.get('name') or '').lower())
        return npcs

    def polish_dm_impersonation_line(
        self,
        npc,
        draft,
        *,
        conversation_system_prompt,
        language='common',
    ):
        draft = str(draft or '').strip()
        if not draft:
            return None

        controller = self.llm_conversation_handler
        if controller is None:
            return None

        send_message = getattr(controller, '_effective_send_message', None)
        if not callable(send_message):
            return None

        attributes = getattr(npc, 'ability_scores', {}) or {}
        attributes_str = '\n'.join(f'{key}: {value}' for key, value in attributes.items())
        gear_summary = format_entity_gear_for_conversation(npc, self.game_session)
        appearance_summary = conversation_self_appearance(npc, session=self.game_session)
        try:
            base_prompt = conversation_system_prompt.format(
                backstory=npc.backstory(),
                name=npc.label(),
                attributes=attributes_str,
                alignment=str(npc.alignment()).replace('_', ' '),
                languages=', '.join(npc.languages() or ['common']),
                gear=gear_summary,
                appearance=appearance_summary,
            )
        except Exception:
            base_prompt = f'You are {entity_label(npc)}.'

        recent_lines = []
        for entry in (getattr(npc, 'conversation_buffer', None) or [])[-5:]:
            line = str((entry or {}).get('message') or '').strip()
            if line:
                recent_lines.append(line)

        polish_instructions = (
            'Rewrite the DM draft into a single spoken line for this NPC. '
            'Match vocabulary, tone, and personality. '
            'Output ONLY the rewritten spoken words with no quotes, brackets, '
            'stage directions, or commentary. Preserve the draft meaning.'
        )
        import json as _json
        user_payload = {
            'draft': draft,
            'language': language,
            'recent_speech': recent_lines,
        }
        messages = [
            {'role': 'system', 'content': f'{base_prompt}\n\n{polish_instructions}'},
            {'role': 'user', 'content': _json.dumps(user_payload, ensure_ascii=True)},
        ]
        try:
            raw_response = send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'dm_impersonation_polish',
                },
            )
        except Exception as exc:
            self.logger.debug(
                'DM impersonation polish failed for %s: %s',
                getattr(npc, 'entity_uid', npc),
                exc,
            )
            return None

        polished = str(raw_response or '').strip()
        polished = re.sub(r'\[.*?\]', '', polished).strip()
        polished = polished.strip('"\'')
        if not polished or is_npc_llm_failure_response(polished):
            return None
        return polished[:500]

    def polish_pc_in_character_line(
        self,
        pc,
        draft,
        *,
        language='common',
    ):
        """Rewrite a player draft into an in-character spoken line using the NPC LLM."""
        draft = str(draft or '').strip()
        if not draft or not isinstance(pc, PlayerCharacter):
            return None

        controller = self.llm_conversation_handler
        if controller is None:
            return None

        send_message = getattr(controller, '_effective_send_message', None)
        if not callable(send_message):
            return None

        from natural20.utils.character_profile import format_profile_for_llm, profile_for_entity

        profile_text = format_profile_for_llm(profile_for_entity(pc), max_field_chars=500)
        props = getattr(pc, 'properties', None) or {}
        background_key = str(props.get('background') or '').strip()
        background_label = background_key.replace('_', ' ').title() if background_key else ''
        try:
            class_desc = pc.class_descriptor() if callable(getattr(pc, 'class_descriptor', None)) else ''
        except Exception:
            class_desc = ''
        try:
            race_label = pc.race() if callable(getattr(pc, 'race', None)) else ''
        except Exception:
            race_label = str(props.get('race') or '')

        character_context_parts = [
            f'Name: {entity_label(pc)}',
        ]
        if race_label:
            character_context_parts.append(f'Race: {race_label}')
        if class_desc:
            character_context_parts.append(f'Class: {class_desc}')
        if background_label:
            character_context_parts.append(f'Background: {background_label}')
        if profile_text:
            character_context_parts.append(profile_text)
        character_context = '\n'.join(character_context_parts)

        recent_lines = []
        for entry in (getattr(pc, 'conversation_buffer', None) or [])[-5:]:
            line = str((entry or {}).get('message') or '').strip()
            if line:
                recent_lines.append(line)

        polish_instructions = (
            'Rewrite the player draft into a single in-character line for this hero. '
            'Shape vocabulary, tone, and manner using their personality traits, ideals, '
            'bonds, flaws, backstory, background, race, and class. '
            'Output ONLY the rewritten line with no quotes, brackets, labels, or commentary. '
            'Preserve the draft meaning and intent. '
            'Keep any *action* text wrapped in asterisks exactly as actions; polish spoken words only.'
        )
        import json as _json
        user_payload = {
            'draft': draft,
            'language': language,
            'recent_speech': recent_lines,
        }
        messages = [
            {
                'role': 'system',
                'content': (
                    f'You are helping a player speak in character.\n\n'
                    f'{character_context}\n\n{polish_instructions}'
                ),
            },
            {'role': 'user', 'content': _json.dumps(user_payload, ensure_ascii=True)},
        ]
        try:
            raw_response = send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'pc_in_character_polish',
                },
            )
        except Exception as exc:
            self.logger.debug(
                'PC in-character polish failed for %s: %s',
                getattr(pc, 'entity_uid', pc),
                exc,
            )
            return None

        polished = str(raw_response or '').strip()
        polished = re.sub(r'\[.*?\]', '', polished).strip()
        polished = polished.strip('"\'')
        if not polished or is_npc_llm_failure_response(polished):
            return None
        return polished[:500]

    def conversation_presence_response(
        self,
        entity_id=None,
        username=None,
        volume=None,
        range_value=None,
        *,
        list_impersonatable=False,
    ):
        if list_impersonatable:
            if not self._requester_is_dm(username):
                return {'error': 'Forbidden'}, 403
            return {
                'impersonatable_npcs': self._impersonatable_npcs(),
                'speaker': None,
            }, 200

        if not entity_id and username:
            current_pov = self.current_game.get_pov_entity_for_user(username)
            entity_id = getattr(current_pov, 'entity_uid', None)

        if not entity_id:
            return {'error': 'Entity ID is required'}, 400

        entity = self.current_game.get_entity_by_uid(entity_id)
        if not entity:
            cache_key = (entity_id, volume, range_value)
            self._conversation_presence_cache.pop(cache_key, None)
            return {'error': 'Entity not found'}, 404

        cache_key = (entity_id, volume, range_value)
        now = time.monotonic()
        cached = self._conversation_presence_cache.get(cache_key)
        if cached is not None:
            age = now - cached['_ts']
            if age < self._CONVERSANCE_PRESENCE_CACHE_TTL:
                _timing_log(
                    f"conv_presence:cache_hit entity={entity_id} age={age:.1f}s"
                )
                cached_result = cached.copy()
                del cached_result['_ts']
                return cached_result, 200

        _t_norm = time.perf_counter()
        normalized_volume = normalize_speech_mode(mode=volume, distance_ft=range_value)
        distance_ft = speech_distance_for(mode=normalized_volume, distance_ft=range_value)
        _timing_log(
            f"conv_presence:normalization={(time.perf_counter()-_t_norm)*1000:.1f}ms "
            f"entity={entity_id} volume={normalized_volume} dist={distance_ft}ft"
        )
        
        _t_rag = time.perf_counter()
        audible = self.entity_rag_handler.get_nearby_entities(entity, distance_ft, volume=normalized_volume, include_extended=True)
        _timing_log(
            f"conv_presence:rag_lookup={(time.perf_counter()-_t_rag)*1000:.1f}ms "
            f"entity={entity_id} audible_count={len(audible) if audible else 0}"
        )

        reachable_entities = [entry for entry in audible if entry.get('reachable_now')]
        louder_voice_entities = [entry for entry in audible if entry.get('status') == 'requires_louder_voice']
        heard_only_entities = [entry for entry in audible if entry.get('status') == 'too_far']

        result = {
            'speaker': {
                'id': entity.entity_uid,
                'name': entity_label(entity),
                'languages': entity.languages() or ['common'],
            },
            'volume': normalized_volume,
            'distance_ft': distance_ft,
            'entities': audible,
            'reachable_entities': reachable_entities,
            'requires_louder_voice_entities': louder_voice_entities,
            'heard_only_entities': heard_only_entities,
        }
        if self._requester_is_dm(username):
            result['impersonatable_npcs'] = self._impersonatable_npcs(entity)

        self._conversation_presence_cache[cache_key] = result.copy()
        self._conversation_presence_cache[cache_key]['_ts'] = now
        return result, 200

    def nearby_entities_response(self, entity_id, volume=None, range_value=None):
        normalized_volume = normalize_speech_mode(mode=volume, distance_ft=range_value)
        range_ft = speech_distance_for(mode=normalized_volume, distance_ft=range_value)

        if not entity_id:
            return {'error': 'Entity ID is required'}, 400

        entity = self.current_game.get_entity_by_uid(entity_id)
        if not entity:
            return {'error': 'Entity not found'}, 404

        response = self.entity_rag_handler.get_nearby_entities(entity, range_ft, volume=normalized_volume)
        return {'entities': response}, 200


def register_conversation_routes(app, conversation_service, conversation_system_prompt_getter):
    def dialog_history():
        payload, status_code = conversation_service.dialog_history_response(
            request.args.get('entity_id'),
            request.args.get('entity_pov', None),
        )
        return jsonify(payload), status_code

    def talk():
        payload, status_code = conversation_service.talk_response(
            request.get_json() or {},
            conversation_system_prompt_getter(),
            requesting_username=session.get('username'),
        )
        return jsonify(payload), status_code

    def talk_continue():
        payload, status_code = conversation_service.talk_continue_response(
            request.get_json() or {},
            requesting_username=session.get('username'),
        )
        return jsonify(payload), status_code

    def talk_item_transfer_complete():
        payload, status_code = conversation_service.handle_npc_gift_transfer_complete(
            request.get_json() or {},
            conversation_system_prompt_getter(),
            requesting_username=session.get('username'),
        )
        return jsonify(payload), status_code

    def conversation_presence():
        payload, status_code = conversation_service.conversation_presence_response(
            entity_id=request.args.get('entity_id'),
            username=session.get('username'),
            volume=request.args.get('volume'),
            range_value=request.args.get('range'),
            list_impersonatable=request.args.get('list_impersonatable') in ('1', 'true', 'yes'),
        )
        return jsonify(payload), status_code

    def nearby_entities():
        payload, status_code = conversation_service.nearby_entities_response(
            entity_id=request.args.get('entity_id'),
            volume=request.args.get('volume'),
            range_value=request.args.get('range'),
        )
        return jsonify(payload), status_code

    app.add_url_rule('/dialog_history', endpoint='dialog_history', view_func=dialog_history, methods=['GET'])
    app.add_url_rule('/talk', endpoint='talk', view_func=talk, methods=['POST'])
    app.add_url_rule('/talk/continue', endpoint='talk_continue', view_func=talk_continue, methods=['POST'])
    app.add_url_rule(
        '/talk/item_transfer_complete',
        endpoint='talk_item_transfer_complete',
        view_func=talk_item_transfer_complete,
        methods=['POST'],
    )
    app.add_url_rule('/conversation_presence', endpoint='conversation_presence', view_func=conversation_presence, methods=['GET'])
    app.add_url_rule('/nearby_entities', endpoint='nearby_entities', view_func=nearby_entities, methods=['GET'])