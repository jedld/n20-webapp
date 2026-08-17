"""Assets blueprint — asset serving and map editor routes.

Extracted from webapp/app.py (lines 1343-1690).
Routes: /assets/*, /create_map, /upload_map_background, /delete_map
"""
import json
import os
import re

from flask import Blueprint, request, send_file, send_from_directory, jsonify, session
from PIL import Image, ImageDraw

from .helpers.runtime_state import (
    get_game_session,
    get_current_game,
    get_level,
    get_othermaps,
    get_logger,
)
from .helpers.asset_utils import resolve_asset_category_file, webapp_static_root
from .helpers.auth_utils import user_role

assets_bp = Blueprint('assets', __name__)


def _resolve_map_background_path(game_session, filename):
    """Find a campaign map background under assets/maps/ or assets/."""
    root = os.path.abspath(game_session.root_path)
    candidates = []
    basename, ext = os.path.splitext(filename)
    if ext:
        candidates.append(filename)
    else:
        candidates.append(filename)
    for suffix in ('.png', '.jpg', '.jpeg', '.webp'):
        alt = f'{basename}{suffix}'
        if alt not in candidates:
            candidates.append(alt)

    search_dirs = (
        os.path.join(root, 'assets', 'maps'),
        os.path.join(root, 'assets'),
    )
    for name in candidates:
        for directory in search_dirs:
            full_path = os.path.join(directory, name)
            if os.path.exists(full_path):
                return full_path, directory, name
    return None, None, None


def _resolve_map_preview_path(game_session, filename):
    """Resolve map-picker thumbnails: <map_id>.png -> map background_image."""
    full_path, directory, resolved_name = _resolve_map_background_path(game_session, filename)
    if full_path:
        return full_path, directory, resolved_name

    map_name, _ext = os.path.splitext(filename)
    if not map_name:
        return None, None, None

    maps = getattr(game_session, 'maps', None) or {}
    map_obj = maps.get(map_name)
    if map_obj is None:
        return None, None, None

    background = map_obj.background_image() if hasattr(map_obj, 'background_image') else None
    if not background:
        return None, None, None

    return _resolve_map_background_path(game_session, background)


@assets_bp.route('/assets/maps/<path:filename>', endpoint='serve_map_image')
def serve_map_image(filename):
    game_session = get_game_session()
    full_path, _directory, _resolved_name = _resolve_map_preview_path(game_session, filename)
    if full_path:
        return send_file(full_path)

    return jsonify(error="File not found"), 404


@assets_bp.route('/assets/sounds/<filename>', endpoint='serve_sound_file')
def serve_sound_file(filename):
    game_session = get_game_session()
    secondary_path = os.path.join(game_session.root_path, "assets", "sounds", filename)
    if os.path.exists(secondary_path):
        return send_file(secondary_path)
    else:
        return jsonify(error="File not found"), 404


@assets_bp.route('/assets/objects/<filename>', endpoint='serve_object_image')
def serve_object_image(filename):
    game_session = get_game_session()
    file_path, fallback_dir = resolve_asset_category_file(game_session, 'objects', filename)
    if file_path is not None:
        return send_file(file_path)
    if fallback_dir is not None:
        return send_from_directory(str(fallback_dir), filename if filename.endswith('.png') else f'{filename}.png')
    return jsonify(error='File not found'), 404


@assets_bp.route('/assets/editor/<filename>', endpoint='serve_editor_image')
def serve_editor_image(filename):
    game_session = get_game_session()
    file_path, fallback_dir = resolve_asset_category_file(game_session, 'editor', filename)
    if file_path is not None:
        return send_file(file_path)
    if fallback_dir is not None:
        return send_from_directory(str(fallback_dir), filename if filename.endswith('.png') else f'{filename}.png')
    return jsonify(error='File not found'), 404


@assets_bp.route('/assets/items/<filename>', endpoint='serve_item_image')
def serve_item_image(filename):
    game_session = get_game_session()
    file_path, fallback_dir = resolve_asset_category_file(game_session, 'items', filename)
    if file_path is not None:
        return send_file(file_path)
    if fallback_dir is not None:
        resolved_name = filename if filename.endswith('.png') else f'{filename}.png'
        return send_from_directory(str(fallback_dir), resolved_name)
    return jsonify(error='File not found'), 404


