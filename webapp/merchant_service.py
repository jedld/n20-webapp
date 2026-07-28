"""Flask-facing merchant trade service (LLM pricing + UI payloads)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from natural20.utils.merchant import (
    build_merchant_catalog,
    build_payment_catalog,
    execute_merchant_trade,
    is_merchant,
    merchant_config,
    merchant_shop_title,
    validate_merchant_trade,
)

logger = logging.getLogger(__name__)

_PRICING_JSON_PATTERN = re.compile(
    r'\{[^{}]*"discount_percent"\s*:\s*-?\d+(?:\.\d+)?[^{}]*\}',
    re.DOTALL,
)


def _entity_label(entity) -> str:
    try:
        return str(entity.label() if callable(getattr(entity, 'label', None)) else entity.label)
    except Exception:
        return str(getattr(entity, 'entity_uid', 'entity'))


def _customer_memory_snippets(memory_store, merchant_uid: str, customer_uid: str, *, limit: int = 8) -> str:
    if memory_store is None:
        return ''
    try:
        summaries = memory_store.list_summaries(merchant_uid, limit=limit)
    except Exception:
        return ''
    if not summaries:
        return 'No prior memories recorded.'
    lines = []
    for item in summaries:
        text = ' '.join(
            part for part in (
                str(item.get('title') or '').strip(),
                str(item.get('summary') or '').strip(),
                str(item.get('body') or '').strip(),
            ) if part
        )
        if not text:
            continue
        if customer_uid and customer_uid not in text and _entity_label_snippet(customer_uid) not in text.lower():
            # Keep general merchant memories too, but prioritize customer-specific ones lightly.
            pass
        lines.append(f"- {text}")
    return '\n'.join(lines[:limit]) or 'No prior memories recorded.'


def _entity_label_snippet(entity_uid: str) -> str:
    return str(entity_uid or '').replace('_', ' ').lower()


def parse_llm_pricing_response(raw: str) -> Tuple[float, str]:
    """Extract discount_percent and reason from an LLM JSON payload."""
    if not raw:
        return 0.0, ''
    text = str(raw).strip()
    fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    match = _PRICING_JSON_PATTERN.search(text)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return 0.0, ''
    try:
        discount = float(payload.get('discount_percent', 0))
    except (TypeError, ValueError):
        discount = 0.0
    discount = max(-50.0, min(50.0, discount))
    reason = str(payload.get('reason') or '').strip()
    return discount, reason


def resolve_merchant_discount(
  merchant,
  customer,
  *,
  memory_store=None,
  llm_handler=None,
) -> Dict[str, Any]:
    """Ask the NPC LLM for a relationship-based price adjustment."""
    config = merchant_config(merchant) or {}
    if not config.get('llm_pricing', True):
        return {'discount_percent': 0.0, 'reason': '', 'source': 'disabled'}

    provider = None
    if llm_handler is not None:
        provider = getattr(llm_handler, 'npc_provider', None) or getattr(llm_handler, 'current_provider', None)
    if provider is None:
        return {'discount_percent': 0.0, 'reason': '', 'source': 'no_provider'}

    merchant_uid = getattr(merchant, 'entity_uid', None)
    customer_uid = getattr(customer, 'entity_uid', None)
    memories = _customer_memory_snippets(memory_store, merchant_uid, customer_uid)
    customer_name = _entity_label(customer)
    merchant_name = _entity_label(merchant)

    system_prompt = (
        f"You are {merchant_name}, a merchant in a D&D town. "
        "Decide a one-time price adjustment for a customer about to browse your wares. "
        "Positive discount_percent lowers prices; negative values are markups. "
        "Stay within -20 to +30 unless memories strongly justify more (hard cap -50 to +50). "
        "Return ONLY compact JSON: "
        '{"discount_percent": <number>, "reason": "<short in-character note>"}'
    )
    user_prompt = (
        f"Customer: {customer_name} (uid={customer_uid}).\n"
        f"Memories about past interactions:\n{memories}\n\n"
        "Consider trust, past deals, insults, favors, and danger they caused. "
        "Output JSON only."
    )
    try:
        response = provider.send_message([
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ])
    except Exception as exc:
        logger.warning('Merchant LLM pricing failed: %s', exc)
        return {'discount_percent': 0.0, 'reason': '', 'source': 'error'}

    discount, reason = parse_llm_pricing_response(response)
    return {
        'discount_percent': discount,
        'reason': reason,
        'source': 'llm',
    }


def build_merchant_session(
    session,
    merchant,
    buyer,
    *,
    discount_percent: float = 0.0,
    pricing_note: str = '',
) -> Dict[str, Any]:
    config = merchant_config(merchant) or {}
    buyback_rate = float(config.get('buyback_rate', 0.5) or 0.0)
    return {
        'merchant_uid': getattr(merchant, 'entity_uid', None),
        'buyer_uid': getattr(buyer, 'entity_uid', None),
        'shop_title': merchant_shop_title(merchant),
        'merchant_name': _entity_label(merchant),
        'buyer_name': _entity_label(buyer),
        'discount_percent': discount_percent,
        'pricing_note': pricing_note,
        'buyback_rate': buyback_rate,
        'wares': build_merchant_catalog(session, merchant, discount_percent=discount_percent),
        'payment_items': build_payment_catalog(session, buyer, buyback_rate=buyback_rate),
    }


def trade_with_merchant(
    session,
    merchant,
    buyer,
    purchase_payload,
    payment_payload,
    *,
    discount_percent: float = 0.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    if not is_merchant(merchant):
        return False, 'This entity is not a merchant.', {}
    return execute_merchant_trade(
        session,
        merchant,
        buyer,
        purchase_payload,
        payment_payload,
        discount_percent=discount_percent,
    )


def open_merchant_shop_dialog(
    current_game,
    *,
    merchant,
    buyer,
    prompt: Optional[str] = None,
) -> bool:
    """Ask a human player's client to open the priced merchant shop UI."""
    from webapp.npc_item_exchange import human_controlled_usernames

    if current_game is None or merchant is None or buyer is None:
        return False
    if not is_merchant(merchant):
        return False

    usernames = human_controlled_usernames(current_game, buyer)
    if not usernames:
        return False

    shop_title = merchant_shop_title(merchant)
    message_payload = {
        'merchant_uid': getattr(merchant, 'entity_uid', None),
        'buyer_uid': getattr(buyer, 'entity_uid', None),
        'shop_title': shop_title,
        'prompt': prompt or f'{_entity_label(merchant)} invites you to browse {shop_title}.',
    }
    payload = {
        'type': 'open_merchant_shop',
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


def preview_merchant_trade(
    session,
    merchant,
    buyer,
    purchase_payload,
    payment_payload,
    *,
    discount_percent: float = 0.0,
) -> Dict[str, Any]:
    ok, message, details = validate_merchant_trade(
        session,
        merchant,
        buyer,
        purchase_payload,
        payment_payload,
        discount_percent=discount_percent,
    )
    details = dict(details or {})
    details['ok'] = ok
    details['message'] = message
    return details
