"""Tests for campaign-configurable login UI."""

from __future__ import annotations

import unittest

from webapp.blueprints.helpers.login_ui import (
    build_login_page_context,
    campaign_asset_url,
    login_background_filename,
    resolve_login_ui,
)


class TestLoginUi(unittest.TestCase):
    def test_campaign_asset_url(self):
        self.assertEqual(campaign_asset_url('title.png'), '/assets/title.png')
        self.assertEqual(campaign_asset_url('assets/title.png'), '/assets/title.png')

    def test_login_background_prefers_login_ui_background(self):
        index = {
            'login_background': 'old.jpg',
            'login_ui': {'background': 'title.png'},
        }
        self.assertEqual(login_background_filename(index), 'title.png')

    def test_wild_sheep_login_context(self):
        index = {
            'login_background': 'title.png',
            'login_ui': {
                'show_title_text': False,
                'background_fit': 'contain',
                'panel': {'layout': 'bottom-right', 'width': '300px'},
                'button': {'text': 'Enter the Chase'},
            },
        }
        ctx = build_login_page_context(title='A Wild Sheep Chase', index_data=index)
        self.assertEqual(ctx['background_url'], '/assets/title.png')
        self.assertFalse(ctx['show_title_text'])
        self.assertEqual(ctx['button_text'], 'Enter the Chase')
        self.assertIn('object-fit: contain', ctx['page_css'])
        self.assertIn('right:', ctx['page_css'])

    def test_defaults_when_login_ui_missing(self):
        ui = resolve_login_ui({})
        self.assertTrue(ui['show_title_text'])
        self.assertEqual(ui['panel']['layout'], 'center')


if __name__ == '__main__':
    unittest.main()
