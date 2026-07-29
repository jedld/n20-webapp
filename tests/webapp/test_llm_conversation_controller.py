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
