"""Merchant trading routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from natural20.player_character import PlayerCharacter
from webapp.blueprints.helpers.auth_utils import logged_in, user_role
from webapp.blueprints.helpers.runtime_state import (
    get_current_game,
    get_game_session,
    get_llm_handler,
    get_socketio,
)
from webapp.blueprints.helpers.template_globals import entities_controlled_by
from webapp.merchant_service import (
    build_merchant_session,
    preview_merchant_trade,
    resolve_merchant_discount,
    trade_with_merchant,
)
from natural20.utils.merchant import is_merchant

merchant_bp = Blueprint('merchant', __name__)


def _resolve_entities(merchant_uid: str, buyer_uid: str):
    game = get_current_game()
    merchant = game.get_entity_by_uid(merchant_uid)
    buyer = game.get_entity_by_uid(buyer_uid)
    return game, merchant, buyer


def _buyer_allowed(buyer) -> bool:
    if buyer is None:
        return False
    if 'dm' in user_role():
        return True
    if not isinstance(buyer, PlayerCharacter):
        return False
    controlled = entities_controlled_by(session.get('username'))
    return buyer in controlled


@merchant_bp.route('/merchant', methods=['GET'])
def merchant_ui():
    if not logged_in():
        return jsonify(error='Not logged in'), 401

    merchant_uid = request.args.get('merchant_uid')
    buyer_uid = request.args.get('buyer_uid')
    if not merchant_uid or not buyer_uid:
        return jsonify(error='merchant_uid and buyer_uid are required'), 400

    game, merchant, buyer = _resolve_entities(merchant_uid, buyer_uid)
    if merchant is None or buyer is None:
        return jsonify(error='Entity not found'), 404
    if not is_merchant(merchant):
        return jsonify(error='Entity is not a merchant'), 400
    if not _buyer_allowed(buyer):
        return jsonify(error='You cannot trade with this character'), 403

    memory_store = getattr(game, 'npc_memory_store', None)
    pricing = resolve_merchant_discount(
        merchant,
        buyer,
        memory_store=memory_store,
        llm_handler=get_llm_handler(),
    )
    discount = float(pricing.get('discount_percent') or 0.0)
    note = str(pricing.get('reason') or '').strip()
    trade_session = build_merchant_session(
        get_game_session(),
        merchant,
        buyer,
        discount_percent=discount,
        pricing_note=note,
    )
    return render_template('merchant_trade.html', trade=trade_session)


@merchant_bp.route('/merchant/preview', methods=['POST'])
def merchant_preview():
    if not logged_in():
        return jsonify(error='Not logged in'), 401

    payload = request.get_json(silent=True) or {}
    merchant_uid = payload.get('merchant_uid')
    buyer_uid = payload.get('buyer_uid')
    discount_percent = float(payload.get('discount_percent') or 0.0)
    _, merchant, buyer = _resolve_entities(merchant_uid, buyer_uid)
    if merchant is None or buyer is None:
        return jsonify(error='Entity not found'), 404
    if not is_merchant(merchant) or not _buyer_allowed(buyer):
        return jsonify(error='Trade not permitted'), 403

    result = preview_merchant_trade(
        get_game_session(),
        merchant,
        buyer,
        payload.get('purchase'),
        payload.get('payment'),
        discount_percent=discount_percent,
    )
    return jsonify(result)


@merchant_bp.route('/merchant/trade', methods=['POST'])
def merchant_trade():
    if not logged_in():
        return jsonify(error='Not logged in'), 401

    payload = request.get_json(silent=True) or {}
    merchant_uid = payload.get('merchant_uid')
    buyer_uid = payload.get('buyer_uid')
    discount_percent = float(payload.get('discount_percent') or 0.0)
    game, merchant, buyer = _resolve_entities(merchant_uid, buyer_uid)
    if merchant is None or buyer is None:
        return jsonify(error='Entity not found'), 404
    if not is_merchant(merchant) or not _buyer_allowed(buyer):
        return jsonify(error='Trade not permitted'), 403

    ok, message, details = trade_with_merchant(
        get_game_session(),
        merchant,
        buyer,
        payload.get('purchase'),
        payload.get('payment'),
        discount_percent=discount_percent,
    )
    if not ok:
        return jsonify(success=False, error=message, **details), 400

    try:
        get_socketio().emit('message', {'type': 'refresh_map'})
    except Exception:
        pass

    return jsonify(success=True, message=message, **details)
