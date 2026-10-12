"""SocketIO event handlers extracted from webapp/app.py.

Call ``register_socketio_handlers(socketio)`` once after globals are
registered so handlers can use ``runtime_state`` accessors.
"""
from flask import request
from flask_socketio import emit

from .helpers.effects import emit_active_effects_for_client
from .helpers.runtime_state import get_current_game, get_logger


def register_socketio_handlers(socketio):
    """Register all SocketIO event handlers on ``socketio``."""

    @socketio.on('connect')
    def _on_connect():
        emit_active_effects_for_client(emit)

    @socketio.on('request_effects')
    def _on_request_effects():
        emit_active_effects_for_client(emit)

    @socketio.on('register')
    def handle_register(data):
        username = data.get('username')
        ws = request.sid
        if ws:
            current_game = get_current_game()
            sids = current_game.username_to_sid.get(username, [])
            if ws not in sids:
                sids.append(ws)
            current_game.username_to_sid[username] = sids
            get_logger().info(f"open connection {ws} for {username}")
            emit('info', {'type': 'info', 'message': ''})

    @socketio.on('message')
    def handle_message(data):
        if data['type'] == 'ping':
            emit('ping', {'type': 'ping', 'message': 'pong'})
        elif data['type'] == 'message':
            get_logger().info(f"message {data['message']}")
            if data['message']['action'] == 'move':
                entity = map.entity_at(data['message']['from']['x'], data['message']['from']['y'])
                if map.placeable(entity, data['message']['to']['x'], data['message']['to']['y']):
                    battle = get_current_game().get_current_battle()
                    map.move_to(entity, data['message']['to']['x'], data['message']['to']['y'], battle)
                    emit('move', {
                        'type': 'move',
                        'message': {'from': data['message']['from'], 'to': data['message']['to']},
                    })
            else:
                emit('error', {'type': 'error', 'message': 'Unknown command!'})
        elif data['type'] == 'command':
            get_logger().info(f"command {data['message']}")
            command = data['message']['command']
            try:
                result = get_current_game().execute_command(command)
                emit('command_response', {'type': 'command_response', 'message': result})
            except Exception as e:
                get_logger().error(f"Error executing command: {e}")
                emit('command_response', {
                    'type': 'command_response',
                    'message': f"Error: {str(e)}",
                })
        else:
            emit('error', {'type': 'error', 'message': 'Unknown command!'})

    @socketio.on('disconnect')
    def handle_disconnect(reason=None):
        # python-socketio may pass a disconnect reason; keep it optional for older versions.
        _ = reason
        ws = request.sid
        if not ws:
            return
        current_game = get_current_game()
        # The socket may have registered under a name other than the Flask
        # session's (or the session may be gone), so drop it everywhere.
        for username, sids in list((current_game.username_to_sid or {}).items()):
            if ws in sids:
                current_game.username_to_sid[username] = [sid for sid in sids if sid != ws]
                get_logger().info(f"close connection {ws} for {username}")
