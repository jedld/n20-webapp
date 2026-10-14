"""Per-session campaign edit mode: DM toggle, isolation, no auto-login."""

from __future__ import annotations

import os
import sys

from flask import session

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "webapp")
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
os.environ.setdefault("TEMPLATE_DIR", template_root)

from webapp.app import app
from webapp.blueprints.helpers.auth_utils import logged_in, session_edit_mode, user_role


def _dm_client(edit_mode=False):
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "dm"
        if edit_mode:
            sess["edit_mode"] = True
    return client


def test_anonymous_cannot_enable_edit_mode():
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.post("/edit/session", json={"enabled": True})
        assert resp.status_code in (302, 403)
        resp = client.get("/edit/overlay")
        assert resp.status_code in (302, 403)


def test_player_cannot_enable_edit_mode():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "gomerin"
            sess["edit_mode"] = True
        resp = client.post("/edit/session", json={"enabled": True})
        assert resp.status_code == 403
        resp = client.get("/edit/overlay")
        assert resp.status_code == 403
        with client.session_transaction() as sess:
            # Player flag must not grant edit APIs even if set in the cookie.
            assert sess.get("username") == "gomerin"


def test_dm_overlay_forbidden_until_session_enabled():
    client = _dm_client(edit_mode=False)
    resp = client.get("/edit/overlay")
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["success"] is False
    assert "edit mode" in body["error"].lower()

    resp = client.post("/edit/session", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.get_json()["edit_mode"] is True

    resp = client.get("/edit/session")
    assert resp.status_code == 200
    assert resp.get_json()["edit_mode"] is True

    resp = client.get("/edit/overlay")
    assert resp.status_code in (200, 500)


def test_dm_can_disable_edit_mode():
    client = _dm_client(edit_mode=True)
    resp = client.get("/edit/overlay")
    assert resp.status_code in (200, 500)

    resp = client.post("/edit/session", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.get_json()["edit_mode"] is False

    resp = client.get("/edit/overlay")
    assert resp.status_code == 403


def test_edit_mode_is_isolated_between_sessions():
    app.config["TESTING"] = True
    dm_client = app.test_client()
    other_dm = app.test_client()
    player_client = app.test_client()
    with dm_client.session_transaction() as sess:
        sess["username"] = "dm"
    with other_dm.session_transaction() as sess:
        sess["username"] = "dm"
    with player_client.session_transaction() as sess:
        sess["username"] = "gomerin"

    enable = dm_client.post("/edit/session", json={"enabled": True})
    assert enable.status_code == 200
    assert enable.get_json()["edit_mode"] is True

    assert other_dm.get("/edit/overlay").status_code == 403
    assert player_client.get("/edit/overlay").status_code == 403
    assert player_client.post("/edit/session", json={"enabled": True}).status_code == 403
    assert dm_client.get("/edit/overlay").status_code in (200, 500)


def test_logged_in_and_roles_ignore_process_edit_mode():
    previous = app.config.get("EDIT_MODE")
    app.config["EDIT_MODE"] = True
    try:
        with app.test_request_context("/"):
            session.clear()
            assert logged_in() is False
            assert user_role() == []
            assert session_edit_mode() is False
    finally:
        app.config["EDIT_MODE"] = previous


def test_edit_boot_flag_seeds_session_on_dm_login():
    previous = app.config.get("EDIT_MODE")
    app.config["EDIT_MODE"] = True
    app.config["TESTING"] = True
    try:
        with app.test_client() as client:
            resp = client.post("/login", data={"username": "dm", "password": "admin"})
            assert resp.status_code == 200
            body = resp.get_json() or {}
            assert body.get("status") == "ok" or "error" not in body
            with client.session_transaction() as sess:
                assert sess.get("username") == "dm"
                assert sess.get("edit_mode") is True
            assert client.get("/edit/session").get_json()["edit_mode"] is True
    finally:
        app.config["EDIT_MODE"] = previous


def test_edit_boot_flag_does_not_seed_player_login():
    previous = app.config.get("EDIT_MODE")
    app.config["EDIT_MODE"] = True
    app.config["TESTING"] = True
    try:
        with app.test_client() as client:
            resp = client.post("/login", data={"username": "gomerin", "password": "gomerin"})
            assert resp.status_code == 200
            with client.session_transaction() as sess:
                assert sess.get("username") == "gomerin"
                assert not sess.get("edit_mode")
            assert client.post("/edit/session", json={"enabled": True}).status_code == 403
    finally:
        app.config["EDIT_MODE"] = previous


def test_edit_move_falls_back_to_live_token_for_runtime_entities():
    from types import SimpleNamespace

    from natural20.event_manager import EventManager
    from natural20.map import Map
    from natural20.player_character import PlayerCharacter
    from natural20.session import Session
    from webapp.blueprints.edit import _move_live_entity

    game_session = Session(root_path="tests/fixtures", event_manager=EventManager())
    battle_map = Map(game_session, "battle_sim")
    pc = PlayerCharacter.load(game_session, "high_elf_fighter.yml")
    battle_map.add(pc, 0, 0)
    bumps = []
    game = SimpleNamespace(
        get_entity_by_uid=lambda uid: pc if uid == pc.entity_uid else None,
        get_current_battle=lambda: None,
        bump_render_epoch=lambda: bumps.append(1),
    )

    moved, error = _move_live_entity(game, battle_map, {"item_id": pc.entity_uid, "kind": "entity"}, 1, 1)
    assert moved and error is None
    assert battle_map.entity_or_object_pos(pc) == [1, 1]
    assert bumps, "cached /update tiles must be invalidated after a live move"

    moved, _ = _move_live_entity(game, battle_map, {"item_id": "nobody", "kind": "entity"}, 2, 2)
    assert not moved


def test_edit_move_persists_player_character_position(tmp_path):
    import shutil
    from types import SimpleNamespace

    from natural20.event_manager import EventManager
    from natural20.map import Map
    from natural20.player_character import PlayerCharacter
    from natural20.session import Session
    from webapp.blueprints.edit import _persist_player_position

    fixtures = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "tests", "fixtures"))
    root = tmp_path / "campaign"
    shutil.copytree(fixtures, root, ignore=shutil.ignore_patterns("tmp", "__pycache__"))

    game_session = Session(root_path=str(root), event_manager=EventManager())
    pc = PlayerCharacter.load(game_session, "high_elf_fighter.yml", override={"entity_uid": "high_elf_fighter"})
    npc = game_session.npc("goblin")
    lookup = {pc.entity_uid: pc, npc.entity_uid: npc}
    game = SimpleNamespace(game_session=game_session, get_entity_by_uid=lookup.get)

    assert _persist_player_position(game, "empty_map", {"item_id": pc.entity_uid}, 2, 3)
    assert not _persist_player_position(game, "empty_map", {"item_id": npc.entity_uid}, 4, 4)
    assert _persist_player_position(game, "empty_map", {"item_id": pc.entity_uid}, 3, 1)

    reloaded = Session(root_path=str(root), event_manager=EventManager())
    battle_map = Map(reloaded, "maps/empty_map")
    placed = [e for e in battle_map.entities if getattr(e, "entity_uid", None) == pc.entity_uid]
    assert placed and battle_map.entity_or_object_pos(placed[0]) == [3, 1]
    entries = [p for p in (battle_map.properties.get("player") or [])
               if (p.get("overrides") or {}).get("entity_uid") == pc.entity_uid]
    assert len(entries) == 1
