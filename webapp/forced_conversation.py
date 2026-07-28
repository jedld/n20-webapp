"""Proximity-triggered forced JRPG conversations with movement lock."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

from natural20.utils.conversation import speech_distance_for

logger = logging.getLogger(__name__)

STATE_KEY = 'forced_conversation'


def is_player_character(entity) -> bool:
    from natural20.player_character import PlayerCharacter

    return isinstance(entity, PlayerCharacter)


def forced_conversation_config(entity) -> Optional[Dict[str, Any]]:
    props = getattr(entity, 'properties', {}) or {}
    raw = props.get('forced_conversation')
    if not isinstance(raw, dict):
        return None
    if raw.get('enabled') is False:
        return None
    return raw


def merge_forced_conversation_config(game_session, entity, config: Dict[str, Any]) -> Dict[str, Any]:
    props = getattr(game_session, 'game_properties', None) or {}
    if not isinstance(props, dict):
        props = {}
    merged = deepcopy(props.get('forced_conversation_defaults') or {})

    npc_uid = str(getattr(entity, 'entity_uid', '') or '')
    campaign_overrides = (props.get('forced_conversations') or {}).get(npc_uid) or {}
    if isinstance(campaign_overrides, dict):
        merged.update(campaign_overrides)

    merged.update(config)
    resolution = deepcopy(merged.get('resolution') or {})
    resolution.update(config.get('resolution') or {})
    if isinstance(campaign_overrides.get('resolution'), dict):
        resolution.update(campaign_overrides['resolution'])
    merged['resolution'] = resolution
    return merged


def resolve_forced_conversation_setting(
    config: Dict[str, Any],
    battle_map,
    game_session,
) -> str:
    if config.get('setting'):
        return str(config['setting'])
    map_props = getattr(battle_map, 'properties', None) or {}
    if not isinstance(map_props, dict):
        map_props = {}
    if map_props.get('forced_conversation_setting'):
        return str(map_props['forced_conversation_setting'])
    map_name = getattr(battle_map, 'name', None)
    props = getattr(game_session, 'game_properties', None) or {}
    if not isinstance(props, dict):
        props = {}
    map_settings = props.get('map_settings') or {}
    if not isinstance(map_settings, dict):
        map_settings = {}
    if map_name and isinstance(map_settings.get(map_name), dict):
        setting = map_settings[map_name].get('forced_conversation_setting')
        if setting:
            return str(setting)
    campaign_maps = props.get('forced_conversation_map_settings') or {}
    if not isinstance(campaign_maps, dict):
        campaign_maps = {}
    if map_name and campaign_maps.get(map_name):
        return str(campaign_maps[map_name])
    return str(config.get('default_setting') or 'public')


def resolve_effective_config(
    raw_config: Dict[str, Any],
    battle_map,
    game_session,
    entity,
) -> Dict[str, Any]:
    config = merge_forced_conversation_config(game_session, entity, raw_config)
    setting = resolve_forced_conversation_setting(config, battle_map, game_session)
    settings_block = config.get('settings') or {}
    setting_cfg = settings_block.get(setting) if isinstance(settings_block, dict) else None
    if isinstance(setting_cfg, dict):
        effective = deepcopy(config)
        effective.update(setting_cfg)
        resolution = deepcopy(effective.get('resolution') or {})
        resolution.update(setting_cfg.get('resolution') or {})
        effective['resolution'] = resolution
        effective['_setting'] = setting
        return effective
    config['_setting'] = setting
    return config


def build_detection_notes(config: Dict[str, Any], battle_map, game_session) -> str:
    targets = config.get('detect_targets') or []
    if not targets or battle_map is None:
        return ''

    notes: List[str] = []
    entities = list(getattr(battle_map, 'entities', {}).keys())
    for rule in targets:
        if not isinstance(rule, dict):
            continue
        note = str(rule.get('presence_note') or rule.get('note') or '').strip()
        if not note:
            continue
        uid = str(rule.get('entity_uid') or '').strip()
        sub_type = str(rule.get('sub_type') or '').strip()
        matched = False
        for ent in entities:
            if uid and str(getattr(ent, 'entity_uid', '')) == uid:
                matched = True
                break
            ent_sub = None
            props = getattr(ent, 'properties', {}) or {}
            ent_sub = props.get('sub_type') or getattr(ent, 'sub_type', None)
            if sub_type and str(ent_sub or '') == sub_type:
                matched = True
                break
            if sub_type and str(getattr(ent, 'name', '') or '').lower().find(sub_type.replace('_', ' ')) >= 0:
                matched = True
                break
        if matched:
            notes.append(note)
    return '\n'.join(notes)


def build_session_state_notes(config: Dict[str, Any], game_session) -> str:
    """Inject context when campaign session flags are set (e.g. Guz defeated)."""
    rules = config.get('session_state_notes') or []
    if not rules:
        return ''

    state = getattr(game_session, 'session_state', None) or {}
    if not isinstance(state, dict):
        state = {}

    notes: List[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        key = str(rule.get('key') or '').strip()
        if not key:
            continue
        note = str(rule.get('note') or '').strip()
        if not note:
            continue
        when = rule.get('when', True)
        present = bool(state.get(key))
        if when is False or str(when).lower() in {'false', '0', 'no'}:
            if not present:
                notes.append(note)
        elif present:
            notes.append(note)
    return '\n'.join(notes)


def build_entourage_note(config: Dict[str, Any], anchor, battle_map) -> str:
    entourage = config.get('entourage_note')
    if entourage:
        return str(entourage).strip()

    if not config.get('describe_group_allies', True) or battle_map is None:
        return ''

    anchor_group = getattr(anchor, 'group', None)
    if not anchor_group:
        return ''

    allies = []
    for ent in getattr(battle_map, 'entities', {}).keys():
        if ent is anchor:
            continue
        if str(getattr(ent, 'group', '')) != str(anchor_group):
            continue
        label = ent.label() if callable(getattr(ent, 'label', None)) else getattr(ent, 'name', 'ally')
        desc = (getattr(ent, 'properties', {}) or {}).get('description') or ''
        allies.append(f"- {label}: {str(desc).strip()[:200]}")
    if not allies:
        return ''
    return (
        'Your entourage is present at your side:\n'
        + '\n'.join(allies)
        + '\nThe wolves follow your lead; the cloaked figure looms behind you until violence begins.'
    )


class ForcedConversationManager:
    def __init__(self, game_manager, *, conversation_service_getter: Optional[Callable] = None):
        self.game_manager = game_manager
        self._conversation_service_getter = conversation_service_getter

    @property
    def session(self):
        return self.game_manager.game_session

    @property
    def conversation_service(self):
        if self._conversation_service_getter is None:
            return None
        return self._conversation_service_getter()

    def active_state(self) -> Optional[Dict[str, Any]]:
        state = getattr(self.session, 'session_state', {}) or {}
        active = state.get(STATE_KEY)
        if isinstance(active, dict) and active.get('active'):
            return active
        return None

    def is_entity_locked(self, entity) -> bool:
        active = self.active_state()
        if not active or entity is None:
            return False
        uid = str(getattr(entity, 'entity_uid', '') or '')
        if not uid:
            return False
        participants = {str(value) for value in (active.get('participant_pc_uids') or [])}
        return uid in participants

    def block_action(self, action) -> Optional[str]:
        active = self.active_state()
        if not active:
            return None
        source = getattr(action, 'source', None)
        if source is None:
            return None
        if getattr(action, 'action_type', None) != 'move':
            return None
        if self.is_entity_locked(source):
            return 'Movement is restricted during this conversation.'
        return None

    def check_after_move(self, moved_entity) -> None:
        if not is_player_character(moved_entity):
            return
        if self.game_manager.get_current_battle():
            return
        if self.active_state():
            return

        battle_map = self.game_manager.get_map_for_entity(moved_entity)
        if battle_map is None:
            return

        for npc in list(getattr(battle_map, 'entities', {}).keys()):
            raw_config = forced_conversation_config(npc)
            if not raw_config:
                continue
            config = resolve_effective_config(raw_config, battle_map, self.session, npc)
            once_key = config.get('once_session_key')
            if once_key:
                session_state = getattr(self.session, 'session_state', {}) or {}
                if session_state.get(once_key):
                    continue
            if not self._entity_in_range(npc, moved_entity, battle_map, config):
                continue
            self.start(npc, moved_entity, battle_map, config)
            return

    def start(self, npc, trigger_pc, battle_map, config: Dict[str, Any]) -> None:
        config = resolve_effective_config(config, battle_map, self.session, npc)
        participants = self._participants_in_range(npc, battle_map, config)
        if trigger_pc not in participants:
            participants.insert(0, trigger_pc)
        if not participants:
            return

        once_key = config.get('once_session_key')
        if once_key:
            self.session.update_state({once_key: True})

        map_name = getattr(battle_map, 'name', None)
        state = {
            'active': True,
            'npc_uid': str(getattr(npc, 'entity_uid', '')),
            'map_name': map_name,
            'trigger_pc_uid': str(getattr(trigger_pc, 'entity_uid', '')),
            'participant_pc_uids': [str(getattr(pc, 'entity_uid', '')) for pc in participants],
            'config': config,
            'player_reply_count': 0,
            'setting': config.get('_setting'),
        }
        session_state = getattr(self.session, 'session_state', {}) or {}
        session_state[STATE_KEY] = state
        self.session.session_state = session_state

        self._emit_opening_narration(map_name, config)

        self._emit_to_participants(participants, {
            'type': 'forced_conversation_start',
            'entity_id': getattr(npc, 'entity_uid', None),
            'entity_name': npc.label(),
            'map_name': map_name,
            'forced': True,
        })

        service = self.conversation_service
        if service is None:
            return

        opening = str(config.get('opening') or 'llm').strip().lower()
        initial_statement = config.get('initial_statement')
        context = config.get('context')
        context_parts = [part for part in (
            context,
            build_entourage_note(config, npc, battle_map),
            build_detection_notes(config, battle_map, self.session),
            build_session_state_notes(config, self.session),
        ) if part]
        merged_context = '\n\n'.join(context_parts) if context_parts else None

        if opening in {'initial_statement', 'statement'} and initial_statement:
            if merged_context:
                service.inject_npc_llm_system_note(
                    getattr(npc, 'entity_uid', None),
                    merged_context,
                )
            self._speak_initial_statement(npc, participants, str(initial_statement), config)
            self._open_dialog_for_participants(npc, participants, map_name=map_name, forced=True)
            return

        if opening == 'conversation_buffer' and not merged_context:
            merged_context = (
                'Begin this forced confrontation now. Use your seeded conversation_buffer as the opening beat.'
            )
        service.initiate_npc_address(
            npc,
            participants,
            context=merged_context,
            open_dialog=True,
            map_name=map_name,
            forced=True,
        )

    def maybe_resolve_after_npc_reply(self, npc, response, *, speaker=None) -> bool:
        active = self.active_state()
        if not active:
            return False
        if str(getattr(npc, 'entity_uid', '')) != str(active.get('npc_uid', '')):
            return False

        config = active.get('config') or {}
        resolution = config.get('resolution') or {}
        text = str(response or '')

        if '[GO_HOSTILE]' in text or self._matches_keywords(text, resolution.get('hostile_keywords')):
            self._resolve_hostile(npc, config)
            return True
        if '[GO_FRIENDLY]' in text or self._matches_keywords(text, resolution.get('friendly_keywords')):
            self._resolve_peaceful(npc, config, outcome='friendly')
            return True
        if self._matches_keywords(text, resolution.get('neutral_keywords')):
            self._resolve_peaceful(npc, config, outcome='neutral')
            return True
        return False

    def on_player_reply(self, speaker) -> None:
        active = self.active_state()
        if not active or not self.is_entity_locked(speaker):
            return
        active['player_reply_count'] = int(active.get('player_reply_count') or 0) + 1
        self._save_active(active)

    def after_npc_reply(self, npc) -> None:
        active = self.active_state()
        if not active or str(getattr(npc, 'entity_uid', '')) != str(active.get('npc_uid', '')):
            return
        config = active.get('config') or {}
        impatience = config.get('impatience') or {}
        max_replies = int(impatience.get('max_player_replies') or 0)
        count = int(active.get('player_reply_count') or 0)
        if max_replies and count >= max_replies:
            self._escalate_to_hostile(npc, config, impatience)

    def impatience_notes_for_turn(self, npc) -> List[str]:
        active = self.active_state()
        if not active or str(getattr(npc, 'entity_uid', '')) != str(active.get('npc_uid', '')):
            return []
        config = active.get('config') or {}
        impatience = config.get('impatience') or {}
        nudge_after = int(impatience.get('nudge_after') or 0)
        count = int(active.get('player_reply_count') or 0)
        if not nudge_after or count < nudge_after:
            return []
        note = str(impatience.get('nudge_context') or impatience.get('context') or '').strip()
        if not note:
            note = (
                'You are losing patience. The party is stalling. Threaten them, then attack '
                'without further warning if they still refuse — use [GO_HOSTILE].'
            )
        return [note]

    def end(self, *, peaceful: bool = False) -> None:
        active = self.active_state()
        if not active:
            return
        session_state = getattr(self.session, 'session_state', {}) or {}
        session_state.pop(STATE_KEY, None)
        self.session.session_state = session_state
        self._emit_to_participant_uids(active.get('participant_pc_uids') or [], {
            'type': 'forced_conversation_end',
            'peaceful': peaceful,
            'npc_uid': active.get('npc_uid'),
        })

    def on_battle_started(self) -> None:
        if self.active_state():
            self.end(peaceful=False)

    def _resolve_hostile(self, npc, config: Dict[str, Any]) -> None:
        handler = None
        service = self.conversation_service
        if service is not None:
            handler = getattr(service, 'entity_rag_handler', None)
        if handler is not None:
            handler._handle_hostile_state_change(npc)
        else:
            resolution = config.get('resolution') or {}
            hostile_group = resolution.get('hostile_group') or getattr(npc, 'group', None) or 'b'
            try:
                npc.update_state('active')
                self.game_manager.update_group(npc, hostile_group)
            except Exception as exc:
                logger.warning('Forced conversation hostile resolution failed for %s: %s', npc, exc)
        self._apply_hostile_reveals(npc, config)
        self.end(peaceful=False)

    def _escalate_to_hostile(self, npc, config: Dict[str, Any], impatience: Dict[str, Any]) -> None:
        narration = impatience.get('escalation_narration') or config.get('on_hostile', {}).get('narration')
        if isinstance(narration, dict):
            map_name = (self.active_state() or {}).get('map_name')
            self._emit_opening_narration(map_name, {'narration': narration})
        self._resolve_hostile(npc, config)

    def _apply_hostile_reveals(self, anchor, config: Dict[str, Any]) -> None:
        on_hostile = config.get('on_hostile') or {}
        reveals = on_hostile.get('reveal') or on_hostile.get('reveal_allies') or []
        if not reveals:
            return
        battle_map = self.game_manager.get_map_for_entity(anchor)
        if battle_map is None:
            return
        changed = False
        for rule in reveals:
            if not isinstance(rule, dict):
                continue
            target = self._resolve_reveal_target(battle_map, rule)
            if target is None:
                continue
            label = rule.get('label')
            if label:
                try:
                    target.properties['label'] = str(label)
                    if hasattr(target, 'name'):
                        target.name = str(label)
                except Exception:
                    pass
            for status in rule.get('remove_statuses') or []:
                token = str(status or '').strip()
                if token and token in getattr(target, 'statuses', []):
                    target.statuses.remove(token)
            if rule.get('break_stealth', True) and hasattr(target, 'break_stealth'):
                try:
                    target.break_stealth()
                except Exception:
                    pass
            narration = rule.get('narration')
            if isinstance(narration, dict):
                self._emit_opening_narration(getattr(battle_map, 'name', None), {'narration': narration})
            changed = True
        if changed:
            try:
                self.game_manager.socketio.emit('message', {'type': 'refresh_map'})
            except Exception:
                pass

    @staticmethod
    def _resolve_reveal_target(battle_map, rule: Dict[str, Any]):
        uid = str(rule.get('entity_uid') or '').strip()
        if uid:
            for ent in getattr(battle_map, 'entities', {}).keys():
                if str(getattr(ent, 'entity_uid', '')) == uid:
                    return ent
        sub_type = str(rule.get('sub_type') or '').strip()
        if sub_type:
            for ent in getattr(battle_map, 'entities', {}).keys():
                props = getattr(ent, 'properties', {}) or {}
                if str(props.get('sub_type') or '') == sub_type:
                    return ent
        return None

    def _emit_opening_narration(self, map_name, config: Dict[str, Any]) -> None:
        narration = config.get('narration')
        if not isinstance(narration, dict):
            return
        text = narration.get('text')
        if not text:
            return
        payload = {
            'on_enter': {
                'title': narration.get('title'),
                'text': text,
                'once': False,
            }
        }
        try:
            self.game_manager.socketio.emit('message', {
                'type': 'narration',
                'message': payload,
                'map_name': map_name,
            })
        except Exception as exc:
            logger.debug('Forced conversation narration emit failed: %s', exc)

    def _save_active(self, active: Dict[str, Any]) -> None:
        session_state = getattr(self.session, 'session_state', {}) or {}
        session_state[STATE_KEY] = active
        self.session.session_state = session_state

    def _resolve_peaceful(self, npc, config: Dict[str, Any], *, outcome: str) -> None:
        handler = None
        service = self.conversation_service
        if service is not None:
            handler = getattr(service, 'entity_rag_handler', None)
        resolution = config.get('resolution') or {}
        if outcome == 'neutral':
            target_group = resolution.get('neutral_group') or resolution.get('friendly_group') or 'c'
        else:
            target_group = resolution.get('friendly_group') or 'c'
        if handler is not None and outcome == 'friendly' and '[GO_FRIENDLY]' not in str(config):
            handler._handle_friendly_state_change(npc)
        else:
            try:
                self.game_manager.update_group(npc, target_group)
            except Exception as exc:
                logger.warning('Forced conversation peaceful resolution failed for %s: %s', npc, exc)
            self._wake_group_allies(npc, target_group, passive=True)
        self.end(peaceful=True)

    def _wake_group_allies(self, anchor, group_name: str, *, passive: bool = False) -> None:
        battle_map = self.game_manager.get_map_for_entity(anchor)
        if battle_map is None:
            return
        for ally in list(getattr(battle_map, 'entities', {}).keys()):
            if ally is anchor:
                continue
            if str(getattr(ally, 'group', '')) != str(group_name):
                continue
            try:
                if not passive:
                    ally.update_state('active')
                self.game_manager.update_group(ally, group_name)
            except Exception:
                continue

    def _speak_initial_statement(self, npc, participants, message: str, config: Dict[str, Any]) -> None:
        service = self.conversation_service
        if service is None:
            return
        volume = config.get('volume') or 'normal'
        distance_ft = speech_distance_for(mode=volume, distance_ft=config.get('distance_ft'))
        try:
            conversations = npc.send_conversation(
                message,
                distance_ft=distance_ft,
                targets=participants,
                language=config.get('language') or 'common',
                volume=volume,
            )
        except TypeError:
            conversations = npc.send_conversation(
                message,
                distance_ft=distance_ft,
                targets=participants,
                language=config.get('language') or 'common',
            )
        recipient_usernames = service.conversation_audience_usernames(
            npc,
            conversations,
            targets=participants,
        )
        payload = service.conversation_payload(
            npc,
            message,
            targets=participants,
            volume=volume,
            distance_ft=distance_ft,
            language=config.get('language') or 'common',
        )
        service.emit_conversation_to_usernames(recipient_usernames, payload, source_entity=npc)
        service._persist_conversation_line(
            npc,
            message,
            targets=participants,
            volume=volume,
            language=config.get('language') or 'common',
        )

    def _open_dialog_for_participants(self, npc, participants, *, map_name=None, forced: bool = False) -> None:
        service = self.conversation_service
        if service is None:
            return
        from webapp.npc_item_exchange import human_controlled_usernames

        seen_usernames = set()
        for target in participants:
            for username in human_controlled_usernames(self.game_manager, target):
                if username in seen_usernames:
                    continue
                seen_usernames.add(username)
                for sid in service._sids_for_username(username):
                    service.socketio.emit('message', {
                        'type': 'open_entity_dialog',
                        'entity_id': npc.entity_uid,
                        'entity_name': npc.label(),
                        'map_name': map_name,
                        'forced': forced,
                    }, to=sid)

    @staticmethod
    def _same_entity(left, right) -> bool:
        if left is right:
            return True
        left_uid = getattr(left, 'entity_uid', None)
        right_uid = getattr(right, 'entity_uid', None)
        return bool(left_uid) and left_uid == right_uid

    def _entity_in_range(self, npc, entity, battle_map, config: Dict[str, Any]) -> bool:
        volume = config.get('volume') or 'normal'
        range_ft = speech_distance_for(mode=volume, distance_ft=config.get('distance_ft'))
        service = self.conversation_service
        if service and service.entity_rag_handler:
            nearby = service.entity_rag_handler.get_nearby_entities(
                npc,
                range_ft,
                volume=volume,
                include_extended=True,
            )
            for entry in nearby:
                if self._same_entity(entry.get('entity'), entity) and entry.get('reachable_now'):
                    if config.get('require_los', True):
                        return self._has_los(battle_map, npc, entity)
                    return True
        try:
            distance = battle_map.distance(npc, entity) * battle_map.feet_per_grid
            if distance > range_ft:
                return False
            if config.get('require_los', True):
                return self._has_los(battle_map, npc, entity)
            return True
        except Exception:
            return False

    def _participants_in_range(self, npc, battle_map, config: Dict[str, Any]) -> List[Any]:
        volume = config.get('volume') or 'normal'
        range_ft = speech_distance_for(mode=volume, distance_ft=config.get('distance_ft'))
        participants: List[Any] = []
        service = self.conversation_service
        if service and service.entity_rag_handler:
            nearby = service.entity_rag_handler.get_nearby_entities(
                npc,
                range_ft,
                volume=volume,
                include_extended=True,
            )
            for entry in nearby:
                entity = entry.get('entity')
                if not is_player_character(entity) or not entry.get('reachable_now'):
                    continue
                if config.get('require_los', True) and not self._has_los(battle_map, npc, entity):
                    continue
                participants.append(entity)
        return participants

    @staticmethod
    def _has_los(battle_map, entity_a, entity_b) -> bool:
        try:
            return battle_map.can_see(entity_a, entity_b) or battle_map.can_see(entity_b, entity_a)
        except Exception:
            return True

    @staticmethod
    def _matches_keywords(text: str, keywords) -> bool:
        if not keywords:
            return False
        lowered = str(text or '').lower()
        for keyword in keywords:
            token = str(keyword or '').strip().lower()
            if not token:
                continue
            if token in lowered:
                return True
            if re.search(rf'\b{re.escape(token)}\b', lowered):
                return True
        return False

    def _emit_to_participants(self, participants, payload: Dict[str, Any]) -> None:
        uids = [str(getattr(pc, 'entity_uid', '')) for pc in participants]
        self._emit_to_participant_uids(uids, payload)

    def _emit_to_participant_uids(self, participant_uids, payload: Dict[str, Any]) -> None:
        service = self.conversation_service
        if service is None:
            return
        from webapp.npc_item_exchange import human_controlled_usernames

        seen_usernames = set()
        for uid in participant_uids:
            entity = self.game_manager.get_entity_by_uid(uid)
            if entity is None:
                continue
            for username in human_controlled_usernames(self.game_manager, entity):
                if username in seen_usernames:
                    continue
                seen_usernames.add(username)
                for sid in service._sids_for_username(username):
                    service.socketio.emit('message', payload, to=sid)
