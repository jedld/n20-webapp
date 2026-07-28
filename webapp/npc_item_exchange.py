"""NPC conversation helpers for ground pickup and item trades."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

from natural20.entity import Entity
from natural20.utils.conversation import entity_label

logger = logging.getLogger('werkzeug')

_PENDING_NPC_GIFT_TRANSFERS: Dict[str, Dict[str, Any]] = {}
_NPC_GIFT_TRANSFER_TTL_SECONDS = 3600


def register_npc_gift_transfer_session(
    npc_uid: str,
    player_uid: str,
    *,
    item_hint: Optional[str] = None,
) -> str:
    """Track a player→NPC gift dialog until the client reports completion."""
    session_id = uuid.uuid4().hex
    _PENDING_NPC_GIFT_TRANSFERS[session_id] = {
        'npc_uid': str(npc_uid or '').strip(),
        'player_uid': str(player_uid or '').strip(),
        'item_hint': _normalize_item_slug(item_hint) if item_hint else None,
        'created_at': time.time(),
    }
    return session_id


def pop_npc_gift_transfer_session(session_id: str) -> Optional[Dict[str, Any]]:
    session = _PENDING_NPC_GIFT_TRANSFERS.pop(str(session_id or '').strip(), None)
    if session is None:
        return None
    created_at = float(session.get('created_at') or 0)
    if created_at and (time.time() - created_at) > _NPC_GIFT_TRANSFER_TTL_SECONDS:
        return None
    return session


def has_active_npc_gift_transfer(npc_uid: str, player_uid: str) -> bool:
    """Return True when a player→NPC gift dialog is already pending."""
    npc_key = str(npc_uid or '').strip()
    player_key = str(player_uid or '').strip()
    if not npc_key or not player_key:
        return False
    now = time.time()
    for session in _PENDING_NPC_GIFT_TRANSFERS.values():
        if session.get('npc_uid') != npc_key or session.get('player_uid') != player_key:
            continue
        created_at = float(session.get('created_at') or 0)
        if created_at and (now - created_at) <= _NPC_GIFT_TRANSFER_TTL_SECONDS:
            return True
    return False


def summarize_player_gift_transfer(items_payload: Optional[Dict[str, Any]]) -> str:
    """Describe items the player gave to the NPC (give-dialog ``from`` side)."""
    if not isinstance(items_payload, dict):
        return ''

    from_side = items_payload.get('from') or {}
    item_names = from_side.get('items') or []
    quantities = from_side.get('qty') or []
    parts: List[str] = []
    for index, raw_name in enumerate(item_names):
        slug = _normalize_item_slug(raw_name)
        if not slug:
            continue
        try:
            qty = int(quantities[index]) if index < len(quantities) else 1
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, qty)
        label = slug.replace('_', ' ')
        parts.append(f"{label} x{qty}" if qty > 1 else label)
    return ', '.join(parts)


def human_controlled_usernames(current_game, entity: Entity) -> List[str]:
    if entity is None or current_game is None:
        return []
    try:
        owners = current_game.entity_owners(entity) or []
    except Exception:
        owners = []
    return [str(name).strip() for name in owners if str(name).strip()]


def is_human_controlled(current_game, entity: Entity) -> bool:
    return bool(human_controlled_usernames(current_game, entity))


def nearby_ground_items(current_game, actor: Entity) -> List[Dict[str, Any]]:
    if actor is None or current_game is None:
        return []
    try:
        battle_map = current_game.get_map_for_entity(actor)
    except Exception:
        battle_map = None
    if battle_map is None or not hasattr(battle_map, 'items_on_the_ground'):
        return []

    entries: List[Dict[str, Any]] = []
    try:
        stacks = battle_map.items_on_the_ground(actor) or []
    except Exception:
        return []

    for ground_obj, item_names in stacks:
        for item_name in item_names or []:
            slug = str(item_name or '').strip()
            if not slug:
                continue
            try:
                qty = int((getattr(ground_obj, 'inventory', {}) or {}).get(slug, {}).get('qty') or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            entries.append({
                'ground': ground_obj,
                'item': slug,
                'qty': qty,
            })
    return entries


def pickup_ground_item(current_game, actor: Entity, item_slug: str, qty: int = 1) -> Tuple[bool, str]:
    slug = _normalize_item_slug(item_slug)
    if not slug:
        return False, 'No item specified.'

    try:
        requested = max(1, int(qty))
    except (TypeError, ValueError):
        requested = 1

    for entry in nearby_ground_items(current_game, actor):
        if entry['item'] != slug:
            continue
        ground = entry['ground']
        transfer_qty = min(requested, int(entry['qty']))
        if transfer_qty <= 0:
            continue
        items_payload = {
            'from': {'items': [slug], 'qty': [str(transfer_qty)]},
            'to': {'items': [], 'qty': []},
        }
        try:
            battle = current_game.get_current_battle() if current_game is not None else None
            ground.transfer(battle, actor, ground, items_payload)
        except Exception as exc:
            logger.warning('Ground pickup failed for %s: %s', slug, exc)
            return False, f'Could not pick up {slug.replace("_", " ")}.'

        try:
            if current_game is not None and getattr(current_game, 'socketio', None) is not None:
                current_game.socketio.emit('message', {'type': 'refresh_map'})
        except Exception:
            pass
        return True, f"Picked up {transfer_qty} {slug.replace('_', ' ')}."

    return False, f'No {slug.replace("_", " ")} is on the ground within reach.'


def open_item_transfer_dialog(
    current_game,
    *,
    operator_entity: Entity,
    counterparty_entity: Entity,
    action_type: str = 'loot',
    prompt: Optional[str] = None,
    transfer_session_id: Optional[str] = None,
) -> bool:
    """Ask a human controller to open the bidirectional item-transfer modal."""
    if current_game is None or operator_entity is None or counterparty_entity is None:
        return False

    usernames = human_controlled_usernames(current_game, operator_entity)
    if not usernames:
        return False

    message_payload = {
        'operator_entity_uid': getattr(operator_entity, 'entity_uid', None),
        'counterparty_entity_uid': getattr(counterparty_entity, 'entity_uid', None),
        'action_type': action_type,
        'prompt': prompt or (
            f"{entity_label(counterparty_entity)} wants to trade items with you."
        ),
    }
    if transfer_session_id:
        message_payload['transfer_session_id'] = transfer_session_id

    payload = {
        'type': 'open_item_transfer',
        'message': message_payload,
    }

    delivered = False
    socketio = getattr(current_game, 'socketio', None)
    username_to_sid = getattr(current_game, 'username_to_sid', {}) or {}
    for username in usernames:
        for sid in username_to_sid.get(str(username).lower(), []):
            try:
                socketio.emit('message', payload, to=sid)
                delivered = True
            except Exception:
                continue
    return delivered


def _inventory_summary(entity: Entity, session=None, limit: int = 12) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if entity is None:
        return rows
    try:
        if session is not None and hasattr(entity, 'inventory_items'):
            items = entity.inventory_items(session) or []
        else:
            items = [
                {'name': name, 'label': name, 'qty': meta.get('qty', 0)}
                for name, meta in (getattr(entity, 'inventory', {}) or {}).items()
            ]
    except Exception:
        items = []

    for item in items[:limit]:
        name = str(item.get('name') or item.get('label') or '').strip()
        if not name:
            continue
        try:
            qty = int(item.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        rows.append({'name': name, 'qty': qty})
    return rows


def adjudicate_item_exchange_with_dm_llm(
    llm_handler,
    actor: Entity,
    target: Entity,
    *,
    intent: str,
    item_slug: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """Let the DM LLM resolve a trade/offer/request when no human controls the target."""
    default = {
        'accepted': False,
        'reason': 'The exchange does not happen.',
        'transfer': None,
    }
    if llm_handler is None or actor is None or target is None:
        return default

    actor_items = _inventory_summary(actor, session=session)
    target_items = _inventory_summary(target, session=session)
    messages = [
        {
            'role': 'system',
            'content': (
                'You are the Dungeon Master adjudicating an in-character item exchange between NPCs '
                'or other non-player-controlled entities during play. '
                'Return JSON only with keys: accepted (bool), reason (short in-character outcome), '
                'and optional transfer when accepted is true. '
                'transfer must use {"from": {"items": [...], "qty": [...]}, "to": {"items": [...], "qty": [...]}} '
                'where "from" moves items from the counterparty to the actor and "to" moves items from the actor to the counterparty. '
                'Only use item slugs that exist in the provided inventories. '
                'If the counterparty would refuse, set accepted=false with a brief in-character reason.'
            ),
        },
        {
            'role': 'user',
            'content': json.dumps({
                'intent': intent,
                'requested_item': item_slug,
                'actor': entity_label(actor),
                'target': entity_label(target),
                'actor_inventory': actor_items,
                'target_inventory': target_items,
            }, default=str),
        },
    ]

    try:
        raw = llm_handler.send_message(
            messages,
            context={
                'response_mode': 'conversation',
                'skip_continuation': True,
                'log_label': 'npc_item_exchange',
            },
        )
    except Exception as exc:
        logger.warning('DM item exchange adjudication failed: %s', exc)
        return default

    text = str(raw or '').strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return default
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return default

    accepted = bool(payload.get('accepted'))
    reason = str(payload.get('reason') or default['reason']).strip()
    transfer = payload.get('transfer')
    if accepted and not isinstance(transfer, dict):
        if intent == 'offer' and item_slug:
            qty = 0
            for row in actor_items:
                if row['name'] == item_slug:
                    qty = min(1, row['qty'])
                    break
            if qty > 0:
                transfer = {
                    'from': {'items': [], 'qty': []},
                    'to': {'items': [item_slug], 'qty': [str(qty)]},
                }
            else:
                accepted = False
                reason = f"{entity_label(target)} has no use for that."
        elif intent == 'request' and item_slug:
            qty = 0
            for row in target_items:
                if row['name'] == item_slug:
                    qty = min(1, row['qty'])
                    break
            if qty > 0:
                transfer = {
                    'from': {'items': [item_slug], 'qty': [str(qty)]},
                    'to': {'items': [], 'qty': []},
                }
            else:
                accepted = False
                reason = f"{entity_label(target)} does not have that item."
        else:
            accepted = False
            reason = 'The exchange fizzles out without agreement.'

    return {
        'accepted': accepted,
        'reason': reason,
        'transfer': transfer if accepted else None,
    }


def apply_transfer_payload(actor: Entity, target: Entity, transfer: Dict[str, Any], battle=None) -> bool:
    if actor is None or target is None or not isinstance(transfer, dict):
        return False
    try:
        if hasattr(target, 'transfer'):
            target.transfer(battle, actor, target, transfer)
            return True
    except Exception as exc:
        logger.warning('Failed to apply NPC transfer payload: %s', exc)
    return False


def _normalize_item_slug(value: str) -> str:
    return re.sub(r'[\s\-]+', '_', str(value or '').strip().lower())


def is_map_container(obj) -> bool:
    inventory = getattr(obj, 'inventory', None)
    return isinstance(inventory, dict) and callable(getattr(obj, 'transfer', None))


def container_item_qty(container, item_slug: str) -> int:
    slug = _normalize_item_slug(item_slug)
    try:
        return int((getattr(container, 'inventory', {}) or {}).get(slug, {}).get('qty') or 0)
    except (TypeError, ValueError):
        return 0


def container_staff_viewers(container) -> set:
    props = getattr(container, 'properties', None)
    if not isinstance(props, dict):
        return set()
    viewers = props.get('staff_viewers') or props.get('staff') or []
    if isinstance(viewers, str):
        viewers = [viewers]
    return {str(entry).strip().lower() for entry in viewers if str(entry).strip()}


def container_interaction_range_ft(
    actor: Entity,
    container,
    *,
    default: float = 15,
    staff_range: float = 30,
) -> float:
    actor_uid = str(getattr(actor, 'entity_uid', '') or '').strip().lower()
    if actor_uid and actor_uid in container_staff_viewers(container):
        return staff_range
    return default


def container_is_accessible(
    actor: Entity,
    container,
    current_game=None,
    *,
    range_ft: float = 15,
) -> bool:
    return container_access_block_reason(actor, container, current_game, range_ft=range_ft) is None


def container_access_block_reason(
    actor: Entity,
    container,
    current_game=None,
    *,
    range_ft: float = 15,
) -> Optional[str]:
    range_ft = container_interaction_range_ft(actor, container, default=range_ft)
    if actor is None or not is_map_container(container):
        return 'That is not a container.'
    if callable(getattr(container, 'locked', None)) and container.locked():
        return 'The container is locked.'
    if callable(getattr(container, 'opened', None)) and not container.opened():
        return 'The container is closed — open it first.'

    battle_map = None
    if current_game is not None:
        try:
            battle_map = current_game.get_map_for_entity(actor)
        except Exception:
            battle_map = None
    if battle_map is None:
        return None

    try:
        distance = battle_map.distance(actor, container) * battle_map.feet_per_grid
        if distance > range_ft:
            return 'That container is not within reach.'
    except Exception:
        pass

    try:
        if not battle_map.can_see(actor, container):
            return 'You cannot see that container from here.'
    except Exception:
        pass
    return None


def nearby_map_containers(current_game, actor: Entity, range_ft: float = 30) -> List[Any]:
    if actor is None or current_game is None:
        return []

    try:
        battle_map = current_game.get_map_for_entity(actor)
    except Exception:
        battle_map = None
    if battle_map is None:
        return []

    containers: List[Any] = []
    raw_objects = getattr(battle_map, 'interactable_objects', None)
    if not isinstance(raw_objects, Mapping):
        return containers

    for obj in list(raw_objects.keys()):
        if not is_map_container(obj):
            continue
        if container_visibility_block_reason(actor, obj, current_game, range_ft=range_ft):
            continue
        containers.append(obj)
    return containers


def find_container_with_item(
    current_game,
    actor: Entity,
    item_slug: str,
    *,
    range_ft: float = 15,
    container=None,
) -> Optional[Any]:
    slug = _normalize_item_slug(item_slug)
    if not slug:
        return None

    if container is not None:
        if container_visibility_block_reason(actor, container, current_game, range_ft=range_ft):
            return None
        if not container_is_accessible(actor, container, current_game, range_ft=range_ft):
            return None
        if container_item_qty(container, slug) > 0:
            return container
        return None

    for candidate in nearby_map_containers(current_game, actor, range_ft=max(range_ft, 30)):
        effective_range = container_interaction_range_ft(actor, candidate, default=range_ft)
        if not container_is_accessible(actor, candidate, current_game, range_ft=effective_range):
            continue
        if container_item_qty(candidate, slug) > 0:
            return candidate
    return None


def format_container_contents(container, session=None) -> str:
    rows: List[str] = []
    for name, meta in (getattr(container, 'inventory', {}) or {}).items():
        try:
            qty = int((meta or {}).get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            rows.append(f"{str(name).replace('_', ' ')} x{qty}")
    return ', '.join(rows) if rows else 'empty'


def _emit_container_refresh(current_game) -> None:
    try:
        if current_game is not None and getattr(current_game, 'socketio', None) is not None:
            current_game.socketio.emit('message', {'type': 'refresh_map'})
    except Exception:
        pass


def _transfer_container_payload(
    current_game,
    operator: Entity,
    container,
    items_payload: Dict[str, Any],
) -> None:
    battle = current_game.get_current_battle() if current_game is not None else None
    container.transfer(battle, operator, container, items_payload)
    _emit_container_refresh(current_game)


def retrieve_from_container(
    current_game,
    actor: Entity,
    container,
    item_slug: str,
    qty: int = 1,
) -> Tuple[bool, str]:
    slug = _normalize_item_slug(item_slug)
    if not slug:
        return False, 'No item specified.'
    block_reason = container_access_block_reason(actor, container, current_game)
    if block_reason:
        return False, block_reason

    try:
        requested = max(1, int(qty))
    except (TypeError, ValueError):
        requested = 1

    available = container_item_qty(container, slug)
    transfer_qty = min(requested, available)
    if transfer_qty <= 0:
        return False, f'No {slug.replace("_", " ")} remains in the container.'

    items_payload = {
        'from': {'items': [slug], 'qty': [str(transfer_qty)]},
        'to': {'items': [], 'qty': []},
    }
    try:
        _transfer_container_payload(current_game, actor, container, items_payload)
    except Exception as exc:
        logger.warning('Container retrieve failed for %s: %s', slug, exc)
        return False, f'Could not retrieve {slug.replace("_", " ")} from the container.'

    return True, f"Retrieved {transfer_qty} {slug.replace('_', ' ')} from the container."


def store_in_container(
    current_game,
    actor: Entity,
    container,
    item_slug: str,
    qty: int = 1,
) -> Tuple[bool, str]:
    slug = _normalize_item_slug(item_slug)
    if not slug:
        return False, 'No item specified.'
    block_reason = container_access_block_reason(actor, container, current_game)
    if block_reason:
        return False, block_reason

    try:
        requested = max(1, int(qty))
    except (TypeError, ValueError):
        requested = 1

    try:
        available = int((getattr(actor, 'inventory', {}) or {}).get(slug, {}).get('qty') or 0)
    except (TypeError, ValueError):
        available = 0
    transfer_qty = min(requested, available)
    if transfer_qty <= 0:
        return False, f'You do not carry any {slug.replace("_", " ")} to store.'

    items_payload = {
        'from': {'items': [], 'qty': []},
        'to': {'items': [slug], 'qty': [str(transfer_qty)]},
    }
    try:
        _transfer_container_payload(current_game, actor, container, items_payload)
    except Exception as exc:
        logger.warning('Container store failed for %s: %s', slug, exc)
        return False, f'Could not store {slug.replace("_", " ")} in the container.'

    return True, f"Stored {transfer_qty} {slug.replace('_', ' ')} in the container."


def give_from_container_to_entity(
    current_game,
    container,
    recipient: Entity,
    item_slug: str,
    qty: int = 1,
) -> Tuple[bool, str]:
    slug = _normalize_item_slug(item_slug)
    if recipient is None or not slug:
        return False, 'No recipient or item specified.'
    if not is_map_container(container):
        return False, 'That is not a container.'

    try:
        requested = max(1, int(qty))
    except (TypeError, ValueError):
        requested = 1

    available = container_item_qty(container, slug)
    transfer_qty = min(requested, available)
    if transfer_qty <= 0:
        return False, f'No {slug.replace("_", " ")} remains in the container.'

    items_payload = {
        'from': {'items': [slug], 'qty': [str(transfer_qty)]},
        'to': {'items': [], 'qty': []},
    }
    try:
        _transfer_container_payload(current_game, recipient, container, items_payload)
    except Exception as exc:
        logger.warning('Container give failed for %s: %s', slug, exc)
        return False, f'Could not give {slug.replace("_", " ")} from the container.'

    return True, f"Gave {transfer_qty} {slug.replace('_', ' ')} from the container."


def container_visibility_block_reason(
    actor: Entity,
    container,
    current_game=None,
    *,
    range_ft: float = 30,
) -> Optional[str]:
    if actor is None or not is_map_container(container):
        return 'That is not a container.'

    battle_map = None
    if current_game is not None:
        try:
            battle_map = current_game.get_map_for_entity(actor)
        except Exception:
            battle_map = None
    if battle_map is None:
        return None

    try:
        distance = battle_map.distance(actor, container) * battle_map.feet_per_grid
        if distance > range_ft:
            return 'That container is not within reach.'
    except Exception:
        pass

    try:
        if not battle_map.can_see(actor, container):
            return 'You cannot see that container from here.'
    except Exception:
        pass
    return None


def _container_list_status(container) -> str:
    if callable(getattr(container, 'locked', None)) and container.locked():
        return 'locked'
    if callable(getattr(container, 'opened', None)) and not container.opened():
        return 'closed'
    return format_container_contents(container)


def format_container_list_summary(
    current_game,
    actor: Entity,
    container=None,
    *,
    range_ft: float = 30,
) -> str:
    if container is not None:
        visibility_block = container_visibility_block_reason(
            actor,
            container,
            current_game,
            range_ft=range_ft,
        )
        if visibility_block:
            return f'[LIST_CONTAINER] {visibility_block}'
        handle = getattr(container, 'entity_uid', None) or 'container'
        label = entity_label(container)
        status = _container_list_status(container)
        return f'[LIST_CONTAINER] @{handle} ({label}): {status}'

    containers = nearby_map_containers(current_game, actor, range_ft=range_ft)
    if not containers:
        return '[LIST_CONTAINER] No accessible containers within range.'

    lines = ['[LIST_CONTAINER] Nearby containers:']
    for cont in containers:
        handle = getattr(cont, 'entity_uid', None) or entity_label(cont)
        label = entity_label(cont)
        status = _container_list_status(cont)
        lines.append(f'- @{handle} ({label}): {status}')
    return '\n'.join(lines)
