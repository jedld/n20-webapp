"""Player and campaign notebook (notes, journals, files, folders).

Not tied to a PlayerCharacter or NPC. Character journals stay on the
entity; map pins stay on ``/dm/notes``.
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request, send_file, session

from natural20.notebook_db import (
    CAMPAIGN_OWNER,
    NotebookDB,
    NotebookError,
)
from .helpers.auth_utils import logged_in, user_role
from .helpers.runtime_state import get_current_game, get_logins, get_socketio

notebook_bp = Blueprint('notebook', __name__)


def _is_dm() -> bool:
    return 'dm' in (user_role() or [])


def _require_login():
    if not logged_in() or not session.get('username'):
        return jsonify(error='Unauthorized'), 401
    return None


def _username() -> str:
    return str(session.get('username') or '').strip().lower()


def _notebook_db() -> NotebookDB:
    game = get_current_game()
    db = getattr(game, 'notebook_db', None)
    if db is None:
        raise RuntimeError('notebook database is not initialized')
    return db


def _resolve_scope(payload=None):
    data = payload if isinstance(payload, dict) else {}
    scope = (
        data.get('scope')
        or request.args.get('scope')
        or ('campaign' if _is_dm() else 'player')
    )
    is_dm = _is_dm()
    db = _notebook_db()
    if str(scope).strip().lower() == 'campaign':
        return db.normalize_scope_owner('campaign', CAMPAIGN_OWNER, is_dm=is_dm)
    owner = _username()
    requested_owner = str(data.get('owner') or request.args.get('owner') or '').strip().lower()
    if requested_owner and requested_owner != owner:
        if not is_dm:
            raise NotebookError('cannot access another player notebook')
        owner = requested_owner
    return db.normalize_scope_owner('player', owner, is_dm=True)


def _can_access_item(item: dict, *, write: bool = False) -> bool:
    if not item:
        return False
    user = _username()
    is_dm = _is_dm()
    if item.get('scope') == 'campaign':
        if write:
            return is_dm
        return is_dm
    if item.get('owner') == user:
        return True
    return is_dm and not write


def _emit(payload: dict, *, usernames=None):
    socketio = get_socketio()
    if socketio is None:
        return
    game = get_current_game()
    sid_map = getattr(game, 'username_to_sid', {}) or {}
    targets = None
    if usernames is not None:
        targets = {str(name).strip().lower() for name in usernames if name}
    try:
        if targets is None:
            socketio.emit('message', payload)
            return
        for name, sids in sid_map.items():
            if str(name).strip().lower() not in targets:
                continue
            for sid in sids or []:
                socketio.emit('message', payload, to=sid)
    except Exception:
        pass


def _error(exc, status=400):
    return jsonify(success=False, error=str(exc)), status


def _resolve_map(map_name):
    current_game = get_current_game()
    requested = str(map_name or '').strip()
    maps = getattr(current_game, 'maps', None) or {}
    session_maps = getattr(getattr(current_game, 'game_session', None), 'maps', None) or {}
    known = set(maps) | set(session_maps)
    if requested:
        if known and requested not in known:
            raise NotebookError(f'Unknown map: {requested}')
        return requested
    username = session.get('username')
    getter = getattr(current_game, 'get_map_for_user', None)
    if callable(getter) and username:
        battle_map = getter(username)
        if battle_map is not None:
            return battle_map.name
    if maps:
        return next(iter(maps))
    raise NotebookError('map_name is required')


def _emit_map_links_changed(map_name, username):
    _emit(
        {'type': 'notebook_map_links_changed', 'map': map_name},
        usernames=[username],
    )


@notebook_bp.route('/notebook', methods=['GET'])
def notebook_tree():
    denied = _require_login()
    if denied:
        return denied
    try:
        scope, owner = _resolve_scope()
        tree = _notebook_db().tree(
            scope,
            owner,
            sort=request.args.get('sort') or 'order',
            order=request.args.get('order') or 'asc',
            query=request.args.get('q') or request.args.get('query'),
        )
        return jsonify(success=True, **tree, is_dm=_is_dm(), username=_username())
    except NotebookError as exc:
        return _error(exc, 403 if 'DM-only' in str(exc) or 'cannot access' in str(exc) else 400)


@notebook_bp.route('/notebook/search', methods=['GET'])
def notebook_search():
    denied = _require_login()
    if denied:
        return denied
    try:
        scope, owner = _resolve_scope()
        items = _notebook_db().search(
            scope,
            owner,
            request.args.get('q') or request.args.get('query') or '',
            limit=int(request.args.get('limit') or 50),
            sort=request.args.get('sort') or 'updated',
            order=request.args.get('order') or 'desc',
        )
        return jsonify(success=True, scope=scope, owner=owner, items=items)
    except (NotebookError, ValueError) as exc:
        return _error(exc)


@notebook_bp.route('/notebook/folders', methods=['POST'])
def notebook_create_folder():
    denied = _require_login()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    try:
        scope, owner = _resolve_scope(payload)
        if scope == 'campaign' and not _is_dm():
            return jsonify(error='Forbidden'), 403
        folder = _notebook_db().create_folder(
            scope,
            owner,
            payload.get('name'),
            parent_id=payload.get('parent_id') or payload.get('folder_id'),
        )
        _emit({'type': 'notebook_changed', 'scope': scope, 'owner': owner})
        return jsonify(success=True, folder=folder)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/folders/<folder_id>', methods=['PATCH', 'POST'])
def notebook_update_folder(folder_id):
    denied = _require_login()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    db = _notebook_db()
    folder = db.get_folder(folder_id)
    if folder is None:
        return jsonify(success=False, error='folder not found'), 404
    if not _can_access_item({'scope': folder['scope'], 'owner': folder['owner']}, write=True):
        return jsonify(error='Forbidden'), 403
    try:
        saved = db.update_folder(
            folder_id,
            name=payload.get('name'),
            parent_id=payload.get('parent_id'),
            sort_order=payload.get('sort_order'),
            move_parent=('parent_id' in payload),
        )
        _emit({'type': 'notebook_changed', 'scope': saved['scope'], 'owner': saved['owner']})
        return jsonify(success=True, folder=saved)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/folders/<folder_id>', methods=['DELETE'])
def notebook_delete_folder(folder_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    folder = db.get_folder(folder_id)
    if folder is None:
        return jsonify(success=False, error='folder not found'), 404
    if not _can_access_item({'scope': folder['scope'], 'owner': folder['owner']}, write=True):
        return jsonify(error='Forbidden'), 403
    db.delete_folder(folder_id)
    _emit({'type': 'notebook_changed', 'scope': folder['scope'], 'owner': folder['owner']})
    return jsonify(success=True, deleted=True, id=folder_id)


@notebook_bp.route('/notebook/items', methods=['POST'])
def notebook_create_item():
    denied = _require_login()
    if denied:
        return denied
    if request.files:
        return _upload_file()
    payload = request.get_json(silent=True) or {}
    try:
        scope, owner = _resolve_scope(payload)
        if scope == 'campaign' and not _is_dm():
            return jsonify(error='Forbidden'), 403
        item = _notebook_db().create_note(
            scope,
            owner,
            payload.get('title') or 'Untitled note',
            content=payload.get('content') or '',
            folder_id=payload.get('folder_id'),
            created_by=_username(),
        )
        _emit({'type': 'notebook_changed', 'scope': scope, 'owner': owner})
        return jsonify(success=True, item=item)
    except NotebookError as exc:
        return _error(exc)


def _upload_file():
    payload = request.form.to_dict() if request.form else {}
    upload = request.files.get('file') or request.files.get('document') or request.files.get('image')
    if upload is None or not upload.filename:
        return jsonify(success=False, error='file is required'), 400
    try:
        scope, owner = _resolve_scope(payload)
        if scope == 'campaign' and not _is_dm():
            return jsonify(error='Forbidden'), 403
        data = upload.read()
        item = _notebook_db().create_file(
            scope,
            owner,
            upload.filename,
            data,
            folder_id=payload.get('folder_id') or request.form.get('folder_id'),
            created_by=_username(),
            mime_type=upload.mimetype,
            title=payload.get('title') or request.form.get('title'),
        )
        _emit({'type': 'notebook_changed', 'scope': scope, 'owner': owner})
        return jsonify(success=True, item=item)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/items/<item_id>', methods=['GET'])
def notebook_get_item(item_id):
    denied = _require_login()
    if denied:
        return denied
    item = _notebook_db().get_item(item_id, include_content=True)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if not _can_access_item(item, write=False):
        return jsonify(error='Forbidden'), 403
    return jsonify(success=True, item=item)


@notebook_bp.route('/notebook/items/<item_id>', methods=['PATCH', 'POST'])
def notebook_update_item(item_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    item = db.get_item(item_id, include_content=False)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if not _can_access_item(item, write=True):
        return jsonify(error='Forbidden'), 403
    payload = request.get_json(silent=True) or {}
    try:
        saved = db.update_item(
            item_id,
            title=payload.get('title'),
            content=payload.get('content'),
            folder_id=payload.get('folder_id'),
            sort_order=payload.get('sort_order'),
            move_folder=('folder_id' in payload),
        )
        _emit({'type': 'notebook_changed', 'scope': saved['scope'], 'owner': saved['owner']})
        return jsonify(success=True, item=saved)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/items/<item_id>', methods=['DELETE'])
def notebook_delete_item(item_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    item = db.get_item(item_id, include_content=False)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if not _can_access_item(item, write=True):
        return jsonify(error='Forbidden'), 403
    db.delete_item(item_id)
    _emit({'type': 'notebook_changed', 'scope': item['scope'], 'owner': item['owner']})
    return jsonify(success=True, deleted=True, id=item_id)


@notebook_bp.route('/notebook/items/<item_id>/file', methods=['GET'])
def notebook_item_file(item_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    item = db.get_item(item_id, include_content=False)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if item.get('kind') != 'file':
        return jsonify(success=False, error='item is not a file'), 400
    if not _can_access_item(item, write=False):
        share_id = request.args.get('share')
        if share_id:
            try:
                view = db.shared_view(share_id, _username())
            except NotebookError:
                return jsonify(error='Forbidden'), 403
            if view['item']['id'] != item['id']:
                return jsonify(error='Forbidden'), 403
        else:
            return jsonify(error='Forbidden'), 403
    path = db.file_path(item)
    if not path:
        return jsonify(success=False, error='file missing on disk'), 404
    as_attachment = request.args.get('download') in ('1', 'true', 'yes')
    if not item.get('is_image') and not str(item.get('mime_type') or '').startswith('text/'):
        as_attachment = True
    return send_file(
        path,
        mimetype=item.get('mime_type') or 'application/octet-stream',
        as_attachment=as_attachment,
        download_name=item.get('filename') or os.path.basename(path),
    )


@notebook_bp.route('/notebook/move', methods=['POST'])
def notebook_move():
    denied = _require_login()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    db = _notebook_db()
    node_type = payload.get('type') or payload.get('kind') or 'item'
    node_id = payload.get('id')
    if node_type == 'folder':
        node = db.get_folder(node_id)
    else:
        node = db.get_item(node_id, include_content=False)
    if node is None:
        return jsonify(success=False, error='not found'), 404
    if not _can_access_item({'scope': node['scope'], 'owner': node['owner']}, write=True):
        return jsonify(error='Forbidden'), 403
    try:
        saved = db.move_node(
            node_type,
            node_id,
            folder_id=payload.get('folder_id', payload.get('parent_id')),
            sort_order=payload.get('sort_order'),
        )
        _emit({'type': 'notebook_changed', 'scope': node['scope'], 'owner': node['owner']})
        return jsonify(success=True, node=saved)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/reorder', methods=['POST'])
def notebook_reorder():
    denied = _require_login()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    try:
        scope, owner = _resolve_scope(payload)
        if scope == 'campaign' and not _is_dm():
            return jsonify(error='Forbidden'), 403
        _notebook_db().reorder(
            scope,
            owner,
            folder_id=payload.get('folder_id'),
            ordered=payload.get('ordered') or payload.get('nodes') or [],
        )
        _emit({'type': 'notebook_changed', 'scope': scope, 'owner': owner})
        return jsonify(success=True)
    except NotebookError as exc:
        return _error(exc)


@notebook_bp.route('/notebook/recipients', methods=['GET'])
def notebook_recipients():
    denied = _require_login()
    if denied:
        return denied
    me = _username()
    game = get_current_game()
    online = {
        str(name).strip().lower()
        for name, sids in (getattr(game, 'username_to_sid', {}) or {}).items()
        if sids
    }
    people = []
    for login in get_logins() or []:
        name = str(login.get('name') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key == me:
            continue
        roles = login.get('role') or []
        people.append({
            'username': name,
            'online': key in online,
            'is_dm': 'dm' in roles,
        })
    return jsonify(success=True, recipients=people)


@notebook_bp.route('/notebook/items/<item_id>/share', methods=['POST'])
def notebook_share_item(item_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    item = db.get_item(item_id, include_content=False)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if not _can_access_item(item, write=True) and item.get('owner') != _username():
        return jsonify(error='Forbidden'), 403
    if item.get('scope') == 'campaign' and not _is_dm():
        return jsonify(error='Forbidden'), 403
    payload = request.get_json(silent=True) or {}
    recipients = list(payload.get('usernames') or payload.get('recipients') or [])
    if payload.get('all'):
        recipients.append('*')
    try:
        shares = db.create_shares(item_id, _username(), recipients)
    except NotebookError as exc:
        return _error(exc)
    targets = []
    for share in shares:
        target = share['to_username']
        event = {
            'type': 'notebook_share',
            'share_id': share['id'],
            'from_username': share['from_username'],
            'title': item.get('title'),
            'kind': item.get('kind'),
            'is_image': bool(item.get('is_image')),
        }
        if target == '*':
            game = get_current_game()
            everyone = list((getattr(game, 'username_to_sid', {}) or {}).keys())
            _emit(event, usernames=[name for name in everyone if str(name).lower() != _username()])
        else:
            targets.append(target)
            _emit(event, usernames=[target])
    return jsonify(success=True, shares=shares)


@notebook_bp.route('/notebook/shared/<share_id>', methods=['GET'])
def notebook_shared_view(share_id):
    denied = _require_login()
    if denied:
        return denied
    try:
        view = _notebook_db().shared_view(share_id, _username())
        return jsonify(success=True, **view)
    except NotebookError as exc:
        status = 404 if 'not found' in str(exc) or 'no longer' in str(exc) else 403
        return _error(exc, status)


@notebook_bp.route('/notebook/map_links', methods=['GET'])
def notebook_map_links_list():
    denied = _require_login()
    if denied:
        return denied
    try:
        map_name = _resolve_map(request.args.get('map_name') or request.args.get('map'))
    except NotebookError as exc:
        return _error(exc)
    links = _notebook_db().list_map_links(_username(), map_name)
    return jsonify(success=True, map=map_name, links=links)


@notebook_bp.route('/notebook/map_links', methods=['POST'])
def notebook_map_links_upsert():
    denied = _require_login()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    db = _notebook_db()
    item = db.get_item(payload.get('item_id'), include_content=False)
    if item is None:
        return jsonify(success=False, error='item not found'), 404
    if not _can_access_item(item, write=False):
        return jsonify(error='Forbidden'), 403
    try:
        map_name = _resolve_map(payload.get('map_name') or payload.get('map'))
        saved = db.upsert_map_link(
            _username(),
            item['id'],
            map_name,
            payload.get('x'),
            payload.get('y'),
        )
    except NotebookError as exc:
        return _error(exc)
    _emit_map_links_changed(map_name, _username())
    return jsonify(success=True, link=saved, map=map_name)


@notebook_bp.route('/notebook/map_links/<link_id>', methods=['DELETE'])
def notebook_map_links_delete(link_id):
    denied = _require_login()
    if denied:
        return denied
    db = _notebook_db()
    try:
        existing = db.get_map_link(link_id)
        if existing is None:
            return jsonify(success=False, error='not found'), 404
        db.delete_map_link(link_id, owner=_username())
    except NotebookError as exc:
        return _error(exc, 403)
    _emit_map_links_changed(existing.get('map'), _username())
    return jsonify(success=True, deleted=True, id=link_id)
