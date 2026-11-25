"""Tests for login character-selection PC vs sidekick helpers."""

import os
import tempfile
import unittest
from types import SimpleNamespace

from webapp.blueprints.helpers.character_selection import (
    collect_selectable_entries,
    is_sidekick_selectable,
    load_character_selection_settings,
    map_defined_player_entries,
    partition_selectables,
)


class TestMapDefinedPlayerEntries(unittest.TestCase):
    """Campaigns may define party PCs only in map ``player:`` YAML blocks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        chars_dir = os.path.join(self.tmpdir, 'characters')
        os.makedirs(chars_dir)
        with open(os.path.join(chars_dir, 'goliath_paladin.yml'), 'w', encoding='utf-8') as fh:
            fh.write('name: Karlach Goliath\nrace: goliath\ndescription: An Oath of Ancients paladin.\n')
        with open(os.path.join(chars_dir, 'chestnut_latchlifter.yml'), 'w', encoding='utf-8') as fh:
            fh.write('name: Chestnut Latchlifter\nrace: kender\n')
        self.session = SimpleNamespace(
            root_path=self.tmpdir,
            maps={
                'priest_quarters': SimpleNamespace(
                    name='priest_quarters',
                    properties={'player': [
                        {
                            'position': [21, 14],
                            'sheet': 'characters/goliath_paladin.yml',
                            'overrides': {'entity_uid': 'ddb-142861711'},
                        },
                        {
                            'position': [22, 12],
                            'sheet': 'characters/chestnut_latchlifter.yml',
                        },
                        {
                            'position': [1, 1],
                            'sheet': 'npcs/sidekick.yml',
                            'overrides': {'entity_uid': 'npc_sidekick'},
                        },
                    ]},
                ),
                'start': SimpleNamespace(name='start', properties={'player': []}),
            },
        )

    def test_derives_entries_from_map_player_defs(self):
        entries = map_defined_player_entries(self.session)
        by_name = {entry['name']: entry for entry in entries}
        self.assertEqual(set(by_name), {'ddb-142861711', 'chestnut_latchlifter'})
        karlach = by_name['ddb-142861711']
        self.assertEqual(karlach['sheet'], 'characters/goliath_paladin.yml')
        self.assertEqual(karlach['map'], 'priest_quarters')
        self.assertEqual(karlach['source'], 'map_player')
        self.assertEqual(karlach['display_name'], 'Karlach Goliath')
        self.assertEqual(karlach['description'], 'An Oath of Ancients paladin.')
        # NPC sheets are handled by sidekick selection, not PC claims.
        self.assertNotIn('npc_sidekick', by_name)

    def test_missing_uid_falls_back_to_sheet_stem(self):
        entries = map_defined_player_entries(self.session)
        entry = next(e for e in entries if e['name'] == 'chestnut_latchlifter')
        self.assertEqual(entry['overrides']['entity_uid'], 'chestnut_latchlifter')

    def test_collect_selectable_entries_merges_without_duplicates(self):
        index_data = {
            'selectable_characters': [
                {
                    'name': 'chestnut_latchlifter',
                    'description': 'index entry wins',
                    'sheet': 'characters/chestnut_latchlifter.yml',
                },
            ],
        }
        entries = collect_selectable_entries(index_data, self.session)
        names = [entry['name'] for entry in entries]
        self.assertEqual(names.count('chestnut_latchlifter'), 1)
        index_row = next(e for e in entries if e['name'] == 'chestnut_latchlifter')
        self.assertEqual(index_row.get('description'), 'index entry wins')
        self.assertIn('ddb-142861711', names)

    def test_no_session_yields_only_index_rows(self):
        entries = collect_selectable_entries({'selectable_characters': [{'name': 'a'}]}, game_session=None)
        self.assertEqual([e['name'] for e in entries], ['a'])


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
