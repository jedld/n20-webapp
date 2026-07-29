"""
Entity RAG Handler

This module handles the Retrieval-Augmented Generation (RAG) aspects of entity conversations,
including inventory queries, observation requests, and language parsing.
"""

import re
import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from typing import List, Tuple, Dict, Any, Optional
from natural20.action import Action
from natural20.actions.interact_action import InteractAction
from natural20.entity import Entity
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from webapp.blueprints.helpers.campaign_config import (
    character_description_suppressed_for,
    dialog_description_suppressed_for,
)
from natural20.concern.generic_event_handler import GenericEventHandler
from natural20.utils.animal_communication import (
    animal_communication_expires_at,
    animal_communication_guidance_lines,
    grant_animal_communication,
)
from natural20.utils.conversation_offers import (
    accept_effect_for_item,
    adjust_item_offer_target,
    canonical_item_slug,
    evaluate_offer_block,
    merged_offer_configs,
    offer_guidance_lines,
    on_accept_auto_use,
    record_completed_item_offer,
)
from natural20.utils.conversation_witness import (
    format_witnessed_actions_summary,
    log_witnessed_action,
    witnessed_action_lines,
)
from natural20.utils.conversation import (
    SPEECH_MODE_ORDER,
    audible_entities,
    conversation_reachability,
    entity_label,
    mention_handle_for,
    normalize_speech_mode,
    resolve_mention_targets,
    unique_entities,
)
from webapp.npc_movement import NpcMovementHelper
from webapp.npc_item_exchange import (
    adjudicate_item_exchange_with_dm_llm,
    apply_transfer_payload,
    find_container_with_item,
    format_container_contents,
    format_container_list_summary,
    give_from_container_to_entity,
    container_interaction_range_ft,
    container_is_accessible,
    human_controlled_usernames,
    is_human_controlled,
    is_map_container,
    nearby_ground_items,
    nearby_map_containers,
    open_item_transfer_dialog,
    pickup_ground_item,
    has_active_npc_gift_transfer,
    pop_npc_gift_transfer_session,
    register_npc_gift_transfer_session,
    retrieve_from_container,
    store_in_container,
)
from natural20.npc_memory_store import NpcMemoryStore
from natural20.utils.outward_appearance import resolve_outward_appearance
from natural20.utils.object_annotations import (
    format_annotation_summary,
    format_annotations_for_subject,
)

logger = logging.getLogger('werkzeug')

_NPC_MOVEMENT_MAX_ATTEMPTS = max(5, int(os.environ.get('NPC_MOVEMENT_MAX_ATTEMPTS', '40')))
_NPC_MOVEMENT_STUCK_TICKS = max(2, int(os.environ.get('NPC_MOVEMENT_STUCK_TICKS', '8')))
_NPC_MOVEMENT_LLM_STUCK_TICKS = max(1, int(os.environ.get('NPC_MOVEMENT_LLM_STUCK_TICKS', '1')))
CONTROL_DIRECTIVE_PATTERN = re.compile(
    r'\[(no_response|to|volume|emotion|tts|tts_instruct|delivery)(?:\s*:\s*([^\]]*))?\]',
    re.IGNORECASE,
)
ACTION_DIRECTIVE_PATTERN = re.compile(
    r'\[(approach|move|follow|stop_move|stop_follow|remember|recall|interact|request_check|set_goal|goal_complete|goal_give_up|offer_item|pickup|trade|open_shop|request_item|accept_gift|retrieve|store)(?:\s*:\s*([^\]]*))?\]',
    re.IGNORECASE,
)
LIST_CONTAINER_PATTERN = re.compile(r'\[LIST_CONTAINER(?:\s*:\s*([^\]]*))?\]', re.IGNORECASE)
ANNOTATIONS_PATTERN = re.compile(r'\[ANNOTATIONS(?:\s*:\s*([^\]]*))?\]', re.IGNORECASE)
RECALL_DIRECTIVE_PATTERN = re.compile(r'\[recall(?:\s*:\s*([^\]]*))?\]', re.IGNORECASE)

SOCIAL_APPROACH_MAX_FT = 5

PERCEPTION_EMPTY_DESCRIPTION = (
    'You study the scene, but nothing new stands out to you right now.'
)

VISIBLE_STATUS_PHRASES = {
    'poisoned': 'They look unwell—sweating and pale.',
    'frightened': 'Fear shows in their wide eyes and tense posture.',
    'stunned': 'They seem dazed and slow to react.',
    'paralyzed': 'They hold unnaturally still.',
    'restrained': 'They move stiffly, as if held back.',
    'grappled': 'They strain against something holding them.',
    'prone': 'They are on the ground.',
    'unconscious': 'They appear unconscious.',
    'invisible': 'You can barely make out their outline.',
    'petrified': 'They stand rigid, like stone.',
    'blinded': 'They squint and turn their head as if searching for sights.',
    'deafened': 'They lean in, straining to hear.',
    'charmed': 'Their manner seems oddly placid and agreeable.',
    'exhaustion': 'Exhaustion weighs on their face and posture.',
}

REQUEST_CHECK_SKILLS = frozenset({
    'acrobatics', 'animal_handling', 'arcana', 'athletics', 'deception', 'history',
    'insight', 'intimidation', 'investigation', 'medicine', 'nature', 'perception',
    'performance', 'persuasion', 'religion', 'sleight_of_hand', 'stealth', 'survival',
})
INSIGHT_DIRECTIVE_PATTERN = re.compile(r'\[insight(?:\s*:\s*([^\]]*))?\]', re.IGNORECASE)
ASIDE_DIRECTIVE_PATTERN = re.compile(r'\[(aside|narration|stage_direction)(?:\s*:\s*([^\]]*))?\]', re.IGNORECASE)
NARRATIVE_FIRST_PERSON_SUBSTITUTIONS = [
    (re.compile(r"\bI['\u2019]m\b", re.IGNORECASE), 'they are'),
    (re.compile(r"\bI['\u2019]ve\b", re.IGNORECASE), 'they have'),
    (re.compile(r"\bI['\u2019]ll\b", re.IGNORECASE), 'they will'),
    (re.compile(r"\bI['\u2019]d\b", re.IGNORECASE), 'they would'),
    (re.compile(r"\bwe['\u2019]re\b", re.IGNORECASE), 'they are'),
    (re.compile(r"\bwe['\u2019]ve\b", re.IGNORECASE), 'they have'),
    (re.compile(r"\bwe['\u2019]ll\b", re.IGNORECASE), 'they will'),
    (re.compile(r"\bwe['\u2019]d\b", re.IGNORECASE), 'they would'),
    (re.compile(r"\bI\b", re.IGNORECASE), 'they'),
    (re.compile(r"\bme\b", re.IGNORECASE), 'them'),
    (re.compile(r"\bmy\b", re.IGNORECASE), 'their'),
    (re.compile(r"\bmine\b", re.IGNORECASE), 'theirs'),
    (re.compile(r"\bmyself\b", re.IGNORECASE), 'themself'),
    (re.compile(r"\bwe\b", re.IGNORECASE), 'they'),
    (re.compile(r"\bus\b", re.IGNORECASE), 'them'),
    (re.compile(r"\bour\b", re.IGNORECASE), 'their'),
    (re.compile(r"\bours\b", re.IGNORECASE), 'theirs'),
    (re.compile(r"\bourselves\b", re.IGNORECASE), 'themselves'),
]
NON_OBSERVABLE_TRUST_PATTERN = re.compile(
    r"\b(?:do(?:es)?\s+not\s+trust|don['\u2019]?t\s+trust|cannot\s+trust|can['\u2019]?t\s+trust|"
    r"distrusts?|mistrusts?|trusts?)\b",
    re.IGNORECASE,
)


