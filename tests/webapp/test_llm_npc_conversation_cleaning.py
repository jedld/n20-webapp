import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.llm_handler import LLMHandler


def test_conversation_mode_preserves_let_me_dialogue():
    handler = LLMHandler()
    line = "Let me go."

    cleaned_dm = handler._clean_response(line, response_mode='dm')
    cleaned_npc = handler._clean_response(line, response_mode='conversation')

    assert 'help with your D&D game' in cleaned_dm
    assert cleaned_npc == line


def test_conversation_mode_empty_fallback_is_silent():
    handler = LLMHandler()
    thinking_only = """<think>
Let me think about how to answer...
</think>"""

    cleaned = handler._clean_response(thinking_only, response_mode='conversation')

    assert cleaned == '...'
    assert 'help with your D&D game' not in cleaned


def test_json_response_is_complete_without_continuation():
    handler = LLMHandler()
    payload = '{"spoken": "Hello.", "narrative": []}'
    assert handler._response_looks_complete(payload) is True


def test_fenced_json_response_is_complete_without_continuation():
    handler = LLMHandler()
    payload = '''```json
{
  "assessment": "uncertain",
  "reason": "The girl seems genuinely terrified."
}
```'''
    assert handler._response_looks_complete(payload) is True


def test_dialogue_with_internal_ellipsis_is_complete():
    handler = LLMHandler()
    line = "It's in the basement... Please will you help us?"
    assert handler._response_looks_complete(line) is True


def test_dialogue_ending_in_closing_quote_is_complete():
    handler = LLMHandler()
    line = (
        '"Well, you certainly don\'t waste any time, do you? One fresh loaf coming right up!" '
        '[ASIDE: She winks playfully.] '
        '"And if \'some\' means more bread, I\'ve got plenty."'
    )
    assert handler._response_looks_complete(line) is True


def test_asterisk_narrative_block_is_complete_with_finish_reason_stop():
    handler = LLMHandler()
    line = '*I stand my ground, hands clenched at my sides.* "Hand it over now!"'
    assert handler._response_looks_complete(line, done_reason='stop') is True


def test_npc_conversation_skips_continuation_loop():
    handler = LLMHandler()
    calls = []

    class Provider:
        last_finish_reason = None

        def send_message(self, _messages):
            calls.append(_messages)
            return 'Only one reply'

        def get_available_models(self):
            return []

    provider = Provider()
    handler.current_provider = provider
    handler.npc_provider = None

    response = handler.send_message(
        [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hello'}],
        {'response_mode': 'conversation', 'log_label': 'test'},
    )

    assert response == 'Only one reply'
    assert len(calls) == 1


def test_dm_mode_empty_fallback_stays_assistant():
    handler = LLMHandler()
    thinking_only = """<think>
Only internal reasoning here.
</think>"""

    cleaned = handler._clean_response(thinking_only, response_mode='dm')

    assert 'help with your D&D game' in cleaned
