from webapp.llm_conversation_controller import LLMConversationController


class _StubEntity:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label

    def label(self):
        return self._label


def test_update_conversation_history_formats_player_actions():
    controller = LLMConversationController(llm_handler=object())
    player = _StubEntity('aldric', 'Aldric')
    npc = _StubEntity('pip_barmaid', 'Pip')

    controller.update_conversation_history('pip_barmaid', [{
        'source': player,
        'target': npc,
        'directed_to': [npc],
        'language': 'common',
        'message': 'Hello.',
        'actions': ['touches her hand'],
    }])

    messages = controller.conversations['pip_barmaid']['messages']
    assert len(messages) == 1
    assert 'says to you' in messages[0]['content']
    assert 'touches her hand' in messages[0]['content']


def test_update_conversation_history_action_only():
    controller = LLMConversationController(llm_handler=object())
    player = _StubEntity('aldric', 'Aldric')
    npc = _StubEntity('pip_barmaid', 'Pip')

    controller.update_conversation_history('pip_barmaid', [{
        'source': player,
        'target': npc,
        'directed_to': [npc],
        'language': 'common',
        'message': '',
        'actions': ['touches her hand'],
    }])

    messages = controller.conversations['pip_barmaid']['messages']
    assert messages[0]['content'] == 'Aldric performs a non-verbal action toward you: touches her hand'
