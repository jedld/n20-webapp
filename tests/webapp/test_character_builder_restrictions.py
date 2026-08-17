"""Tests for campaign-level character builder restrictions."""

import unittest

from webapp.blueprints.helpers.character_builder_restrictions import (
    CharacterBuilderRestrictions,
    load_character_builder_restrictions,
)


class TestCharacterBuilderRestrictions(unittest.TestCase):
    def test_load_from_game_yml(self):
        session = type('S', (), {
            'game_properties': {
                'character_builder': {
                    'min_level': 1,
                    'max_level': 3,
                    'blacklist': {
                        'classes': ['warlock'],
                        'spells': ['fireball'],
                        'feats': ['sharpshooter'],
                        'abilities': ['action_surge'],
                    },
                },
            },
        })()
        restrictions = load_character_builder_restrictions(session, index_data={})
        self.assertEqual(restrictions.min_level, 1)
        self.assertEqual(restrictions.max_level, 3)
        self.assertFalse(restrictions.class_allowed('warlock'))
        self.assertTrue(restrictions.class_allowed('fighter'))
        self.assertFalse(restrictions.spell_allowed('fireball'))
        self.assertFalse(restrictions.feat_allowed('sharpshooter'))
        self.assertFalse(restrictions.ability_allowed('action_surge'))

    def test_allowed_backgrounds_filter(self):
        session = type('S', (), {
            'game_properties': {
                'character_builder': {
                    'allowed_backgrounds': ['haunted_one'],
                },
            },
        })()
        restrictions = load_character_builder_restrictions(session, index_data={})
        backgrounds = {
            'haunted_one': {'label': 'Haunted One'},
            'acolyte': {'label': 'Acolyte'},
        }
        filtered = restrictions.filter_backgrounds(backgrounds)
        self.assertEqual(set(filtered.keys()), {'haunted_one'})
        self.assertTrue(restrictions.background_allowed('haunted_one'))
        self.assertFalse(restrictions.background_allowed('acolyte'))

    def test_empty_allowed_backgrounds_keeps_all(self):
        restrictions = CharacterBuilderRestrictions()
        backgrounds = {
            'haunted_one': {'label': 'Haunted One'},
            'acolyte': {'label': 'Acolyte'},
        }
        self.assertEqual(set(restrictions.filter_backgrounds(backgrounds).keys()), {'haunted_one', 'acolyte'})

    def test_game_yml_overrides_index_json(self):
        session = type('S', (), {
            'game_properties': {'character_builder': {'max_level': 2}},
        })()
        restrictions = load_character_builder_restrictions(
            session,
            index_data={'character_builder': {'max_level': 5}},
        )
        self.assertEqual(restrictions.max_level, 2)

    def test_filter_classes_removes_blacklisted_spells(self):
        restrictions = CharacterBuilderRestrictions(
            blacklisted_classes={'warlock'},
            blacklisted_spells={'fireball'},
        )
        classes = {
            'wizard': {
                'spell_list': {
                    'cantrip': ['fire_bolt', 'fireball'],
                    'level_1': ['magic_missile', 'fireball'],
                },
                'feat_choices': ['sharpshooter', 'alert'],
            },
            'warlock': {'spell_list': {'cantrip': ['eldritch_blast']}},
        }
        filtered = restrictions.filter_classes(classes)
        self.assertNotIn('warlock', filtered)
        wizard_spells = filtered['wizard']['spell_list']
        self.assertIn('fire_bolt', wizard_spells['cantrip'])
        self.assertNotIn('fireball', wizard_spells['cantrip'])
        self.assertNotIn('fireball', wizard_spells['level_1'])

    def test_validate_selection_reports_errors(self):
        restrictions = CharacterBuilderRestrictions(
            min_level=1,
            max_level=2,
            blacklisted_classes={'warlock'},
            blacklisted_spells={'fireball'},
        )
        errors = restrictions.validate_selection(
            klass='warlock',
            level=5,
            cantrips=['fireball'],
            spells_by_level={1: ['magic_missile']},
        )
        self.assertTrue(any('warlock' in e for e in errors))
        self.assertTrue(any('Level must be' in e for e in errors))
        self.assertTrue(any('fireball' in e for e in errors))

    def test_clamp_level(self):
        restrictions = CharacterBuilderRestrictions(min_level=2, max_level=4)
        self.assertEqual(restrictions.clamp_level(1), 2)
        self.assertEqual(restrictions.clamp_level(3), 3)
        self.assertEqual(restrictions.clamp_level(9), 4)


if __name__ == '__main__':
    unittest.main()
