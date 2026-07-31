"""NPC-initiated debrief when a movement/goal destination is reached."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

from webapp.npc_situational_context import build_npc_situational_context

logger = logging.getLogger(__name__)

STATE_KEY = 'goal_arrival_conversation'


def is_player_character(entity) -> bool:
    return isinstance(entity, PlayerCharacter)


class GoalArrivalConversationManager:
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

    def start(
        self,
        npc,
        requester_pc,
        *,
        goal_state: Optional[Dict[str, Any]] = None,
        task: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if npc is None or requester_pc is None or not is_player_character(requester_pc):
            return False
        if self.active_state() is not None:
            return False

        service = self.conversation_service
        if service is None:
            return False

        rag = getattr(service, 'entity_rag_handler', None)
        situational = ''
        if rag is not None:
            situational = build_npc_situational_context(
                self.session,
                self.game_manager,
                npc,
                task=task,
                requester=requester_pc,
                movement_helper=getattr(rag, 'movement_helper', None),
            )

        goal_text = (goal_state or {}).get('goal') or ''
        context_parts = [
            'You have reached your movement destination or the guest room door.',
            f'Your active goal: {goal_text}' if goal_text else '',
            'Speak to the guest in character. Confirm whether your errand is complete.',
            'If the guest has the key and is at the suite door, use [GOAL_COMPLETE] and stop trying to unlock the door.',
            'You may use [GOAL_GIVE_UP] if the errand cannot be finished.',
            situational,
        ]
        context = '\n'.join(part for part in context_parts if part).strip()

        map_name = None
        try:
            battle_map = self.game_manager.get_map_for_entity(npc)
            map_name = getattr(battle_map, 'name', None) if battle_map is not None else None
        except Exception:
            map_name = None

        session_state = getattr(self.session, 'session_state', {}) or {}
        session_state[STATE_KEY] = {
            'active': True,
            'npc_uid': str(getattr(npc, 'entity_uid', '')),
            'requester_uid': str(getattr(requester_pc, 'entity_uid', '')),
            'goal': goal_text,
            'map_name': map_name,
        }
        self.session.session_state = session_state

        if isinstance(goal_state, dict):
            goal_state['arrival_debrief_done'] = True
        elif isinstance(task, dict):
            task['arrival_debrief_done'] = True

        self._emit_to_participants([requester_pc], {
            'type': 'goal_arrival_start',
            'entity_id': getattr(npc, 'entity_uid', None),
            'entity_name': npc.label(),
            'map_name': map_name,
            'goal_arrival': True,
        })

        service.initiate_npc_address(
            npc,
            [requester_pc],
            context=context,
            open_dialog=True,
            map_name=map_name,
            forced=False,
            goal_arrival=True,
        )
        return True

    def on_player_no_response(self, speaker) -> bool:
        active = self.active_state()
        if not active or speaker is None:
            return False
        if str(getattr(speaker, 'entity_uid', '')) != str(active.get('requester_uid', '')):
            return False
        self.end(peaceful=True)
        return True

    def maybe_complete_after_npc_reply(self, npc, response: str) -> bool:
        active = self.active_state()
        if not active:
            return False
        if str(getattr(npc, 'entity_uid', '')) != str(active.get('npc_uid', '')):
            return False
        text = str(response or '')
        if '[GOAL_COMPLETE]' in text or '[GOAL_GIVE_UP]' in text:
            self.end(peaceful=True)
            return True
        return False

    def end(self, *, peaceful: bool = True) -> None:
        active = self.active_state()
        if not active:
            return
        session_state = getattr(self.session, 'session_state', {}) or {}
        session_state.pop(STATE_KEY, None)
        self.session.session_state = session_state
        self._emit_to_participant_uids(active.get('requester_uid'), {
            'type': 'goal_arrival_end',
            'peaceful': peaceful,
            'npc_uid': active.get('npc_uid'),
        })

    def _emit_to_participants(self, participants, payload: Dict[str, Any]) -> None:
        uids = [str(getattr(pc, 'entity_uid', '')) for pc in participants]
        for uid in uids:
            self._emit_to_participant_uids(uid, payload)

    def _emit_to_participant_uids(self, participant_uid, payload: Dict[str, Any]) -> None:
        service = self.conversation_service
        if service is None or not participant_uid:
            return
        from webapp.npc_item_exchange import human_controlled_usernames

        entity = self.game_manager.get_entity_by_uid(participant_uid)
        if entity is None:
            return
        seen_usernames = set()
        for username in human_controlled_usernames(self.game_manager, entity):
            if username in seen_usernames:
                continue
            seen_usernames.add(username)
            for sid in service._sids_for_username(username):
                service.socketio.emit('message', payload, to=sid)
