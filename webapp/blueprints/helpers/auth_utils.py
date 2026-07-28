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


def logged_in():
    """Return True when the current session has a username."""
    if get_builder_only_mode():
        if 'username' not in session:
            session['username'] = 'builder'
        return True
    if get_edit_mode():
        if 'username' not in session:
            session['username'] = _edit_mode_username()
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
