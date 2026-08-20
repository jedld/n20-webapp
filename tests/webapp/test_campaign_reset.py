"""Tests for DM campaign reset helpers."""
import os
import sys
import tempfile

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402
from webapp.utils import GameManagement  # noqa: E402


class _StubSession:
    root_path = template_root
    event_manager = None
    conversation_handlers = {}


class TestCampaignReset:
    def test_clear_save_files_removes_yaml_and_last_save(self):
        manager = GameManagement.__new__(GameManagement)
        with tempfile.TemporaryDirectory() as tmpdir:
            manager.save_dir = tmpdir
            manager.save_states = ['save_0.yml']
            manager.current_save_index = 3
            manager.logger = type('L', (), {'warning': lambda *a, **k: None})()

            for name in ('save_0.yml', 'save_named.yml.gz', 'save_0.log.json', 'last_save.txt', 'campaign_logs.sqlite', 'notebook.sqlite'):
                with open(os.path.join(tmpdir, name), 'w', encoding='utf-8') as handle:
                    handle.write('x')

            removed = manager.clear_save_files()

            assert 'save_0.yml' in removed
            assert 'save_named.yml.gz' in removed
            assert 'last_save.txt' in removed
            assert 'save_0.log.json' in removed
            assert 'campaign_logs.sqlite' not in removed
            assert 'notebook.sqlite' not in removed
            assert manager.save_states == []
            assert manager.current_save_index == 0
            assert os.path.exists(os.path.join(tmpdir, 'campaign_logs.sqlite'))
            assert os.path.exists(os.path.join(tmpdir, 'notebook.sqlite'))

    def test_admin_campaign_reset_requires_dm(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = 'player'
        response = client.post('/admin/campaign/reset', json={})
        assert response.status_code == 403
