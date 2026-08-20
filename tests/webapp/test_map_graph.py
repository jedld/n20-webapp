"""Tests for campaign map graph helper and edit-mode routes."""

from __future__ import annotations

import os
import sys
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "webapp")
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
os.environ.setdefault("TEMPLATE_DIR", template_root)

from natural20.session import Session
from webapp.app import app
from webapp.blueprints.helpers.map_graph import (
    build_campaign_map_graph,
    load_map_graph_layout,
    normalize_map_graph_positions,
    save_map_graph_layout,
)


@pytest.fixture
def edit_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "dm"
            sess["edit_mode"] = True
        yield client


def test_build_campaign_map_graph_includes_teleporter_edges():
    session = Session(root_path="user_levels/wild_sheep_chase")
    current_game = Mock()
    current_game.game_session = session

    graph = build_campaign_map_graph(current_game, current_map_name="town_market")
    assert graph["nodes"]
    assert isinstance(graph.get("positions"), dict)
    node_ids = {node["id"] for node in graph["nodes"]}
    assert "town_market" in node_ids

    edges = graph["edges"]
    assert isinstance(edges, list)
    if edges:
        edge = edges[0]
        assert "from_map" in edge
        assert "from_pos" in edge
        assert "to_map" in edge
        assert "to_pos" in edge


def test_edit_map_graph_view_requires_edit_mode():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "dm"
        resp = client.get("/edit/map_graph")
        assert resp.status_code == 403


def test_edit_map_graph_view_ok_in_edit_mode(edit_client):
    resp = edit_client.get("/edit/map_graph")
    assert resp.status_code == 200
    assert b"Map Connections" in resp.data


def test_edit_map_graph_data_ok_in_edit_mode(edit_client):
    resp = edit_client.get("/edit/map_graph/data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body.get("nodes"), list)
    assert isinstance(body.get("edges"), list)
    assert isinstance(body.get("positions"), dict)


def test_normalize_map_graph_positions_accepts_wrapped_and_bare():
    wrapped = normalize_map_graph_positions({
        "positions": {"tavern": {"x": 10.24, "y": 20}},
    })
    assert wrapped == {"tavern": {"x": 10.2, "y": 20}}
    bare = normalize_map_graph_positions({"road": {"x": 4, "y": 8}})
    assert bare == {"road": {"x": 4, "y": 8}}
    assert normalize_map_graph_positions(None) == {}
    assert normalize_map_graph_positions({"tavern": "nope"}) == {}


def test_save_and_load_map_graph_layout(tmp_path):
    session = SimpleNamespace(root_path=str(tmp_path))
    saved = save_map_graph_layout(session, {"tavern": {"x": 12.4, "y": 80}})
    assert saved == {"tavern": {"x": 12.4, "y": 80}}
    assert (tmp_path / "map_graph.yml").is_file()
    loaded = load_map_graph_layout(session)
    assert loaded == saved


def test_edit_map_graph_layout_requires_positions_object(edit_client):
    resp = edit_client.post("/edit/map_graph/layout", json={"positions": []})
    assert resp.status_code == 400


def test_edit_map_graph_layout_persists(edit_client):
    with patch(
        "webapp.blueprints.helpers.map_graph.save_map_graph_layout",
        return_value={"tavern": {"x": 10, "y": 20}},
    ) as save:
        resp = edit_client.post(
            "/edit/map_graph/layout",
            json={"positions": {"tavern": {"x": 10, "y": 20}}},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["positions"]["tavern"] == {"x": 10, "y": 20}
    save.assert_called_once()
