"""Web helpers for pausing combat on Bardic Inspiration choices."""

from natural20.action import BardicInspirationPrompt


def emit_bardic_inspiration_prompt(socketio, exc: BardicInspirationPrompt):
    context = exc.context or {}
    socketio.emit('message', {
        'type': 'bardic_inspiration_prompt',
        'message': {
            'entity_id': getattr(exc.entity, 'entity_uid', None),
            'die': context.get('die'),
            'base_total': context.get('base_total'),
            'threshold': context.get('threshold'),
            'roll_kind': context.get('roll_kind'),
        },
    })


def resume_bardic_inspiration_prompt(game, exc: BardicInspirationPrompt, pending_action, use_die: bool):
    """Apply the player's choice and resume the interrupted action."""
    die_roll = exc.die_roll
    if die_roll is not None and hasattr(die_roll, 'metadata'):
        die_roll.metadata['bardic_inspiration_choice'] = bool(use_die)
    exc.generator.send(bool(use_die))
    battle = game.get_current_battle()
    if battle is None:
        return False
    battle.action(pending_action)
    battle.commit(pending_action)
    return True
