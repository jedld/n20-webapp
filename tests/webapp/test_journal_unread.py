"""Tests for journal unread tracking and YAML seeding helpers."""

import unittest
from unittest.mock import Mock, patch

from natural20.event_manager import EventManager
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from webapp.blueprints.helpers.journal_utils import (
    grant_npc_journal_hooks,
    unread_journal_summary_for_username,
)


def _make_session():
    return Session(root_path='tests/fixtures', event_manager=EventManager())


class TestJournalUnread(unittest.TestCase):
    def test_unread_summary_for_username(self):
        pc = Mock(spec=PlayerCharacter)
        pc.entity_uid = 'investigator'
        pc.label.return_value = 'Investigator'
        pc.unread_journal_count.return_value = 2

        with patch(
            'webapp.blueprints.helpers.journal_utils.entities_controlled_by',
            return_value=[pc],
        ):
            summary = unread_journal_summary_for_username('player1')

        self.assertEqual(summary['total_unread'], 2)
        self.assertIn('investigator', summary['characters'])

    def test_grant_npc_journal_hooks_once(self):
        session = _make_session()
        pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')

        npc = Mock()
        npc.entity_uid = 'detective_jaro'
        npc.npc_type = 'detective_jaro'
        npc.properties = {
            'journal': [
                {'seed_id': 'jaro_hook', 'title': 'Ask Jaro', 'text': 'Gate briefing.', 'kind': 'quest'},
            ],
        }

        first = grant_npc_journal_hooks(pc, npc)
        second = grant_npc_journal_hooks(pc, npc)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(pc.journal), 1)