@assets_bp.route('/assets/<path:asset_name>', endpoint='get_asset')
def get_asset(asset_name):
    """Serve asset files from multiple possible locations."""
    game_session = get_game_session()
    level = get_level()
    asset_paths = [
        os.path.join(level, "assets", asset_name),
        os.path.join(game_session.root_path, "assets", asset_name),
        str(webapp_static_root() / 'assets' / asset_name),
    ]

    for file_path in asset_paths:
        if os.path.exists(file_path):
            return send_file(file_path)

    return jsonify(error="File not found"), 404


@assets_bp.route('/create_map', methods=['POST'], endpoint='create_map')
def create_map():
    """Create a new empty map in the current game's maps folder and register it.
    Expects form data: name (map id). Creates:
      - assets/maps/<name>.yml (basic empty map)
      - assets/maps/<name>.png (placeholder image)
    """
    logger = get_logger()
    game_session = get_game_session()
    current_game = get_current_game()
    othermaps = get_othermaps()
    try:
        name = request.form.get('name') or ''
        name = name.strip().lower()
        if not name or not re.match(r'^[a-z0-9_\-]+$', name):
            return jsonify(error='Invalid map name'), 400

        # Dimensions (optional)
        try:
            width = int(request.form.get('width') or 16)
            height = int(request.form.get('height') or 8)
        except ValueError:
            return jsonify(error='Invalid dimensions'), 400
        width = max(2, min(width, 100))
        height = max(2, min(height, 100))

        # YAML lives in <root>/maps, PNG lives in <root>/assets/maps
        maps_yml_dir = os.path.join(game_session.root_path, 'maps')
        maps_png_dir = os.path.join(game_session.root_path, 'assets', 'maps')
        os.makedirs(maps_yml_dir, exist_ok=True)
        os.makedirs(maps_png_dir, exist_ok=True)

        yml_path = os.path.join(maps_yml_dir, f'{name}.yml')
        png_path = os.path.join(maps_png_dir, f'{name}.png')

        if os.path.exists(yml_path) or os.path.exists(png_path):
            return jsonify(error='Map already exists'), 400

        # Generate a small empty map template
        empty_map = {
            'name': name,
            'description': f'Empty map {name}',
            'map': {
                'illumination': 1.0,
                'base': ['.' * width for _ in range(height)],
            },
            'legend': {},
            'player': [],
        }

        # Save YAML
        with open(yml_path, 'w') as f:
            import yaml as _yaml
            _yaml.safe_dump(empty_map, f, sort_keys=False)

        # Create a placeholder PNG thumbnail
        img = Image.new('RGB', (max(160, width * 10), max(100, height * 10)), color=(230, 233, 237))
        d = ImageDraw.Draw(img)
        d.text((10, 10), name, fill=(90, 90, 90))
        img.save(png_path)

        # Optional map set membership (existing set id, or new id to create)
        map_set_id = (request.form.get('map_set') or '').strip().lower()
        new_set_label = (request.form.get('map_set_label') or '').strip()

        # Register into current session maps if needed
        map_id = name
        relative_map_ref = f'maps/{name}'
        target_set = map_set_id or None
        if map_id not in game_session.maps:
            game_session.register_map(map_id, relative_map_ref, map_set=target_set)
            current_game.maps = game_session.maps
        elif target_set:
            try:
                from natural20.map_set import slug_map_set_id
                from webapp.blueprints.helpers.map_sets import assign_map_to_set, create_map_set
                sid = slug_map_set_id(target_set)
                if game_session.map_sets.get(sid) is None:
                    create_map_set(current_game, sid, label=new_set_label or None)
                assign_map_to_set(current_game, map_id, sid)
            except ValueError:
                pass

        # Ensure available for switching/reloading
        othermaps[map_id] = relative_map_ref
        current_game.other_maps[map_id] = relative_map_ref

        # Persist to game.yml so maps load after restart
        try:
            import yaml as _yaml
            from natural20.map_set import persist_map_sets_to_game_yml, slug_map_set_id
            game_yml_path = os.path.join(game_session.root_path, 'game.yml')
            if os.path.exists(game_yml_path):
                with open(game_yml_path, 'r') as f:
                    props = _yaml.safe_load(f) or {}
            else:
                props = {}
            maps_dict = props.get('maps') or {}
            maps_dict[map_id] = f'maps/{map_id}'
            props['maps'] = maps_dict
            game_session.game_properties = props
            if map_set_id:
                sid = slug_map_set_id(map_set_id)
                if sid:
                    if game_session.map_sets.get(sid) is None:
                        game_session.map_sets.ensure_set(
                            sid, label=new_set_label or sid.replace('_', ' ').title()
                        )
                    game_session.map_sets.assign_map(map_id, sid)
                    game_session._link_maps_within_sets()
            persist_map_sets_to_game_yml(game_session)
        except Exception:
            logger.exception('Failed to persist new map to game.yml')

        # Persist to index.json other_maps for auxiliary use
        try:
            index_json_path = os.path.join(game_session.root_path, 'index.json')
            if os.path.exists(index_json_path):
                with open(index_json_path, 'r') as jf:
                    idx = json.load(jf)
            else:
                idx = {}
            other_maps = idx.get('other_maps') or {}
            other_maps[map_id] = f'maps/{map_id}'
            idx['other_maps'] = other_maps
            with open(index_json_path, 'w') as jf:
                json.dump(idx, jf, indent=2)
        except Exception:
            logger.exception('Failed to persist new map to index.json')

        return jsonify(status='ok', name=map_id)
    except Exception as e:
        logger.exception('Failed to create map')
        return jsonify(error=str(e)), 500


