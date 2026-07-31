"""Socket helpers for contextual sound toasts on the VTT map."""

from __future__ import annotations

from webapp.utils import _json_safe_uid


def emit_contextual_sound_payload(socketio, payload, *, suppress_ui: bool = False) -> None:
    if suppress_ui or socketio is None or not payload:
        return
    message = payload.get('message') or ''
    if not message:
        return
    source = payload.get('source')
    source_uid = payload.get('source_uid')
    if source_uid is None and source is not None:
        source_uid = getattr(source, 'entity_uid', None)
    socketio.emit('message', {
        'type': 'contextual_sound',
        'message': message,
        'position': payload.get('position'),
        'source': _json_safe_uid(source_uid),
        'duration_ms': payload.get('duration_ms', 4000),
        'label': payload.get('label'),
        'sound_id': payload.get('sound_id'),
    })


def emit_contextual_sounds_from_results(socketio, results, *, suppress_ui: bool = False) -> None:
    if not results:
        return
    for item in results:
        if isinstance(item, dict) and item.get('type') == 'contextual_sound':
            emit_contextual_sound_payload(socketio, item, suppress_ui=suppress_ui)
