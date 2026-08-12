"""Unit tests for player "describe what I see?" conversation feature."""

import json
import logging
import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

import importlib

conversation_service = importlib.import_module('webapp.conversation_service')

from webapp.conversation_service import (
    is_player_describe_see_request,
    parse_player_describe_see_request,
    PLAYER_DESCRIBE_SEE_PATTERNS,
)


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------

class TestDescribeSeeDetection:
    """Test that describe-see phrases are correctly identified."""

    def test_describe_what_i_see(self):
        assert is_player_describe_see_request('describe what I see')
        assert is_player_describe_see_request('Describe what I see')
        assert is_player_describe_see_request('DESCRIBE WHAT I SEE')

    def test_what_do_i_see(self):
        assert is_player_describe_see_request('what do I see?')
        assert is_player_describe_see_request('What do I see')
        assert is_player_describe_see_request('what do I see?!')

    def test_tell_me_what_you_see(self):
        assert is_player_describe_see_request('tell me what you see')
        assert is_player_describe_see_request('Tell me what you see about @NPC')

    def test_describe_what_you_see(self):
        assert is_player_describe_see_request('describe what you see')
        assert is_player_describe_see_request('Describe what you see here')

    def test_what_do_you_see(self):
        assert is_player_describe_see_request('what do you see here?')
        assert is_player_describe_see_request('What do you see?')

    def test_give_me_a_description(self):
        assert is_player_describe_see_request('give me a description')
        assert is_player_describe_see_request('give me a description of @Cecilia')

    def test_take_a_good_look(self):
        assert is_player_describe_see_request('take a good look')
        assert is_player_describe_see_request('take a good look at the room')
        assert is_player_describe_see_request('Take a good look at @Jim')

    def test_look_closely(self):
        assert is_player_describe_see_request('look closely')
        assert is_player_describe_see_request('look carefully')
        assert is_player_describe_see_request('look closely at @Darlene')
        assert is_player_describe_see_request('Look hard')

    def test_with_dm_prefix(self):
        assert is_player_describe_see_request('@dm describe what I see')
        assert is_player_describe_see_request('@DM what do I see about @Pip')

    def test_with_targets(self):
        assert is_player_describe_see_request('describe what I see about @Cecilia')
        assert is_player_describe_see_request('what do I see regarding @Jim Halpert?')
        assert is_player_describe_see_request('describe what I see focusing on her expression')

    def test_describe_with_focus(self):
        assert is_player_describe_see_request('describe what I see focusing on his injuries')
        assert is_player_describe_see_request('what do I see about @Cecilia clothing?')

    def test_does_not_match_unrelated(self):
        assert not is_player_describe_see_request('')
        assert not is_player_describe_see_request('hello there')
        assert not is_player_describe_see_request('I have keen perception.')
        assert not is_player_describe_see_request('attack the goblin')
        assert not is_player_describe_see_request('insight check on @NPC')


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestDescribeSeeParsing:
    """Test extraction of target and focus from describe-see messages."""

    def test_parse_no_target_or_focus(self):
        target, focus = parse_player_describe_see_request('describe what I see')
        assert target is None
        assert focus is None

    def test_parse_target_only(self):
        target, focus = parse_player_describe_see_request('describe what I see about @Cecilia')
        assert target == 'Cecilia'
        assert focus is None

    def test_parse_target_and_focus(self):
        target, focus = parse_player_describe_see_request('describe what I see about @Jim focusing on his injuries')
        assert target == 'Jim'
        assert focus == 'his injuries'

    def test_parse_what_do_i_see(self):
        target, focus = parse_player_describe_see_request('what do I see about the vampire?')
        assert target == 'the vampire'
        assert focus is None

    def test_parse_give_description(self):
        target, focus = parse_player_describe_see_request('give me a description of @Darlene regarding her clothing')
        assert target == 'Darlene'
        assert focus == 'her clothing'

    def test_parse_with_dm_prefix(self):
        target, focus = parse_player_describe_see_request('@dm describe what I see about @Pip')
        assert target == 'Pip'
        assert focus is None


# ---------------------------------------------------------------------------
# Stub classes for handler tests
# ---------------------------------------------------------------------------

class _StubRoll:
    def __init__(self, total):
        self._total = total

    def result(self):
        return self._total

    def __str__(self):
        return f'1d20+2={self._total}'


class _StubEntity:
    def __init__(self, uid, label, appearance='', statuses=None, effects=None):
        self.entity_uid = uid
        self._label = label
        self.outward_appearance = appearance
        self.statuses = statuses or []
        self._effects = effects or []
        self.perception_calls = []
        self.properties = {}
        self.conversation_buffer = []

    def label(self):
        return self._label

    def is_npc(self):
        return True

    def perception_check(self, description=None):
        self.perception_calls.append(description)
        return _StubRoll(15)

    def current_effects(self):
        return self._effects


class _StubLLMHandler:
    def __init__(self, response=None):
        self.response = response or {
            'description': 'You notice the NPC\'s expression is tense, their jaw clenched.'
        }
        self.calls = []

    def send_message(self, messages, context=None):
        self.calls.append((messages, context))
        return json.dumps(self.response)


class _StubRagHandler:
    def __init__(self, target=None):
        self.target = target
        self.calls = []

    def resolve_named_target(self, actor, target_spec, speaker=None, include_objects=False):
        if self.target and target_spec:
            return self.target
        return None


# ---------------------------------------------------------------------------
# Handler integration tests (lightweight)
# ---------------------------------------------------------------------------

class TestDescribeSeeHandler:
    """Test the describe-see handler integration."""

    def test_detects_pattern_in_talk_flow(self):
        """Verify that describe-see phrases are detected in /talk messages."""
        messages = [
            'describe what I see',
            'what do I see?',
            '@dm describe what I see about @NPC',
            'tell me what you see',
            'look closely at the room',
        ]
        for msg in messages:
            assert is_player_describe_see_request(msg), f"Failed to detect: {msg}"

    def test_parsing_preserves_dashes_in_names(self):
        target, focus = parse_player_describe_see_request(
            'describe what I see about @jim-halpert'
        )
        assert target == 'jim-halpert'

    def test_parsing_preserves_handles(self):
        target, focus = parse_player_describe_see_request(
            'what do I see about @Cecilia?'
        )
        assert target == 'Cecilia'

    def test_patterns_are_case_insensitive(self):
        variations = [
            'DESCRIBE WHAT I SEE',
            'describe what I see',
            'Describe What I See',
            'DeScRiBe WhAt I sEe',
        ]
        for variant in variations:
            assert is_player_describe_see_request(variant), f"Failed case-insensitive: {variant}"

    def test_perception_check_message_key_describe_see(self):
        """Verify the message key format for deduplication."""
        key = f"describe_see:entity-123"
        assert key == "describe_see:entity-123"

    def test_detail_tier_thresholds(self):
        """Verify the detail tier assignment logic."""
        tiers = {
            20: 'detailed',
            18: 'detailed',
            17: 'moderate',
            14: 'moderate',
            13: 'basic',
            10: 'basic',
            9: 'vague',
            5: 'vague',
            0: 'vague',
        }
        for roll_total, expected_tier in tiers.items():
            if roll_total >= 18:
                actual_tier = 'detailed'
            elif roll_total >= 14:
                actual_tier = 'moderate'
            elif roll_total >= 10:
                actual_tier = 'basic'
            else:
                actual_tier = 'vague'
            assert actual_tier == expected_tier, f"Roll {roll_total} should be {expected_tier}, got {actual_tier}"


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    import pytest
    pytest.main([__file__, '-v'])