@assets_bp.route('/upload_map_background', methods=['POST'], endpoint='upload_map_background')
def upload_map_background():
    """Upload and set a map's background image. Expects form fields:
    - map: map name
    - image: file upload
    Saves to assets/maps/<map>.png and updates maps/<map>.yml background_image.
    """
    logger = get_logger()
    game_session = get_game_session()
    try:
        map_name = request.form.get('map') or ''
        if not map_name or map_name not in game_session.maps:
            return jsonify(error='Unknown map'), 400

        if 'image' not in request.files:
            return jsonify(error='No file provided'), 400
        file = request.files['image']
        if file.filename == '':
            return jsonify(error='Empty filename'), 400

        # Save PNG (force .png extension)
        maps_png_dir = os.path.join(game_session.root_path, 'assets', 'maps')
        os.makedirs(maps_png_dir, exist_ok=True)
        png_path = os.path.join(maps_png_dir, f'{map_name}.png')

        # Convert to PNG if needed using PIL
        try:
            image = Image.open(file.stream).convert('RGBA')
            image.save(png_path, format='PNG')
        except Exception:
            # Fallback: save directly (may already be PNG)
            file.stream.seek(0)
            file.save(png_path)

        # Update in-memory properties
        _map = game_session.maps.get(map_name)
        if _map:
            _map.properties['background_image'] = f'{map_name}.png'

        # Persist to YAML
        maps_ref = game_session.game_properties.get('maps', {})
        rel_ref = maps_ref.get(map_name, f'maps/{map_name}')
        if not rel_ref.endswith('.yml'):
            rel_ref += '.yml'
        yml_path = os.path.join(game_session.root_path, rel_ref)
        try:
            import yaml as _yaml
            if os.path.exists(yml_path):
                with open(yml_path, 'r') as f:
                    content = _yaml.safe_load(f) or {}
            else:
                content = {}
            content['background_image'] = f'{map_name}.png'
            with open(yml_path, 'w') as f:
                _yaml.safe_dump(content, f, sort_keys=False)
        except Exception as e:
            logger.exception('Failed to update YAML with background_image')

        return jsonify(status='ok', name=map_name, background=f'assets/maps/{map_name}.png')
    except Exception as e:
        logger.exception('Failed to upload map background')
        return jsonify(error=str(e)), 500


