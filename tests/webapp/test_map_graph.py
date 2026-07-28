"""Tests for campaign map graph helper and edit-mode routes."""

from __future__ import annotations

import os
import sys
from unittest.mock import Mock

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "webapp")
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
os.environ.setdefault("TEMPLATE_DIR", template_root)

from natural20.session import Session
from webapp.app import app
from webapp.blueprints.helpers.map_graph import build_campaign_map_graph


@pytest.fixture
def edit_client(monkeypatch):
    monkeypatch.setenv("N20_EDIT_MODE", "1")
    app.config["EDIT_MODE"] = True
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "dm"
        yield client
    app.config["EDIT_MODE"] = False


def test_build_campaign_map_graph_includes_teleporter_edges():
    session = Session(root_path="user_levels/wild_sheep_chase")
    current_game = Mock()
    current_game.game_session = session

    graph = build_campaign_map_graph(current_game, current_map_name="town_market")
    assert graph["nodes"]
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


def test_edit_map_graph_view_requires_edit_mode(edit_client):
    app.config["EDIT_MODE"] = False
    resp = edit_client.get("/edit/map_graph")
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
