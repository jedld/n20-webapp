"""Tests for the ``--reset`` startup flag (wipe persisted save state at boot).

Covers:
- ``GameManagement._wipe_save_state``: deletes save files, last_save.txt,
  per-save log/read-notes snapshots, the campaign-logs and notebook sqlite
  databases, and the notebook files directory; preserves unrelated files and
  resets in-memory save bookkeeping.
- ``webapp.app._reset_saves_requested``: resolves the flag from the
  ``N20_RESET_SAVES`` env var or ``--reset`` in argv.
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import _reset_saves_requested  # noqa: E402
from webapp.utils import GameManagement  # noqa: E402


def _make_gm(save_dir):
    """GameManagement-like shell without running __init__."""
    gm = GameManagement.__new__(GameManagement)
    gm.logger = MagicMock()
    gm.save_dir = save_dir
    gm.save_states = ['save_0.yml']
    gm.current_save_index = 2
    return gm


class TestWipeSaveState:
    def test_wipes_saves_dbs_and_notebook_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ('save_0.yml', 'save_1.yml.gz', 'save_named.yml',
                         'last_save.txt', 'pc_1.log.json', 'pc_1.read_notes.json',
                         'campaign_logs.sqlite', 'notebook.sqlite'):
                with open(os.path.join(tmpdir, name), 'w', encoding='utf-8') as handle:
                    handle.write('x')
            # Notebook attachment files (directory must be removed wholesale)
            files_dir = os.path.join(tmpdir, 'notebook_files')
            os.makedirs(os.path.join(files_dir, 'sub'))
            with open(os.path.join(files_dir, 'sub', 'attach.txt'), 'w', encoding='utf-8') as handle:
                handle.write('x')
            # Unrelated files must be preserved
            with open(os.path.join(tmpdir, 'keep_me.txt'), 'w', encoding='utf-8') as handle:
                handle.write('x')

            gm = _make_gm(tmpdir)
            removed = gm._wipe_save_state()

            assert 'save_0.yml' in removed
            assert 'save_1.yml.gz' in removed
            assert 'save_named.yml' in removed
            assert 'last_save.txt' in removed
            assert 'pc_1.log.json' in removed
            assert 'pc_1.read_notes.json' in removed
            assert 'campaign_logs.sqlite' in removed
            assert 'notebook.sqlite' in removed
            assert 'notebook_files' in removed
            for name in ('save_0.yml', 'last_save.txt', 'campaign_logs.sqlite',
                         'notebook.sqlite', 'notebook_files'):
                assert not os.path.exists(os.path.join(tmpdir, name))
            assert os.path.exists(os.path.join(tmpdir, 'keep_me.txt'))
            # In-memory save bookkeeping reset so nothing stale autoloads
            assert gm.save_states == []
            assert gm.current_save_index == 0
            gm.logger.info.assert_called()

    def test_wipe_empty_dir_is_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gm = _make_gm(tmpdir)
            removed = gm._wipe_save_state()
            assert removed == []
            assert gm.save_states == []
            assert gm.current_save_index == 0

    def test_wipe_missing_dir_returns_empty(self):
        gm = _make_gm(os.path.join(tempfile.gettempdir(), 'definitely-missing-dir-xyz'))
        assert gm._wipe_save_state() == []

    def test_clear_npc_memory_files_removes_json_only(self):
        with tempfile.TemporaryDirectory() as campaign_root:
            memories_dir = os.path.join(campaign_root, 'npc_memories')
            os.makedirs(memories_dir)
            for name in ('npc_a.json', 'npc_b.json', 'keep.txt'):
                with open(os.path.join(memories_dir, name), 'w', encoding='utf-8') as handle:
                    handle.write('x')

            gm = _make_gm(campaign_root)
            gm.npc_memory_store = type('S', (), {'memories_dir': memories_dir})()
            removed = gm._clear_npc_memory_files()

            assert removed == 2
            assert not os.path.exists(os.path.join(memories_dir, 'npc_a.json'))
            assert os.path.exists(os.path.join(memories_dir, 'keep.txt'))


class TestResetSavesRequested:
    def test_env_flag_true(self, monkeypatch):
        monkeypatch.setenv('N20_RESET_SAVES', '1')
        assert _reset_saves_requested(argv=[]) is True

    def test_env_flag_falsy_values(self, monkeypatch):
        for value in ('0', 'false', 'no', 'off', 'disabled'):
            monkeypatch.setenv('N20_RESET_SAVES', value)
            assert _reset_saves_requested(argv=[]) is False

    def test_env_flag_unset_with_reset_in_argv(self, monkeypatch):
        monkeypatch.delenv('N20_RESET_SAVES', raising=False)
        assert _reset_saves_requested(argv=['prog', '--reset']) is True

    def test_env_flag_unset_without_reset_in_argv(self, monkeypatch):
        monkeypatch.delenv('N20_RESET_SAVES', raising=False)
        assert _reset_saves_requested(argv=['prog', 'campaign_name']) is False

    def test_env_flag_wins_over_argv(self, monkeypatch):
        monkeypatch.setenv('N20_RESET_SAVES', '1')
        assert _reset_saves_requested(argv=[]) is True
        monkeypatch.setenv('N20_RESET_SAVES', '0')
        # Explicit --reset in argv still triggers a reset
        assert _reset_saves_requested(argv=['prog', '--reset']) is True
