"""Player confirmation for party map-set teleporters."""

from __future__ import annotations

import logging

from natural20.item_library.teleporter import resolve_party_travel_prompt
from natural20.player_character import PlayerCharacter

from .runtime_state import get_current_game

logger = logging.getLogger(__name__)

_AFFIRMATIVE = frozenset({'yes', 'travel', 'ok', 'continue', 'y'})


def _usernames_for_entity(current_game, entity) -> list[str] | None:
    try:
        from .template_globals import entity_owners
        owners = [str(name).strip().lower() for name in (entity_owners(entity) or []) if str(name).strip()]
    except Exception:
        owners = []
    if owners:
        return owners
    pov_map = getattr(current_game, 'pov_entity_for_user', None) or {}
    matches = []
    uid = str(getattr(entity, 'entity_uid', '') or '')
    for username, pov in pov_map.items():
        if pov is entity or str(getattr(pov, 'entity_uid', '') or '') == uid:
            matches.append(str(username).strip().lower())
    return matches or None


def handle_party_travel_prompt(event: dict) -> None:
    entity = event.get('source')
    target_map = event.get('target_map')
    if entity is None or not target_map:
        return
    if not isinstance(entity, PlayerCharacter):
        return

    current_game = get_current_game()
    if current_game is None:
        return
    session = current_game.game_session
    message = event.get('message') or (
        f"The entire party will be transported to {target_map}. Continue?"
    )
    title = event.get('title') or 'Party travel'
    usernames = _usernames_for_entity(current_game, entity)

    def _on_response(payload):
        text = str((payload or {}).get('response') or '').strip().lower()
        accepted = text in _AFFIRMATIVE
        try:
            resolve_party_travel_prompt(session, entity, declined=not accepted, target_map=target_map)
        except Exception:
            logger.exception('failed to clear party travel prompt state')
        if not accepted:
            return
        try:
            from .map_sets import travel_party_to_map
            travel_party_to_map(current_game, target_map)
        except Exception:
            logger.exception('party travel to %s failed', target_map)
            try:
                current_game.prompt(
                    f"Could not transport the party to {target_map}.",
                    usernames=usernames,
                    title=title,
                )
            except Exception:
                pass

    try:
        current_game.prompt(
            message,
            callback=_on_response,
            options=['Yes', 'No'],
            usernames=usernames,
            title=title,
        )
    except Exception:
        logger.exception('failed to show party travel prompt')
        resolve_party_travel_prompt(session, entity, declined=True, target_map=target_map)
