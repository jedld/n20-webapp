"""Tests for NPC conversation history formatting."""

from webapp.llm_conversation_controller import LLMConversationController


class _StubProvider:
    def send_message(self, messages, context=None):
        return '{"responders": []}'


def _controller():
    handler = type('H', (), {'npc_provider': _StubProvider(), 'current_provider': _StubProvider()})()
    return LLMConversationController(handler)


def test_update_conversation_history_uses_narrative_for_empty_npc_line():
    controller = _controller()
    controller.create_conversation('pip_barmaid', 'system prompt')
    controller.update_conversation_history('pip_barmaid', [{
        'source': type('E', (), {'entity_uid': 'pip_barmaid', 'label': lambda self: 'Pip'})(),
        'message': '',
        'narrative': ['Pip giggles, leaning in closer.'],
        'language': 'common',
        'directed_to': [],
    }])

    messages = controller.conversations['pip_barmaid']['messages']
    assert len(messages) == 1
    assert messages[0]['role'] == 'assistant'
    assert 'Pip giggles' in messages[0]['content']


def test_update_conversation_history_marks_empty_overheard_lines_nonverbal():
    controller = _controller()
    controller.create_conversation('pip_barmaid', 'system prompt')
    controller.update_conversation_history('pip_barmaid', [{
        'source': type('E', (), {'entity_uid': 'aldric', 'label': lambda self: 'Aldric'})(),
        'message': '',
        'narrative': [],
        'language': 'common',
        'directed_to': ['someone_else'],
    }])

    messages = controller.conversations['pip_barmaid']['messages']
    assert len(messages) == 1
    assert '(nonverbal)' in messages[0]['content']


def test_update_conversation_history_can_emit_consecutive_npc_lines():
    controller = _controller()
    controller.create_conversation('mara_bartender', 'system prompt')
    npc = type('E', (), {'entity_uid': 'mara_bartender', 'label': lambda self: 'Mara'})()
    player = type('E', (), {'entity_uid': 'mira', 'label': lambda self: 'Mira'})()
    controller.update_conversation_history('mara_bartender', [
        {
            'source': player,
            'target': npc,
            'message': 'hello',
            'language': 'common',
            'directed_to': [],
        },
        {
            'source': npc,
            'message': 'Welcome.',
            'language': 'common',
            'directed_to': [],
        },
        {
            'source': npc,
            'message': 'What can I get you?',
            'language': 'common',
            'directed_to': [],
        },
    ])

    roles = [msg['role'] for msg in controller.conversations['mara_bartender']['messages']]
    assert roles == ['user', 'assistant', 'assistant']


def test_update_conversation_history_treats_explicit_direct_target_as_user_prompt():
    controller = _controller()
    controller.create_conversation('mara_bartender', 'system prompt')
    npc = type('E', (), {'entity_uid': 'mara_bartender', 'label': lambda self: 'Mara'})()
    player = type('E', (), {'entity_uid': 'mira', 'label': lambda self: 'Mira'})()
    controller.update_conversation_history('mara_bartender', [{
        'source': player,
        'target': npc,
        'message': 'do you have a room available?',
        'language': 'common',
        'directed_to': [player],
    }])

    messages = controller.conversations['mara_bartender']['messages']
    assert len(messages) == 1
    assert messages[0]['role'] == 'user'
    assert 'room available' in messages[0]['content']


def test_update_conversation_history_keeps_multiple_player_lines():
    controller = _controller()
    controller.create_conversation('mara_bartender', 'system prompt')
    npc = type('E', (), {'entity_uid': 'mara_bartender', 'label': lambda self: 'Mara'})()
    player = type('E', (), {'entity_uid': 'mira', 'label': lambda self: 'Mira'})()
    controller.update_conversation_history('mara_bartender', [
        {
            'source': player,
            'target': npc,
            'message': 'hi there!',
            'language': 'common',
            'directed_to': [npc],
        },
        {
            'source': npc,
            'message': 'Welcome.',
            'language': 'common',
            'directed_to': [player],
        },
        {
            'source': player,
            'target': npc,
            'message': 'do you have a room available?',
            'language': 'common',
            'directed_to': [npc],
        },
    ])

    roles = [msg['role'] for msg in controller.conversations['mara_bartender']['messages']]
    assert roles == ['user', 'assistant', 'user']
    assert 'room available' in controller.conversations['mara_bartender']['messages'][-1]['content']


def test_message_spell_turn_is_formatted_for_npc_llm():
    controller = _controller()
    npc = type('E', (), {'entity_uid': 'guz', 'label': lambda self: 'Guz'})()
    player = type('E', (), {'entity_uid': 'mira', 'label': lambda self: 'Mira'})()
    formatted = controller._format_incoming_player_turn({
        'source': player,
        'message': 'meet me at the gate',
        'language': 'common',
        'message_spell_link_id': 'link-123',
    })
    assert 'MESSAGE SPELL' in formatted
    assert 'Message spell' in formatted
    assert 'meet me at the gate' in formatted
    assert 'not normal room speech' in formatted.lower()


def test_message_spell_lines_are_not_overheard_in_npc_history():
    controller = _controller()
    controller.create_conversation('guz', 'system prompt')
    npc = type('E', (), {'entity_uid': 'guz', 'label': lambda self: 'Guz'})()
    player = type('E', (), {'entity_uid': 'mira', 'label': lambda self: 'Mira'})()
    bystander = type('E', (), {'entity_uid': 'pip', 'label': lambda self: 'Pip'})()
    controller.update_conversation_history('guz', [{
        'source': player,
        'message': 'secret plan',
        'language': 'common',
        'directed_to': [npc],
        'message_spell_link_id': 'link-abc',
    }])
    controller.update_conversation_history('pip', [{
        'source': player,
        'message': 'secret plan',
        'language': 'common',
        'directed_to': [npc],
        'message_spell_link_id': 'link-abc',
    }])
    assert len(controller.conversations['guz']['messages']) == 1
    assert 'secret plan' in controller.conversations['guz']['messages'][0]['content']
    assert controller.conversations['pip']['messages'] == []
