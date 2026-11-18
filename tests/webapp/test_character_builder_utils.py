"""Tests for webapp.blueprints.helpers.character_builder_utils."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch, mock_open


class TestCharacterBuilderUtils(unittest.TestCase):
    """Test cases for character_builder_utils module."""

    def setUp(self):
        """Set up test fixtures."""
        from webapp.blueprints.helpers import character_builder_utils as mod
        self.mod = mod

    # ------------------------------------------------------------------ #
    # Form parsing helpers
    # ------------------------------------------------------------------ #

    def test_parse_json_list_form_empty(self):
        """_parse_json_list_form returns [] for missing/empty key."""
        form = MagicMock()
        form.get.return_value = None
        result = self.mod._parse_json_list_form(form, 'key')
        self.assertEqual(result, [])

    def test_parse_json_list_form_valid(self):
        """_parse_json_list_form returns parsed list."""
        form = MagicMock()
        form.get.return_value = json.dumps(['a', 'b', 'c'])
        result = self.mod._parse_json_list_form(form, 'key')
        self.assertEqual(result, ['a', 'b', 'c'])

    def test_parse_json_list_form_invalid_json(self):
        """_parse_json_list_form returns [] for invalid JSON."""
        form = MagicMock()
        form.get.return_value = '{bad json'
        result = self.mod._parse_json_list_form(form, 'key')
        self.assertEqual(result, [])

    def test_parse_json_list_form_non_list(self):
        """_parse_json_list_form returns [] when parsed value is not a list."""
        form = MagicMock()
        form.get.return_value = json.dumps({'not': 'a list'})
        result = self.mod._parse_json_list_form(form, 'key')
        self.assertEqual(result, [])

    def test_parse_json_dict_form_empty(self):
        """_parse_json_dict_form returns {} for missing/empty key."""
        form = MagicMock()
        form.get.return_value = None
        result = self.mod._parse_json_dict_form(form, 'key')
        self.assertEqual(result, {})

    def test_parse_json_dict_form_valid(self):
        """_parse_json_dict_form returns parsed dict."""
        form = MagicMock()
        form.get.return_value = json.dumps({'a': 1, 'b': 2})
        result = self.mod._parse_json_dict_form(form, 'key')
        self.assertEqual(result, {'a': 1, 'b': 2})

    def test_parse_json_dict_form_invalid_json(self):
        """_parse_json_dict_form returns {} for invalid JSON."""
        form = MagicMock()
        form.get.return_value = '{bad json'
        result = self.mod._parse_json_dict_form(form, 'key')
        self.assertEqual(result, {})

    def test_parse_json_dict_form_non_dict(self):
        """_parse_json_dict_form returns {} when parsed value is not a dict."""
        form = MagicMock()
        form.get.return_value = json.dumps([1, 2, 3])
        result = self.mod._parse_json_dict_form(form, 'key')
        self.assertEqual(result, {})

    # ------------------------------------------------------------------ #
    # Ability score helpers
    # ------------------------------------------------------------------ #

    def test_ability_mod_positive(self):
        """_ability_mod returns positive modifier for score > 10."""
        self.assertEqual(self.mod._ability_mod(14), 2)

    def test_ability_mod_negative(self):
        """_ability_mod returns negative modifier for score < 10."""
        self.assertEqual(self.mod._ability_mod(6), -2)

    def test_ability_mod_zero(self):
        """_ability_mod returns 0 for score 10."""
        self.assertEqual(self.mod._ability_mod(10), 0)

    def test_ability_mod_str(self):
        """_ability_mod handles string input."""
        self.assertEqual(self.mod._ability_mod('16'), 3)

    def test_ability_mod_invalid(self):
        """_ability_mod returns 0 for invalid input."""
        self.assertEqual(self.mod._ability_mod('abc'), 0)
        self.assertEqual(self.mod._ability_mod(None), 0)

    # ------------------------------------------------------------------ #
    # Spell choice helpers
    # ------------------------------------------------------------------ #

    def test_spell_choice_caps_wizard(self):
        """_spell_choice_caps returns correct caps for wizard."""
        caps = self.mod._spell_choice_caps('Wizard', 1, {'int': 14}, {})
        self.assertIsInstance(caps['cantrip_cap'], int)
        self.assertIsInstance(caps['level1_cap'], int)
        self.assertIsInstance(caps['spellbook_cap'], int)
        # Wizard spellbook grows with level
        caps_l2 = self.mod._spell_choice_caps('Wizard', 2, {'int': 14}, {})
        self.assertGreater(caps_l2['spellbook_cap'], caps['spellbook_cap'])

    def test_spell_choice_caps_cleric(self):
        """_spell_choice_caps returns correct caps for cleric."""
        caps = self.mod._spell_choice_caps('Cleric', 1, {'wisdom': 14}, {})
        self.assertIsInstance(caps['cantrip_cap'], int)
        self.assertIsInstance(caps['level1_cap'], int)
        self.assertEqual(caps['spellbook_cap'], 0)

    def test_spell_choice_caps_empty_class(self):
        """_spell_choice_caps returns zero caps for unknown class."""
        caps = self.mod._spell_choice_caps('Unknown', 1, {}, {})
        # The wrapper normalises the full caps dict (known/prepared/max level,
        # pact slots and per-level slots) on top of the legacy three keys.
        self.assertEqual(caps, {
            'cantrip_cap': 0,
            'level1_cap': 0,
            'spellbook_cap': 0,
            'spells_known_cap': 0,
            'spells_prepared_cap': 0,
            'max_spell_level': 0,
            'pact_slot_level': 0,
            'slots_by_level': {},
        })

    def test_spell_choice_caps_none_class(self):
        """_spell_choice_caps handles None class."""
        caps = self.mod._spell_choice_caps(None, 1, {}, {})
        self.assertIsInstance(caps['cantrip_cap'], int)

    def test_spell_choice_caps_bard(self):
        """_spell_choice_caps returns known spells for bard."""
        for lvl in range(1, 21):
            caps = self.mod._spell_choice_caps('Bard', lvl, {'charisma': 14}, {})
            self.assertGreaterEqual(caps['level1_cap'], 0)

    def test_spell_choice_caps_warlock(self):
        """_spell_choice_caps returns known spells for warlock."""
        caps = self.mod._spell_choice_caps('Warlock', 1, {'charisma': 14}, {})
        self.assertGreaterEqual(caps['level1_cap'], 0)

    def test_spell_choice_caps_sorcerer(self):
        """_spell_choice_caps returns known spells for sorcerer."""
        caps = self.mod._spell_choice_caps('Sorcerer', 1, {'charisma': 14}, {})
        self.assertGreaterEqual(caps['level1_cap'], 0)

    def test_spell_choice_caps_paladin(self):
        """_spell_choice_caps returns correct caps for paladin."""
        caps = self.mod._spell_choice_caps('Paladin', 1, {'charisma': 14}, {})
        self.assertIsInstance(caps['level1_cap'], int)

    def test_spell_choice_caps_ranger(self):
        """_spell_choice_caps returns correct caps for ranger."""
        caps = self.mod._spell_choice_caps('Ranger', 1, {'wisdom': 14}, {})
        self.assertIsInstance(caps['level1_cap'], int)

    def test_spell_choice_caps_with_class_def_spell_ability(self):
        """_spell_choice_caps uses class_def spellcasting_ability for cleric/druid."""
        class_def = {'spellcasting_ability': 'constitution'}
        caps = self.mod._spell_choice_caps('Cleric', 1, {'con': 14}, class_def)
        self.assertIsInstance(caps['level1_cap'], int)

    # ------------------------------------------------------------------ #
    # Class and feat choices application
    # ------------------------------------------------------------------ #

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_skills(self, mock_get_session):
        """_apply_class_and_feat_choices applies skill selections."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'dex': 14}, 'skills': []}
        classes_def = {'Wizard': {'available_skills': ['Arcana', 'History'], 'available_skills_choices': 2}}
        self.mod._apply_class_and_feat_choices(
            pc, 'Wizard', 1, classes_def,
            ['Arcana', 'History'], [], [], []
        )
        self.assertIn('Arcana', pc.get('skills', []))
        self.assertIn('History', pc.get('skills', []))

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_truncates_skills(self, mock_get_session):
        """_apply_class_and_feat_choices truncates skills to max_choices."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'dex': 14}, 'skills': []}
        classes_def = {'Wizard': {'available_skills': ['Arcana', 'History', 'Nature'], 'available_skills_choices': 1}}
        self.mod._apply_class_and_feat_choices(
            pc, 'Wizard', 1, classes_def,
            ['Arcana', 'History'], [], [], []
        )
        self.assertEqual(len(pc.get('skills', [])), 1)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_prepared_spells(self, mock_get_session):
        """_apply_class_and_feat_choices applies prepared spells."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'int': 14}}
        classes_def = {
            'Wizard': {
                'spell_list': {'cantrip': ['fire_bolt', 'light'], 'level_1': ['magic_missile', 'shield']},
            }
        }
        self.mod._apply_class_and_feat_choices(
            pc, 'Wizard', 1, classes_def,
            [], ['fire_bolt', 'light'], ['magic_missile'], []
        )
        self.assertIn('fire_bolt', pc.get('prepared_spells', []))

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_wizard_spellbook(self, mock_get_session):
        """_apply_class_and_feat_choices builds wizard spellbook."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'int': 14}}
        classes_def = {
            'Wizard': {
                'spell_list': {'cantrip': ['fire_bolt'], 'level_1': ['magic_missile', 'shield', 'burning_hands']},
            }
        }
        self.mod._apply_class_and_feat_choices(
            pc, 'Wizard', 2, classes_def,
            [], ['fire_bolt'], ['magic_missile'], []
        )
        self.assertIn('spellbook', pc)
        self.assertIsInstance(pc['spellbook'], list)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_feats(self, mock_get_session):
        """_apply_class_and_feat_choices applies feat selections."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'dex': 14}}
        classes_def = {
            'Fighter': {
                'feat_choices': ['Dodge', 'Great Weapon Master', 'Sharpshooter'],
                'feat_choices_count': 1,
            }
        }
        self.mod._apply_class_and_feat_choices(
            pc, 'Fighter', 1, classes_def,
            [], [], [], ['Dodge', 'Great Weapon Master']
        )
        self.assertEqual(len(pc.get('feats', [])), 1)
        self.assertEqual(pc['feats'][0] if pc['feats'] else None, 'Dodge')

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_empty_class(self, mock_get_session):
        """_apply_class_and_feat_choices handles empty class definition."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'dex': 14}}
        self.mod._apply_class_and_feat_choices(
            pc, 'Unknown', 1, {},
            [], [], [], []
        )
        self.assertNotIn('skills', pc)
        self.assertNotIn('prepared_spells', pc)
        self.assertNotIn('feats', pc)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_removes_prepared_spells_when_empty(self, mock_get_session):
        """_apply_class_and_feat_choices removes prepared_spells key when no spells."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'int': 14}, 'prepared_spells': ['old_spell']}
        classes_def = {
            'Wizard': {
                'spell_list': {'cantrip': [], 'level_1': []},
            }
        }
        self.mod._apply_class_and_feat_choices(
            pc, 'Wizard', 1, classes_def,
            [], [], [], []
        )
        self.assertNotIn('prepared_spells', pc)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_apply_class_and_feat_choices_no_feat_options(self, mock_get_session):
        """_apply_class_and_feat_choices uses selected_feats when no feat_options."""
        mock_get_session.return_value = MagicMock()
        pc = {'ability': {'dex': 14}}
        classes_def = {'Fighter': {'feat_choices': [], 'available_feats': []}}
        self.mod._apply_class_and_feat_choices(
            pc, 'Fighter', 1, classes_def,
            [], [], [], ['Dodge']
        )
        self.assertEqual(pc.get('feats'), ['Dodge'])

    # ------------------------------------------------------------------ #
    # Ability mod edge cases
    # ------------------------------------------------------------------ #

    def test_ability_mod_extreme_scores(self):
        """_ability_mod handles extreme scores."""
        self.assertEqual(self.mod._ability_mod(1), -5)
        self.assertEqual(self.mod._ability_mod(30), 10)

    # ------------------------------------------------------------------ #
    # Image processing helpers
    # ------------------------------------------------------------------ #

    @patch('natural20.image_gen.tokens.ImageDraw')
    @patch('natural20.image_gen.tokens.Image')
    def test_make_circular_token_basic(self, mock_image_cls, mock_draw_cls):
        """_make_circular_token creates a circular token from PIL image."""
        pil_img = MagicMock()
        pil_img.mode = 'RGBA'
        pil_img.size = (512, 256)
        pil_img.crop.return_value = pil_img
        pil_img.resize.return_value = pil_img

        # Mock the mask image and draw operations
        mask_img = MagicMock()
        result_img = MagicMock()

        mock_image_cls.new.side_effect = lambda mode, size, color=None: mask_img if mode == 'L' else result_img

        result = self.mod._make_circular_token(pil_img, size=256, ring_width=4)

        self.assertIsNotNone(result)
        pil_img.crop.assert_called_once()
        pil_img.resize.assert_called_once()

    def test_make_circular_token_converts_rgba(self):
        """_make_circular_token converts non-RGBA images."""
        from PIL import Image as RealImage
        pil_img = RealImage.new('RGB', (64, 64), color=(10, 20, 30))
        result = self.mod._make_circular_token(pil_img, size=32, ring_width=1)
        self.assertEqual(result.mode, 'RGBA')
        self.assertEqual(result.size, (32, 32))
        self.assertEqual(result.getpixel((0, 0))[3], 0)

    def test_decode_data_url_image_basic(self):
        """_decode_data_url_image decodes valid data URL."""
        import base64
        from PIL import Image
        # Create a minimal 1x1 PNG
        img = Image.new('RGB', (1, 1), color='red')
        import io
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        img_bytes = buf.getvalue()
        b64 = base64.b64encode(img_bytes).decode('ascii')
        data_url = f'data:image/png;base64,{b64}'

        result = self.mod._decode_data_url_image(data_url)
        self.assertIsNotNone(result)

    def test_decode_data_url_image_invalid(self):
        """_decode_data_url_image returns None for invalid input."""
        self.assertIsNone(self.mod._decode_data_url_image('not a url'))
        self.assertIsNone(self.mod._decode_data_url_image(None))
        self.assertIsNone(self.mod._decode_data_url_image(123))

    def test_decode_data_url_image_wrong_format(self):
        """_decode_data_url_image returns None for unparseable image data."""
        self.assertIsNone(self.mod._decode_data_url_image('data:image/png;base64,invalidbase64!!!'))

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_resolve_character_yaml_path_direct(self, mock_get_session):
        """_resolve_character_yaml_path finds direct match."""
        mock_session = MagicMock()
        mock_session.root_path = '/tmp/test_campaign'
        mock_get_session.return_value = mock_session
        with patch.object(self.mod.os.path, 'isdir', return_value=True):
            with patch.object(self.mod.os.path, 'exists', return_value=True):
                result = self.mod._resolve_character_yaml_path('test_character')
                self.assertIsNotNone(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_resolve_character_yaml_path_fuzzy(self, mock_get_session):
        """_resolve_character_yaml_path does fuzzy match on YAML content."""
        mock_session = MagicMock()
        mock_session.root_path = '/tmp/test_campaign'
        mock_get_session.return_value = mock_session

        def exists_side_effect(path):
            return 'invalid' in path or 'nonexistent' in path

        with patch.object(self.mod.os.path, 'isdir', return_value=True):
            with patch.object(self.mod.os.path, 'exists', side_effect=exists_side_effect):
                with patch.object(self.mod.os, 'listdir', return_value=['test_character.yml']):
                    # read_data (not read.return_value): yaml.safe_load reads in chunks
                    # until EOF, so a constant read() return loops and grows memory forever.
                    mock_file = mock_open(read_data='entity_uid: test-character\nname: Test Character\n')
                    with patch('builtins.open', mock_file):
                        result = self.mod._resolve_character_yaml_path('test character')
                        self.assertIsNotNone(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_resolve_character_yaml_path_not_found(self, mock_get_session):
        """_resolve_character_yaml_path returns None when not found."""
        mock_session = MagicMock()
        mock_session.root_path = '/tmp/test_campaign'
        mock_get_session.return_value = mock_session
        with patch.object(self.mod.os.path, 'isdir', return_value=False):
            result = self.mod._resolve_character_yaml_path('nonexistent')
            self.assertIsNone(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    def test_resolve_character_yaml_path_skips_non_yml(self, mock_get_session):
        """_resolve_character_yaml_path skips non-YAML files."""
        mock_session = MagicMock()
        mock_session.root_path = '/tmp/test_campaign'
        mock_get_session.return_value = mock_session
        with patch.object(self.mod.os.path, 'isdir', return_value=True):
            with patch.object(self.mod.os.path, 'exists', return_value=False):
                with patch.object(self.mod.os, 'listdir', return_value=['file.txt', 'file.yml']):
                    result = self.mod._resolve_character_yaml_path('anything')
                    self.assertIsNone(result)

    def test_can_edit_character_builder_only_mode(self):
        """_can_edit_character returns True in builder-only mode."""
        with patch.object(self.mod, 'get_builder_only_mode', return_value=True):
            result = self.mod._can_edit_character('test')
            self.assertTrue(result)

    # _can_edit_character lazily imports these helpers from their source
    # modules, so patches must target those modules, not this one.
    _ROLES = 'webapp.blueprints.helpers.auth_utils.roles_for_username'
    _CONTROLLER_OF = 'webapp.blueprints.helpers.template_globals.controller_of'
    _SELECTABLE = 'webapp.blueprints.helpers.pvp.selectable_character_entry'

    def test_can_edit_character_dm_role(self):
        """_can_edit_character returns True for DM users."""
        with patch.object(self.mod, 'get_builder_only_mode', return_value=False):
            with patch('webapp.blueprints.helpers.character_builder_utils.flask_session', {'username': 'dm1'}):
                with patch(self._ROLES, return_value=['dm']):
                    result = self.mod._can_edit_character('test')
                    self.assertTrue(result)

    def test_can_edit_character_controller(self):
        """_can_edit_character returns True for controller of character."""
        with patch.object(self.mod, 'get_builder_only_mode', return_value=False):
            with patch('webapp.blueprints.helpers.character_builder_utils.flask_session', {'username': 'player1'}):
                with patch(self._ROLES, return_value=['player1']):
                    with patch(self._CONTROLLER_OF, return_value=True):
                        result = self.mod._can_edit_character('test')
                        self.assertTrue(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.flask_session', {'username': 'player1'})
    @patch(_ROLES, return_value=['player1'])
    @patch(_CONTROLLER_OF, return_value=False)
    @patch(_SELECTABLE, return_value=None)
    def test_can_edit_character_no_access(self, mock_selectable, mock_ctrl, mock_roles):
        """_can_edit_character returns False when no access."""
        result = self.mod._can_edit_character('test')
        self.assertFalse(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.flask_session', {})
    @patch(_ROLES, return_value=[])
    def test_can_edit_character_not_logged_in(self, mock_roles):
        """_can_edit_character returns False when not logged in."""
        with patch.object(self.mod, 'get_builder_only_mode', return_value=False):
            result = self.mod._can_edit_character('test')
            self.assertFalse(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.os.makedirs')
    @patch('webapp.blueprints.helpers.character_builder_utils.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_save_character_images(self, mock_join, mock_makedirs):
        """_save_character_images saves profile and token images."""
        from unittest.mock import PropertyMock

        profile_pil = MagicMock()
        token_pil = MagicMock()
        profile_pil.convert.return_value = profile_pil
        token_pil.convert.return_value = token_pil
        type(token_pil).size = PropertyMock(return_value=(256, 256))
        token_pil.mode = 'RGBA'

        self.mod._save_character_images('test_uid', '/assets', profile_pil, token_pil)

        profile_pil.convert.assert_called()
        token_pil.convert.assert_called()

    @patch('webapp.blueprints.helpers.character_builder_utils.Image')
    def test_load_character_image_from_file(self, mock_image):
        """_load_character_image_from_request loads from file upload."""
        req = MagicMock()
        mock_file = MagicMock()
        mock_file.filename = 'test.png'
        mock_stream = MagicMock()
        mock_file.stream = mock_stream
        req.files = {'profile': mock_file}
        req.form = {}

        mock_img = MagicMock()
        mock_image.open.return_value = mock_img

        result = self.mod._load_character_image_from_request(req, 'profile', 'prebuilt')
        self.assertIsNotNone(result)
        mock_image.open.assert_called_once_with(mock_stream)

    @patch('webapp.blueprints.helpers.character_builder_utils.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_load_character_image_from_prebuilt(self, mock_join):
        """_load_character_image_from_request loads from prebuilt reference."""
        req = MagicMock()
        req.files = {}
        req.form = {'prebuilt': 'test_character'}

        mock_path = '/templates/assets/characters/test_character.png'
        with patch.object(self.mod, '_resolve_prebuilt_character_image', return_value=mock_path):
            with patch('PIL.Image.open') as mock_open:
                result = self.mod._load_character_image_from_request(req, 'profile', 'prebuilt')
                self.assertIsNotNone(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_load_character_image_from_data_url(self, mock_join):
        """_load_character_image_from_request loads from data URL."""
        import base64
        from PIL import Image
        import io

        img = Image.new('RGB', (1, 1), color='red')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        data_url = f'data:image/png;base64,{b64}'

        req = MagicMock()
        req.files = {}
        req.form = {'prebuilt': '', 'data_url': data_url}

        result = self.mod._load_character_image_from_request(req, 'profile', 'prebuilt', 'data_url')
        self.assertIsNotNone(result)

    @patch('webapp.blueprints.helpers.character_builder_utils.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_load_character_image_fallback_none(self, mock_join):
        """_load_character_image_from_request returns None when no image source works."""
        req = MagicMock()
        mock_file = MagicMock()
        mock_file.filename = ''
        req.files = {'profile': mock_file}
        req.form = {'prebuilt': '', 'data_url': ''}

        result = self.mod._load_character_image_from_request(req, 'profile', 'prebuilt')
        self.assertIsNone(result)

    def test_resolve_prebuilt_character_image(self):
        """_resolve_prebuilt_character_image resolves gallery filenames safely."""
        sample = os.path.join(self.mod.PREBUILT_CHARACTER_DIR, 'character1.png')
        if not os.path.isfile(sample):
            self.skipTest('prebuilt gallery not present')
        self.assertEqual(self.mod._resolve_prebuilt_character_image('character1.png'), sample)
        self.assertIsNone(self.mod._resolve_prebuilt_character_image('../character1.png'))
        self.assertIsNone(self.mod._resolve_prebuilt_character_image('missing.png'))

    def test_prebuilt_character_dir_constant(self):
        """PREBUILT_CHARACTER_DIR is a valid path constant."""
        self.assertIn('prebuild_character', self.mod.PREBUILT_CHARACTER_DIR)

    def test_logger_exists(self):
        """Module has a logger."""
        self.assertIsNotNone(self.mod.logger)

    @patch('webapp.blueprints.helpers.character_builder_utils.json.dump')
    @patch('builtins.open', new_callable=mock_open, read_data='{"selectable_characters": []}')
    @patch('webapp.blueprints.helpers.character_builder_utils.os.path.exists', return_value=True)
    @patch('webapp.blueprints.helpers.character_builder_utils.get_builder_only_mode', return_value=False)
    @patch('webapp.blueprints.helpers.character_builder_utils.get_index_data', return_value={'selectable_characters': []})
    @patch('webapp.blueprints.helpers.character_builder_utils.get_game_session')
    @patch('webapp.blueprints.helpers.character_builder_utils.get_current_game')
    def test_register_new_character_includes_sheet(
        self,
        mock_current_game,
        mock_game_session,
        mock_index_data,
        mock_builder_only,
        mock_exists,
        mock_open_file,
        mock_json_dump,
    ):
        mock_current_game.return_value.defer_player_spawn = True
        mock_session = MagicMock()
        mock_session.root_path = '/tmp/death_house'
        mock_game_session.return_value = mock_session

        pc = {
            'name': 'Test Hero',
            'entity_uid': 'test_hero',
            'description': 'A test hero',
            'profile_image': 'characters/test_hero.png',
        }
        self.mod._register_new_character_in_campaign(pc, 'Test_Hero')

        written = mock_json_dump.call_args[0][0]
        entry = written['selectable_characters'][0]
        self.assertEqual(entry['name'], 'test_hero')
        self.assertEqual(entry['sheet'], 'characters/Test_Hero.yml')
        self.assertEqual(entry['overrides'], {'entity_uid': 'test_hero'})

    def test_race_tool_choice_validation_and_apply(self):
        race_def = {
            'tool_proficiencies_choice': 1,
            'tool_proficiencies': ['smiths_tools', 'brewers_supplies', 'masons_tools'],
        }
        ok, err = self.mod.validate_race_tool_selections(race_def, {}, [])
        self.assertFalse(ok)
        self.assertIn('Choose 1', err)

        ok, err = self.mod.validate_race_tool_selections(
            race_def, {}, ['smiths_tools'],
        )
        self.assertTrue(ok)

        pc = {'tool_proficiencies': ['thieves_tools', 'brewers_supplies']}
        self.mod.apply_race_tool_proficiencies(pc, race_def, {}, ['smiths_tools'])
        self.assertEqual(pc['tool_proficiencies'], ['thieves_tools', 'smiths_tools'])

    # ------------------------------------------------------------------ #
    # Weapon Mastery picks count weapon kinds (SRD 5.2)
    # ------------------------------------------------------------------ #

    FIGHTER_DEF = {
        'class_features': ['weapon_mastery'],
        'weapon_mastery_known': 3,
        'weapon_proficiencies': ['longsword', 'pike', 'longbow', 'dagger'],
    }
    WEAPONS = {
        'longsword': {'name': 'Longsword', 'weapon_mastery': ['sap']},
        'pike': {'name': 'Pike', 'weapon_mastery': ['push']},
        'longbow': {'name': 'Longbow', 'weapon_mastery': ['slow']},
        'dagger': {'name': 'Dagger', 'weapon_mastery': ['nick']},
        'warhammer': {'name': 'Warhammer', 'weapon_mastery': ['push']},
        'club': {'name': 'Club', 'weapon_mastery': ['slow']},
    }

    def test_mastery_weapon_options_lists_proficient_mastery_weapons(self):
        options = self.mod.mastery_weapon_options(self.FIGHTER_DEF, self.WEAPONS)
        kinds = [o['kind'] for o in options]
        self.assertEqual(sorted(kinds), ['dagger', 'longbow', 'longsword', 'pike'])
        longsword = next(o for o in options if o['kind'] == 'longsword')
        self.assertEqual(longsword['masteries'], ['sap'])

    def test_validate_weapon_mastery_selections_accepts_weapon_kinds(self):
        ok, _ = self.mod.validate_weapon_mastery_selections(
            'fighter', 1, self.FIGHTER_DEF, ['longsword', 'pike', 'longbow'],
            weapon_options=['longsword', 'pike', 'longbow', 'dagger'],
        )
        self.assertTrue(ok)

        # Too few picks, or a mastery *name* where a weapon kind is expected
        # in a new-form campaign (names only allowed as legacy fallback).
        ok, err = self.mod.validate_weapon_mastery_selections(
            'fighter', 1, self.FIGHTER_DEF, ['longsword', 'pike'],
            weapon_options=['longsword', 'pike', 'longbow', 'dagger'],
        )
        self.assertFalse(ok)
        self.assertIn('Choose 3', err)

    def test_apply_class_and_feat_choices_stores_weapon_kinds(self):
        classes_def = {'fighter': dict(self.FIGHTER_DEF, progression={
            'level_1': {'class_features': ['weapon_mastery']},
        })}
        pc = {}
        self.mod._apply_class_and_feat_choices(
            pc, 'fighter', 1, classes_def, [], [], [], [],
            selected_weapon_masteries=['longsword', 'pike', 'longbow'],
            weapon_mastery_choice_options=['longsword', 'pike', 'longbow', 'dagger'],
        )
        self.assertEqual(pc['known_weapon_masteries'], ['longsword', 'pike', 'longbow'])


if __name__ == '__main__':
    unittest.main()
