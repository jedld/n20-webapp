"""Campaign map edit mode routes — YAML-backed drag-and-drop authoring."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from natural20.edit_properties import resolve_property_binding, update_property_binding
from natural20.edit_schema import editable_object_types, get_edit_ui_schema, object_type_has_editor, resolve_editor_schema
from natural20.map_annotations import normalize_annotation, normalize_annotation_id, validate_annotation_id
from natural20.map_editor import (
    apply_terrain_placement_to_live_map,
    apply_terrain_removal_to_live_map,
    build_edit_overlay,
    enrich_edit_overlay_with_runtime_uids,
    list_map_annotation_documents,
    move_map_annotation,
    move_map_item,
    place_map_layer_item,
    place_map_terrain,
    remove_map_annotation,
    remove_map_item,
    save_map_annotation,
)
from webapp.blueprints.helpers.auth_utils import user_role
from webapp.blueprints.helpers.map_graph import build_campaign_map_graph
from webapp.blueprints.helpers.runtime_state import (
    get_current_game,
    get_edit_mode,
    get_socketio,
)


def _resolve_edit_map_name(current_game, battle_map, data) -> str:
    """Pick the YAML map to edit; honors stack overlay map_name from the client."""
    requested = (data or {}).get("map_name")
    if not requested:
        return battle_map.name
    requested = str(requested)
    game_session = current_game.game_session
    stack = getattr(battle_map, "map_stack", None)
    if stack is not None and requested in stack.maps_in_stack():
        return requested
    if requested in game_session.maps:
        return requested
    return battle_map.name


def _edit_map_object(current_game, map_name):
    game_session = current_game.game_session
    if map_name not in game_session.maps:
        raise KeyError(f"Unknown map: {map_name}")
    return game_session.maps[map_name]

edit_bp = Blueprint("edit", __name__)


def _require_edit_dm():
    if not get_edit_mode():
        return jsonify({"success": False, "error": "Edit mode is not enabled"}), 403
    if "dm" not in user_role():
        return jsonify({"success": False, "error": "DM access required"}), 403
    return None


def _binding_from_payload(data: dict) -> dict:
    return {
        "item_id": str(data["item_id"]),
        "kind": str(data["kind"]),
        "source": data.get("source"),
        "token": data.get("token"),
        "index": data.get("index"),
        "layer": data.get("layer"),
    }


@edit_bp.route("/edit/overlay", methods=["GET"])
def edit_overlay():
    denied = _require_edit_dm()
    if denied:
        return denied

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    overlay = build_edit_overlay(battle_map.properties)
    overlay = enrich_edit_overlay_with_runtime_uids(battle_map, overlay)
    game_session = current_game.game_session
    editable_types = editable_object_types(game_session)
    for collection in (overlay.get("items") or [], overlay.get("terrain") or []):
        for item in collection:
            object_type = item.get("object_type")
            item["editable"] = bool(object_type and str(object_type) in editable_types)
    return jsonify(
        {
            "success": True,
            "map": battle_map.name,
            "overlay": overlay,
            "map_stack": _map_stack_payload(battle_map),
        }
    )


def _map_stack_payload(battle_map):
    stack = getattr(battle_map, 'map_stack', None)
    if stack is None:
        return None
    return {
        'id': stack.id,
        'floors': [
            {
                'map': f.map_name,
                'anchor': list(f.anchor),
                'elevation_ft': f.elevation_ft,
                'ceiling_height_ft': f.ceiling_height_ft,
                'role': f.role,
            }
            for f in stack.floors
        ],
    }


@edit_bp.route("/edit/object/schema", methods=["GET"])
def edit_object_schema():
    denied = _require_edit_dm()
    if denied:
        return denied

    object_type = request.args.get("type")
    if not object_type:
        return jsonify({"success": False, "error": "type is required"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    schema = get_edit_ui_schema(current_game.game_session, object_type)
    if not schema:
        return jsonify({"success": False, "error": f"No editor schema for {object_type}"}), 404

    values = request.args.get("values")
    parsed_values = None
    if values:
        import json

        try:
            parsed_values = json.loads(values)
        except json.JSONDecodeError:
            parsed_values = None

    resolved = resolve_editor_schema(
        current_game.game_session,
        object_type,
        current_map=battle_map.name,
        values=parsed_values,
    )
    return jsonify({"success": True, "object_type": object_type, "schema": resolved})


@edit_bp.route("/edit/object/properties", methods=["GET"])
def edit_object_properties_get():
    denied = _require_edit_dm()
    if denied:
        return denied

    required = ("item_id", "kind")
    if any(key not in request.args for key in required):
        return jsonify({"success": False, "error": "item_id and kind are required"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    binding_args = _binding_from_payload(dict(request.args))
    index_raw = binding_args.get("index")
    if index_raw not in (None, ""):
        try:
            binding_args["index"] = int(index_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "index must be a number"}), 400
    else:
        binding_args["index"] = None

    try:
        resolved = resolve_property_binding(
            current_game.game_session,
            battle_map.name,
            **binding_args,
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    schema = resolve_editor_schema(
        current_game.game_session,
        resolved["object_type"],
        current_map=battle_map.name,
        values=resolved.get("values"),
    )
    return jsonify(
        {
            "success": True,
            "map": battle_map.name,
            "object_type": resolved["object_type"],
            "binding": resolved["binding"],
            "values": resolved["values"],
            "schema": schema,
        }
    )


@edit_bp.route("/edit/object/properties", methods=["POST"])
def edit_object_properties_post():
    denied = _require_edit_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    required = ("item_id", "kind", "object_type", "binding", "values")
    if any(key not in data for key in required):
        return jsonify({"success": False, "error": "Missing required parameters"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])

    try:
        update_property_binding(
            current_game.game_session,
            battle_map.name,
            object_type=str(data["object_type"]),
            binding=data["binding"],
            values=data["values"],
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    current_game.reload_map_by_name(battle_map.name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": battle_map.name})


@edit_bp.route("/edit/catalog/maps", methods=["GET"])
def edit_catalog_maps():
    denied = _require_edit_dm()
    if denied:
        return denied

    current_game = get_current_game()
    maps = (current_game.game_session.game_properties or {}).get("maps") or {}
    items = [{"value": key, "label": str(key)} for key in sorted(maps.keys())]
    return jsonify({"success": True, "maps": items})


def _require_dm():
    if "dm" not in user_role():
        return jsonify({"success": False, "error": "DM access required"}), 403
    return None


@edit_bp.route("/edit/annotations", methods=["GET"])
def edit_annotations_list():
    denied = _require_dm()
    if denied:
        return denied

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    annotations = list_map_annotation_documents(current_game.game_session, battle_map.name)
    return jsonify({"success": True, "map": battle_map.name, "annotations": annotations})


@edit_bp.route("/edit/annotations", methods=["POST"])
def edit_annotations_save():
    denied = _require_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    annotation = data.get("annotation")
    if not isinstance(annotation, dict):
        return jsonify({"success": False, "error": "annotation object is required"}), 400

    try:
        normalized = normalize_annotation(annotation)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = battle_map.name

    try:
        saved = save_map_annotation(current_game.game_session, map_name, normalized)
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "annotation": saved})


@edit_bp.route("/edit/annotations/<annotation_id>", methods=["DELETE"])
def edit_annotations_delete(annotation_id):
    denied = _require_dm()
    if denied:
        return denied

    if not validate_annotation_id(normalize_annotation_id(annotation_id)):
        return jsonify({"success": False, "error": "Invalid annotation id"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = battle_map.name

    try:
        remove_map_annotation(current_game.game_session, map_name, annotation_id)
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "id": annotation_id})


@edit_bp.route("/edit/annotations/move", methods=["POST"])
def edit_annotations_move():
    denied = _require_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    annotation_id = normalize_annotation_id(str(data.get("id") or data.get("annotation_id") or ""))
    if not validate_annotation_id(annotation_id):
        return jsonify({"success": False, "error": "Invalid annotation id"}), 400

    try:
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "x and y coordinates are required"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = battle_map.name

    if x < 0 or y < 0 or x >= battle_map.size[0] or y >= battle_map.size[1]:
        return jsonify(
            {
                "success": False,
                "error": f"Coordinates out of bounds ({battle_map.size[0]}x{battle_map.size[1]})",
            }
        ), 400

    try:
        moved = move_map_annotation(current_game.game_session, map_name, annotation_id, x, y)
    except KeyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "annotation": moved})


@edit_bp.route("/edit/move", methods=["POST"])
def edit_move():
    denied = _require_edit_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    required = ("item_id", "kind", "x", "y")
    if any(key not in data for key in required):
        return jsonify({"success": False, "error": "Missing required parameters"}), 400

    try:
        x = int(data["x"])
        y = int(data["y"])
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Coordinates must be numbers"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = _resolve_edit_map_name(current_game, battle_map, data)
    target_map = _edit_map_object(current_game, map_name)

    if x < 0 or y < 0 or x >= target_map.size[0] or y >= target_map.size[1]:
        return jsonify(
            {
                "success": False,
                "error": f"Coordinates out of bounds for {map_name} ({target_map.size[0]}x{target_map.size[1]})",
            }
        ), 400

    try:
        from_x = int(data["from_x"]) if data.get("from_x") is not None else None
        from_y = int(data["from_y"]) if data.get("from_y") is not None else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "from_x/from_y must be numbers"}), 400

    index_raw = data.get("index")
    try:
        index = int(index_raw) if index_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "index must be a number"}), 400

    try:
        move_map_item(
            current_game.game_session,
            map_name,
            item_id=str(data["item_id"]),
            kind=str(data["kind"]),
            x=x,
            y=y,
            token=data.get("token"),
            layer=data.get("layer"),
            source=data.get("source"),
            index=index,
            from_x=from_x,
            from_y=from_y,
        )
    except (KeyError, ValueError, IndexError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify(
        {
            "success": True,
            "map": map_name,
            "item_id": data["item_id"],
            "x": x,
            "y": y,
        }
    )


@edit_bp.route("/edit/remove", methods=["POST"])
def edit_remove():
    denied = _require_edit_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    required = ("item_id", "kind")
    if any(key not in data for key in required):
        return jsonify({"success": False, "error": "Missing required parameters"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = battle_map.name

    index_raw = data.get("index")
    try:
        index = int(index_raw) if index_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "index must be a number"}), 400

    x_raw = data.get("x")
    y_raw = data.get("y")
    try:
        x = int(x_raw) if x_raw is not None else None
        y = int(y_raw) if y_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "x/y must be numbers"}), 400

    try:
        removed = remove_map_item(
            current_game.game_session,
            map_name,
            item_id=str(data["item_id"]),
            kind=str(data["kind"]),
            token=data.get("token"),
            layer=data.get("layer"),
            source=data.get("source"),
            index=index,
            x=x,
            y=y,
        )
    except (KeyError, ValueError, IndexError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    if removed.get("x") is not None and removed.get("y") is not None:
        apply_terrain_removal_to_live_map(battle_map, current_game.game_session, map_name, removed)
    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "removed": removed})


@edit_bp.route("/edit/layer/place", methods=["POST"])
def edit_layer_place():
    denied = _require_edit_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    object_type = data.get("object_type")
    token = data.get("token")
    if not object_type and not token:
        return jsonify({"success": False, "error": "object_type or token is required"}), 400

    try:
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "x and y coordinates are required"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = _resolve_edit_map_name(current_game, battle_map, data)
    target_map = _edit_map_object(current_game, map_name)

    if x < 0 or y < 0 or x >= target_map.size[0] or y >= target_map.size[1]:
        return jsonify(
            {
                "success": False,
                "error": f"Coordinates out of bounds for {map_name} ({target_map.size[0]}x{target_map.size[1]})",
            }
        ), 400

    extra_fields = data.get("fields") if isinstance(data.get("fields"), dict) else None
    try:
        placed = place_map_layer_item(
            current_game.game_session,
            map_name,
            object_type=str(object_type) if object_type else None,
            token=str(token) if token else None,
            layer=data.get("layer"),
            x=x,
            y=y,
            placement_id=data.get("id") or data.get("placement_id"),
            extra_fields=extra_fields,
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    if placed.get("placement_kind") != "object":
        apply_terrain_placement_to_live_map(target_map, current_game.game_session, map_name, placed)
    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "placement": placed})


@edit_bp.route("/edit/terrain/place", methods=["POST"])
def edit_terrain_place():
    denied = _require_edit_dm()
    if denied:
        return denied

    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 400

    data = request.get_json() or {}
    object_type = data.get("object_type")
    if not object_type:
        return jsonify({"success": False, "error": "object_type is required"}), 400

    try:
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "x and y coordinates are required"}), 400

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    map_name = _resolve_edit_map_name(current_game, battle_map, data)
    target_map = _edit_map_object(current_game, map_name)

    if x < 0 or y < 0 or x >= target_map.size[0] or y >= target_map.size[1]:
        return jsonify(
            {
                "success": False,
                "error": f"Coordinates out of bounds for {map_name} ({target_map.size[0]}x{target_map.size[1]})",
            }
        ), 400

    try:
        placed = place_map_terrain(
            current_game.game_session,
            map_name,
            object_type=str(object_type),
            x=x,
            y=y,
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"Failed to write map YAML: {exc}"}), 500

    if placed.get("placement_kind") != "object":
        apply_terrain_placement_to_live_map(target_map, current_game.game_session, map_name, placed)
    current_game.reload_map_by_name(map_name)
    get_socketio().emit("message", {"type": "refresh_map"})
    return jsonify({"success": True, "map": map_name, "placement": placed})


@edit_bp.route("/edit/map_graph", methods=["GET"])
def edit_map_graph_view():
    denied = _require_edit_dm()
    if denied:
        return denied

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    graph = build_campaign_map_graph(
        current_game,
        current_map_name=battle_map.name if battle_map is not None else None,
    )
    return render_template(
        "map_graph.html",
        title="Map Connections",
        graph=graph,
        current_map=graph.get("current_map"),
    )


@edit_bp.route("/edit/map_graph/data", methods=["GET"])
def edit_map_graph_data():
    denied = _require_edit_dm()
    if denied:
        return denied

    current_game = get_current_game()
    battle_map = current_game.get_map_for_user(session["username"])
    graph = build_campaign_map_graph(
        current_game,
        current_map_name=battle_map.name if battle_map is not None else None,
    )
    return jsonify({"success": True, **graph})