@assets_bp.route('/delete_map', methods=['POST'], endpoint='delete_map')
def delete_map():
    """Delete an existing map safely.
    Expects form data: name
    Removes maps/<name>.yml and assets/maps/<name>.png if present,
    unregisters from session, updates game.yml maps and index.json other_maps.
    Prevent deleting 'index' or the only remaining map.
    """
    logger = get_logger()
    game_session = get_game_session()
    current_game = get_current_game()
    othermaps = get_othermaps()
    try:
        # Only DMs can delete maps
        if 'dm' not in user_role():
            return jsonify(error='Forbidden'), 403
        map_name = request.form.get('name') or ''
        map_name = map_name.strip()
        if not map_name:
            return jsonify(error='No map specified'), 400

        # Safety checks
        if map_name == 'index':
            return jsonify(error='Cannot delete the default index map'), 400
        if map_name not in game_session.maps:
            return jsonify(error='Unknown map'), 404
        if len(game_session.maps) <= 1:
            return jsonify(error='Cannot delete the only remaining map'), 400

        # File paths
        # Resolve YAML reference via game.yml maps entry if available
        maps_ref = game_session.game_properties.get('maps', {})
        rel_ref = maps_ref.get(map_name, f'maps/{map_name}')
        if not rel_ref.endswith('.yml'):
            rel_ref += '.yml'
        yml_path = os.path.join(game_session.root_path, rel_ref)
        png_path = os.path.join(game_session.root_path, 'assets', 'maps', f'{map_name}.png')

        # If deleting current map for this user, switch to another map first
        try:
            current_for_user = current_game.get_map_for_user(session['username']).name
        except Exception:
            current_for_user = None
        if current_for_user == map_name:
            # pick any other map (prefer 'index' if exists)
            fallback = 'index' if 'index' in game_session.maps and map_name != 'index' else None
            if not fallback:
                # pick first key that's not the one being deleted
                fallback = next((k for k in game_session.maps.keys() if k != map_name), None)
            if fallback:
                current_game.switch_map_for_user(session['username'], fallback)

        # Remove from in-memory maps and current_game references
        if map_name in current_game.other_maps:
            try:
                del current_game.other_maps[map_name]
            except Exception:
                pass
        try:
            othermaps.pop(map_name, None)
        except Exception:
            pass
        if map_name in game_session.maps:
            try:
                del game_session.maps[map_name]
            except Exception:
                pass
        # Keep current_game maps reference aligned
        current_game.maps = game_session.maps

        # Update game.yml: remove from maps
        try:
            import yaml as _yaml
            game_yml_path = os.path.join(game_session.root_path, 'game.yml')
            if os.path.exists(game_yml_path):
                with open(game_yml_path, 'r') as f:
                    props = _yaml.safe_load(f) or {}
            else:
                props = {}
            maps_dict = props.get('maps') or {}
            if map_name in maps_dict:
                maps_dict.pop(map_name, None)
            props['maps'] = maps_dict
            with open(game_yml_path, 'w') as f:
                _yaml.safe_dump(props, f, sort_keys=False)
            game_session.game_properties = props
            try:
                game_session.map_sets.remove_map(map_name)
                from natural20.map_set import persist_map_sets_to_game_yml
                persist_map_sets_to_game_yml(game_session)
            except Exception:
                pass
        except Exception:
            logger.exception('Failed to update game.yml while deleting map')

        # Update index.json other_maps
        try:
            index_json_path = os.path.join(game_session.root_path, 'index.json')
            if os.path.exists(index_json_path):
                with open(index_json_path, 'r') as jf:
                    idx = json.load(jf)
            else:
                idx = {}
            other_maps = idx.get('other_maps') or {}
            other_maps.pop(map_name, None)
            idx['other_maps'] = other_maps
            with open(index_json_path, 'w') as jf:
                json.dump(idx, jf, indent=2)
        except Exception:
            logger.exception('Failed to update index.json while deleting map')

        # Delete files last
        try:
            if os.path.exists(yml_path):
                os.remove(yml_path)
        except Exception:
            logger.exception('Failed to delete map YAML')
        try:
            if os.path.exists(png_path):
                os.remove(png_path)
        except Exception:
            logger.exception('Failed to delete map image')

        return jsonify(status='ok', name=map_name)
    except Exception as e:
        logger.exception('Failed to delete map')
        return jsonify(error=str(e)), 500