class EntityRAGHandler:
    """
    Handles RAG (Retrieval-Augmented Generation) operations for entity conversations.
    
    This class extracts and processes special commands in entity responses that require
    real-time game state information, such as inventory queries and observation requests.
    """
    
    def __init__(self, game_session: Session, current_game):
        """
        Initialize the Entity RAG Handler.
        
        Args:
            game_session: The current game session
            current_game: The current game instance
        """
        self.game_session = game_session
        self.current_game = current_game
        self.movement_helper = NpcMovementHelper(game_session, current_game)

    def _npc_memory_store(self) -> NpcMemoryStore:
        return getattr(self.current_game, 'npc_memory_store', None)

    def npc_memory_context_summary(self, entity: Entity, *, limit: int = 8) -> str:
        store = self._npc_memory_store()
        if store is None or entity is None:
            return ''
        try:
            return store.format_context_summary(getattr(entity, 'entity_uid', ''), limit=limit)
        except Exception:
            return ''

    @staticmethod
    def _entity_log_handles(entity: Entity) -> set:
        handles = set()
        label = entity_label(entity).lower()
        if label:
            handles.add(label)
            for token in re.split(r'\s+', label):
                if len(token) >= 3:
                    handles.add(token)
        props = getattr(entity, 'properties', None) or {}
        for key in ('handle', 'name', 'kind'):
            value = props.get(key)
            if value:
                handles.add(str(value).lower().lstrip('@'))
        uid = str(getattr(entity, 'entity_uid', '') or '')
        if uid:
            handles.add(uid.lower())
            for part in uid.split('_'):
                if len(part) >= 3:
                    handles.add(part)
        return {h for h in handles if h}

    def conversation_buffer_context_summary(self, entity: Entity, *, limit: int = 40) -> str:
        buffer = list(getattr(entity, 'conversation_buffer', None) or [])
        if not buffer:
            return ''
        lines: List[str] = []
        for entry in buffer[-max(1, int(limit)):]:
            if not isinstance(entry, dict):
                continue
            message = str(entry.get('message') or '').strip()
            if not message:
                continue
            source = entry.get('source')
            speaker = entity_label(source) if source is not None else 'someone'
            lines.append(f'- {speaker}: {message}')
        if not lines:
            return ''
        return (
            '\nRecent overheard and direct conversation (from your short-term memory buffer):\n'
            + '\n'.join(lines)
            + '\n'
        )

    def campaign_log_context_summary(
        self,
        entity: Entity,
        *,
        combat_limit: int = 20,
        conversation_limit: int = 40,
    ) -> str:
        db = getattr(self.current_game, 'campaign_log_db', None)
        uid = getattr(entity, 'entity_uid', None)
        if db is None or not uid:
            return ''
        try:
            buckets = db.context_lines_for_entity(
                uid,
                handles=self._entity_log_handles(entity),
                combat_limit=combat_limit,
                conversation_limit=conversation_limit,
            )
        except Exception:
            return ''

        parts: List[str] = []
        combat_lines = buckets.get('combat') or []
        if combat_lines:
            parts.append(
                'Recent combat you witnessed or were involved in (campaign log):\n'
                + '\n'.join(f'- {line}' for line in combat_lines)
            )
        conversation_lines = buckets.get('conversation') or []
        if conversation_lines:
            parts.append(
                'Recent conversations you spoke in, were addressed in, or overheard (campaign log):\n'
                + '\n'.join(f'- {line}' for line in conversation_lines)
            )
        if not parts:
            return ''
        return '\n' + '\n\n'.join(parts) + '\n'

    def npc_recap_context_block(
        self,
        entity: Entity,
        *,
        witnessed_limit: int = 20,
        memory_limit: int = 12,
        combat_limit: int = 20,
        conversation_limit: int = 40,
        buffer_limit: int = 40,
    ) -> str:
        """Durable and recent context for long-rest / autonomous NPC ticks."""
        parts: List[str] = []
        memory_text = self.npc_memory_context_summary(entity, limit=memory_limit)
        if memory_text:
            parts.append(memory_text)
        witnessed_text = self.witnessed_events_summary(entity, limit=witnessed_limit)
        if witnessed_text:
            parts.append(witnessed_text)
        campaign_text = self.campaign_log_context_summary(
            entity,
            combat_limit=combat_limit,
            conversation_limit=conversation_limit,
        )
        if campaign_text:
            parts.append(campaign_text)
        buffer_text = self.conversation_buffer_context_summary(entity, limit=buffer_limit)
        if buffer_text:
            parts.append(buffer_text)
        if not parts:
            return ''
        return '\n' + ''.join(parts)
    
    def process_entity_response(self, response: str, receiver: Entity, speaker: Entity = None, llm_conversation_handler=None) -> Tuple[str, str]:
        """
        Process an entity response for RAG commands and return the cleaned response and language.
        
        Args:
            response: The raw response from the LLM
            receiver: The entity receiving the response
            llm_conversation_handler: The LLM conversation handler instance

        Returns:
            Tuple of (language, cleaned_response)
        """
        if llm_conversation_handler is None and speaker is not None and hasattr(speaker, 'generate_response'):
            llm_conversation_handler = speaker
            speaker = None

        plan = self.build_conversation_response_plan(
            response,
            receiver,
            speaker=speaker,
            llm_conversation_handler=llm_conversation_handler,
        )
        return plan['language'], plan['message']

    @staticmethod
    def _normalize_keyword_text(text: str) -> str:
        normalized = re.sub(r'[^a-z0-9\s]', ' ', str(text or '').lower())
        return ' '.join(normalized.split())

    def _keyword_matched_by_text(self, response_text: str, keyword: str) -> bool:
        response_norm = self._normalize_keyword_text(response_text)
        keyword_norm = self._normalize_keyword_text(keyword)
        if not response_norm or not keyword_norm:
            return False
        if keyword_norm in response_norm:
            return True

        # Allow small connective words between keyword tokens, e.g.
        # "hunters closing in" matching "hunters are closing in".
        response_tokens = response_norm.split()
        keyword_tokens = keyword_norm.split()
        if len(keyword_tokens) <= 1:
            return False

        idx = 0
        for token in response_tokens:
            if token == keyword_tokens[idx]:
                idx += 1
                if idx == len(keyword_tokens):
                    return True
        return False

    def _llm_keyword_matches(self, response: str, keyword_entries: List[Dict[str, Any]], llm_conversation_handler) -> List[str]:
        if llm_conversation_handler is None:
            return []

        llm_handler = getattr(llm_conversation_handler, 'llm_hander', None)
        if llm_handler is None:
            return []

        candidate_keywords = [str(entry.get('keyword', '')).strip() for entry in keyword_entries if str(entry.get('keyword', '')).strip()]
        if not candidate_keywords:
            return []

        messages = [
            {
                'role': 'system',
                'content': (
                    'You detect whether an NPC reply semantically expresses configured trigger phrases. '
                    'Return JSON only with key matched_keywords as an array of exact keyword strings from the provided list. '
                    'Do not invent new keywords.'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({
                    'response': response,
                    'keywords': candidate_keywords,
                }, ensure_ascii=True),
            },
        ]

        try:
            raw_response = llm_handler.send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'semantic_keyword_match',
                },
            )
            text = str(raw_response or '').strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                text = match.group(0)
            payload = json.loads(text)
        except Exception:
            return []

        matched = payload.get('matched_keywords')
        if not isinstance(matched, list):
            return []

        allowed = {keyword for keyword in candidate_keywords}
        return [str(keyword).strip() for keyword in matched if str(keyword).strip() in allowed]

    def parse_response_controls(self, response: str) -> Dict[str, Any]:
        controls = {
            'no_response': False,
            'target_spec': None,
            'volume': None,
            'emotion': None,
            'tts_instruct': None,
            'response': response or '',
        }

        def _replace(match):
            directive = (match.group(1) or '').strip().lower()
            value = (match.group(2) or '').strip()
            if directive == 'no_response':
                controls['no_response'] = True
            elif directive == 'to' and value:
                controls['target_spec'] = value
            elif directive == 'volume' and value:
                controls['volume'] = normalize_speech_mode(mode=value)
            elif directive == 'emotion' and value:
                emotion, instruct = self._split_emotion_or_instruct(value)
                if emotion and not controls['emotion']:
                    controls['emotion'] = emotion
                if instruct and not controls['tts_instruct']:
                    controls['tts_instruct'] = instruct
            elif directive in ('tts', 'tts_instruct', 'delivery') and value:
                controls['tts_instruct'] = value
            return ''

        controls['response'] = CONTROL_DIRECTIVE_PATTERN.sub(_replace, controls['response']).strip()
        return controls

    @staticmethod
    def _split_emotion_or_instruct(value: str) -> Tuple[Optional[str], Optional[str]]:
        """Treat short emotion tokens as emotion; longer free-form as TTS instruct."""
        raw = (value or '').strip()
        if not raw:
            return None, None

        aliases = {
            'shout': 'shouting',
            'yell': 'yelling',
            'yelling': 'yelling',
            'shouting': 'shouting',
            'soft': 'whisper',
            'hushed': 'whisper',
            'quiet': 'whisper',
            'scared': 'fearful',
            'afraid': 'fearful',
            'terrified': 'fearful',
            'fearful': 'fearful',
            'sobbing': 'sad',
            'crying': 'sad',
            'sad': 'sad',
            'happy': 'cheerful',
            'joy': 'cheerful',
            'cheerful': 'cheerful',
            'laughing': 'laughing',
            'mad': 'angry',
            'angry': 'angry',
            'furious': 'angry',
            'enraged': 'angry',
            'nervous': 'nervous',
            'anxious': 'anxious',
            'worried': 'anxious',
            'urgent': 'anxious',
            'calm': 'calm',
            'neutral': 'calm',
            'disgusted': 'disgusted',
            'surprised': 'surprised',
            'lazy': 'lazy',
        }
        known = set(aliases.keys()) | set(aliases.values()) | {
            'hostile', 'rage', 'hiding', 'secretive', 'muttering', 'excited',
            'joyful', 'gleeful', 'merry', 'enthusiastic', 'miserable', 'sorrowful',
            'depressed', 'grieving', 'mourning', 'weeping', 'measured', 'composed',
            'serene', 'peaceful', 'stoic', 'monotone', 'formal', 'diplomatic',
            'panicked', 'dreading', 'contemptuous', 'sneering', 'shocked',
            'astonished', 'drawling', 'mumbling', 'slurred', 'stuttering',
            'stammering', 'hesitant', 'uncertain',
        }

        compact = re.sub(r'[\s\-]+', '_', raw.lower()).strip('_')
        # Short token → emotion; longer / punctuated → free-form instruct
        if (
            compact in known
            and ',' not in raw
            and len(raw.split()) <= 2
            and re.fullmatch(r'[A-Za-z_\-\s]+', raw)
        ):
            return aliases.get(compact, compact.replace('_', '')), None
        return None, raw

    def get_conversation_targets(self, receiver: Entity, speaker: Entity = None) -> List[Entity]:
        candidates = []
        if speaker is not None and speaker != receiver:
            candidates.append(speaker)

        try:
            battle_map = self.current_game.get_map_for_entity(receiver)
        except Exception:
            battle_map = None

        if battle_map is not None:
            try:
                reachability = conversation_reachability(receiver, battle_map, mode='shout')
            except Exception:
                reachability = []

            for entry in reachability:
                target = entry.get('entity')
                if target is not None and target != receiver:
                    candidates.append(target)

        return unique_entities(candidates)

    def resolve_response_targets(self, receiver: Entity, speaker: Entity = None, target_spec: Optional[str] = None) -> List[Entity]:
        candidates = self.get_conversation_targets(receiver, speaker=speaker)
        if not target_spec:
            return [speaker] if speaker is not None else []

        resolved_targets = []
        mention_tokens = []
        include_all = False
        for token in re.split(r'[,;\n]+', target_spec):
            normalized = token.strip()
            if not normalized:
                continue
            lowered = normalized.lower().lstrip('@')
            if lowered in {'speaker', 'you'} and speaker is not None:
                resolved_targets.append(speaker)
            elif lowered == 'all':
                include_all = True
            else:
                mention_tokens.append(f"@{lowered}")

        if include_all:
            resolved_targets.extend(candidates)
        if mention_tokens:
            resolved_targets.extend(resolve_mention_targets(' '.join(mention_tokens), candidates))

        resolved_targets = [target for target in unique_entities(resolved_targets) if target != receiver]
        if resolved_targets:
            return resolved_targets
        return [speaker] if speaker is not None else []

    def _log_conversation_plan(self, receiver: Entity, event: str, level: int = logging.INFO, **fields) -> None:
        receiver_name = entity_label(receiver) if receiver is not None else 'unknown'
        extras = ' '.join(f"{key}={value!r}" for key, value in fields.items())
        message = f"[ConversationPlan] {receiver_name}: {event}"
        if extras:
            message = f"{message} ({extras})"
        logger.log(level, message)

    def plan_response_volume(self, receiver: Entity, targets: List[Entity], volume: Optional[str] = None) -> Tuple[Optional[str], List[Entity]]:
        if not targets:
            return None, []

        try:
            battle_map = self.current_game.get_map_for_entity(receiver)
        except Exception:
            battle_map = None

        if battle_map is None:
            return normalize_speech_mode(mode=volume), list(targets)

        if volume:
            selected_mode = normalize_speech_mode(mode=volume)
            reachability = {
                entry['entity'].entity_uid: entry
                for entry in conversation_reachability(receiver, battle_map, mode=selected_mode)
            }
            reachable_targets = [
                target for target in targets
                if reachability.get(target.entity_uid, {}).get('reachable_now')
            ]
            return (selected_mode if reachable_targets else None), reachable_targets

        reachability = {
            entry['entity'].entity_uid: entry
            for entry in conversation_reachability(receiver, battle_map, mode='whisper')
        }
        reachable_targets = []
        required_mode_index = 0
        for target in targets:
            entry = reachability.get(target.entity_uid)
            minimum_volume = entry.get('minimum_volume') if entry else None
            if minimum_volume is None:
                continue
            reachable_targets.append(target)
            required_mode_index = max(required_mode_index, SPEECH_MODE_ORDER.index(minimum_volume))

        if not reachable_targets:
            return None, []
        return SPEECH_MODE_ORDER[required_mode_index], reachable_targets

    def build_conversation_response_plan(
        self,
        response: str,
        receiver: Entity,
        speaker: Entity = None,
        llm_conversation_handler=None,
        player_speaker: Entity = None,
    ) -> Dict[str, Any]:
        plan = {
            'language': 'common',
            'message': '',
            'narrative': [],
            'targets': [],
            'volume': None,
            'tts_emotion': None,
            'tts_instruct': None,
            'skip': True,
            'approach': None,
            'move_to': None,
            'follow': None,
            'stop_move': False,
            'stop_follow': False,
            'remember': None,
            'recall': None,
            'interact': None,
            'request_check': None,
            'offer_item': None,
            'pickup': None,
            'trade': None,
            'open_shop': None,
            'request_item': None,
            'accept_gift': None,
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
        }
        if not response:
            self._log_conversation_plan(receiver, 'skip empty LLM response')
            return plan

        self._log_conversation_plan(
            receiver,
            'building reply plan',
            level=logging.DEBUG,
            raw_preview=(response or '')[:160],
        )

        pre_controls = self.parse_response_controls(response)
        if pre_controls['no_response'] and not (pre_controls['response'] or '').strip():
            self._log_conversation_plan(receiver, 'skip [NO_RESPONSE]')
            return plan

        language, processed_response = self.parse_language_from_response(response)
        if hasattr(receiver, 'languages') and receiver.languages():
            language = self.validate_language_for_entity(language, receiver)

        processed_response = self._process_rag_commands(
            processed_response,
            speaker,
            receiver,
            llm_conversation_handler,
            include_keywords=False,
        )
        rerendered_language, processed_response = self.parse_language_from_response(processed_response)
        if rerendered_language != 'common' or language == 'common':
            if hasattr(receiver, 'languages') and receiver.languages():
                language = self.validate_language_for_entity(rerendered_language, receiver)
            else:
                language = rerendered_language

        action_directives = self.parse_action_directives(
            processed_response,
            receiver,
            speaker=speaker,
            player_speaker=player_speaker,
        )
        processed_response = action_directives['response']
        plan.update({
            'approach': action_directives['approach'],
            'move_to': action_directives['move_to'],
            'follow': action_directives['follow'],
            'stop_move': action_directives['stop_move'],
            'stop_follow': action_directives['stop_follow'],
            'remember': action_directives['remember'],
            'recall': action_directives['recall'],
            'interact': action_directives['interact'],
            'request_check': action_directives['request_check'],
            'offer_item': action_directives['offer_item'],
            'pickup': action_directives['pickup'],
            'trade': action_directives['trade'],
            'open_shop': action_directives['open_shop'],
            'request_item': action_directives['request_item'],
            'accept_gift': action_directives['accept_gift'],
            'set_goal': action_directives['set_goal'],
            'goal_complete': action_directives['goal_complete'],
            'goal_give_up': action_directives['goal_give_up'],
            'action_sequence': list(action_directives.get('action_sequence') or []),
        })

        controls = self.parse_response_controls(processed_response)
        if controls['no_response']:
            has_non_speech_directives = any([
                plan.get('move_to'),
                plan.get('follow'),
                plan.get('approach'),
                plan.get('stop_move'),
                plan.get('stop_follow'),
                plan.get('interact'),
                plan.get('action_sequence'),
            ])
            if not has_non_speech_directives:
                self._log_conversation_plan(receiver, 'skip [NO_RESPONSE] after directive parse')
                return plan

        _, _, raw_segments = self.extract_narrative_asides(
            controls['response'],
            llm_conversation_handler=llm_conversation_handler,
            speaker=receiver,
        )
        first_beat, pending_segments = self._split_segments_first_beat(list(raw_segments or []))
        segments, narrative, message, inferred_request_check = self._apply_narrative_segment_policies(
            first_beat,
            receiver=receiver,
            speaker=speaker,
        )
        if plan['request_check'] is None and inferred_request_check is not None:
            plan['request_check'] = inferred_request_check
        if not message:
            if self._plan_has_nonverbal_content(plan, narrative):
                return self._finalize_nonverbal_reply_plan(
                    plan,
                    receiver,
                    speaker,
                    language=language,
                    narrative=narrative,
                    segments=segments,
                    controls=controls,
                )
            self._log_conversation_plan(
                receiver,
                'skip empty spoken line after aside extraction',
                controls_preview=(controls.get('response') or '')[:160],
            )
            return plan

        targets = self.resolve_response_targets(receiver, speaker=speaker, target_spec=controls.get('target_spec'))
        volume, targets = self.plan_response_volume(receiver, targets, volume=controls.get('volume'))
        if (not targets or not volume) and speaker is not None:
            self._log_conversation_plan(
                receiver,
                'volume plan retrying with shout toward speaker',
                speaker=entity_label(speaker),
            )
            volume, targets = self.plan_response_volume(receiver, [speaker], volume='shout')
        if (not targets or not volume) and speaker is not None:
            volume, targets = 'shout', [speaker]
            self._log_conversation_plan(
                receiver,
                'volume plan forced shout fallback toward speaker',
                speaker=entity_label(speaker),
            )

        if not targets or not volume:
            self._log_conversation_plan(
                receiver,
                'skip unreachable targets',
                target_spec=controls.get('target_spec'),
                resolved_target_ids=[getattr(t, 'entity_uid', t) for t in (targets or [])],
                volume=volume,
            )
            return plan

        tts_emotion = controls.get('emotion')
        tts_instruct = controls.get('tts_instruct')
        if not tts_emotion and volume == 'whisper':
            tts_emotion = 'whisper'
        elif not tts_emotion and volume == 'shout':
            tts_emotion = 'shouting'

        from natural20.utils.conversation import strip_spoken_address_prefix

        message = strip_spoken_address_prefix(message)

        plan.update({
            'language': language,
            'message': message,
            'narrative': narrative,
            'segments': segments,
            'pending_segments': pending_segments,
            'continuation_beats_remaining': self.count_speech_beats(pending_segments),
            'targets': targets,
            'volume': volume,
            'tts_emotion': tts_emotion,
            'tts_instruct': tts_instruct,
            'skip': False,
        })
        if plan.get('request_check'):
            self.ensure_request_check_dc(receiver, plan['request_check'], speaker=speaker)
        self._log_conversation_plan(
            receiver,
            'reply plan ready',
            language=language,
            volume=volume,
            tts_emotion=tts_emotion,
            tts_instruct=(tts_instruct or '')[:80] or None,
            target_ids=[getattr(t, 'entity_uid', t) for t in targets],
            message_preview=message[:160],
            narrative_count=len(narrative or []),
        )
        return plan

    @staticmethod
    def _speaker_name_tokens(speaker) -> List[str]:
        if speaker is None:
            return []
        label = entity_label(speaker)
        tokens = re.split(r'[\s()]+', label or '')
        return [token for token in tokens if token]

    @classmethod
    def _looks_like_stage_direction(cls, line: str, speaker=None) -> bool:
        text = (line or '').strip()
        if not text:
            return False
        if text.startswith(('"', "'")):
            return False
        if text.startswith(('*', '(', '[')):
            return True
        if re.match(r'^(?:They|She|He|The)\b', text):
            return True
        first_token = text.split()[0].rstrip('.,:;')
        for token in cls._speaker_name_tokens(speaker):
            if first_token.lower() == token.lower():
                return True
        return False

    @staticmethod
    def _clean_speech_fragment(text: str) -> str:
        cleaned = re.sub(r'\[.*?\]', '', text or '').strip()
        return ' '.join(cleaned.split())

    def _parse_tagged_segments(self, response_text: str) -> List[Dict[str, str]]:
        segments: List[Dict[str, str]] = []
        last = 0
        for match in ASIDE_DIRECTIVE_PATTERN.finditer(response_text or ''):
            before = self._clean_speech_fragment(response_text[last:match.start()])
            if before:
                segments.append({'type': 'speech', 'text': before})
            aside = (match.group(2) or '').strip()
            if aside:
                segments.append({'type': 'aside', 'text': aside})
            last = match.end()
        tail = self._clean_speech_fragment(response_text[last:])
        if tail:
            segments.append({'type': 'speech', 'text': tail})
        return segments

    def _parse_quoted_content(self, text: str, speaker=None) -> List[Dict[str, str]]:
        line = (text or '').strip()
        if not line:
            return []

        segments: List[Dict[str, str]] = []
        pos = 0
        for match in re.finditer(r'"([^"]*)"', line):
            between = line[pos:match.start()].strip()
            if between:
                kind = 'aside' if self._looks_like_stage_direction(between, speaker) else 'speech'
                value = between if kind == 'aside' else self._clean_speech_fragment(between)
                if value:
                    segments.append({'type': kind, 'text': value})
            quote = (match.group(1) or '').strip()
            if quote:
                segments.append({'type': 'speech', 'text': quote})
            pos = match.end()

        remainder = line[pos:].strip()
        if remainder:
            kind = 'aside' if self._looks_like_stage_direction(remainder, speaker) else 'speech'
            value = remainder if kind == 'aside' else self._clean_speech_fragment(remainder)
            if value:
                segments.append({'type': kind, 'text': value})

        if segments:
            return segments

        kind = 'aside' if self._looks_like_stage_direction(line, speaker) else 'speech'
        value = line if kind == 'aside' else self._clean_speech_fragment(line)
        return [{'type': kind, 'text': value}] if value else []

    def _parse_asterisk_segments(self, text: str) -> List[Dict[str, str]]:
        segments: List[Dict[str, str]] = []
        last = 0
        for match in re.finditer(r'\*([^*]+)\*', text or '', re.DOTALL):
            before = self._clean_speech_fragment(text[last:match.start()])
            if before:
                segments.extend(self._parse_quoted_content(before))
            aside = (match.group(1) or '').strip()
            if aside:
                segments.append({'type': 'aside', 'text': aside})
            last = match.end()
        tail = self._clean_speech_fragment(text[last:])
        if tail:
            segments.extend(self._parse_quoted_content(tail))
        return segments

    def _parse_line_segments(self, lines: List[str], speaker=None) -> List[Dict[str, str]]:
        segments: List[Dict[str, str]] = []
        for line in lines:
            if self._looks_like_stage_direction(line, speaker):
                text = line.strip()
                if text:
                    segments.append({'type': 'aside', 'text': text})
                continue
            if '"' in line:
                segments.extend(self._parse_quoted_content(line, speaker))
                continue
            cleaned = self._clean_speech_fragment(line)
            if cleaned:
                segments.append({'type': 'speech', 'text': cleaned})
        return segments

    def _segments_from_llm_split(self, split: Dict[str, Any]) -> List[Dict[str, str]]:
        raw_segments = split.get('segments')
        if isinstance(raw_segments, list) and raw_segments:
            segments: List[Dict[str, str]] = []
            for entry in raw_segments:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get('type') or '').strip().lower()
                text = str(entry.get('text') or '').strip()
                if not text:
                    continue
                if kind in ('aside', 'narrative', 'stage_direction'):
                    segments.append({'type': 'aside', 'text': text})
                else:
                    cleaned = self._clean_speech_fragment(text)
                    if cleaned:
                        segments.append({'type': 'speech', 'text': cleaned})
            if segments:
                return segments

        segments = []
        spoken = (split.get('spoken') or '').strip()
        if spoken:
            segments.append({'type': 'speech', 'text': spoken})
        for entry in split.get('narrative') or []:
            text = str(entry).strip()
            if text:
                segments.append({'type': 'aside', 'text': text})
        return segments

    def _segments_to_message_and_narrative(self, segments: List[Dict[str, str]]) -> Tuple[str, List[str]]:
        speech = [segment['text'] for segment in segments if segment.get('type') == 'speech' and segment.get('text')]
        narrative = [segment['text'] for segment in segments if segment.get('type') == 'aside' and segment.get('text')]
        return ' '.join(speech).strip(), narrative

    def _split_segments_first_beat(
        self, segments: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Return the first spoken beat and any deferred follow-up beats."""
        if not segments:
            return [], []
        speech_idx = next(
            (
                index
                for index, segment in enumerate(segments)
                if segment.get('type') == 'speech' and str(segment.get('text') or '').strip()
            ),
            None,
        )
        if speech_idx is None:
            return [segments[0]], segments[1:]
        first_beat = [segments[speech_idx]]
        next_idx = speech_idx + 1
        if next_idx < len(segments) and segments[next_idx].get('type') == 'aside':
            first_beat.append(segments[next_idx])
            next_idx += 1
        pending = segments[:speech_idx] + segments[next_idx:]
        if pending:
            logger.info(
                '[ConversationPlan] deferred multi-beat NPC reply from %s to %s segments '
                '(%s beat(s) remaining)',
                len(segments),
                len(first_beat),
                self.count_speech_beats(pending),
            )
        return first_beat, pending

    @staticmethod
    def count_speech_beats(segments: List[Dict[str, str]]) -> int:
        return sum(
            1
            for segment in segments or []
            if segment.get('type') == 'speech' and str(segment.get('text') or '').strip()
        )

    def pop_next_reply_beat(
        self, segments: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Pop the next spoken beat (plus an immediate aside) from deferred segments."""
        first_beat, pending = self._split_segments_first_beat(list(segments or []))
        return first_beat, pending

    def _apply_narrative_segment_policies(
        self,
        segments: List[Dict[str, str]],
        *,
        receiver,
        speaker,
    ) -> Tuple[List[Dict[str, str]], List[str], str, Optional[Dict[str, Any]]]:
        processed: List[Dict[str, str]] = []
        inferred_request_check = None

        for segment in segments or []:
            kind = segment.get('type')
            if kind == 'aside':
                normalized = self.normalize_narrative_asides([segment.get('text', '')])
                if not normalized:
                    continue
                filtered, inferred = self.enforce_narrative_visibility_constraints(
                    normalized,
                    receiver=receiver,
                    speaker=speaker,
                )
                if filtered:
                    processed.append({'type': 'aside', 'text': filtered[0]})
                elif inferred is not None and inferred_request_check is None:
                    inferred_request_check = inferred
                continue

            cleaned = self._clean_speech_fragment(segment.get('text', ''))
            if cleaned:
                processed.append({'type': 'speech', 'text': cleaned})

        message, narrative = self._segments_to_message_and_narrative(processed)
        return processed, narrative, message, inferred_request_check

    def extract_narrative_asides(
        self,
        response_text: str,
        llm_conversation_handler=None,
        speaker=None,
    ) -> Tuple[str, List[str], List[Dict[str, str]]]:
        text = response_text or ''
        segments: List[Dict[str, str]] = []

        if ASIDE_DIRECTIVE_PATTERN.search(text):
            segments = self._parse_tagged_segments(text)
        else:
            cleaned = re.sub(r'\[.*?\]', '', text).strip()
            if not cleaned:
                return '', [], []
            if re.search(r'\*[^*]+\*', cleaned):
                segments = self._parse_asterisk_segments(cleaned)
            elif '\n' not in cleaned:
                fragment = self._clean_speech_fragment(cleaned)
                segments = [{'type': 'speech', 'text': fragment}] if fragment else []
            else:
                lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
                if lines and not any(self._looks_like_stage_direction(line, speaker) for line in lines):
                    joined = self._clean_speech_fragment(' '.join(lines))
                    logger.debug(
                        '[ConversationPlan] joined %s dialogue lines without narrative split preview=%r',
                        len(lines),
                        joined[:120],
                    )
                    segments = [{'type': 'speech', 'text': joined}] if joined else []
                else:
                    segments = self._parse_line_segments(lines, speaker)
                    if not any(segment.get('type') == 'aside' for segment in segments):
                        logger.info(
                            '[ConversationPlan] requesting narrative LLM split preview=%r',
                            cleaned[:160],
                        )
                        split = self._extract_narrative_asides_via_llm(cleaned, llm_conversation_handler)
                        if split is not None:
                            segments = self._segments_from_llm_split(split)
                        elif not segments:
                            segments = [{'type': 'speech', 'text': cleaned}]

        message, narrative = self._segments_to_message_and_narrative(segments)
        return message, narrative, segments

    def _extract_narrative_asides_via_llm(self, message: str, llm_conversation_handler=None) -> Optional[Dict[str, Any]]:
        if llm_conversation_handler is None:
            return None

        llm_handler = getattr(llm_conversation_handler, 'llm_hander', None)
        if llm_handler is None:
            return None

        messages = [
            {
                'role': 'system',
                'content': (
                    'Separate an NPC reply into ordered spoken dialogue and narrative aside text. '
                    'Return JSON only with key segments: an array of objects in original order, '
                    'each with type (speech or aside) and text. '
                    'speech is dialogue spoken aloud; aside is third-person stage direction not spoken aloud. '
                    'Legacy keys spoken and narrative are accepted only when order cannot be preserved.'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({'reply': message}, ensure_ascii=True),
            },
        ]

        try:
            raw_response = llm_handler.send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'narrative_split',
                },
            )
        except Exception:
            return None

        if not raw_response:
            return None

        payload_text = str(raw_response).strip()
        json_match = re.search(r'\{.*\}', payload_text, re.DOTALL)
        if json_match:
            payload_text = json_match.group(0)

        try:
            payload = json.loads(payload_text)
        except Exception:
            return None

        segments = payload.get('segments')
        spoken = payload.get('spoken')
        narrative = payload.get('narrative')

        if not segments and spoken is None and not narrative:
            return None

        if isinstance(narrative, str):
            narrative = [narrative]
        if not isinstance(narrative, list):
            narrative = []

        result: Dict[str, Any] = {
            'spoken': str(spoken or ''),
            'narrative': [str(item) for item in narrative if str(item).strip()],
        }
        if isinstance(segments, list) and segments:
            result['segments'] = segments
        return result

    @staticmethod
    def _plan_has_nonverbal_content(plan: Dict[str, Any], narrative: List[str]) -> bool:
        if narrative:
            return True
        if plan.get('request_check'):
            return True
        return any(
            plan.get(key)
            for key in (
                'offer_item',
                'pickup',
                'trade',
                'open_shop',
                'request_item',
                'accept_gift',
                'retrieve',
                'store',
                'approach',
                'move_to',
                'follow',
                'interact',
                'remember',
                'recall',
                'set_goal',
            )
        )

    def _finalize_nonverbal_reply_plan(
        self,
        plan: Dict[str, Any],
        receiver: Entity,
        speaker: Entity = None,
        *,
        language: str = 'common',
        narrative: Optional[List[str]] = None,
        segments: Optional[List[Dict[str, str]]] = None,
        controls: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        controls = controls or {}
        narrative = list(narrative or [])
        if not segments:
            segments = [{'type': 'aside', 'text': entry} for entry in narrative]
        targets = self.resolve_response_targets(
            receiver,
            speaker=speaker,
            target_spec=controls.get('target_spec'),
        )
        volume, targets = self.plan_response_volume(receiver, targets, volume=controls.get('volume'))
        if (not targets or not volume) and speaker is not None:
            volume, targets = self.plan_response_volume(receiver, [speaker], volume='shout')
        if (not targets or not volume) and speaker is not None:
            volume, targets = 'shout', [speaker]

        tts_emotion = controls.get('emotion')
        tts_instruct = controls.get('tts_instruct')
        if not tts_emotion and volume == 'whisper':
            tts_emotion = 'whisper'
        elif not tts_emotion and volume == 'shout':
            tts_emotion = 'shouting'

        plan.update({
            'language': language,
            'message': '',
            'narrative': narrative,
            'segments': segments,
            'targets': targets or ([speaker] if speaker is not None else []),
            'volume': volume or 'normal',
            'tts_emotion': tts_emotion,
            'tts_instruct': tts_instruct,
            'skip': True,
        })
        if plan.get('request_check'):
            self.ensure_request_check_dc(receiver, plan['request_check'], speaker=speaker)
        self._log_conversation_plan(
            receiver,
            'nonverbal reply plan ready',
            language=language,
            volume=plan.get('volume'),
            target_ids=[getattr(t, 'entity_uid', t) for t in (plan.get('targets') or [])],
            narrative_count=len(narrative),
            request_check=bool(plan.get('request_check')),
        )
        return plan

    def normalize_narrative_asides(self, narrative: List[str]) -> List[str]:
        normalized = []
        for entry in narrative or []:
            text = str(entry or '').strip()
            if not text:
                continue

            rewritten = text
            for pattern, replacement in NARRATIVE_FIRST_PERSON_SUBSTITUTIONS:
                rewritten = pattern.sub(replacement, rewritten)

            rewritten = re.sub(
                r'(^|[.!?]\s+)(they)\b',
                lambda match: f"{match.group(1)}They",
                rewritten,
            )

            normalized.append(rewritten)
        return normalized

    def enforce_narrative_visibility_constraints(self, narrative: List[str], receiver: Entity = None, speaker: Entity = None) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        filtered = []
        removed_non_observable = []

        for entry in narrative or []:
            text = str(entry or '').strip()
            if not text:
                continue

            if NON_OBSERVABLE_TRUST_PATTERN.search(text):
                removed_non_observable.append(text)
                continue

            filtered.append(text)

        inferred_request_check = None
        if removed_non_observable and speaker is not None:
            inferred_skill = self.infer_requested_social_check_skill(
                receiver,
                speaker,
                removed_non_observable,
            )
            inferred_request_check = {
                'skill': inferred_skill,
                'target': speaker,
                'target_spec': 'speaker',
                'dc': None,
            }
            self.ensure_request_check_dc(receiver, inferred_request_check, speaker=speaker)

        return filtered, inferred_request_check

    def conversation_attitude_toward_speaker(self, receiver: Entity, speaker: Entity) -> str:
        if receiver is None or speaker is None:
            return 'unknown'

        try:
            battle = self.current_game.get_current_battle()
        except Exception:
            battle = None

        if battle is not None:
            try:
                if battle.opposing(receiver, speaker) is True:
                    return 'hostile'
            except Exception:
                pass
            try:
                if battle.allies(receiver, speaker) is True:
                    return 'allied'
            except Exception:
                pass

        receiver_group = getattr(receiver, 'group', None)
        speaker_group = getattr(speaker, 'group', None)
        if receiver_group and speaker_group:
            if receiver_group == speaker_group:
                return 'allied'
            try:
                if self.game_session.opposing(receiver_group, speaker_group) is True:
                    return 'hostile'
            except Exception:
                pass
            return 'wary'

        try:
            receiver_owners = set(self.current_game.entity_owners(receiver))
            speaker_owners = set(self.current_game.entity_owners(speaker))
            if receiver_owners and speaker_owners and receiver_owners.intersection(speaker_owners):
                return 'allied'
        except Exception:
            pass

        return 'guarded'

    def infer_requested_social_check_skill(self, receiver: Entity, speaker: Entity, removed_entries: List[str]) -> str:
        attitude = self.conversation_attitude_toward_speaker(receiver, speaker)
        removed_text = ' '.join(str(item or '') for item in (removed_entries or [])).lower()

        intimidation_cues = (
            'threat', 'threaten', 'menace', 'menacing', 'snarl', 'growl',
            'afraid of', 'terrified of', 'fear', 'flinch', 'back away',
            'cower', 'intimidat',
        )
        has_intimidation_cue = any(cue in removed_text for cue in intimidation_cues)

        if attitude == 'hostile' or has_intimidation_cue:
            return 'intimidation'
        return 'persuasion'

    _DEFAULT_SKILL_DC = 15
    _SOCIAL_SKILL_DC_BY_ATTITUDE = {
        'persuasion': {
            'allied': 10,
            'guarded': 13,
            'wary': 15,
            'hostile': 18,
            'unknown': 15,
        },
        'intimidation': {
            'allied': 16,
            'guarded': 14,
            'wary': 12,
            'hostile': 10,
            'unknown': 13,
        },
        'deception': {
            'allied': 12,
            'guarded': 14,
            'wary': 15,
            'hostile': 17,
            'unknown': 14,
        },
    }

    def _entity_properties_dict(self, entity) -> Dict[str, Any]:
        props = getattr(entity, 'properties', None)
        if isinstance(props, Mapping):
            try:
                return dict(props)
            except Exception:
                return {}
        return {}

    def _skill_dc_from_entity_properties(self, entity, skill: str) -> Optional[int]:
        props = self._entity_properties_dict(entity)
        candidate_keys = (
            f'{skill}_dc',
            f'request_{skill}_dc',
            'social_dc',
            'conversation_dc',
        )
        for key in candidate_keys:
            if key not in props:
                continue
            try:
                return int(props[key])
            except (TypeError, ValueError):
                continue
        return None

    def _skill_dc_from_backstory_text(self, entity, skill: str) -> Optional[int]:
        text_parts = []
        props = self._entity_properties_dict(entity)
        for key in ('backstory', 'description', 'notes'):
            value = props.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
        backstory_fn = getattr(entity, 'backstory', None)
        if callable(backstory_fn):
            try:
                backstory_text = str(backstory_fn() or '').strip()
                if backstory_text:
                    text_parts.append(backstory_text)
            except Exception:
                pass
        if not text_parts:
            return None

        combined = '\n'.join(text_parts)
        skill_pattern = re.escape(skill.replace('_', r'[\s_]+'))
        patterns = (
            rf'{skill_pattern}[^\d]{{0,48}}dc\s*[:=]?\s*(\d{{1,2}})',
            rf'dc\s*[:=]?\s*(\d{{1,2}})[^\d]{{0,48}}{skill_pattern}',
        )
        for pattern in patterns:
            match = re.search(pattern, combined, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (TypeError, ValueError):
                    continue
        return None

    def _situational_request_check_dc(self, requester, request_check, *, speaker=None) -> int:
        skill = str((request_check or {}).get('skill') or '').strip().lower()
        target = (request_check or {}).get('target')
        attitude = 'unknown'
        if requester is not None and target is not None:
            attitude = self.conversation_attitude_toward_speaker(requester, target)
        elif requester is not None and speaker is not None:
            attitude = self.conversation_attitude_toward_speaker(requester, speaker)

        social_table = self._SOCIAL_SKILL_DC_BY_ATTITUDE.get(skill)
        if social_table is not None:
            return int(social_table.get(attitude, self._DEFAULT_SKILL_DC))

        moderate_skills = {
            'insight', 'perception', 'investigation', 'arcana', 'history',
            'nature', 'religion', 'medicine', 'survival', 'animal_handling',
        }
        if skill in moderate_skills:
            return 13 if attitude in ('allied', 'guarded') else self._DEFAULT_SKILL_DC
        return self._DEFAULT_SKILL_DC

    def ensure_request_check_dc(self, requester, request_check, *, speaker=None) -> Optional[Dict[str, Any]]:
        if not request_check:
            return request_check

        existing = request_check.get('dc')
        if existing is not None:
            try:
                request_check['dc'] = int(existing)
                return request_check
            except (TypeError, ValueError):
                pass

        skill = str(request_check.get('skill') or '').strip().lower()
        resolved = None
        if requester is not None and skill:
            resolved = self._skill_dc_from_entity_properties(requester, skill)
            if resolved is None:
                resolved = self._skill_dc_from_backstory_text(requester, skill)
        if resolved is None:
            resolved = self._situational_request_check_dc(
                requester,
                request_check,
                speaker=speaker,
            )
        request_check['dc'] = int(resolved)
        return request_check

    def parse_move_target_info(self, actor: Entity, params: Dict[str, Any], speaker: Entity = None) -> Optional[Dict[str, Any]]:
        square = None
        if params.get('x') is not None and params.get('y') is not None:
            try:
                square = (int(float(params['x'])), int(float(params['y'])))
            except (TypeError, ValueError):
                square = None
        if square is None:
            square = self.movement_helper.parse_square_spec(params.get('square'))
        if square is not None:
            return {
                'kind': 'square',
                'square': square,
                'target_spec': f"{square[0]},{square[1]}",
            }

        target_spec = (
            params.get('target')
            or params.get('entity')
            or params.get('object')
            or params.get('name')
            or params.get('landmark')
            or params.get('place')
        )
        if target_spec:
            annotation, annotation_map = self.resolve_map_annotation_target_info(actor, target_spec)
            if annotation is not None:
                target_info = {
                    'kind': 'annotation',
                    'annotation': annotation,
                    'target_spec': target_spec,
                }
                if annotation_map is not None:
                    target_info['annotation_map'] = annotation_map
                return target_info

        target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=True)
        if target is None:
            return None

        kind = 'object' if not isinstance(target, Entity) else 'entity'
        return {
            'kind': kind,
            'target': target,
            'target_spec': target_spec,
        }

    def parse_action_directives(
        self,
        response: str,
        actor: Entity,
        speaker: Entity = None,
        player_speaker: Entity = None,
    ) -> Dict[str, Any]:
        directives = {
            'response': response or '',
            'approach': None,
            'move_to': None,
            'follow': None,
            'stop_move': False,
            'stop_follow': False,
            'remember': None,
            'recall': None,
            'interact': None,
            'request_check': None,
            'offer_item': None,
            'pickup': None,
            'trade': None,
            'open_shop': None,
            'request_item': None,
            'accept_gift': None,
            'retrieve': None,
            'store': None,
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'action_sequence': [],
        }

        def _replace(match):
            directive = (match.group(1) or '').strip().lower()
            value = (match.group(2) or '').strip()

            if directive in ('approach', 'move'):
                params = self._parse_named_params(value, positional_keys=('target', 'distance'))
                try:
                    distance_ft = int(float(params.get('distance', params.get('distance_ft', 5))))
                except (TypeError, ValueError):
                    distance_ft = 5
                target_info = self.parse_move_target_info(actor, params, speaker=speaker)
                if directive == 'approach':
                    distance_ft = min(max(0, distance_ft), SOCIAL_APPROACH_MAX_FT)
                else:
                    distance_ft = max(0, distance_ft)
                payload = {
                    'target_info': target_info,
                    'distance_ft': distance_ft,
                }
                if directive == 'approach':
                    directives['approach'] = payload
                else:
                    directives['move_to'] = payload
            elif directive == 'follow':
                params = self._parse_named_params(value, positional_keys=('target', 'distance'))
                target_spec = params.get('target') or params.get('entity') or 'speaker'
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                try:
                    distance_ft = int(float(params.get('distance', params.get('distance_ft', 5))))
                except (TypeError, ValueError):
                    distance_ft = 5
                if target is not None:
                    directives['follow'] = {
                        'target': target,
                        'target_spec': target_spec,
                        'distance_ft': max(0, distance_ft),
                    }
            elif directive == 'stop_move':
                directives['stop_move'] = True
            elif directive == 'stop_follow':
                directives['stop_follow'] = True
            elif directive == 'remember':
                params = self._parse_named_params(value, positional_keys=('title', 'summary', 'body'))
                title = (params.get('title') or params.get('summary') or value or '').strip()
                if title:
                    directives['remember'] = {
                        'title': title[:200],
                        'summary': (params.get('summary') or title).strip()[:500],
                        'body': (params.get('body') or params.get('summary') or title).strip()[:8000],
                        'tags': params.get('tags'),
                        'importance': params.get('importance'),
                    }
            elif directive == 'recall':
                params = self._parse_named_params(value, positional_keys=('id', 'query'))
                recall_id = (params.get('id') or params.get('memory_id') or '').strip()
                recall_query = (params.get('query') or params.get('q') or value or '').strip()
                if recall_id or recall_query:
                    directives['recall'] = {
                        'id': recall_id or None,
                        'query': recall_query or None,
                    }
            elif directive == 'interact':
                params = self._parse_named_params(value, positional_keys=('target', 'action'))
                target_spec = params.get('target') or params.get('entity') or params.get('object')
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=True)
                directives['interact'] = {
                    'target': target,
                    'target_spec': target_spec,
                    'action': params.get('action'),
                }
            elif directive == 'request_check':
                params = self._parse_named_params(value, positional_keys=('skill', 'target'))
                skill = (params.get('skill') or params.get('check') or '').strip().lower()
                target_spec = params.get('target') or params.get('entity') or 'speaker'
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                try:
                    dc = int(params['dc']) if params.get('dc') is not None else None
                except (TypeError, ValueError):
                    dc = None
                if skill in REQUEST_CHECK_SKILLS and target is not None:
                    directives['request_check'] = {
                        'skill': skill,
                        'target': target,
                        'target_spec': target_spec,
                        'dc': dc,
                    }
            elif directive == 'offer_item':
                params = self._parse_named_params(value, positional_keys=('item', 'target'))
                item_raw = (params.get('item') or params.get('name') or '').strip()
                if item_raw:
                    target_spec = params.get('target') or params.get('entity') or 'speaker'
                    target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                    if target is None and speaker is not None:
                        target = speaker
                    item_slug = self._canonical_item_slug(item_raw)
                    target = adjust_item_offer_target(
                        target,
                        target_spec=target_spec,
                        speaker=speaker,
                        player_speaker=player_speaker,
                        item_slug=item_slug,
                        game_properties=self._game_properties(),
                        actor=actor,
                    )
                    directives['offer_item'] = {
                        'item': item_slug,
                        'target': target,
                        'target_spec': target_spec,
                        'auto_use': self._parse_bool(params.get('auto_use')),
                    }
                    directives['action_sequence'].append({
                        'kind': 'offer_item',
                        **directives['offer_item'],
                    })
            elif directive == 'pickup':
                params = self._parse_named_params(value, positional_keys=('item', 'qty'))
                item_raw = (params.get('item') or params.get('name') or value or '').strip()
                if item_raw:
                    try:
                        qty = int(params.get('qty') or 1)
                    except (TypeError, ValueError):
                        qty = 1
                    directives['pickup'] = {
                        'item': self._canonical_item_slug(item_raw),
                        'qty': max(1, qty),
                    }
            elif directive == 'trade':
                params = self._parse_named_params(value, positional_keys=('target',))
                target_spec = params.get('target') or params.get('entity') or 'speaker'
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                if target is None and speaker is not None:
                    target = speaker
                if target is not None:
                    directives['trade'] = {
                        'target': target,
                        'target_spec': target_spec,
                    }
            elif directive in ('open_shop', 'open_merchant'):
                params = self._parse_named_params(value, positional_keys=('target',))
                target_spec = params.get('target') or params.get('entity') or 'speaker'
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                if target is None and speaker is not None:
                    target = speaker
                if target is not None:
                    directives['open_shop'] = {
                        'target': target,
                        'target_spec': target_spec,
                    }
            elif directive == 'request_item':
                params = self._parse_named_params(value, positional_keys=('item', 'target'))
                item_raw = (params.get('item') or params.get('name') or '').strip()
                if item_raw:
                    target_spec = params.get('target') or params.get('entity') or 'speaker'
                    target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                    if target is None and speaker is not None:
                        target = speaker
                    directives['request_item'] = {
                        'item': self._canonical_item_slug(item_raw),
                        'target': target,
                        'target_spec': target_spec,
                    }
            elif directive == 'accept_gift':
                params = self._parse_named_params(value, positional_keys=('target', 'item'))
                target_spec = params.get('target') or params.get('entity') or 'speaker'
                target = self.resolve_named_target(actor, target_spec, speaker=speaker, include_objects=False)
                if target is None and speaker is not None:
                    target = speaker
                item_raw = (params.get('item') or params.get('name') or '').strip()
                if target is not None:
                    directives['accept_gift'] = {
                        'target': target,
                        'target_spec': target_spec,
                        'item': self._canonical_item_slug(item_raw) if item_raw else None,
                    }
            elif directive == 'retrieve':
                params = self._parse_named_params(value, positional_keys=('item', 'target', 'qty'))
                item_raw = (params.get('item') or params.get('name') or '').strip()
                target_spec = params.get('target') or params.get('container') or params.get('entity')
                if item_raw and target_spec:
                    container = self.resolve_named_target(
                        actor,
                        target_spec,
                        speaker=speaker,
                        include_objects=True,
                    )
                    try:
                        qty = int(params.get('qty') or 1)
                    except (TypeError, ValueError):
                        qty = 1
                    if container is not None and is_map_container(container):
                        directives['retrieve'] = {
                            'item': self._canonical_item_slug(item_raw),
                            'container': container,
                            'target_spec': target_spec,
                            'qty': max(1, qty),
                        }
                        directives['action_sequence'].append({
                            'kind': 'retrieve',
                            **directives['retrieve'],
                        })
            elif directive == 'store':
                params = self._parse_named_params(value, positional_keys=('item', 'target', 'qty'))
                item_raw = (params.get('item') or params.get('name') or '').strip()
                target_spec = params.get('target') or params.get('container') or params.get('entity')
                if item_raw and target_spec:
                    container = self.resolve_named_target(
                        actor,
                        target_spec,
                        speaker=speaker,
                        include_objects=True,
                    )
                    try:
                        qty = int(params.get('qty') or 1)
                    except (TypeError, ValueError):
                        qty = 1
                    if container is not None and is_map_container(container):
                        directives['store'] = {
                            'item': self._canonical_item_slug(item_raw),
                            'container': container,
                            'target_spec': target_spec,
                            'qty': max(1, qty),
                        }
            elif directive == 'set_goal' and value:
                directives['set_goal'] = value.strip()
            elif directive == 'goal_complete':
                directives['goal_complete'] = True
            elif directive == 'goal_give_up':
                directives['goal_give_up'] = True
            return ''

        directives['response'] = ACTION_DIRECTIVE_PATTERN.sub(_replace, directives['response']).strip()
        return directives

    def _parse_named_params(self, raw_value: str, positional_keys=()):
        parsed = {}
        positional_values = []

        for part in re.split(r'[,;]', raw_value or ''):
            segment = part.strip()
            if not segment:
                continue
            if '=' in segment:
                key, value = segment.split('=', 1)
                parsed[key.strip().lower()] = value.strip()
            else:
                positional_values.append(segment)

        for index, key in enumerate(positional_keys):
            if key not in parsed and index < len(positional_values):
                parsed[key] = positional_values[index]

        return parsed

    def _parse_bool(self, value) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

    def _game_properties(self) -> Dict[str, Any]:
        return getattr(self.game_session, 'game_properties', None) or {}

    def campaign_setting_prompt(self) -> str:
        props = self._game_properties()
        setting = props.get('campaign_setting')
        if not setting:
            return ''
        if isinstance(setting, str):
            text = setting.strip()
        elif isinstance(setting, dict):
            lines = []
            for key in ('world', 'location', 'era', 'summary', 'lore'):
                value = setting.get(key)
                if value:
                    lines.append(str(value).strip())
            extra = setting.get('details')
            if isinstance(extra, list):
                lines.extend(str(item).strip() for item in extra if str(item).strip())
            elif extra:
                lines.append(str(extra).strip())
            text = '\n'.join(line for line in lines if line)
        else:
            text = str(setting).strip()
        if not text:
            return ''
        return f"Campaign setting (Forgotten Realms backdrop — treat as established local knowledge):\n{text}"

    def _canonical_item_slug(self, item_slug: str) -> str:
        return canonical_item_slug(item_slug, merged_offer_configs(self._game_properties()))

    def resolve_named_target(self, actor: Entity, target_spec: Optional[str], speaker: Entity = None, include_objects: bool = False):
        if not target_spec:
            return None

        normalized = target_spec.strip()
        lowered = normalized.lower().lstrip('@')
        if lowered in {'speaker', 'you'}:
            return speaker

        for uid_candidate in (normalized, lowered):
            try:
                target = self.game_session.entity_by_uid(uid_candidate)
                if target is not None:
                    return target
            except Exception:
                pass

        if speaker is not None:
            speaker_uid = str(getattr(speaker, 'entity_uid', '') or '').strip().lower()
            speaker_handle = mention_handle_for(speaker).lower()
            if lowered == speaker_uid or lowered == speaker_handle:
                return speaker

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        candidates = [entity for entity in battle_map.entities if entity != actor]
        if include_objects:
            candidates.extend(list(battle_map.interactable_objects.keys()))
        if speaker is not None and speaker not in candidates and speaker != actor:
            candidates.append(speaker)

        exact_match = None
        partial_match = None
        for candidate in candidates:
            candidate_handle = mention_handle_for(candidate).lower()
            candidate_label = entity_label(candidate).lower()
            candidate_uid = str(getattr(candidate, 'entity_uid', ''))
            if lowered == candidate_handle or normalized == candidate_uid:
                return candidate
            if candidate_label == normalized.lower():
                exact_match = candidate
            elif normalized.lower() in candidate_label and partial_match is None:
                partial_match = candidate

        return exact_match or partial_match

    def _find_map_annotation_by_spec(
        self,
        target_spec: Optional[str],
        prefer_map=None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
        if not target_spec:
            return None, None

        from natural20.map_annotations import normalize_annotation_id

        normalized = str(target_spec).strip().lstrip('@')
        lowered = normalized.lower()
        slug = normalize_annotation_id(normalized)

        if slug:
            ann, ann_map = self._find_map_annotation(slug, prefer_map)
            if ann is not None:
                return ann, ann_map

        search_order = []
        if prefer_map is not None:
            search_order.append(prefer_map)
        for map_obj in getattr(self.game_session, 'maps', {}).values():
            if map_obj is not prefer_map:
                search_order.append(map_obj)

        partial: Optional[Dict[str, Any]] = None
        partial_map = None
        for map_obj in search_order:
            for annotation in map_obj.map_annotations():
                label = str(annotation.get('label') or '').lower()
                if lowered == label:
                    return annotation, map_obj
                if lowered in label and partial is None:
                    partial = annotation
                    partial_map = map_obj
        return partial, partial_map

    def resolve_map_annotation_target(self, actor: Entity, target_spec: Optional[str]) -> Optional[Dict[str, Any]]:
        battle_map = self.current_game.get_map_for_entity(actor)
        annotation, _annotation_map = self._find_map_annotation_by_spec(target_spec, battle_map)
        return annotation

    def resolve_map_annotation_target_info(
        self,
        actor: Entity,
        target_spec: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
        battle_map = self.current_game.get_map_for_entity(actor)
        return self._find_map_annotation_by_spec(target_spec, battle_map)

    def _find_map_annotation(
        self,
        annotation_id: str,
        prefer_map=None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
        """Resolve a landmark on prefer_map first, then any loaded campaign map."""
        from natural20.map_annotations import normalize_annotation_id

        slug = normalize_annotation_id(str(annotation_id))
        if not slug:
            return None, None

        search_order = []
        if prefer_map is not None:
            search_order.append(prefer_map)
        for map_obj in getattr(self.game_session, 'maps', {}).values():
            if map_obj is not prefer_map:
                search_order.append(map_obj)

        for map_obj in search_order:
            ann = map_obj.map_annotation_by_id(slug)
            if ann is not None:
                return ann, map_obj
        return None, None

    def map_landmarks_prompt_for_entity(self, entity: Entity) -> str:
        battle_map = self.current_game.get_map_for_entity(entity)
        if battle_map is None:
            return ''

        from natural20.map_annotations import format_annotation_for_llm

        lines: List[str] = []
        known_ids = set()
        props = getattr(entity, 'properties', None) or {}
        for raw_id in props.get('known_places') or props.get('known_landmarks') or []:
            ann, ann_map = self._find_map_annotation(str(raw_id), battle_map)
            if ann is None:
                continue
            known_ids.add(ann.get('id'))
            line = format_annotation_for_llm(ann)
            if ann_map is not None and ann_map is not battle_map:
                map_name = getattr(ann_map, 'name', None) or getattr(ann_map, 'map_id', None)
                if map_name:
                    line = f'{line} — upstairs ({map_name})'
            lines.append(f"- (known) {line}")

        try:
            pos = battle_map.position_of(entity)
            for ann in battle_map.map_annotations_at(int(pos[0]), int(pos[1])):
                if ann.get('id') in known_ids:
                    continue
                lines.append(f"- (here) {format_annotation_for_llm(ann)}")
        except Exception:
            pass

        for ann in battle_map.map_annotations():
            if ann.get('id') in known_ids:
                continue
            if len(lines) >= 12:
                break
            lines.append(f"- {format_annotation_for_llm(ann)}")

        if not lines:
            return ''
        return (
            "Map landmarks (use as [MOVE: target=<id>] or [MOVE: target=@label]; "
            "descriptions are staff/local knowledge):\n"
            + '\n'.join(lines[:12])
        )

    def get_interactable_objects(
        self,
        entity: Entity,
        range_ft: int = 30,
        *,
        require_enabled_actions: bool = True,
    ) -> List[Dict[str, Any]]:
        battle_map = self.current_game.get_map_for_entity(entity)
        if battle_map is None:
            return []

        objects = []
        for obj in list(battle_map.interactable_objects.keys()):
            try:
                distance_ft = battle_map.distance(entity, obj) * battle_map.feet_per_grid
            except Exception:
                continue

            if distance_ft > range_ft:
                continue

            try:
                visible = battle_map.can_see(entity, obj)
            except Exception:
                visible = True

            if not visible:
                continue

            try:
                interactions = obj.available_interactions(entity, battle=None) or {}
            except Exception:
                interactions = {}

            enabled_actions = [
                action_name for action_name, details in interactions.items()
                if not (details or {}).get('disabled')
            ]
            if require_enabled_actions and not enabled_actions:
                continue

            objects.append({
                'id': getattr(obj, 'entity_uid', None),
                'name': entity_label(obj),
                'distance_ft': distance_ft,
                'position': battle_map.entity_or_object_pos(obj),
                'mention_handle': mention_handle_for(obj),
                'actions': enabled_actions,
                'appearance': self._outward_appearance_for(entity, obj, battle_map),
                'kind': self._observation_kind_label(obj),
                'stock': format_container_contents(obj) if is_map_container(obj) else '',
                'subject': obj,
            })

        return objects

    def _observation_kind_label(self, entity: Entity) -> str:
        try:
            if callable(getattr(entity, 'object', None)) and entity.object():
                return 'object'
        except Exception:
            pass
        try:
            if callable(getattr(entity, 'is_npc', None)) and entity.is_npc():
                return 'npc'
        except Exception:
            pass
        class_name = entity.__class__.__name__
        if isinstance(entity, PlayerCharacter) or class_name == 'PlayerCharacter':
            return 'player'
        if class_name == 'Npc':
            return 'npc'
        if getattr(entity, 'conversable', lambda: False)():
            return 'creature'
        return 'entity'

    def _observer_can_read_annotations(self, observer: Entity) -> bool:
        if observer is None:
            return False
        try:
            return bool(observer.is_npc())
        except Exception:
            return False

    def _observer_knows_entity(self, observer: Entity, target: Entity) -> bool:
        if observer is None or target is None:
            return False
        if observer == target:
            return True

        target_uid = str(getattr(target, 'entity_uid', '') or '').strip()
        if not target_uid:
            return False

        target_props = getattr(target, 'properties', {}) or {}
        if target_props.get('publicly_known') or target_props.get('always_known'):
            return True

        try:
            observer_owners = set(self.current_game.entity_owners(observer) or [])
            target_owners = set(self.current_game.entity_owners(target) or [])
            if observer_owners and target_owners and observer_owners.intersection(target_owners):
                return True
        except Exception:
            pass

        observer_group = getattr(observer, 'group', None)
        target_group = getattr(target, 'group', None)
        if observer_group and target_group and observer_group == target_group:
            return True

        for message in getattr(observer, 'memory_buffer', []) or []:
            source = message.get('source')
            directed_to = message.get('directed_to') or []
            target_field = message.get('target')
            source_uid = str(getattr(source, 'entity_uid', '') or '').strip()
            if source_uid == target_uid or source == target or target in directed_to or target_field == target:
                return True

        return False

    def _outward_appearance_for(self, observer: Entity, subject: Entity, battle_map=None) -> str:
        return resolve_outward_appearance(
            subject,
            session=self.game_session,
            observer=observer,
        )

    def _observed_name_for(self, observer: Entity, target: Entity) -> str:
        if self._observer_knows_entity(observer, target):
            return entity_label(target)

        props = getattr(target, 'properties', {}) or {}
        alias = str(props.get('observe_as') or props.get('unknown_label') or '').strip()
        if alias:
            return alias

        race = props.get('race') or props.get('sub_type') or props.get('kind')
        kind = self._observation_kind_label(target)
        if kind == 'player':
            return 'an unfamiliar adventurer'
        if race:
            return f'an unknown {race}'
        if kind == 'npc':
            return 'an unfamiliar person'
        if kind == 'object':
            return 'an object'
        return 'an unknown figure'

    def _entity_condition_hint(self, entity: Entity) -> Optional[str]:
        try:
            if callable(getattr(entity, 'dead', None)) and entity.dead():
                return 'appears dead'
        except Exception:
            pass
        try:
            if callable(getattr(entity, 'unconscious', None)) and entity.unconscious():
                return 'appears unconscious'
        except Exception:
            pass
        return None

    def _visible_health_hint(self, entity: Entity) -> Optional[str]:
        try:
            max_hp = int(entity.max_hp())
            hp = int(entity.hp())
        except Exception:
            return None
        if max_hp <= 0 or hp <= 0:
            return None
        ratio = hp / max_hp
        if ratio <= 0.25:
            return 'They look badly hurt—moving carefully and favoring their wounds.'
        if ratio <= 0.5:
            return 'They bear visible injuries and seem worse for wear.'
        if ratio <= 0.75:
            return 'They show a few cuts and bruises.'
        return None

    def _visible_status_phrases(self, entity: Entity) -> str:
        statuses = getattr(entity, 'statuses', None) or []
        phrases = []
        for status in statuses:
            key = str(status or '').strip().lower()
            phrase = VISIBLE_STATUS_PHRASES.get(key)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        return ' '.join(phrases)

    def _conversation_expression_hint(
        self,
        target: Entity,
        recent_lines: List[str],
        roll_total: Optional[int],
    ) -> str:
        if not recent_lines:
            return ''
        target_label = entity_label(target)
        last_from_target = ''
        for line in reversed(recent_lines):
            prefix = f"{target_label}:"
            if str(line).startswith(prefix):
                last_from_target = str(line)[len(prefix):].strip()
                break
        if not last_from_target:
            if roll_total is not None and roll_total >= 12:
                return 'You watch their manner as you speak—they seem engaged in the exchange.'
            return ''

        lowered = last_from_target.lower()
        if '?' in last_from_target:
            return 'Their expression shifts with curiosity as they listen.'
        if any(word in lowered for word in ('thank', 'welcome', 'glad', 'happy', 'please')):
            return 'A warm, open expression crosses their face.'
        if any(word in lowered for word in ("don't", 'cannot', "won't", 'refuse', 'no ')):
            return 'Their face tightens—a guarded, doubtful look.'
        if roll_total is not None and roll_total >= 14:
            return 'You catch small shifts in their expression as the conversation continues.'
        return ''

    def _perception_focus_note(
        self,
        target: Entity,
        focus: str,
        appearance: str,
        roll_total: Optional[int],
    ) -> str:
        focus_text = str(focus or '').strip()
        if not focus_text:
            return ''
        appearance_lower = str(appearance or '').lower()
        focus_lower = focus_text.lower()
        focus_tokens = [token for token in re.split(r'\W+', focus_lower) if len(token) > 2]
        if focus_tokens and any(token in appearance_lower for token in focus_tokens):
            return f"Regarding {focus_text}, you notice details that match what you can see: {appearance}"
        if roll_total is not None and roll_total >= 12:
            return f"You study {focus_text}, but nothing beyond the general impression stands out."
        if roll_total is not None and roll_total < 12:
            return f"You try to focus on {focus_text}, but the details stay fuzzy."
        return ''

    def _vague_appearance_summary(self, appearance: str, target_label: str) -> str:
        text = str(appearance or '').strip()
        if not text:
            return f'You make out {target_label}, but finer details blur in the bustle of the scene.'
        first_sentence = re.split(r'[.!?]\s+', text, maxsplit=1)[0].strip()
        if first_sentence and not first_sentence.endswith(('.', '!', '?')):
            first_sentence += '.'
        return (
            f'You take a quick look at {target_label}. '
            f'Only a general impression comes through: {first_sentence}'
        )

    def _build_perception_description_fallback(
        self,
        observer: Entity,
        target: Entity,
        focus: str = '',
        roll_total: Optional[int] = None,
    ) -> Dict[str, str]:
        battle_map = self.current_game.get_map_for_entity(observer) if self.current_game is not None else None
        target_label = entity_label(target) if target is not None else 'the scene'
        appearance = ''
        if target is not None:
            appearance = self._outward_appearance_for(observer, target, battle_map)

        if target is None:
            return {'description': PERCEPTION_EMPTY_DESCRIPTION}

        roll = roll_total if roll_total is not None else 10
        paragraphs: List[str] = []

        if roll < 8:
            paragraphs.append(self._vague_appearance_summary(appearance, target_label))
        else:
            if appearance:
                paragraphs.append(
                    f'You take a closer look at {target_label}. {appearance}'
                )
            else:
                paragraphs.append(f'You study {target_label}.')

        if roll >= 10:
            condition = self._entity_condition_hint(target)
            if condition:
                paragraphs.append(f'They {condition}.')
            health_hint = self._visible_health_hint(target)
            if health_hint:
                paragraphs.append(health_hint)
            status_text = self._visible_status_phrases(target)
            if status_text:
                paragraphs.append(status_text)

        if roll >= 12:
            recent = self._conversation_snippet_between(observer, target, limit=6)
            expression_hint = self._conversation_expression_hint(target, recent, roll_total=roll)
            if expression_hint:
                paragraphs.append(expression_hint)

        focus_note = self._perception_focus_note(target, focus, appearance, roll_total=roll)
        if focus_note and roll >= 10:
            paragraphs.append(focus_note)

        description = ' '.join(part for part in paragraphs if part).strip()
        if not description:
            description = (
                f'You study {target_label}, but nothing beyond a fleeting impression stands out.'
            )
        return {'description': description}

    def _resolve_perception_llm_handler(self, llm_conversation_handler):
        llm_handler = getattr(llm_conversation_handler, 'llm_hander', None)
        if llm_handler is None:
            llm_handler = self._get_llm_handler()
        return llm_handler

    def _scan_visible_entities(self, observer: Entity, battle_map, range_ft: int = 30) -> List[Tuple[Entity, float]]:
        if battle_map is None or observer is None:
            return []

        visible: List[Tuple[Entity, float]] = []
        for other in list(getattr(battle_map, 'entities', {}).keys()):
            if other == observer:
                continue
            try:
                distance_ft = battle_map.distance(observer, other) * battle_map.feet_per_grid
            except Exception:
                continue
            if distance_ft > range_ft:
                continue
            try:
                if not battle_map.can_see(observer, other):
                    continue
            except Exception:
                continue
            visible.append((other, distance_ft))

        return sorted(visible, key=lambda item: item[1])

    def build_observation_summary(self, observer: Entity, range_ft: int = 30) -> str:
        battle_map = self.current_game.get_map_for_entity(observer)
        if battle_map is None:
            return '[OBSERVE] You cannot make out your surroundings from here.'

        lines = ['[OBSERVE]']
        try:
            position = battle_map.entity_or_object_pos(observer)
        except Exception:
            position = None
        if position is not None:
            lines.append(f'Your position: {position}')

        entity_lines: List[str] = []
        for other, distance_ft in self._scan_visible_entities(observer, battle_map, range_ft=range_ft):
            name = self._observed_name_for(observer, other)
            known = self._observer_knows_entity(observer, other)
            kind = self._observation_kind_label(other)
            position = None
            try:
                position = battle_map.entity_or_object_pos(other)
            except Exception:
                pass
            appearance = self._outward_appearance_for(observer, other, battle_map)
            condition = self._entity_condition_hint(other)
            handle = mention_handle_for(other)

            parts = [
                f"@{handle}",
                f"{'name: ' + entity_label(other) if known else 'unknown ' + kind + ': ' + name}",
                f"{int(round(distance_ft))}ft away",
            ]
            if position is not None:
                parts.append(f"at {position}")
            if condition:
                parts.append(condition)
            if appearance:
                parts.append(f"appearance: {appearance}")
            entity_lines.append('- ' + '; '.join(parts))

        lines.append('Nearby people and creatures:')
        if entity_lines:
            lines.extend(entity_lines)
        else:
            lines.append('- none visible within range')

        object_lines: List[str] = []
        for obj in self.get_interactable_objects(observer, range_ft=range_ft, require_enabled_actions=False):
            handle = obj.get('mention_handle') or 'object'
            name = obj.get('name') or 'object'
            distance_ft = int(round(obj.get('distance_ft') or 0))
            position = obj.get('position')
            appearance = obj.get('appearance') or ''
            actions = obj.get('actions') or []
            parts = [
                f"@{handle}",
                f"name: {name}",
                f"{distance_ft}ft away",
            ]
            if position is not None:
                parts.append(f"at {position}")
            if actions:
                parts.append(f"interactions: {', '.join(actions)}")
            if appearance:
                parts.append(f"appearance: {appearance}")
            stock = obj.get('stock') or ''
            if stock and stock != 'empty':
                parts.append(f"stock: {stock}")
            if self._observer_can_read_annotations(observer):
                subject = obj.get('subject')
                if subject is not None:
                    annotation_text = format_annotations_for_subject(observer, subject)
                    if annotation_text:
                        parts.append(f"staff annotations: {annotation_text}")
            object_lines.append('- ' + '; '.join(parts))

        lines.append('Interactable objects:')
        if object_lines:
            lines.extend(object_lines)
        else:
            lines.append('- none visible within range')

        ground_summary = self.ground_items_summary(observer)
        if ground_summary:
            lines.append(ground_summary)

        return '\n'.join(lines)

    def build_approach_action(self, actor: Entity, target, distance_ft: int = 5):
        if actor is None or target is None:
            return None
        target_info = {
            'kind': 'entity' if isinstance(target, Entity) else 'object',
            'target': target,
        }
        return self.movement_helper.build_move_step(actor, target_info, distance_ft=distance_ft)

    def build_interact_action(self, actor: Entity, target, action_name: Optional[str] = None):
        if actor is None or target is None or not hasattr(target, 'available_interactions'):
            return None

        try:
            interactions = target.available_interactions(actor, battle=None) or {}
        except Exception:
            interactions = {}

        enabled_actions = [
            interaction_name for interaction_name, details in interactions.items()
            if not (details or {}).get('disabled')
        ]
        if not enabled_actions:
            return None

        selected_action = action_name
        if selected_action and selected_action not in enabled_actions:
            return None
        if not selected_action:
            selected_action = enabled_actions[0]

        interact = InteractAction(self.game_session, actor, 'interact')
        interact.target = target
        interact.object_action = selected_action
        built_action = interact.build_custom_action(selected_action, target)
        if isinstance(built_action, InteractAction):
            return built_action
        if isinstance(built_action, Action):
            return built_action
        return None

    def _directive_enabled(self, name: str, only: Optional[set] = None, skip: Optional[set] = None) -> bool:
        if only is not None and name not in only:
            return False
        if skip is not None and name in skip:
            return False
        return True

    def _ui_updates_suppressed(self) -> bool:
        try:
            return bool(self.current_game.realtime_ui_suppressed())
        except Exception:
            return False

    def _emit_refresh_map_if_allowed(self) -> None:
        if self._ui_updates_suppressed():
            return
        try:
            self.current_game.socketio.emit('message', {'type': 'refresh_map'})
        except Exception:
            pass

    def _commit_exploration_move(self, actor: Entity, move_action, execution_username: str) -> bool:
        if move_action is None:
            return False
        self.current_game.commit_and_update(execution_username, move_action, [actor])
        animated = self.current_game.emit_out_of_combat_move_animation(move_action, execution_username)
        if not animated:
            self._emit_refresh_map_if_allowed()
        return True

    def _try_commit_npc_move_step(
        self,
        entity: Entity,
        task: Dict[str, Any],
        target_info: Dict[str, Any],
        directive_result: Dict[str, Any],
    ) -> bool:
        """Take one exploration step toward the movement target. Returns True if a move was committed."""
        move_action = self.movement_helper.build_move_step(
            entity,
            target_info,
            distance_ft=task.get('distance_ft', 5),
        )
        if move_action is None:
            return False

        try:
            dest = tuple(move_action.move_path[-1])
        except Exception:
            dest = None

        recent_positions = list(task.get('recent_positions') or [])
        if dest is not None and dest in recent_positions[-3:]:
            logger.warning(
                'Skipping oscillating NPC move for %s (dest=%s recent=%s)',
                getattr(entity, 'entity_uid', entity),
                dest,
                recent_positions[-3:],
            )
            task['stuck_ticks'] = int(task.get('stuck_ticks') or 0) + 1
            return False

        execution_username = self._execution_username_for(entity)
        if not self._commit_exploration_move(entity, move_action, execution_username):
            return False

        directive_result['executed_actions'].append(task.get('mode', 'move_to'))
        if dest is not None:
            recent_positions.append(dest)
            task['recent_positions'] = recent_positions[-6:]
        task['stuck_ticks'] = 0
        return True

    def _hydrate_route_blocker(
        self,
        actor: Entity,
        route_blocker: Optional[Dict[str, Any]],
        target_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not route_blocker:
            return None
        if route_blocker.get('object') is not None:
            return route_blocker

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return self.movement_helper.detect_blocking_door_on_route(actor, target_info)

        entity_uid = route_blocker.get('entity_uid')
        door_obj = None
        if entity_uid:
            door_obj = battle_map.object_by_uid(entity_uid) or battle_map.entity_by_uid(entity_uid)

        position = route_blocker.get('position') or [None, None]
        from_position = route_blocker.get('from_position') or [None, None]
        if door_obj is None and position[0] is not None and position[1] is not None:
            for obj in battle_map.objects_at(int(position[0]), int(position[1])):
                if self.movement_helper._is_door_object(obj):
                    door_obj = obj
                    break

        if door_obj is None:
            return self.movement_helper.detect_blocking_door_on_route(actor, target_info)

        if route_blocker.get('available_actions'):
            merged = dict(route_blocker)
            merged['object'] = door_obj
            return merged

        from_pos = (
            int(from_position[0]),
            int(from_position[1]),
        ) if from_position[0] is not None and from_position[1] is not None else battle_map.position_of(actor)
        door_pos = (int(position[0]), int(position[1]))
        return self.movement_helper._describe_blocking_door(
            actor,
            battle_map,
            door_obj,
            door_pos,
            from_pos,
        )

    def try_clear_route_blocking_door(
        self,
        actor: Entity,
        route_blocker: Optional[Dict[str, Any]],
        target_info: Dict[str, Any],
    ) -> bool:
        """Open or unlock a blocking door when the NPC is already adjacent."""
        blocker = self._hydrate_route_blocker(actor, route_blocker, target_info)
        if blocker is None:
            return False

        door_obj = blocker.get('object')
        if door_obj is None:
            return False

        available_actions = list(blocker.get('available_actions') or [])
        for action_name in ('unlock', 'open'):
            if action_name not in available_actions:
                continue
            interact_action = self.build_interact_action(actor, door_obj, action_name)
            if interact_action is None:
                continue
            execution_username = self._execution_username_for(actor)
            self.current_game.commit_and_update(execution_username, interact_action, [actor])
            return True
        return False

    def _trigger_immediate_npc_movement(self, entity: Entity, llm_conversation_handler=None) -> None:
        """Queue a movement tick on a worker thread (never block the /talk request)."""
        entity_uid = str(getattr(entity, 'entity_uid', '') or '')
        if not entity_uid:
            return

        def _run() -> None:
            handler = llm_conversation_handler
            if handler is None:
                try:
                    from webapp.blueprints.helpers.runtime_state import get_llm_conversation_handler
                    handler = get_llm_conversation_handler()
                except Exception:
                    handler = None
            try:
                self.execute_scheduled_movement(
                    entity_uid,
                    handler,
                    allow_movement_llm=False,
                    advance_time=False,
                )
            except Exception as exc:
                logger.exception(
                    'Deferred NPC movement tick failed for %s: %s',
                    entity_uid,
                    exc,
                )

        thread = threading.Thread(
            target=_run,
            name=f'n20-move-kick-{entity_uid}',
            daemon=True,
        )
        thread.start()

    def _clear_movement_llm_conversation(self, entity: Entity, llm_conversation_handler=None) -> None:
        entity_uid = str(getattr(entity, 'entity_uid', '') or '')
        if not entity_uid:
            return
        handler = llm_conversation_handler
        if handler is None:
            try:
                from webapp.blueprints.helpers.runtime_state import get_llm_conversation_handler
                handler = get_llm_conversation_handler()
            except Exception:
                handler = None
        if handler is None:
            return
        conversation_id = f"{entity_uid}:movement"
        try:
            handler.conversations.pop(conversation_id, None)
        except Exception:
            pass

    def _schedule_approach_to_target_info(
        self,
        actor: Entity,
        target_info: Dict[str, Any],
        speaker: Entity,
        result: Dict[str, Any],
        *,
        distance_ft: int = SOCIAL_APPROACH_MAX_FT,
        kick_movement: bool = False,
    ) -> bool:
        if actor is None or not target_info or self.current_game.get_current_battle() is not None:
            return False

        approach_distance_ft = min(
            int(distance_ft or SOCIAL_APPROACH_MAX_FT),
            SOCIAL_APPROACH_MAX_FT,
        )
        execution_username = self._execution_username_for(actor)
        route_blocker = None
        try:
            route_blocker = self.movement_helper.detect_blocking_door_on_route(actor, target_info)
        except Exception:
            route_blocker = None
        if route_blocker is not None:
            self.try_clear_route_blocking_door(actor, route_blocker, target_info)
        move_action = self.movement_helper.build_move_step(
            actor,
            target_info,
            distance_ft=approach_distance_ft,
        )
        if move_action is not None:
            try:
                if self._commit_exploration_move(actor, move_action, execution_username):
                    result['executed_actions'].append('approach')
            except Exception:
                logger.exception(
                    'Approach move failed for %s toward %s',
                    entity_label(actor),
                    self.movement_helper.describe_target(target_info),
                )
        if self.movement_helper.movement_reached(actor, target_info, distance_ft=approach_distance_ft):
            return 'approach' in result['executed_actions']

        scheduled = self.current_game.schedule_npc_move_to(
            actor,
            target_info,
            distance_ft=approach_distance_ft,
            speaker=speaker,
        )
        if scheduled is not None:
            result['scheduled_movement'] = scheduled
            result['executed_actions'].append('move_to')
            if kick_movement:
                self._trigger_immediate_npc_movement(actor)
            return True
        return False

    def _apply_exploration_movement_directives(
        self,
        plan: Dict[str, Any],
        actor: Entity,
        speaker: Entity,
        result: Dict[str, Any],
        *,
        only: Optional[set] = None,
        skip: Optional[set] = None,
        kick_movement: bool = False,
    ) -> None:
        if self.current_game.get_current_battle() is not None:
            return

        approach_directive = plan.get('approach')
        if approach_directive and self._directive_enabled('approach', only, skip):
            target_info = approach_directive.get('target_info')
            if target_info is None and approach_directive.get('target') is not None:
                target = approach_directive['target']
                target_info = {
                    'kind': 'entity' if isinstance(target, Entity) else 'object',
                    'target': target,
                }
            if target_info:
                self._schedule_approach_to_target_info(
                    actor,
                    target_info,
                    speaker,
                    result,
                    distance_ft=int(
                        approach_directive.get('distance_ft', SOCIAL_APPROACH_MAX_FT)
                        or SOCIAL_APPROACH_MAX_FT
                    ),
                    kick_movement=kick_movement,
                )

        move_directive = plan.get('move_to')
        if move_directive and move_directive.get('target_info') and self._directive_enabled('move_to', only, skip):
            result['scheduled_movement'] = self.current_game.schedule_npc_move_to(
                actor,
                move_directive['target_info'],
                distance_ft=move_directive.get('distance_ft', 5),
                speaker=speaker,
            )
            if result['scheduled_movement'] is not None:
                result['executed_actions'].append('move_to')
                if kick_movement:
                    self._trigger_immediate_npc_movement(actor)

        follow_directive = plan.get('follow')
        if follow_directive and follow_directive.get('target') is not None and self._directive_enabled('follow', only, skip):
            result['scheduled_movement'] = self.current_game.schedule_npc_follow(
                actor,
                follow_directive['target'],
                distance_ft=follow_directive.get('distance_ft', 5),
                speaker=speaker,
            )
            if result['scheduled_movement'] is not None:
                result['executed_actions'].append('follow')
                if kick_movement:
                    self._trigger_immediate_npc_movement(actor)

    def apply_response_plan_directives(
        self,
        plan: Dict[str, Any],
        actor: Entity,
        speaker: Entity = None,
        advance_time: bool = False,
        *,
        only: Optional[set] = None,
        skip: Optional[set] = None,
        kick_movement: bool = False,
    ) -> Dict[str, Any]:
        result = {
            'scheduled_goal': None,
            'scheduled_movement': None,
            'executed_actions': [],
            'goal_status': None,
            'movement_status': None,
        }
        if actor is None or not plan:
            return result

        if plan.get('stop_move') and self._directive_enabled('stop_move', only, skip):
            self.current_game.cancel_npc_movement(actor, reason='Stopped by LLM', mode='move_to')
            result['movement_status'] = 'cancelled'
            result['executed_actions'].append('stop_move')
        if plan.get('stop_follow') and self._directive_enabled('stop_follow', only, skip):
            self.current_game.cancel_npc_movement(actor, reason='Stopped by LLM', mode='follow')
            result['movement_status'] = 'cancelled'
            result['executed_actions'].append('stop_follow')

        remember_directive = plan.get('remember')
        if remember_directive and remember_directive.get('title') and self._directive_enabled('remember', only, skip):
            created = self._create_npc_memory(actor, remember_directive, speaker=speaker)
            if created is not None:
                result['executed_actions'].append('remember')

        if plan.get('set_goal') and self._directive_enabled('set_goal', only, skip):
            result['scheduled_goal'] = self.current_game.schedule_short_term_goal(actor, plan['set_goal'], speaker=speaker)

        if plan.get('goal_complete') and self._directive_enabled('goal_complete', only, skip):
            result['goal_status'] = 'completed'
            self.current_game.complete_short_term_goal(actor, status='completed', reason='Marked complete by LLM')
        elif plan.get('goal_give_up') and self._directive_enabled('goal_give_up', only, skip):
            result['goal_status'] = 'abandoned'
            self.current_game.complete_short_term_goal(actor, status='abandoned', reason='Abandoned by LLM')

        self._apply_exploration_movement_directives(
            plan,
            actor,
            speaker,
            result,
            only=only,
            skip=skip,
            kick_movement=kick_movement,
        )
        self._apply_item_exchange_directives(
            plan,
            actor,
            speaker,
            result,
            only=only,
            skip=skip,
            kick_movement=kick_movement,
        )

        trade_directive = plan.get('trade')
        if trade_directive and trade_directive.get('target') is not None and self._directive_enabled('trade', only, skip):
            if self._initiate_trade(actor, trade_directive['target']):
                result['executed_actions'].append('trade')

        open_shop_directive = plan.get('open_shop')
        if open_shop_directive and open_shop_directive.get('target') is not None and self._directive_enabled('open_shop', only, skip):
            if self._open_merchant_shop(actor, open_shop_directive['target']):
                result['executed_actions'].append('open_shop')

        request_item_directive = plan.get('request_item')
        if request_item_directive and request_item_directive.get('item') and self._directive_enabled('request_item', only, skip):
            request_target = request_item_directive.get('target') or speaker
            if request_target is not None and self._request_item_from_target(
                actor,
                request_target,
                request_item_directive['item'],
            ):
                result['executed_actions'].append('request_item')

        accept_gift_directive = plan.get('accept_gift')
        if accept_gift_directive and accept_gift_directive.get('target') is not None and self._directive_enabled('accept_gift', only, skip):
            if self._accept_gift_from_speaker(
                actor,
                accept_gift_directive['target'],
                item_hint=accept_gift_directive.get('item'),
            ):
                result['executed_actions'].append('accept_gift')

        if self.current_game.get_current_battle() is None:
            execution_username = self._execution_username_for(actor)
            pov_entities = [actor]

            request_check = plan.get('request_check')
            if request_check and request_check.get('target') is not None and self._directive_enabled('request_check', only, skip):
                self._log_requested_check(
                    actor,
                    request_check['target'],
                    request_check['skill'],
                    dc=request_check.get('dc'),
                )
                result['executed_actions'].append('request_check')

            interact_directive = plan.get('interact')
            if interact_directive and interact_directive.get('target') is not None and self._directive_enabled('interact', only, skip):
                interact_action = self.build_interact_action(
                    actor,
                    interact_directive['target'],
                    action_name=interact_directive.get('action'),
                )
                if interact_action is not None:
                    self.current_game.commit_and_update(execution_username, interact_action, pov_entities)
                    result['executed_actions'].append('interact')

        if advance_time and self.current_game.get_current_battle() is None:
            self.current_game.advance_world_time(
                seconds=6,
                trigger_environment=not result['executed_actions'],
            )

        return result

    def _inventory_qty(self, entity: Entity, item_slug: str) -> int:
        inventory = getattr(entity, 'inventory', None) or {}
        try:
            return int((inventory.get(item_slug) or {}).get('qty') or 0)
        except (TypeError, ValueError):
            return 0

    def _actor_has_offer_source(self, actor: Entity, item_slug: str) -> bool:
        if self._inventory_qty(actor, item_slug) > 0:
            return True
        if self._find_offerable_map_object(actor, item_slug) is not None:
            return True
        return find_container_with_item(self.current_game, actor, item_slug) is not None

    def _item_exchange_action_sequence(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        sequence = list(plan.get('action_sequence') or [])
        if sequence:
            return sequence

        legacy: List[Dict[str, Any]] = []
        retrieve_directive = plan.get('retrieve')
        if retrieve_directive and retrieve_directive.get('item'):
            legacy.append({'kind': 'retrieve', **retrieve_directive})
        store_directive = plan.get('store')
        if store_directive and store_directive.get('item'):
            legacy.append({'kind': 'store', **store_directive})
        pickup_directive = plan.get('pickup')
        if pickup_directive and pickup_directive.get('item'):
            legacy.append({'kind': 'pickup', **pickup_directive})
        offer_item = plan.get('offer_item')
        if offer_item and offer_item.get('item'):
            legacy.append({'kind': 'offer_item', **offer_item})
        return legacy

    def _apply_item_exchange_directives(
        self,
        plan: Dict[str, Any],
        actor: Entity,
        speaker: Entity,
        result: Dict[str, Any],
        *,
        only: Optional[set] = None,
        skip: Optional[set] = None,
        kick_movement: bool = False,
    ) -> None:
        for entry in self._item_exchange_action_sequence(plan):
            kind = entry.get('kind')
            if kind == 'retrieve' and entry.get('item') and self._directive_enabled('retrieve', only, skip):
                container = entry.get('container')
                if container is not None:
                    ok, message = retrieve_from_container(
                        self.current_game,
                        actor,
                        container,
                        entry['item'],
                        qty=entry.get('qty') or 1,
                    )
                    if ok:
                        result['executed_actions'].append('retrieve')
                        self._log_witnessed_offer_outcome(actor, actor, message)
                    else:
                        if 'not within reach' in str(message or '').lower():
                            target_info = {
                                'kind': 'object',
                                'target': container,
                            }
                            if self._schedule_approach_to_target_info(
                                actor,
                                target_info,
                                speaker,
                                result,
                                kick_movement=kick_movement,
                            ):
                                logger.info(
                                    'Scheduled approach to %s after out-of-reach retrieve for %s',
                                    entity_label(container),
                                    entry['item'],
                                )
                        logger.info(
                            'Retrieve suppressed for %s (%s): %s',
                            entity_label(actor),
                            entry['item'],
                            message,
                        )
            elif kind == 'store' and entry.get('item') and self._directive_enabled('store', only, skip):
                container = entry.get('container')
                if container is not None:
                    ok, message = store_in_container(
                        self.current_game,
                        actor,
                        container,
                        entry['item'],
                        qty=entry.get('qty') or 1,
                    )
                    if ok:
                        result['executed_actions'].append('store')
                        self._log_witnessed_offer_outcome(actor, actor, message)
                    else:
                        logger.info(
                            'Store suppressed for %s (%s): %s',
                            entity_label(actor),
                            entry['item'],
                            message,
                        )
            elif kind == 'pickup' and entry.get('item') and self._directive_enabled('pickup', only, skip):
                ok, message = pickup_ground_item(
                    self.current_game,
                    actor,
                    entry['item'],
                    qty=entry.get('qty') or 1,
                )
                if ok:
                    result['executed_actions'].append('pickup')
                    self._log_witnessed_offer_outcome(actor, actor, message)
                else:
                    logger.info('Pickup suppressed for %s (%s): %s', entity_label(actor), entry['item'], message)
            elif kind == 'offer_item' and entry.get('item') and self._directive_enabled('offer_item', only, skip):
                offer_target = entry.get('target') or speaker
                if offer_target is not None:
                    open_shop = plan.get('open_shop') or {}
                    shop_target = open_shop.get('target')
                    if shop_target is not None:
                        shop_uid = getattr(shop_target, 'entity_uid', shop_target)
                        offer_uid = getattr(offer_target, 'entity_uid', offer_target)
                        if shop_uid and shop_uid == offer_uid:
                            logger.info(
                                'Skipping offer_item prompt for %s because open_shop is active',
                                entity_label(offer_target),
                            )
                            continue
                    can_offer, block_reason = self._can_offer_item(
                        actor,
                        offer_target,
                        entry['item'],
                    )
                    if can_offer and self._offer_item_with_prompt(
                        actor,
                        offer_target,
                        entry['item'],
                        auto_use=bool(entry.get('auto_use')),
                    ):
                        result['executed_actions'].append('offer_item')
                    elif not can_offer:
                        logger.info(
                            'Offer item suppressed for %s -> %s (%s): %s',
                            entity_label(actor),
                            entity_label(offer_target),
                            entry['item'],
                            block_reason,
                        )

    def _can_offer_item(self, actor: Entity, target: Entity, item_slug: str) -> Tuple[bool, str]:
        return evaluate_offer_block(
            self.game_session,
            actor,
            target,
            item_slug,
            game_properties=self._game_properties(),
            actor_has_map_item=self._actor_has_offer_source(actor, item_slug),
        )

    def offer_item_guidance_for_conversation(
        self,
        actor: Entity,
        speaker: Entity = None,
        player_speaker: Entity = None,
    ) -> str:
        """Prompt lines from campaign ``conversation_item_offers`` (and optional NPC overrides)."""
        lines = offer_guidance_lines(
            self.game_session,
            actor,
            speaker,
            player_speaker=player_speaker,
            game_properties=self._game_properties(),
            actor_has_map_item_fn=lambda entity, slug: self._actor_has_offer_source(entity, slug),
        )
        return '\n'.join(lines)

    def container_serving_guidance_for_conversation(self, actor: Entity) -> str:
        """Prompt lines listing nearby container stock and the retrieve-then-offer workflow."""
        if actor is None or self.current_game is None:
            return ''

        containers = nearby_map_containers(self.current_game, actor, range_ft=30)
        stocked_lines = []
        distant_lines = []
        for container in containers:
            effective_range = container_interaction_range_ft(actor, container)
            stock = format_container_contents(container)
            if not stock or stock == 'empty':
                continue
            handle = getattr(container, 'entity_uid', None) or mention_handle_for(container)
            label = entity_label(container)
            line = f"  - @{handle} ({label}): {stock}"
            if container_is_accessible(actor, container, self.current_game, range_ft=effective_range):
                stocked_lines.append(line)
            else:
                distant_lines.append(line)

        if not stocked_lines and not distant_lines:
            return ''

        blocks = []
        if stocked_lines:
            blocks.append(
                "Container stock and serving workflow:\n"
                "- When you do not carry an item, fetch it with "
                "[RETRIEVE: item=<item_slug>, target=@container_handle, qty=1] then "
                "[OFFER_ITEM: item=<item_slug>, target=speaker].\n"
                "- You may also use [OFFER_ITEM] directly when the item is in an open container within reach; "
                "the server transfers from the container when the listener accepts.\n"
                "- Use [LIST_CONTAINER: target=@container_handle] to double-check stock before promising service.\n"
                "- Nearby containers you can reach right now:\n"
                + '\n'.join(stocked_lines)
            )
        if distant_lines:
            blocks.append(
                "Containers visible but not within reach yet (use "
                "[APPROACH: target=@container_handle, distance=5] before [RETRIEVE]):\n"
                + '\n'.join(distant_lines)
            )
        return '\n'.join(blocks)

    def animal_communication_guidance_for_conversation(
        self,
        observer: Entity,
        speaker: Entity = None,
        *,
        nearby_entities=None,
    ) -> str:
        lines = animal_communication_guidance_lines(
            self.game_session,
            observer,
            speaker=speaker,
            nearby_entities=nearby_entities,
        )
        return '\n'.join(lines)

    def witnessed_events_summary(self, observer: Entity, *, limit: int = 10) -> str:
        """Recent console lines the NPC was scoped to witness (item trades, scroll use, etc.)."""
        output_logger = getattr(self.current_game, 'output_logger', None)
        lines = witnessed_action_lines(output_logger, observer, limit=limit)
        return format_witnessed_actions_summary(lines)

    def _offer_item_aliases(self, item_slug: str) -> set:
        aliases = {str(item_slug or '').strip().lower()}
        cfg = merged_offer_configs(self._game_properties()).get(item_slug) or {}
        if isinstance(cfg, dict):
            for alias in cfg.get('aliases') or []:
                aliases.add(str(alias).strip().lower().replace(' ', '_'))
        return aliases

    def _find_offerable_map_object(self, actor: Entity, item_slug: str):
        try:
            battle_map = self.current_game.get_map_for_entity(actor)
        except Exception:
            battle_map = None
        if battle_map is None:
            return None

        raw_objects = getattr(battle_map, 'interactable_objects', None)
        if not isinstance(raw_objects, Mapping):
            return None

        aliases = self._offer_item_aliases(item_slug)
        for obj in list(raw_objects.keys()):
            obj_type = str(getattr(obj, 'type', '') or '').strip().lower()
            obj_name = str(getattr(obj, '_name', '') or '').strip().lower()
            if obj_type in aliases or obj_name in aliases:
                return obj
        return None

    def _log_witnessed_offer_outcome(self, actor: Entity, target: Entity, message: str) -> None:
        output_logger = getattr(self.current_game, 'output_logger', None)
        log_witnessed_action(
            output_logger,
            self.game_session,
            message,
            source=actor,
            target=target,
        )

    def _notify_item_offer_resolution(
        self,
        actor: Entity,
        target: Entity,
        item_slug: str,
        *,
        accepted: bool,
        effect_message: Optional[str] = None,
    ) -> None:
        try:
            from webapp.blueprints.helpers.runtime_state import get_conversation_service

            service = get_conversation_service()
            if service is None:
                return
            owners = human_controlled_usernames(self.current_game, target)
            service.handle_npc_item_offer_resolution(
                actor,
                target,
                item_slug,
                accepted=accepted,
                effect_message=effect_message,
                requesting_username=owners[0] if owners else None,
                proactive_reply=accepted,
            )
        except Exception:
            logger.warning(
                'Failed to notify NPC %s of item offer resolution for %s',
                entity_label(actor),
                item_slug,
                exc_info=True,
            )

    def _apply_offer_accept_effect(self, actor: Entity, target: Entity, item_slug: str) -> Optional[str]:
        game_properties = self._game_properties()
        if not (on_accept_auto_use(item_slug, game_properties=game_properties, actor=actor)):
            return None
        effect = accept_effect_for_item(item_slug, game_properties=game_properties, actor=actor)
        if effect == 'animal_communication':
            grant_animal_communication(self.game_session, entity=target)
            target.deduct_item(item_slug, 1)
            expiration = animal_communication_expires_at(self.game_session, target)
            return (
                f"{entity_label(target)} reads the scroll and can understand beasts "
                f"until game time {expiration}."
            )
        return None

    def _is_offer_accept_response(self, payload: Dict[str, Any]) -> bool:
        raw = (payload or {}).get('response', '')

        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw == 1

        text = str(raw or '').strip().lower()
        if not text:
            return False

        # Allow punctuation/spacing variants such as "OK.", " yes ", etc.
        normalized = ''.join(ch for ch in text if ch.isalnum() or ch in {'_', '-'})
        return normalized in {
            'y',
            'yes',
            'accept',
            'accepted',
            'take',
            'ok',
            'okay',
            'true',
            '1',
        }

    def _offer_item_with_prompt(self, actor: Entity, target: Entity, item_slug: str, auto_use: bool = False) -> bool:
        can_offer, _reason = self._can_offer_item(actor, target, item_slug)
        if not can_offer:
            return False

        source_qty = self._inventory_qty(actor, item_slug)
        map_object = None
        container_source = None
        if source_qty <= 0:
            container_source = find_container_with_item(self.current_game, actor, item_slug)
            if container_source is None:
                map_object = self._find_offerable_map_object(actor, item_slug)
                if map_object is None:
                    return False

        target_owners = human_controlled_usernames(self.current_game, target)
        if not target_owners:
            return self._offer_item_via_dm_llm(actor, target, item_slug, auto_use=auto_use)

        prompt_message = f"{entity_label(actor)} offers {item_slug.replace('_', ' ')} to {entity_label(target)}. Accept item?"

        def _emit_offer_ui_refresh():
            try:
                self._emit_refresh_map_if_allowed()
                self.current_game.socketio.emit('message', {
                    'type': 'turn',
                    'message': {'game_time': self.game_session.game_time},
                })
            except Exception:
                pass

        def _callback(payload):
            accepted = self._is_offer_accept_response(payload)

            if not accepted:
                self._log_witnessed_offer_outcome(
                    actor,
                    target,
                    f"{entity_label(target)} declines the offered item.",
                )
                self._notify_item_offer_resolution(
                    actor,
                    target,
                    item_slug,
                    accepted=False,
                )
                _emit_offer_ui_refresh()
                return

            delivered = False
            current_qty = int((getattr(actor, 'inventory', {}) or {}).get(item_slug, {}).get('qty') or 0)
            if current_qty > 0:
                removed_item = actor.deduct_item(item_slug, 1)
                if removed_item:
                    target.add_item(item_slug, 1, source_item=removed_item)
                    delivered = True
            elif container_source is not None:
                delivered, _message = give_from_container_to_entity(
                    self.current_game,
                    container_source,
                    target,
                    item_slug,
                    qty=1,
                )
            elif map_object is not None:
                try:
                    local_map = self.current_game.get_map_for_entity(actor)
                    local_map.remove(map_object)
                    self._emit_refresh_map_if_allowed()
                except Exception:
                    pass
                target.add_item(item_slug, 1)
                delivered = True

            if not delivered:
                self._log_witnessed_offer_outcome(
                    actor,
                    target,
                    f"{entity_label(target)} could not receive the offered item.",
                )
                _emit_offer_ui_refresh()
                return

            record_completed_item_offer(self.game_session, actor, target, item_slug)

            effect_message = None
            if auto_use or on_accept_auto_use(
                item_slug,
                game_properties=self._game_properties(),
                actor=actor,
            ):
                effect_message = self._apply_offer_accept_effect(actor, target, item_slug)
            if effect_message:
                self._log_witnessed_offer_outcome(actor, target, effect_message)
                self._notify_item_offer_resolution(
                    actor,
                    target,
                    item_slug,
                    accepted=True,
                    effect_message=effect_message,
                )
                _emit_offer_ui_refresh()
                return

            self._log_witnessed_offer_outcome(
                actor,
                target,
                f"{entity_label(target)} accepts {item_slug.replace('_', ' ')}.",
            )
            self._notify_item_offer_resolution(
                actor,
                target,
                item_slug,
                accepted=True,
            )
            _emit_offer_ui_refresh()

        self.current_game.prompt(
            prompt_message,
            callback=_callback,
            options=['Yes', 'No'],
            usernames=target_owners,
        )
        return True

    def _get_llm_handler(self):
        try:
            from webapp.blueprints.helpers.runtime_state import get_llm_handler
            return get_llm_handler()
        except Exception:
            return None

    def _offer_item_via_dm_llm(self, actor: Entity, target: Entity, item_slug: str, auto_use: bool = False) -> bool:
        verdict = adjudicate_item_exchange_with_dm_llm(
            self._get_llm_handler(),
            actor,
            target,
            intent='offer',
            item_slug=item_slug,
            session=self.game_session,
        )
        if not verdict.get('accepted'):
            self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'The offer is declined.')
            self._notify_item_offer_resolution(actor, target, item_slug, accepted=False)
            return False

        transfer = verdict.get('transfer')
        if transfer and apply_transfer_payload(actor, target, transfer, battle=self.current_game.get_current_battle()):
            record_completed_item_offer(self.game_session, actor, target, item_slug)
            if auto_use or on_accept_auto_use(
                item_slug,
                game_properties=self._game_properties(),
                actor=actor,
            ):
                effect_message = self._apply_offer_accept_effect(actor, target, item_slug)
                if effect_message:
                    self._log_witnessed_offer_outcome(actor, target, effect_message)
                    self._notify_item_offer_resolution(
                        actor,
                        target,
                        item_slug,
                        accepted=True,
                        effect_message=effect_message,
                    )
                    return True
            self._log_witnessed_offer_outcome(
                actor,
                target,
                verdict.get('reason') or f"{entity_label(target)} accepts {item_slug.replace('_', ' ')}.",
            )
            self._notify_item_offer_resolution(actor, target, item_slug, accepted=True)
            try:
                self._emit_refresh_map_if_allowed()
            except Exception:
                pass
            return True
        self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'The offer is declined.')
        return False

    def _open_merchant_shop(self, merchant: Entity, customer: Entity) -> bool:
        """Open the priced merchant UI for a human-controlled customer."""
        from natural20.utils.merchant import is_merchant
        from webapp.merchant_service import open_merchant_shop_dialog

        if merchant is None or customer is None or not is_merchant(merchant):
            return False
        if not is_human_controlled(self.current_game, customer):
            return False

        delivered = open_merchant_shop_dialog(
            self.current_game,
            merchant=merchant,
            buyer=customer,
            prompt=f"{entity_label(merchant)} shows you what is for sale.",
        )
        if delivered:
            self._log_witnessed_offer_outcome(
                merchant,
                customer,
                f"{entity_label(merchant)} opens the shop for {entity_label(customer)}.",
            )
        return delivered

    def _initiate_trade(self, actor: Entity, target: Entity) -> bool:
        if actor is None or target is None:
            return False

        if is_human_controlled(self.current_game, target):
            prompt = (
                f"{entity_label(actor)} wants to trade items with you. "
                "Use the transfer window to exchange goods."
            )
            if open_item_transfer_dialog(
                self.current_game,
                operator_entity=target,
                counterparty_entity=actor,
                action_type='loot',
                prompt=prompt,
            ):
                self._log_witnessed_offer_outcome(
                    actor,
                    target,
                    f"{entity_label(actor)} opens trade with {entity_label(target)}.",
                )
                return True
            return False

        if is_human_controlled(self.current_game, actor):
            prompt = f"Trade with {entity_label(target)}."
            return open_item_transfer_dialog(
                self.current_game,
                operator_entity=actor,
                counterparty_entity=target,
                action_type='loot',
                prompt=prompt,
            )

        verdict = adjudicate_item_exchange_with_dm_llm(
            self._get_llm_handler(),
            actor,
            target,
            intent='trade',
            session=self.game_session,
        )
        if verdict.get('accepted') and verdict.get('transfer'):
            apply_transfer_payload(actor, target, verdict['transfer'], battle=self.current_game.get_current_battle())
            self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'An exchange is made.')
            try:
                self._emit_refresh_map_if_allowed()
            except Exception:
                pass
            return True
        self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'No trade is agreed.')
        return False

    def _request_item_from_target(self, actor: Entity, target: Entity, item_slug: str) -> bool:
        if actor is None or target is None or not item_slug:
            return False

        if is_human_controlled(self.current_game, target):
            prompt = (
                f"{entity_label(actor)} asks you for {item_slug.replace('_', ' ')}. "
                "Use the transfer window to hand over items."
            )
            if open_item_transfer_dialog(
                self.current_game,
                operator_entity=target,
                counterparty_entity=actor,
                action_type='give',
                prompt=prompt,
            ):
                self._log_witnessed_offer_outcome(
                    actor,
                    target,
                    f"{entity_label(actor)} asks {entity_label(target)} for {item_slug.replace('_', ' ')}.",
                )
                return True
            return False

        verdict = adjudicate_item_exchange_with_dm_llm(
            self._get_llm_handler(),
            actor,
            target,
            intent='request',
            item_slug=item_slug,
            session=self.game_session,
        )
        if verdict.get('accepted') and verdict.get('transfer'):
            apply_transfer_payload(actor, target, verdict['transfer'], battle=self.current_game.get_current_battle())
            self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'The item changes hands.')
            try:
                self._emit_refresh_map_if_allowed()
            except Exception:
                pass
            return True
        self._log_witnessed_offer_outcome(actor, target, verdict.get('reason') or 'The request is refused.')
        return False

    def _accept_gift_from_speaker(self, actor: Entity, target: Entity, item_hint: Optional[str] = None) -> bool:
        """Open the give-item dialog when a player offers to hand something to this NPC."""
        if actor is None or target is None:
            return False
        if not is_human_controlled(self.current_game, target):
            return False
        if has_active_npc_gift_transfer(
            getattr(actor, 'entity_uid', None),
            getattr(target, 'entity_uid', None),
        ):
            return True

        session_id = register_npc_gift_transfer_session(
            getattr(actor, 'entity_uid', None),
            getattr(target, 'entity_uid', None),
            item_hint=item_hint,
        )
        item_text = ''
        if item_hint:
            item_text = f" {str(item_hint).replace('_', ' ')}"
        prompt = (
            f"{entity_label(actor)} is ready to receive{item_text}. "
            "Use the transfer window to give items."
        )
        if open_item_transfer_dialog(
            self.current_game,
            operator_entity=target,
            counterparty_entity=actor,
            action_type='give',
            prompt=prompt,
            transfer_session_id=session_id,
        ):
            self._log_witnessed_offer_outcome(
                actor,
                target,
                f"{entity_label(actor)} opens the item transfer window for {entity_label(target)}.",
            )
            return True

        pop_npc_gift_transfer_session(session_id)
        return False

    def ground_items_summary(self, actor: Entity) -> str:
        entries = nearby_ground_items(self.current_game, actor)
        if not entries:
            return ''
        parts = []
        for entry in entries[:8]:
            parts.append(f"{entry['item'].replace('_', ' ')} x{entry['qty']}")
        return 'Ground items within reach: ' + ', '.join(parts) + '.'

    def execute_scheduled_goal(
        self,
        entity_uid: str,
        llm_conversation_handler,
        *,
        advance_time: bool = True,
        extra_system_context: str = '',
    ) -> Optional[Dict[str, Any]]:
        entity = self.current_game.get_entity_by_uid(entity_uid)
        if entity is None:
            return None

        goal_state = self.current_game.get_short_term_goal(entity)
        if goal_state is None or goal_state.get('status') != 'active':
            return None

        requester = None
        requester_uid = goal_state.get('requester_uid')
        if requester_uid:
            try:
                requester = self.game_session.entity_by_uid(requester_uid)
            except Exception:
                requester = None

        if not getattr(entity, 'dialog', False):
            self.current_game.complete_short_term_goal(entity, status='abandoned', reason='Entity cannot use dialog goals')
            return {'goal_status': 'abandoned'}

        conversation_id = f"{entity.entity_uid}:goal"
        system_prompt = self.goal_execution_prompt(entity, goal_state, requester=requester)
        if extra_system_context:
            system_prompt += str(extra_system_context)
        llm_conversation_handler.create_conversation(conversation_id, system_prompt)
        try:
            llm_conversation_handler.conversations[conversation_id]['system_prompt'] = system_prompt
        except Exception:
            pass

        llm_conversation_handler.add_message(conversation_id, 'user', self.goal_turn_snapshot(entity, goal_state, requester=requester))
        response = llm_conversation_handler.generate_response(conversation_id)
        plan = self.build_conversation_response_plan(
            response,
            entity,
            speaker=requester,
            llm_conversation_handler=llm_conversation_handler,
        )
        directive_result = self.apply_response_plan_directives(
            plan,
            entity,
            speaker=requester,
            advance_time=advance_time,
            kick_movement=True,
        )
        self.current_game.record_short_term_goal_history(entity, {
            'time': self.game_session.game_time,
            'response': response,
            'executed_actions': list(directive_result['executed_actions']),
            'goal_status': directive_result.get('goal_status') or 'active',
        })
        return directive_result

    def movement_execution_prompt(self, entity: Entity, task: Dict[str, Any], requester: Entity = None) -> str:
        requester_label = entity_label(requester) if requester is not None else 'nobody in particular'
        target_label = self.movement_helper.describe_target(task.get('target_info'))
        mode = task.get('mode', 'move_to')
        door_lines = ''
        if task.get('route_blocker') or task.get('mode') in ('move_to', 'follow'):
            door_lines = (
                "- [INTERACT: target=<door name or @handle>, action=open|unlock|lockpick] "
                "when a closed or locked door blocks your route.\n"
            )
        return (
            f"You are {entity_label(entity)} acting autonomously in a D&D world.\n"
            f"Your active movement task ({mode}) targets: {target_label}.\n"
            f"This task was last requested or influenced by: {requester_label}.\n"
            "Take one 6-second out-of-combat movement tick. Return only optional control tags plus an optional spoken line.\n"
            "Available movement tags:\n"
            "- [MOVE: target=@handle, distance=5] or [MOVE: x=3, y=5] or [MOVE: square=3,5] or [MOVE: target=landmark_id] to retarget.\n"
            "- [FOLLOW: target=@handle, distance=5] to switch into follow mode.\n"
            "- [STOP_MOVE] to cancel a move-to task.\n"
            "- [STOP_FOLLOW] to cancel follow mode.\n"
            "- [APPROACH: target=@handle, distance=5] for a single immediate step instead of a persistent task.\n"
            f"{door_lines}"
            "- [NO_RESPONSE] if you do not say anything aloud this tick.\n"
        )

    def execute_scheduled_movement(
        self,
        entity_uid: str,
        llm_conversation_handler,
        triggered_by_follow: bool = False,
        *,
        allow_movement_llm: bool = True,
        advance_time: bool = True,
        extra_system_context: str = '',
    ) -> Optional[Dict[str, Any]]:
        in_progress = getattr(self, '_movement_tick_in_progress', None)
        if in_progress is None:
            in_progress = set()
            self._movement_tick_in_progress = in_progress
        if entity_uid in in_progress:
            return None

        in_progress.add(entity_uid)
        try:
            return self._execute_scheduled_movement_impl(
                entity_uid,
                llm_conversation_handler,
                triggered_by_follow=triggered_by_follow,
                allow_movement_llm=allow_movement_llm,
                advance_time=advance_time,
                extra_system_context=extra_system_context,
            )
        finally:
            in_progress.discard(entity_uid)

    def _execute_scheduled_movement_impl(
        self,
        entity_uid: str,
        llm_conversation_handler,
        triggered_by_follow: bool = False,
        *,
        allow_movement_llm: bool = True,
        advance_time: bool = True,
        extra_system_context: str = '',
    ) -> Optional[Dict[str, Any]]:
        entity = self.current_game.get_entity_by_uid(entity_uid)
        if entity is None:
            return None

        task = self.current_game.get_npc_movement_task(entity)
        if task is None or task.get('status') != 'active':
            return None

        if self.current_game.get_current_battle() is not None:
            return None

        requester = None
        requester_uid = task.get('requester_uid')
        if requester_uid:
            try:
                requester = self.game_session.entity_by_uid(requester_uid)
            except Exception:
                requester = None

        now = time.time()
        llm_due = task.get('next_run_at') is None or task.get('next_run_at') <= now
        directive_result = {
            'executed_actions': [],
            'movement_status': 'active',
        }
        response = None

        target_info = dict(task.get('target_info') or {})
        if task.get('mode') == 'follow':
            follow_target = None
            target_uid = task.get('target_uid')
            if target_uid:
                try:
                    follow_target = self.game_session.entity_by_uid(target_uid)
                except Exception:
                    follow_target = None
            if follow_target is None:
                self.current_game.complete_npc_movement(
                    entity,
                    status='abandoned',
                    reason='Follow target missing',
                )
                directive_result['movement_status'] = 'abandoned'
                return directive_result
            target_info = {
                'kind': 'entity',
                'target': follow_target,
                'target_spec': mention_handle_for(follow_target),
            }

        viable, abandon_reason = self.movement_helper.movement_target_viable(entity, target_info)
        if not viable:
            self.current_game.complete_npc_movement(
                entity,
                status='abandoned',
                reason=abandon_reason or 'Unreachable movement target',
            )
            directive_result['movement_status'] = 'abandoned'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'abandoned',
                'reason': abandon_reason,
            })
            return directive_result

        route_blocker = self.movement_helper.detect_blocking_door_on_route(entity, target_info)
        if route_blocker is not None:
            task['route_blocker'] = self.movement_helper.serialize_route_blocker(route_blocker)
        else:
            task.pop('route_blocker', None)

        skip_auto_move = False
        if route_blocker is not None and self.try_clear_route_blocking_door(entity, route_blocker, target_info):
            directive_result['executed_actions'].append('interact')
            task.pop('route_blocker', None)
            route_blocker = None
            target_info = dict(task.get('target_info') or target_info)

        distance_ft = task.get('distance_ft', 5)
        if self.movement_helper.movement_reached(entity, target_info, distance_ft=distance_ft):
            self.current_game.complete_npc_movement(
                entity,
                status='completed',
                reason='Destination reached',
            )
            directive_result['movement_status'] = 'completed'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'completed',
            })
            return directive_result

        if int(task.get('attempts') or 0) >= _NPC_MOVEMENT_MAX_ATTEMPTS:
            self.current_game.complete_npc_movement(
                entity,
                status='abandoned',
                reason='Movement attempt limit reached',
            )
            directive_result['movement_status'] = 'abandoned'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            return directive_result

        moved = self._try_commit_npc_move_step(entity, task, target_info, directive_result)
        if moved and self.movement_helper.movement_reached(entity, target_info, distance_ft=distance_ft):
            self.current_game.complete_npc_movement(
                entity,
                status='completed',
                reason='Destination reached',
            )
            directive_result['movement_status'] = 'completed'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'completed',
            })
            return directive_result

        if moved:
            if (
                advance_time
                and task.get('mode') == 'move_to'
                and not triggered_by_follow
            ):
                self.current_game.advance_world_time(
                    seconds=6,
                    trigger_environment=False,
                )
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'active',
                'triggered_by_follow': triggered_by_follow,
            })
            return directive_result

        if not moved:
            task['stuck_ticks'] = int(task.get('stuck_ticks') or 0) + 1

        if int(task.get('stuck_ticks') or 0) >= _NPC_MOVEMENT_STUCK_TICKS:
            self.current_game.complete_npc_movement(
                entity,
                status='abandoned',
                reason='Movement stuck',
            )
            directive_result['movement_status'] = 'abandoned'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'abandoned',
            })
            return directive_result

        need_movement_llm = (
            route_blocker is not None
            or int(task.get('stuck_ticks') or 0) >= _NPC_MOVEMENT_LLM_STUCK_TICKS
        )
        if (
            need_movement_llm
            and allow_movement_llm
            and getattr(entity, 'dialog', False)
            and llm_conversation_handler is not None
            and (llm_due or route_blocker is not None)
        ):
            conversation_id = f"{entity.entity_uid}:movement"
            if int(task.get('stuck_ticks') or 0) == _NPC_MOVEMENT_LLM_STUCK_TICKS:
                self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            system_prompt = self.movement_execution_prompt(entity, task, requester=requester)
            if extra_system_context:
                system_prompt += str(extra_system_context)
            llm_conversation_handler.create_conversation(conversation_id, system_prompt)
            try:
                llm_conversation_handler.conversations[conversation_id]['system_prompt'] = system_prompt
            except Exception:
                pass

            snapshot = self.movement_helper.movement_snapshot(entity, task)
            llm_conversation_handler.add_message(conversation_id, 'user', snapshot)
            response = llm_conversation_handler.generate_response(conversation_id)
            plan = self.build_conversation_response_plan(
                response,
                entity,
                speaker=requester,
                llm_conversation_handler=llm_conversation_handler,
            )
            directive_result = self.apply_response_plan_directives(
                plan,
                entity,
                speaker=requester,
                kick_movement=False,
            )
            task = self.current_game.get_npc_movement_task(entity)
            if task is None or task.get('status') != 'active':
                self.current_game.record_npc_movement_history(entity, {
                    'time': self.game_session.game_time,
                    'response': response,
                    'executed_actions': list(directive_result.get('executed_actions') or []),
                    'movement_status': directive_result.get('movement_status') or 'cancelled',
                })
                return directive_result
            target_info = dict(task.get('target_info') or target_info)
            distance_ft = task.get('distance_ft', distance_ft)
            viable, abandon_reason = self.movement_helper.movement_target_viable(entity, target_info)
            if not viable:
                self.current_game.complete_npc_movement(
                    entity,
                    status='abandoned',
                    reason=abandon_reason or 'Unreachable movement target',
                )
                directive_result['movement_status'] = 'abandoned'
                self._clear_movement_llm_conversation(entity, llm_conversation_handler)
                self.current_game.record_npc_movement_history(entity, {
                    'time': self.game_session.game_time,
                    'response': response,
                    'executed_actions': list(directive_result.get('executed_actions') or []),
                    'movement_status': 'abandoned',
                    'reason': abandon_reason,
                })
                return directive_result
            if route_blocker is not None and 'interact' not in (directive_result.get('executed_actions') or []):
                skip_auto_move = True
            elif 'interact' in (directive_result.get('executed_actions') or []):
                task.pop('route_blocker', None)
                route_blocker = None

        task = self.current_game.get_npc_movement_task(entity)
        if task is None or task.get('status') != 'active':
            return directive_result

        if self.movement_helper.movement_reached(
            entity,
            target_info,
            distance_ft=task.get('distance_ft', 5),
        ):
            self.current_game.complete_npc_movement(
                entity,
                status='completed',
                reason='Destination reached',
            )
            directive_result['movement_status'] = 'completed'
            self._clear_movement_llm_conversation(entity, llm_conversation_handler)
            self.current_game.record_npc_movement_history(entity, {
                'time': self.game_session.game_time,
                'response': response,
                'executed_actions': list(directive_result.get('executed_actions') or []),
                'movement_status': 'completed',
            })
            return directive_result

        moved_this_tick = False
        if not skip_auto_move:
            moved_this_tick = self._try_commit_npc_move_step(entity, task, target_info, directive_result)

        if not moved_this_tick and not skip_auto_move:
            task['stuck_ticks'] = int(task.get('stuck_ticks') or 0) + 1
            if int(task.get('stuck_ticks') or 0) >= _NPC_MOVEMENT_STUCK_TICKS:
                self.current_game.complete_npc_movement(
                    entity,
                    status='abandoned',
                    reason='Movement stuck',
                )
                directive_result['movement_status'] = 'abandoned'
                self._clear_movement_llm_conversation(entity, llm_conversation_handler)
                self.current_game.record_npc_movement_history(entity, {
                    'time': self.game_session.game_time,
                    'response': response,
                    'executed_actions': list(directive_result.get('executed_actions') or []),
                    'movement_status': 'abandoned',
                })
                return directive_result

        if (
            advance_time
            and task.get('mode') == 'move_to'
            and not triggered_by_follow
        ):
            self.current_game.advance_world_time(
                seconds=6,
                trigger_environment=not directive_result.get('executed_actions'),
            )

        refreshed_task = self.current_game.get_npc_movement_task(entity)
        if (
            refreshed_task is not None
            and refreshed_task.get('status') == 'active'
            and self.movement_helper.movement_reached(
                entity,
                target_info,
                distance_ft=refreshed_task.get('distance_ft', 5),
            )
        ):
            self.current_game.complete_npc_movement(
                entity,
                status='completed',
                reason='Destination reached',
            )
            directive_result['movement_status'] = 'completed'

        self.current_game.record_npc_movement_history(entity, {
            'time': self.game_session.game_time,
            'response': response,
            'executed_actions': list(directive_result.get('executed_actions') or []),
            'movement_status': directive_result.get('movement_status') or 'active',
            'triggered_by_follow': triggered_by_follow,
            'route_blocker': task.get('route_blocker'),
        })
        return directive_result

    def goal_execution_prompt(self, entity: Entity, goal_state: Dict[str, Any], requester: Entity = None) -> str:
        requester_label = entity_label(requester) if requester is not None else 'nobody in particular'
        return (
            f"You are {entity_label(entity)} acting autonomously in a D&D world.\n"
            f"Your active short-term goal is: {goal_state.get('goal', '')}\n"
            f"This goal was last requested or influenced by: {requester_label}.\n"
            "Take one 6-second out-of-combat turn. Return only optional control tags plus an optional spoken line.\n"
            "Available control tags:\n"
            "- [APPROACH: target=@handle, distance=5] to move up to full movement speed until you are within the requested distance.\n"
            "- [MOVE: target=@handle, distance=5] or [MOVE: x=3, y=5] to keep moving toward a target each 6-second tick.\n"
            "- [MOVE: target=<landmark_id>] to path toward a named map landmark (see landmarks list).\n"
            "- [FOLLOW: target=@handle, distance=5] to trail someone and move whenever they move.\n"
            "- [STOP_MOVE] or [STOP_FOLLOW] to end movement tasks.\n"
            "- [INTERACT: target=<name or @handle>, action=<interaction>] to use an interactable object.\n"
            "- [INSIGHT: target=speaker] or [INSIGHT: target=@handle] to privately assess whether someone seems truthful before responding.\n"
            "- [REQUEST_CHECK: skill=persuasion|intimidation|arcana|investigation|..., target=speaker|@handle, dc=optional] to ask for a skill check.\n"
            "- [OFFER_ITEM: item=<item_slug>, target=speaker] to offer an item you possess or can serve from a nearby open container (triggers Accept Item Yes/No once per item per listener).\n"
            "- [PICKUP: item=<item_slug>] or [PICKUP: item=<item_slug>, qty=2] to pick up a ground item within reach.\n"
            "- [LIST_CONTAINER: target=@handle] or [LIST_CONTAINER] to inspect bar stock or other nearby containers before deciding.\n"
            "- [ANNOTATIONS: target=@handle] or [ANNOTATIONS] to read staff-only markings you notice on objects (NPC-only; not visible to players).\n"
            "- [RETRIEVE: item=<item_slug>, target=@container, qty=1] to take items from an open container into your inventory.\n"
            "- [STORE: item=<item_slug>, target=@container, qty=2] to deposit items into an open container.\n"
            "- [TRADE: target=speaker|@handle] to open a full item trade.\n"
            "- [OPEN_SHOP: target=speaker|@handle] when you are a merchant with wares — opens your priced shop UI for the customer (use after welcoming them or when they ask to buy).\n"
            "- [REQUEST_ITEM: item=<item_slug>, target=speaker|@handle] to ask someone to hand you an item.\n"
            "- [ACCEPT_GIFT: target=speaker|@handle] when the speaker offers to give you something (or performs a hand-over action). Opens the player's give-item transfer dialog.\n"
            f"{self.campaign_setting_prompt()}\n"
            f"{self.map_landmarks_prompt_for_entity(entity)}\n"
            f"{self.offer_item_guidance_for_conversation(entity, requester)}\n"
            f"{self.animal_communication_guidance_for_conversation(entity, speaker=requester)}\n"
            "- [SET_GOAL: new short goal] to replace your current short-term goal.\n"
            "- [GOAL_COMPLETE] when the current goal is done.\n"
            "- [GOAL_GIVE_UP] when the current goal cannot be completed or is no longer worth pursuing.\n"
            "- [OBSERVE] or [INVENTORY] if you need refreshed world state before deciding.\n"
            "- [LIST_CONTAINER: target=@tavern_bar] to read bar stock when working behind the counter.\n"
            "- [REMEMBER: title=..., summary=..., body=...] to store a lasting memory.\n"
            "- [RECALL: id=<memory_id>] or [RECALL: query=keyword] to retrieve memory details.\n"
            "- [NO_RESPONSE] if you do not say anything aloud this turn.\n"
        )

    def goal_turn_snapshot(self, entity: Entity, goal_state: Dict[str, Any], requester: Entity = None) -> str:
        context = self.get_entity_context(entity)
        nearby_entities = self.get_nearby_entities(entity, range_ft=60, include_extended=True)
        interactable_objects = self.get_interactable_objects(entity, range_ft=60)
        history_entries = goal_state.get('history', [])[-3:]

        lines = [
            f"Goal: {goal_state.get('goal', '')}",
        ]
        try:
            from webapp.blueprints.helpers.time_of_day import get_time_of_day_state, world_time_context_lines
            state = get_time_of_day_state(self.current_game)
            lines.extend(world_time_context_lines(state))
        except Exception:
            lines.append(f"Game time: {self.game_session.game_time}")
        lines.extend([
            f"Your position: {context.get('position')}",
            f"Requester: {entity_label(requester) if requester is not None else 'none'}",
            f"Attempts so far: {goal_state.get('attempts', 0)}",
            "Nearby entities:",
        ])

        if nearby_entities:
            for nearby in nearby_entities[:8]:
                lines.append(
                    f"- {nearby['name']} (@{nearby['mention_handle']}) at {nearby['distance']}ft; reachable_now={nearby['reachable_now']}"
                )
        else:
            lines.append("- none")

        lines.append("Visible interactable objects:")
        if interactable_objects:
            for obj in interactable_objects[:8]:
                actions = ', '.join(obj['actions'])
                lines.append(
                    f"- {obj['name']} (@{obj['mention_handle']}) at {obj['distance_ft']}ft; actions={actions}"
                )
        else:
            lines.append("- none")

        lines.append("Recent goal history:")
        if history_entries:
            for entry in history_entries:
                lines.append(f"- t={entry.get('time')}: {entry}")
        else:
            lines.append("- none")

        lines.append("Choose your next turn.")
        return '\n'.join(lines)

    def _execution_username_for(self, entity: Entity) -> str:
        owners = self.current_game.entity_owners(entity)
        return owners[0] if owners else 'dm'
    
    def parse_language_from_response(self, response: str) -> Tuple[str, str]:
        """
        Parse language specification from AI response.
        
        Args:
            response: The raw response from the AI
            
        Returns:
            Tuple of (language, response_text)
        """
        if not response or "[in" not in response:
            return "common", response
        
        try:
            # Find the start of [in
            start_idx = response.find("[in")
            if start_idx != -1:
                # Find the closing bracket after [in
                end_bracket_idx = response.find("]", start_idx)
                if end_bracket_idx != -1:
                    # Extract language (everything between [in and ])
                    language = response[start_idx + 3:end_bracket_idx].strip()
                    # Extract the rest of the response after the closing bracket
                    response_text = response[end_bracket_idx + 1:].strip()
                    return language, response_text
                else:
                    # No closing bracket found, treat as common
                    return "common", response
            else:
                return "common", response
        except (IndexError, ValueError):
            # Fallback to common if parsing fails
            return "common", response

    def apply_conversation_keywords(
        self,
        response: str,
        receiver: Entity,
        speaker: Entity = None,
        llm_conversation_handler=None,
    ) -> None:
        """Fire keyword side effects after NPC speech is delivered (literal match only)."""
        try:
            keyword_entries = receiver.conversation_keywords()
        except Exception:
            keyword_entries = []

        if not isinstance(keyword_entries, (list, tuple)):
            keyword_entries = []

        matched_entries = []
        for keywords in keyword_entries:
            keyword = str(keywords.get('keyword', '')).strip()
            if not keyword:
                continue
            if self._keyword_matched_by_text(response, keyword):
                matched_entries.append(keywords)

        if not matched_entries:
            self._log_conversation_plan(
                receiver,
                'no literal keyword matches in delivered speech',
                level=logging.DEBUG,
                speech_preview=(response or '')[:120],
            )
            return

        for keywords in matched_entries:
            keyword = str(keywords.get('keyword', '')).strip()
            self._log_conversation_plan(
                receiver,
                'applying conversation keyword side effect',
                keyword=keyword,
            )
            generic_handler = GenericEventHandler(self.game_session, receiver, keywords)
            generic_handler.handle(receiver, opts={'speaker': speaker})

    def _process_rag_commands(
        self,
        response: str,
        speaker: Entity,
        receiver: Entity,
        llm_conversation_handler,
        *,
        include_keywords: bool = True,
    ) -> str:
        """
        Process RAG commands in the response and generate appropriate responses.

        Args:
            response: The response containing RAG commands
            receiver: The entity processing the response
            llm_conversation_handler: The LLM conversation handler

        Returns:
            The processed response
        """
        # Handle hostile state change
        if "[GO_HOSTILE]" in response:
            self._handle_hostile_state_change(receiver)
            response = re.sub(r'\[GO_HOSTILE\]', '', response, flags=re.IGNORECASE).strip()

        if "[GO_FRIENDLY]" in response:
            self._handle_friendly_state_change(receiver)
            response = re.sub(r'\[GO_FRIENDLY\]', '', response, flags=re.IGNORECASE).strip()

        # Handle inventory queries
        if "[INVENTORY" in response or "[LIST_INVENTORY" in response:
            return self._handle_inventory_query(receiver, llm_conversation_handler)

        if "[LIST_CONTAINER" in response:
            return self._handle_list_container_query(response, receiver, llm_conversation_handler)

        if "[ANNOTATIONS" in response:
            return self._handle_annotations_query(response, receiver, llm_conversation_handler)

        # Handle observation requests
        if "[OBSERVE" in response:
            return self._handle_observation_request(receiver, llm_conversation_handler)

        # Handle insight requests
        if "[INSIGHT" in response:
            return self._handle_insight_request(response, receiver, speaker, llm_conversation_handler)

        if "[RECALL" in response:
            return self._handle_recall_request(response, receiver, llm_conversation_handler)

        try:
            keyword_entries = receiver.conversation_keywords()
        except Exception:
            keyword_entries = []

        if not isinstance(keyword_entries, (list, tuple)):
            keyword_entries = []

        if not include_keywords:
            return response

        matched_entries = []
        for keywords in keyword_entries:
            keyword = str(keywords.get('keyword', '')).strip()
            if not keyword:
                continue
            if self._keyword_matched_by_text(response, keyword):
                matched_entries.append(keywords)

        if not matched_entries:
            llm_matches = self._llm_keyword_matches(response, keyword_entries, llm_conversation_handler)
            llm_match_set = set(llm_matches)
            matched_entries = [entry for entry in keyword_entries if str(entry.get('keyword', '')).strip() in llm_match_set]

        for keywords in matched_entries:
            keyword = str(keywords.get('keyword', '')).strip()
            logger.info(f"Processing event for keyword '{keyword}': {keywords}")
            generic_handler = GenericEventHandler(self.game_session, receiver, keywords)
            generic_handler.handle(receiver, opts={'speaker': speaker})
            if keyword:
                response = re.sub(re.escape(keyword), '', response, flags=re.IGNORECASE)

        return response

    def _create_npc_memory(self, actor: Entity, remember_directive: Dict[str, Any], speaker: Entity = None) -> Optional[Dict[str, Any]]:
        store = self._npc_memory_store()
        if store is None or actor is None:
            return None
        entity_uid = getattr(actor, 'entity_uid', None)
        if not entity_uid:
            return None
        try:
            importance = int(remember_directive.get('importance') or 3)
        except (TypeError, ValueError):
            importance = 3
        return store.create(
            entity_uid,
            title=remember_directive.get('title'),
            summary=remember_directive.get('summary'),
            body=remember_directive.get('body'),
            tags=remember_directive.get('tags'),
            importance=importance,
            game_time=self.game_session.game_time,
            source='conversation',
            created_by='npc',
        )

    def _handle_recall_request(self, response: str, receiver: Entity, llm_conversation_handler) -> str:
        if llm_conversation_handler is None:
            return re.sub(RECALL_DIRECTIVE_PATTERN, '', response or '').strip()

        store = self._npc_memory_store()
        if store is None:
            return re.sub(RECALL_DIRECTIVE_PATTERN, '', response or '').strip()

        match = RECALL_DIRECTIVE_PATTERN.search(response or '')
        params = self._parse_named_params(match.group(1) if match else '', positional_keys=('id', 'query'))
        recall_id = (params.get('id') or params.get('memory_id') or '').strip()
        recall_query = (params.get('query') or params.get('q') or '').strip()

        entity_uid = getattr(receiver, 'entity_uid', None)
        recalled_items = []
        if recall_id:
            item = store.get(entity_uid, recall_id) if entity_uid else store.find(recall_id)
            if item is not None:
                recalled_items = [item]
        elif recall_query and entity_uid:
            recalled_items = store.list_summaries(entity_uid, limit=3, query=recall_query)
            recalled_items = [
                store.get(entity_uid, summary['id'])
                for summary in recalled_items
                if summary.get('id')
            ]
            recalled_items = [item for item in recalled_items if item is not None]

        if not recalled_items:
            system_response = '[RECALL] No matching memories found.'
        else:
            system_response = '\n\n'.join(store.format_recall_detail(item) for item in recalled_items)

        llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
        regenerated_response = llm_conversation_handler.generate_response(receiver.entity_uid)
        if regenerated_response:
            _language, regenerated_response = self.parse_language_from_response(regenerated_response)
            return regenerated_response
        return ''

    def _handle_insight_request(self, response: str, receiver: Entity, speaker: Entity, llm_conversation_handler) -> str:
        if llm_conversation_handler is None:
            return ""

        match = INSIGHT_DIRECTIVE_PATTERN.search(response or '')
        params = self._parse_named_params(match.group(1) if match else '', positional_keys=('target',))
        target_spec = params.get('target') or 'speaker'
        target = self.resolve_named_target(receiver, target_spec, speaker=speaker, include_objects=False)
        if target is None:
            return re.sub(INSIGHT_DIRECTIVE_PATTERN, '', response or '').strip()

        try:
            roll = receiver.insight_check(description=f"insight check on {entity_label(target)}")
        except Exception as e:
            logger.error(f"Error rolling insight check for {entity_label(receiver)}: {e}")
            return ""

        statement = self._latest_statement_from_source(receiver, speaker or target)
        assessment = self._evaluate_insight_assessment(receiver, target, statement, roll, llm_conversation_handler)
        system_response = (
            f"[INSIGHT] You rolled {roll} = {roll.result()} while reading {entity_label(target)}. "
            f"DM assessment: {assessment['assessment']}. {assessment['reason']}"
        )
        self._log_social_check(
            f"{entity_label(receiver)} makes an insight check on {entity_label(target)} and rolls {roll} = {roll.result()}. "
            f"Assessment: {assessment['assessment']}. {assessment['reason']}",
            entities=[receiver, target],
        )
        llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
        regenerated_response = llm_conversation_handler.generate_response(receiver.entity_uid)
        if regenerated_response:
            _language, regenerated_response = self.parse_language_from_response(regenerated_response)
            return regenerated_response
        return ""

    def _latest_statement_from_source(self, receiver: Entity, source: Entity) -> str:
        if receiver is None or source is None:
            return ''

        for message in reversed(getattr(receiver, 'memory_buffer', []) or []):
            if message.get('source') == source:
                return message.get('message', '') or ''
        return ''

    def _evaluate_insight_assessment(self, observer: Entity, target: Entity, statement: str, roll, llm_conversation_handler) -> Dict[str, str]:
        default_assessment = {
            'assessment': 'uncertain',
            'reason': 'There is not enough reliable evidence to determine whether the statement is true or deceptive.',
        }

        llm_handler = getattr(llm_conversation_handler, 'llm_hander', None)
        if llm_handler is None:
            return default_assessment

        messages = [
            {
                'role': 'system',
                'content': (
                    'You are the Dungeon Master adjudicating an NPC insight check. '
                    'Use the insight roll as a confidence gate. '
                    'If the roll is low, the evidence is mixed, or the context is insufficient, return uncertain. '
                    'Return JSON only with keys assessment and reason. '
                    'assessment must be one of truthful, lie, or uncertain. '
                    'IMPORTANT: the "reason" field is shown verbatim to the player\'s character. '
                    'Write it strictly in-character and only describe cues the observer could plausibly notice '
                    '(tone of voice, body language, hesitations, eye contact, inconsistencies with what was said earlier). '
                    'NEVER reveal DM-only knowledge such as the target\'s true nature, alignment, stat block, '
                    'hidden identity, illusion/undead/construct status, secret motives, backstory facts the observer '
                    'has not learned in play, or any meta-game information. '
                    'If the roll is too low to learn anything, say so vaguely without referencing hidden truths.'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({
                    'statement': statement,
                    'insight_roll_total': roll.result(),
                    'observer': entity_label(observer),
                    'target': entity_label(target),
                    'dm_context': self._build_dm_insight_context(observer, target, statement),
                }, default=str),
            },
        ]

        try:
            # Isolated JSON adjudication: never continue, never touch DM chat history.
            raw_response = llm_handler.send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'insight_assessment',
                },
            )
        except Exception as e:
            logger.error(f"Error adjudicating insight check for {entity_label(observer)}: {e}")
            return default_assessment

        return self._parse_insight_assessment_response(raw_response, default_assessment)

    def _parse_insight_assessment_response(self, raw_response: str, default_assessment: Dict[str, str]) -> Dict[str, str]:
        if not raw_response:
            return default_assessment

        text = str(raw_response).strip()
        # Prefer fenced JSON body when present (```json ... ```), then fall back
        # to the first JSON object. Ignore any trailing narrative continuation.
        fence = re.search(r'```(?:json|JSON)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            text = match.group(0)

        try:
            payload = json.loads(text)
            assessment = str(payload.get('assessment', 'uncertain')).strip().lower()
            reason = str(payload.get('reason', '')).strip()
        except Exception:
            lowered = text.lower()
            if 'truthful' in lowered or 'honest' in lowered or re.search(r'\btrue\b', lowered):
                assessment = 'truthful'
            elif 'lying' in lowered or 'decept' in lowered or re.search(r'\blie\b', lowered):
                assessment = 'lie'
            else:
                assessment = 'uncertain'
            # Never dump raw JSON / fences into the player-facing reason.
            reason = default_assessment['reason']

        if assessment not in {'truthful', 'lie', 'uncertain'}:
            assessment = 'uncertain'
        if not reason:
            reason = default_assessment['reason']
        reason = self._sanitize_insight_reason(reason, default_assessment['reason'])
        return {'assessment': assessment, 'reason': reason}

    # Sentences containing any of these terms reveal DM-only knowledge that
    # the observing character could not perceive from cues alone. They are
    # dropped from the player-facing reason text. If everything would be
    # dropped, we fall back to a vague default.
    _DM_LEAK_TERMS = (
        'illusion', 'illusory', 'illusionary', 'phantasm',
        'construct', 'undead', 'ghost', 'spectre', 'specter', 'apparition',
        'incorporeal', 'shapechanger', 'doppelganger', 'simulacrum',
        'true form', 'true nature', 'actually a', 'in reality',
        'stat block', 'alignment', 'backstory', 'hit point', 'hp ',
        'meta', 'dm note', 'dungeon master', 'behind the scenes',
        'created by the house', 'manifestation of', 'spirit of',
    )

    def _sanitize_insight_reason(self, reason: str, fallback: str) -> str:
        text = str(reason or '').strip()
        if not text:
            return fallback
        # Split on sentence boundaries while keeping it lightweight; drop any
        # sentence that name-drops DM-only metadata.
        sentences = re.split(r'(?<=[\.\!\?])\s+', text)
        kept = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(term in lowered for term in self._DM_LEAK_TERMS):
                continue
            kept.append(sentence.strip())
        cleaned = ' '.join(part for part in kept if part).strip()
        return cleaned or fallback

    def _build_dm_insight_context(self, observer: Entity, target: Entity, statement: str) -> Dict[str, Any]:
        battle_map = self.current_game.get_map_for_entity(observer)
        player_characters = []
        if battle_map is not None:
            for entity in getattr(battle_map, 'entities', []):
                if isinstance(entity, PlayerCharacter):
                    player_characters.append(entity)

        return {
            'statement': statement,
            'observer': self.get_entity_context(observer),
            'target': self._dm_pc_context(target) if isinstance(target, PlayerCharacter) else self._dm_entity_context(target),
            'all_player_characters': [self._dm_pc_context(entity) for entity in player_characters],
        }

    def _conversation_snippet_between(self, observer: Entity, other: Entity, limit: int = 10) -> List[str]:
        if observer is None or other is None:
            return []

        lines: List[str] = []
        for message in getattr(observer, 'memory_buffer', []) or []:
            if not isinstance(message, dict):
                continue
            source = message.get('source')
            if source not in (observer, other):
                continue
            text = str(message.get('message') or '').strip()
            if not text:
                continue
            speaker_label = entity_label(source)
            lines.append(f"{speaker_label}: {text}")

        if len(lines) > limit:
            lines = lines[-limit:]
        return lines

    def _build_dm_perception_context(
        self,
        observer: Entity,
        target: Entity,
        focus: str = '',
    ) -> Dict[str, Any]:
        battle_map = self.current_game.get_map_for_entity(observer)
        target_context = (
            self._dm_entity_context(target)
            if target is not None and not isinstance(target, PlayerCharacter)
            else self._dm_pc_context(target)
        ) if target is not None else {}

        appearance = ''
        distance_ft = None
        target_position = None
        if battle_map is not None and target is not None:
            appearance = self._outward_appearance_for(observer, target, battle_map)
            try:
                distance_ft = int(round(battle_map.distance(observer, target) * battle_map.feet_per_grid))
            except Exception:
                distance_ft = None
            try:
                target_position = battle_map.entity_or_object_pos(target)
            except Exception:
                target_position = None

        nearby_objects = []
        if battle_map is not None:
            try:
                for obj in self.get_interactable_objects(observer, range_ft=15, require_enabled_actions=False)[:5]:
                    nearby_objects.append({
                        'name': obj.get('name'),
                        'distance_ft': obj.get('distance_ft'),
                        'appearance': obj.get('appearance'),
                        'position': obj.get('position'),
                    })
            except Exception:
                nearby_objects = []

        map_name = getattr(battle_map, 'name', None) or getattr(battle_map, 'map_name', None)
        return {
            'focus': str(focus or '').strip(),
            'observer': self.get_entity_context(observer),
            'target': target_context,
            'target_appearance': appearance,
            'distance_ft': distance_ft,
            'target_position': target_position,
            'map_name': map_name,
            'recent_conversation': self._conversation_snippet_between(observer, target),
            'nearby_objects': nearby_objects,
        }

    def _parse_perception_description_response(self, raw_response: str, default: Dict[str, str]) -> Dict[str, str]:
        if not raw_response:
            return default

        text = str(raw_response).strip()
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

        if not description:
            return default
        return {'description': description}

    def _evaluate_perception_description(
        self,
        observer: Entity,
        target: Entity,
        focus: str,
        roll,
        llm_conversation_handler,
    ) -> Dict[str, str]:
        roll_total = None
        try:
            roll_total = roll.result()
        except Exception:
            roll_total = None

        fallback = self._build_perception_description_fallback(
            observer,
            target,
            focus=focus,
            roll_total=roll_total,
        )

        llm_handler = self._resolve_perception_llm_handler(llm_conversation_handler)
        if llm_handler is None:
            return fallback

        messages = [
            {
                'role': 'system',
                'content': (
                    'You are the Dungeon Master describing what a player character can SEE during an active conversation. '
                    'Always include a concrete physical description of the target when one is provided in dm_context '
                    '(outward appearance, race, visible class cues, gear). '
                    'Layer in visible injury, health, and status effects when present. '
                    'Use recent conversation to suggest facial expression, posture, and manner—not hidden motives. '
                    'Use the perception roll as a detail gate: low rolls yield vague impressions; high rolls reveal subtle visual cues. '
                    'Ground the description in the provided scene and recent conversation. '
                    'Write second-person present tense ("You notice..."). '
                    'Describe only visible, plausible details—never stat blocks, hidden motives, or DM-only secrets. '
                    'Do not say nothing stands out when studying a person; describe what they look like at minimum. '
                    'Return JSON only with key description (one or two short paragraphs).'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({
                    'perception_roll_total': roll_total,
                    'focus': str(focus or '').strip(),
                    'dm_context': self._build_dm_perception_context(observer, target, focus=focus),
                }, default=str),
            },
        ]

        try:
            raw_response = llm_handler.send_message(
                messages,
                context={
                    'response_mode': 'conversation',
                    'skip_continuation': True,
                    'log_label': 'perception_description',
                },
            )
        except Exception as exc:
            logger.error(f"Error describing perception scene for {entity_label(observer)}: {exc}")
            return fallback

        parsed = self._parse_perception_description_response(raw_response, fallback)
        description = str(parsed.get('description') or '').strip()
        if not description or description == PERCEPTION_EMPTY_DESCRIPTION:
            return fallback
        return parsed

    def _dm_entity_context(self, entity: Entity) -> Dict[str, Any]:
        context = self.get_entity_context(entity)
        context['background'] = entity.backstory() if hasattr(entity, 'backstory') else ''
        context['statuses'] = list(getattr(entity, 'statuses', []) or [])
        if hasattr(entity, 'current_effects') and callable(getattr(entity, 'current_effects')):
            try:
                context['effects'] = [str(effect['effect']) for effect in entity.current_effects()]
            except Exception:
                context['effects'] = []
        else:
            context['effects'] = []
        context['recent_memory'] = [
            {
                'source': entity_label(message.get('source')),
                'message': message.get('message'),
                'time': message.get('time'),
            }
            for message in (getattr(entity, 'memory_buffer', []) or [])[-8:]
        ]
        output_logger = getattr(self.current_game, 'output_logger', None)
        if output_logger is not None and hasattr(output_logger, 'get_logs_for_entity'):
            try:
                context['recent_logs'] = output_logger.get_logs_for_entity(entity)[-12:]
            except Exception:
                context['recent_logs'] = []
        else:
            context['recent_logs'] = []
        try:
            from webapp.blueprints.helpers.runtime_state import get_game_context_provider
            provider = get_game_context_provider()
            if provider is not None:
                details = provider.get_entity_details(entity_label(entity))
                if isinstance(details, dict):
                    context['dm_details'] = details
        except Exception:
            pass
        return context

    def _dm_pc_context(self, entity: Entity) -> Dict[str, Any]:
        context = self._dm_entity_context(entity)
        context['is_player_character'] = isinstance(entity, PlayerCharacter)
        return context

    def _log_social_check(self, message: str, entities: List[Entity]):
        output_logger = getattr(self.current_game, 'output_logger', None)
        if output_logger is None or not hasattr(output_logger, 'log'):
            return

        entity_uids = [getattr(entity, 'entity_uid', None) for entity in (entities or []) if entity is not None]
        output_logger.log(message, visibility={'kind': 'entities', 'entity_uids': entity_uids})

    def _log_requested_check(self, actor: Entity, target: Entity, skill: str, dc: Optional[int] = None):
        dc_suffix = f" (DC {dc})" if dc is not None else ''
        self._log_social_check(
            f"{entity_label(actor)} requests a {skill} check from {entity_label(target)}{dc_suffix}.",
            entities=[actor, target],
        )

    def _handle_friendly_state_change(self, receiver: Entity) -> str:
        """
        Handle friendly state change command.

        Args:
            receiver: The entity changing to friendly state

        Returns:
            Empty string (response will be handled by caller)
        """
        try:
            receiver.update_state('active')
            self.current_game.update_group(receiver, 'a')
            logger.info(f"Entity {receiver.label()} is now in the friendly group")
            return ""
        except Exception as e:
            logger.error(f"Error changing entity to friendly state: {e}")
            return ""

    def _handle_hostile_state_change(self, receiver: Entity) -> str:
        """
        Handle hostile state change command.
        
        Args:
            receiver: The entity changing to hostile state
            
        Returns:
            Empty string (response will be handled by caller)
        """
        try:
            receiver.update_state('active')
            self.current_game.update_group(receiver, 'b')
            self._activate_group_allies(receiver)
            logger.info(f"Entity {receiver.label()} is now in the hostile group")
            return ""
        except Exception as e:
            logger.error(f"Error changing entity to hostile state: {e}")
            return ""

    def _activate_group_allies(self, receiver: Entity):
        """Wake passive allies in the same group and map when hostility starts."""
        battle_map = None
        try:
            battle_map = self.current_game.get_map_for_entity(receiver)
        except Exception:
            battle_map = None
        if battle_map is None:
            return

        receiver_group = getattr(receiver, 'group', None)
        if not receiver_group:
            return

        for ally in getattr(battle_map, 'entities', []):
            if ally is receiver:
                continue
            if getattr(ally, 'group', None) != receiver_group:
                continue
            try:
                ally_group = getattr(self.current_game, '_entity_group', lambda e: getattr(e, 'group', None))(ally)
                if getattr(self.current_game, '_faction_is_neutral', lambda _g: False)(ally_group):
                    continue
                if ally.passive():
                    ally.update_state('active')
                # Ensure group affinity stays aligned for downstream hostility checks.
                self.current_game.update_group(ally, receiver_group)
            except Exception:
                continue
    
    def _handle_annotations_query(self, response: str, receiver: Entity, llm_conversation_handler) -> str:
        """Handle NPC-only annotation queries before regenerating the reply."""
        if not self._observer_can_read_annotations(receiver):
            return re.sub(ANNOTATIONS_PATTERN, '', response or '').strip()

        try:
            match = ANNOTATIONS_PATTERN.search(response or '')
            subject = None
            battle_map = self.current_game.get_map_for_entity(receiver)
            if match and match.group(1):
                params = self._parse_named_params(match.group(1), positional_keys=('target',))
                target_spec = (
                    params.get('target')
                    or params.get('object')
                    or params.get('entity')
                    or match.group(1).strip()
                )
                if target_spec:
                    subject = self.resolve_named_target(
                        receiver,
                        target_spec,
                        speaker=None,
                        include_objects=True,
                    )

            if subject is not None:
                system_response = format_annotation_summary(
                    receiver,
                    [subject],
                    range_ft=30,
                    battle_map=battle_map,
                )
            else:
                candidates = []
                if battle_map is not None:
                    raw_objects = getattr(battle_map, 'interactable_objects', None) or {}
                    candidates = list(raw_objects.keys())
                system_response = format_annotation_summary(
                    receiver,
                    candidates,
                    range_ft=30,
                    battle_map=battle_map,
                )

            llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
            regenerated = llm_conversation_handler.generate_response(receiver.entity_uid)
            if regenerated:
                _language, regenerated = self.parse_language_from_response(regenerated)
                return regenerated
            return ""
        except Exception as exc:
            logger.error("Error handling annotations query: %s", exc)
            return ""

    def _handle_list_container_query(self, response: str, receiver: Entity, llm_conversation_handler) -> str:
        """Handle container stock query before regenerating the NPC reply."""
        try:
            match = LIST_CONTAINER_PATTERN.search(response or '')
            container = None
            if match and match.group(1):
                params = self._parse_named_params(match.group(1), positional_keys=('target',))
                target_spec = (
                    params.get('target')
                    or params.get('container')
                    or params.get('entity')
                    or match.group(1).strip()
                )
                if target_spec:
                    container = self.resolve_named_target(
                        receiver,
                        target_spec,
                        speaker=None,
                        include_objects=True,
                    )
                    if container is not None and not is_map_container(container):
                        container = None

            system_response = format_container_list_summary(
                self.current_game,
                receiver,
                container=container,
            )
            llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
            regenerated = llm_conversation_handler.generate_response(receiver.entity_uid)
            if regenerated:
                _language, regenerated = self.parse_language_from_response(regenerated)
                return regenerated
            return ""
        except Exception as exc:
            logger.error("Error handling list container query: %s", exc)
            return ""
    
    def _handle_inventory_query(self, receiver: Entity, llm_conversation_handler) -> str:
        """
        Handle inventory query command.
        
        Args:
            receiver: The entity whose inventory is being queried
            llm_conversation_handler: The LLM conversation handler
            
        Returns:
            The response after inventory processing
        """
        try:
            # Get inventory items
            inventory_items = [item['label'] for item in receiver.inventory_items(self.game_session)]
            system_response = f'[INVENTORY] {", ".join(inventory_items)}'
            
            # Add system message and regenerate response
            llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
            response = llm_conversation_handler.generate_response(receiver.entity_uid)
            
            # Re-parse language for the new response
            if response:
                language, response = self.parse_language_from_response(response)
                return response
            
            return ""
        except Exception as e:
            logger.error(f"Error handling inventory query: {e}")
            return ""
    
    def _handle_observation_request(self, receiver: Entity, llm_conversation_handler) -> str:
        """
        Handle observation request command.
        
        Args:
            receiver: The entity making the observation
            llm_conversation_handler: The LLM conversation handler
            
        Returns:
            The response after observation processing
        """
        try:
            system_response = self.build_observation_summary(receiver)
            
            # Add system message and regenerate response
            llm_conversation_handler.add_message(receiver.entity_uid, 'system', system_response)
            response = llm_conversation_handler.generate_response(receiver.entity_uid)
            
            # Re-parse language for the new response
            if response:
                language, response = self.parse_language_from_response(response)
                return response
            
            return ""
        except Exception as e:
            logger.error(f"Error handling observation request: {e}")
            return ""
    
    def get_entity_context(self, entity: Entity, *, viewer_is_dm: bool = True) -> Dict[str, Any]:
        """
        Get comprehensive context information for an entity.
        
        Args:
            entity: The entity to get context for
            
        Returns:
            Dictionary containing entity context information
        """
        context = {
            'name': entity.label() if hasattr(entity, 'label') else str(entity),
            'entity_uid': getattr(entity, 'entity_uid', None),
            'description': entity.description() if hasattr(entity, 'description') else 'No description available.'
        }
        props = getattr(entity, 'properties', None) or {}
        if hasattr(entity, 'profile_image'):
            context['profile_image'] = entity.profile_image()
        if hasattr(entity, 'token_image'):
            context['token_image'] = entity.token_image()
        context['has_full_portrait'] = bool(props.get('profile_image'))
        if character_description_suppressed_for(entity, viewer_is_dm=viewer_is_dm):
            context['description'] = None
            context['description_suppressed'] = True
        
        # Add combat stats
        if hasattr(entity, 'hp') and callable(getattr(entity, 'hp')):
            context['hp'] = entity.hp()
        elif hasattr(entity, 'hp'):
            context['hp'] = entity.hp
        
        if hasattr(entity, 'max_hp') and callable(getattr(entity, 'max_hp')):
            context['max_hp'] = entity.max_hp()
        elif hasattr(entity, 'max_hp'):
            context['max_hp'] = entity.max_hp
        
        if hasattr(entity, 'armor_class') and callable(getattr(entity, 'armor_class')):
            context['ac'] = entity.armor_class()
        elif hasattr(entity, 'ac'):
            context['ac'] = entity.ac
        
        # Add level information
        if hasattr(entity, 'level') and callable(getattr(entity, 'level')):
            context['level'] = entity.level()
        elif hasattr(entity, 'level'):
            context['level'] = entity.level
        
        # Add race information
        if hasattr(entity, 'race') and callable(getattr(entity, 'race')):
            context['race'] = entity.race()
        elif hasattr(entity, 'race'):
            context['race'] = entity.race
        
        # Add class information
        class_value = None
        if hasattr(entity, 'class_descriptor') and callable(getattr(entity, 'class_descriptor')):
            class_value = entity.class_descriptor()
        if class_value:
            context['class'] = class_value
        elif hasattr(entity, 'class_and_level') and callable(getattr(entity, 'class_and_level')):
            class_info = entity.class_and_level()
            if class_info:
                context['class'] = ', '.join([f"{cls} {lvl}" for cls, lvl in class_info])
        
        # Add inventory information
        try:
            inventory_items = entity.inventory_items(self.game_session)
            context['inventory'] = [item['label'] for item in inventory_items] if inventory_items else []
        except:
            context['inventory'] = []

        try:
            from natural20.utils.merchant import is_merchant, merchant_config, merchant_shop_title
            context['is_merchant'] = is_merchant(entity)
            if context['is_merchant']:
                cfg = merchant_config(entity) or {}
                context['merchant_shop_title'] = merchant_shop_title(entity)
                context['merchant_llm_pricing'] = bool(cfg.get('llm_pricing', True))
        except Exception:
            context['is_merchant'] = False

        # Add position information
        try:
            battle_map = self.current_game.get_map_for_entity(entity)
            if battle_map and hasattr(battle_map, 'entity_or_object_pos'):
                context['position'] = battle_map.entity_or_object_pos(entity)
        except:
            context['position'] = None
        
        return context
    
    # Module-level LRU cache for nearby entities queries to reduce repeated
    # LoS ray-tracing during rapid polling from the web client.
    from functools import lru_cache
    
    @staticmethod
    def _nearby_cache_key(entity_uid: str, range_ft: int, volume: str, include_extended: bool) -> str:
        return f"{entity_uid}:{range_ft}:{volume}:{include_extended}"
    
    def get_nearby_entities(self, entity: Entity, range_ft: int = 30, volume: Optional[str] = None, include_extended: bool = False) -> List[Dict[str, Any]]:
        """
        Get nearby entities for an entity.
        
        Args:
            entity: The entity to get nearby entities for
            range_ft: The range in feet to search
            
        Returns:
            List of nearby entity information
        """
        # Short-lived cache keyed by entity + parameters to avoid redundant
        # conversation_reachability + can_see calculations during polling.
        cache_key = self._nearby_cache_key(entity.entity_uid, range_ft, volume or '', include_extended)
        if hasattr(self, '_nearby_cache'):
            cached_ts, cached_result = self._nearby_cache.get(cache_key, (0, None))
            if cached_result is not None and (time.monotonic() - cached_ts) < 5.0:
                return cached_result
        
        try:
            battle_map = self.current_game.get_map_for_entity(entity)
            try:
                if include_extended:
                    nearby = conversation_reachability(entity, battle_map, distance_ft=range_ft, mode=volume)
                else:
                    nearby = audible_entities(entity, battle_map, distance_ft=range_ft, mode=volume)
            except Exception:
                nearby = None

            if not isinstance(nearby, list):
                nearby = []

            if nearby and isinstance(nearby[0], dict):
                audience_entries = nearby
            else:
                if battle_map is None:
                    observed = []
                else:
                    observed = entity.observe(battle_map)
                audience_entries = []
                for observed_entity, distance in observed or []:
                    audience_entries.append({
                        'entity': observed_entity,
                        'distance_ft': distance,
                        'adjusted_distance_ft': distance,
                        'effective_distance_ft': distance,
                        'passive_perception': None,
                        'hearing_modifier_ft': 0,
                        'reachable_now': distance <= range_ft,
                        'reachable_with_shout': distance <= range_ft,
                        'minimum_volume': volume or 'normal',
                        'status': 'reachable' if distance <= range_ft else 'too_far',
                        'acoustic_penalty_ft': 0,
                        'acoustic_summary': '',
                        'closed_doors': 0,
                        'walls': 0,
                        'opaque_objects': 0,
                    })
            
            response = []
            # Short-lived cache for can_see results to avoid repeated LoS ray traces
            # within a single get_nearby_entities call (same speaker, same map state).
            _visibility_cache: Dict[Tuple[str, str], bool] = {}

            for audience_entry in audience_entries:
                nearby_entity = audience_entry['entity']
                # Hide entities the speaker cannot see; otherwise listing them in the
                # UI would reveal hidden/invisible/unseen creatures even though they
                # may still be able to hear the speaker.
                try:
                    if battle_map is not None and hasattr(battle_map, 'can_see'):
                        vis_key = (entity.entity_uid, nearby_entity.entity_uid)
                        if vis_key in _visibility_cache:
                            visible = _visibility_cache[vis_key]
                        else:
                            visible = battle_map.can_see(entity, nearby_entity)
                            _visibility_cache[vis_key] = visible
                        if not visible:
                            continue
                except Exception:
                    pass
                response.append({
                    'id': nearby_entity.entity_uid,
                    'name': entity_label(nearby_entity),
                    'distance': audience_entry['distance_ft'],
                    'adjusted_distance_ft': audience_entry.get('adjusted_distance_ft', audience_entry['distance_ft']),
                    'effective_distance_ft': audience_entry['effective_distance_ft'],
                    'passive_perception': audience_entry['passive_perception'],
                    'hearing_modifier_ft': audience_entry['hearing_modifier_ft'],
                    'reachable_now': audience_entry.get('reachable_now', True),
                    'reachable_with_shout': audience_entry.get('reachable_with_shout', True),
                    'minimum_volume': audience_entry.get('minimum_volume'),
                    'status': audience_entry.get('status', 'reachable'),
                    'acoustic_penalty_ft': audience_entry.get('acoustic_penalty_ft', 0),
                    'acoustic_summary': audience_entry.get('acoustic_summary', ''),
                    'closed_doors': audience_entry.get('closed_doors', 0),
                    'walls': audience_entry.get('walls', 0),
                    'opaque_objects': audience_entry.get('opaque_objects', 0),
                    'mention_handle': mention_handle_for(nearby_entity),
                    'conversable': nearby_entity.conversable()
                })
            
            # Store result in cache for subsequent polling requests
            if not hasattr(self, '_nearby_cache'):
                self._nearby_cache = {}
            self._nearby_cache[cache_key] = (time.monotonic(), response)
            
            return response
        except Exception as e:
            logger.error(f"Error getting nearby entities: {e}")
            return []
    
    def validate_language_for_entity(self, language: str, entity: Entity) -> str:
        """
        Validate that an entity can speak the specified language.
        
        Args:
            language: The language to validate
            entity: The entity to check
            
        Returns:
            The validated language (falls back to first available if invalid)
        """
        if language not in entity.languages():
            return entity.languages()[0] if entity.languages() else "common"
        return language 