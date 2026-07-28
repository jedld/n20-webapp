"""Tests for campaign door-highlight UI flag."""

import os
import sys
import unittest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.blueprints.helpers.campaign_config import highlight_doors_enabled  # noqa: E402


class TestHighlightDoorsConfig(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(highlight_doors_enabled({}))

    def test_enabled_when_flag_set(self):
        self.assertTrue(highlight_doors_enabled({'highlight_doors': True}))


if __name__ == '__main__':
    unittest.main()
