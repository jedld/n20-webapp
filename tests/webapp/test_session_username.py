"""Session username helpers for edit/builder modes."""

from flask import Flask, session

from webapp.blueprints.helpers import auth_utils
from webapp.blueprints.helpers import runtime_state


def test_session_username_repairs_falsy_edit_mode_username(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test"

    monkeypatch.setattr(auth_utils, "get_edit_mode", lambda: True)
    monkeypatch.setattr(auth_utils, "get_builder_only_mode", lambda: False)
    monkeypatch.setattr(auth_utils, "get_logins", lambda: [{"name": "DM", "role": ["dm"]}])
    monkeypatch.setattr(runtime_state, "get_current_game", lambda: None)

    with app.test_request_context("/"):
        session["username"] = None
        assert auth_utils.session_username() == "dm"
        assert session["username"] == "dm"
        assert auth_utils.logged_in() is True


def test_session_username_migrates_stale_none_map_view(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test"

    class _Manager:
        def __init__(self):
            self.current_map_for_user = {None: ("2nd_floor", object())}
            self.pov_entity_for_user = {}

    class _Registry:
        def __init__(self):
            self.manager = _Manager()

    class _Game:
        def __init__(self):
            self.entity_registry = _Registry()
            self._render_epoch_by_user = {}

    game = _Game()

    monkeypatch.setattr(auth_utils, "get_edit_mode", lambda: True)
    monkeypatch.setattr(auth_utils, "get_builder_only_mode", lambda: False)
    monkeypatch.setattr(auth_utils, "get_logins", lambda: [{"name": "DM", "role": ["dm"]}])
    monkeypatch.setattr(runtime_state, "get_current_game", lambda: game)

    with app.test_request_context("/"):
        session["username"] = None
        username = auth_utils.session_username()
        assert username == "dm"
        assert None not in game.entity_registry.manager.current_map_for_user
        assert "dm" in game.entity_registry.manager.current_map_for_user
        assert game.entity_registry.manager.current_map_for_user["dm"][0] == "2nd_floor"
