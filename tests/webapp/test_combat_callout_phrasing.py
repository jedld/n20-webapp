from types import SimpleNamespace
from unittest.mock import MagicMock

from natural20.utils import combat_comms
from webapp.combat_callout_phrasing import build_prompts, install_callout_phraser, phrase_callout


def _speaker():
    return SimpleNamespace(
        entity_uid='npc-1',
        name='vampire',
        label=lambda: 'The Count',
        properties={
            'kind': 'vampire',
            'backstory': 'A cold, cultured aristocrat who toys with his prey.',
            'voice': {'prompt': 'low aristocratic baritone', 'traits': ['cold', 'precise']},
        },
    )


def _request(**over):
    req = {'speaker': _speaker(), 'kind': 'focus', 'target_name': 'Oogway',
           'intent': 'finish off Oogway, the most wounded enemy', 'max_words': 12,
           'language': 'common', 'telepathic': False}
    req.update(over)
    return req


def test_prompt_carries_persona_intent_and_word_cap():
    system, user = build_prompts(_request())
    assert 'The Count' in system and 'cultured aristocrat' in system
    assert 'low aristocratic baritone' in system and 'cold, precise' in system
    assert 'at most 12 words' in system
    assert 'finish off Oogway' in user


def test_prompt_mentions_telepathy_and_foreign_language():
    _, user = build_prompts(_request(telepathic=True, language='abyssal'))
    assert 'telepathic' in user and 'speaking abyssal' in user


def test_prompt_steers_away_from_downed_target_and_repeats():
    _, user = build_prompts(_request(kind='revive', target_name='Malachi', target_unconscious=True,
                                     recent_lines=['Malachi, rise!']))
    assert 'Malachi is unconscious' in user and 'conscious allies' in user
    assert 'do not repeat' in user and 'Malachi, rise!' in user


def test_phrase_uses_npc_conversation_mode():
    handler = MagicMock(current_provider=object())
    handler.send_message.return_value = 'Bleed for me, Oogway.'
    assert phrase_callout(_request(), llm_handler=handler) == 'Bleed for me, Oogway.'
    _, context = handler.send_message.call_args.args
    assert context['response_mode'] == 'conversation'
    assert context['entity_uid'] == 'npc-1'


def test_phrase_returns_none_on_failure_or_missing_provider():
    handler = MagicMock(current_provider=object())
    handler.send_message.return_value = ''
    assert phrase_callout(_request(), llm_handler=handler) is None

    handler.send_message.side_effect = RuntimeError('down')
    assert phrase_callout(_request(), llm_handler=handler) is None

    idle = MagicMock(current_provider=None, npc_provider=None)
    assert phrase_callout(_request(), llm_handler=idle) is None
    idle.send_message.assert_not_called()


def test_install_registers_phraser():
    try:
        install_callout_phraser()
        assert combat_comms.get_callout_phraser() is phrase_callout
    finally:
        combat_comms.set_callout_phraser(None)
