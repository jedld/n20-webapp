"""Tests for campaign path resolution (split-repo / submodule layout)."""

import os
import unittest


class TestCampaignConfigResolution(unittest.TestCase):
    def test_resolve_campaign_slug_via_n20_campaigns_dir(self):
        from webapp.blueprints.helpers.campaign_config import _resolve_campaign_level

        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', '..')
        )
        templates = os.path.join(repo_root, 'templates')
        prev = os.environ.get('N20_CAMPAIGNS_DIR')
        os.environ['N20_CAMPAIGNS_DIR'] = repo_root
        try:
            resolved = _resolve_campaign_level('templates')
            self.assertEqual(resolved, os.path.normpath(templates))
        finally:
            if prev is None:
                os.environ.pop('N20_CAMPAIGNS_DIR', None)
            else:
                os.environ['N20_CAMPAIGNS_DIR'] = prev


if __name__ == '__main__':
    unittest.main()
