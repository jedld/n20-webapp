"""Tests for campaign UI configuration helpers."""

import unittest
from unittest.mock import Mock, patch

from natural20.player_character import PlayerCharacter
from webapp.blueprints.helpers.campaign_config import (
    character_description_suppressed_for,
    dialog_description_suppressed_for,
)


class TestDialogDescriptionSuppression(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(dialog_description_suppressed_for(index_data={}))

    def test_suppress_all(self):
        data = {'suppress_dialog_descriptions': True}
        self.assertTrue(dialog_description_suppressed_for(index_data=data))

    def test_suppress_all_string(self):
        data = {'suppress_dialog_descriptions': 'all'}
        self.assertTrue(dialog_description_suppressed_for(index_data=data))

    def test_suppress_player_characters_only(self):
        data = {'suppress_dialog_descriptions': 'player_characters'}
        pc = Mock(spec=PlayerCharacter)
        npc = Mock()
        self.assertTrue(dialog_description_suppressed_for(pc, index_data=data))
        self.assertFalse(dialog_description_suppressed_for(npc, index_data=data))

    def test_dm_viewer_never_suppressed(self):
        data = {'suppress_dialog_descriptions': True}
        self.assertFalse(
            character_description_suppressed_for(Mock(), viewer_is_dm=True, index_data=data)
        )

    def test_player_viewer_suppressed_when_configured(self):
        data = {'suppress_dialog_descriptions': True}
        self.assertTrue(
            character_description_suppressed_for(Mock(), viewer_is_dm=False, index_data=data)
        )


class TestEntityContextDescriptionSuppression(unittest.TestCase):
    def test_get_entity_context_hides_description_for_players_when_configured(self):
        from webapp.entity_rag_handler import EntityRAGHandler

        mock_entity = Mock()
        mock_entity.label.return_value = "Investigator"
        mock_entity.entity_uid = "investigator"
        mock_entity.description.return_value = "Spoiler hook text"
        mock_entity.inventory_items.return_value = []

        handler = EntityRAGHandler(Mock(), Mock())

        with patch(
            'webapp.entity_rag_handler.character_description_suppressed_for',
            return_value=True,
        ):
            context = handler.get_entity_context(mock_entity, viewer_is_dm=False)

        self.assertIsNone(context['description'])
        self.assertTrue(context['description_suppressed'])

    def test_get_entity_context_keeps_description_for_dm(self):
        from webapp.entity_rag_handler import EntityRAGHandler

        mock_entity = Mock()
        mock_entity.label.return_value = "Investigator"
        mock_entity.entity_uid = "investigator"
        mock_entity.description.return_value = "Spoiler hook text"
        mock_entity.inventory_items.return_value = []

        handler = EntityRAGHandler(Mock(), Mock())
        context = handler.get_entity_context(mock_entity, viewer_is_dm=True)

        self.assertEqual(context['description'], "Spoiler hook text")
        self.assertNotIn('description_suppressed', context)
