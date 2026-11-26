"""Regression tests for selectable-character materialization.

A selectable entry is keyed by display name (e.g. 'crysania'), while a PC
loaded straight from its sheet keeps the sheet stem as ``entity_uid``
('high_elf_mage'). Re-picking the character (typically after a server restart
clears in-memory controllers) must ADOPT the existing entity instead of
spawning a duplicate token and orphaning the one the player uses -- an owned
entity uid is what gates the action bar (and the Instruct button).
"""

import os
import sys
from types import SimpleNamespace

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.blueprints import auth as auth_module  # noqa: E402
from webapp.blueprints.helpers import pvp as pvp_module  # noqa: E402

CRYSANIA_SELECTABLE = {
    'name': 'crysania',
    'sheet': 'characters/high_elf_mage.yml',
}


class FakeGame:
    def __init__(self, entities=None):
        self.entities = {str(uid): ent for uid, ent in (entities or {}).items()}
        self.maps = {}
        self.deferred_players = {}

    def get_entity_by_uid(self, uid):
        return self.entities.get(str(uid))


def _patch_pvp_runtime(monkeypatch, game, index_data):
    session = SimpleNamespace(
        game_properties={},
        register_entity=lambda entity: None,
    )
    monkeypatch.setattr(pvp_module, 'get_current_game', lambda: game)
    monkeypatch.setattr(pvp_module, 'get_game_session', lambda: session)
    monkeypatch.setattr(pvp_module, 'get_index_data', lambda: index_data)


def test_reselect_adopts_existing_sheet_stem_entity(monkeypatch):
    """'crysania' re-pick adopts the live 'high_elf_mage' token, no duplicate."""
    played = SimpleNamespace(entity_uid='high_elf_mage', name='Crysania')
    game = FakeGame({'high_elf_mage': played})
    _patch_pvp_runtime(
        monkeypatch,
        game,
        {'selectable_characters': [CRYSANIA_SELECTABLE]},
    )

    entity = pvp_module.ensure_character_entity_loaded('crysania')

    assert entity is played
    assert game.deferred_players == {}  # no duplicate spawn was queued


def test_reselect_still_falls_back_when_sheet_entity_missing(monkeypatch):
    """Without an existing sheet-stem entity, materialize continues normally."""
    game = FakeGame({})
    _patch_pvp_runtime(
        monkeypatch,
        game,
        {'selectable_characters': [CRYSANIA_SELECTABLE]},
    )

    # No maps with player_spawn_points exist in the fake, so materialization
    # runs the normal spawn path and reports no slot instead of adopting.
    assert pvp_module.ensure_character_entity_loaded('crysania') is None


def test_npc_sheet_entity_is_not_adopted(monkeypatch):
    """Generic NPC/sidekick sheets must not hijack unrelated placed NPCs."""
    placed = SimpleNamespace(entity_uid='sidekick', name='Generic Sidekick')
    game = FakeGame({'sidekick': placed})
    _patch_pvp_runtime(
        monkeypatch,
        game,
        {'selectable_characters': [
            {'name': 'Betty', 'sheet': 'npcs/sidekick.yml', 'kind': 'npc'},
        ]},
    )

    entity = pvp_module.ensure_character_entity_loaded('Betty')

    assert entity is not placed


# --- _materialize_login_pick claims the adopted entity's real uid ----------

def _patch_auth_login_pick(monkeypatch, entity, owners):
    controllers = []

    def ensure_controller_entry(entity_uid):
        entity_uid = str(entity_uid)
        for controller in controllers:
            if str(controller.get('entity_uid')) == entity_uid:
                return controller
        controller = {'entity_uid': entity_uid, 'controllers': []}
        controllers.append(controller)
        return controller

    monkeypatch.setattr(auth_module, 'get_game_session', lambda: SimpleNamespace())
    monkeypatch.setattr(auth_module, 'taken_selectable_uids', lambda session=None: set())
    monkeypatch.setattr(
        auth_module, 'selectable_character_entry',
        lambda name: dict(CRYSANIA_SELECTABLE),
    )
    monkeypatch.setattr(auth_module, 'pvp_team_config', lambda: None)
    monkeypatch.setattr(
        auth_module, 'ensure_character_entity_loaded', lambda name: entity,
    )
    monkeypatch.setattr(auth_module, 'entity_owners', lambda ent: list(owners))
    monkeypatch.setattr(auth_module, 'ensure_controller_entry', ensure_controller_entry)
    return controllers


def _make_logger():
    class _Logger:
        def exception(self, *args, **kwargs):
            pass

    return _Logger()


def test_login_pick_claims_adopted_entity_uid(monkeypatch):
    entity = SimpleNamespace(entity_uid='high_elf_mage')
    controllers = _patch_auth_login_pick(monkeypatch, entity, owners=[])

    error = auth_module._materialize_login_pick(
        'crysania', 'crysania', '', _make_logger(),
    )

    assert error is None
    by_uid = {c['entity_uid']: c['controllers'] for c in controllers}
    assert by_uid['crysania'] == ['crysania']
    assert by_uid['high_elf_mage'] == ['crysania']


def test_login_pick_refuses_entity_owned_by_someone_else(monkeypatch):
    entity = SimpleNamespace(entity_uid='high_elf_mage')
    controllers = _patch_auth_login_pick(monkeypatch, entity, owners=['other'])

    error = auth_module._materialize_login_pick(
        'crysania', 'crysania', '', _make_logger(),
    )

    assert error == 'Character is already taken'
    by_uid = {c['entity_uid']: c['controllers'] for c in controllers}
    assert 'high_elf_mage' not in by_uid or not by_uid['high_elf_mage']
