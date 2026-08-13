"""Authentication and authorization utilities.

Extracted from ``webapp.app`` to keep auth logic in a single, testable
module that blueprints can import without pulling in the Flask app.
"""

from flask import session

from .runtime_state import get_logins, get_builder_only_mode, get_edit_mode


def _edit_mode_username() -> str:
    for login in get_logins() or []:
        if "dm" in login.get("role", []):
            return str(login["name"]).lower()
    return "dm"


def _migrate_stale_username_state(username: str) -> None:
    """Move per-user view state off a stale ``None`` session key."""
    if not username:
        return
    try:
        from .runtime_state import get_current_game

        game = get_current_game()
        if game is None:
            return
        manager = game.entity_registry.manager
        stale_map = manager.current_map_for_user.pop(None, None)
        if stale_map is not None and username not in manager.current_map_for_user:
            manager.current_map_for_user[username] = stale_map
        stale_pov = manager.pov_entity_for_user.pop(None, None)
        if stale_pov is not None and username not in manager.pov_entity_for_user:
            manager.pov_entity_for_user[username] = stale_pov
        stale_epoch = game._render_epoch_by_user.pop(None, None)
        if stale_epoch is not None and username not in game._render_epoch_by_user:
            game._render_epoch_by_user[username] = stale_epoch
    except Exception:
        pass


def session_username():
    """Return the active session username, assigning edit/builder defaults when needed."""
    if get_builder_only_mode():
        if not session.get("username"):
            session["username"] = "builder"
        return session["username"]
    if get_edit_mode():
        if not session.get("username"):
            session["username"] = _edit_mode_username()
            _migrate_stale_username_state(session["username"])
        return session["username"]
    return session.get("username")


def logged_in():
    """Return True when the current session has a username."""
    if get_builder_only_mode():
        session_username()
        return True
    if get_edit_mode():
        session_username()
        return True
    return session.get('username') is not None


def roles_for_username(username):
    """Return the role list for ``username`` (e.g. ``['dm']``)."""
    if get_builder_only_mode():
        return ['dm']
    if get_edit_mode():
        return ['dm']
    if not username:
        return []
    login_info = next(
        (login for login in get_logins() if login["name"].lower() == username),
        None,
    )
    return login_info["role"] if login_info else []


def user_role():
    """Return the role list for the *current* session user."""
    return roles_for_username(session.get('username'))
