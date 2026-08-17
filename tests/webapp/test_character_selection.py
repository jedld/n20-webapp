"""Tests for login character-selection PC vs sidekick helpers."""

import unittest

from webapp.blueprints.helpers.character_selection import (
    is_sidekick_selectable,
    load_character_selection_settings,
    partition_selectables,
)


class TestCharacterSelectionHelpers(unittest.TestCase):
    def test_partition_separates_pcs_and_sidekicks(self):
        entries = [
            {'name': 'gomerin', 'sheet': 'characters/gomerin.yml'},
            {
                'name': 'npc_jarek_volkov',
                'kind': 'npc',
                'role_label': 'Sidekick',
                'sheet': 'npcs/jarek_volkov',
            },
            {'name': 'high_elf_mage', 'sheet': 'characters/high_elf_mage.yml'},
        ]
        players, sidekicks = partition_selectables(entries)
        self.assertEqual([row['name'] for row in players], ['gomerin', 'high_elf_mage'])
        self.assertEqual([row['name'] for row in sidekicks], ['npc_jarek_volkov'])

    def test_is_sidekick_selectable_from_sheet_path(self):
        self.assertTrue(is_sidekick_selectable({'sheet': 'npcs/mira_krev.yml'}))
        self.assertFalse(is_sidekick_selectable({'sheet': 'characters/mira.yml'}))
        self.assertFalse(is_sidekick_selectable(None))

    def test_selection_settings_default_to_single_hero(self):
        session = type('S', (), {'game_properties': {}})()
        settings = load_character_selection_settings(session, index_data={})
        self.assertEqual(settings['player_slots'], 1)
        self.assertEqual(settings['sidekick_slots'], 0)

    def test_selection_settings_from_game_yml(self):
        session = type('S', (), {
            'game_properties': {'character_selection': {'sidekick_slots': 1}},
        })()
        settings = load_character_selection_settings(session, index_data={})
        self.assertEqual(settings['sidekick_slots'], 1)

    def test_require_sidekick_alias(self):
        session = type('S', (), {
            'game_properties': {'character_selection': {'require_sidekick': True}},
        })()
        settings = load_character_selection_settings(session, index_data={})
        self.assertEqual(settings['sidekick_slots'], 1)

    def test_game_yml_overrides_index_json(self):
        session = type('S', (), {
            'game_properties': {'character_selection': {'sidekick_slots': 1}},
        })()
        settings = load_character_selection_settings(
            session,
            index_data={'character_selection': {'sidekick_slots': 0}},
        )
        self.assertEqual(settings['sidekick_slots'], 1)

    def test_death_house_enables_sidekick_slot(self):
        import os
        import yaml

        path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', '..',
            'user_levels', 'death_house', 'game.yml',
        )
        if not os.path.isfile(path):
            self.skipTest('death_house campaign not present')
        with open(path, encoding='utf-8') as fh:
            data = yaml.safe_load(fh)
        self.assertEqual((data.get('character_selection') or {}).get('sidekick_slots'), 1)

    def test_serialize_sidekick_details_without_pc_api(self):
        from webapp.blueprints.helpers.character_selection import (
            serialize_selection_character_details,
        )

        class FakeSidekick:
            entity_uid = 'npc_jarek_volkov'
            properties = {
                'description': "A wolf-scarred hunter.",
                'sidekick_class': 'sidekick_warrior',
                'sidekick_role': 'attacker',
                'kind': 'Jarek Volkov',
                'maneuvers': [],
            }
            race_properties = None
            resources = {}
            journal = [{'text': 'should not leak'}]

            def label(self):
                return 'Jarek'

            def name(self):
                return 'Jarek'

            def race(self):
                return 'human'

            def c_class(self):
                return 'Jarek Volkov'

            def level(self):
                return 1

            def hp(self):
                return 13

            def max_hp(self):
                return 13

            def armor_class(self):
                return 13

            def speed(self):
                return 30

            def ability_score_str(self):
                return 16

            def ability_score_dex(self):
                return 14

            def ability_score_con(self):
                return 14

            def ability_score_int(self):
                return 8

            def ability_score_wis(self):
                return 13

            def ability_score_cha(self):
                return 10

            def str_mod(self):
                return 3

            def dex_mod(self):
                return 2

            def con_mod(self):
                return 2

            def int_mod(self):
                return -1

            def wis_mod(self):
                return 1

            def cha_mod(self):
                return 0

            def proficiency_bonus(self):
                return 2

            def passive_perception(self):
                return 13

            def languages(self):
                return ['common']

            def equipped_items(self):
                return [{'type': 'armor', 'name': 'Leather Armor', 'ac': 11}]

            def class_feature(self, feature):
                return feature == 'second_wind'

        details = serialize_selection_character_details(FakeSidekick())
        self.assertEqual(details['name'], 'Jarek')
        self.assertEqual(details['kind'], 'npc')
        self.assertTrue(details['is_sidekick'])
        self.assertEqual(details['classes'], {'warrior': 1})
        self.assertEqual(details['hit_points']['maximum'], 13)
        self.assertEqual(details['equipment']['armor'][0]['name'], 'Leather Armor')
        self.assertIn('Second Wind', details['class_features'])
        self.assertEqual(details['journal'], [])


if __name__ == '__main__':
    unittest.main()
