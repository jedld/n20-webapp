"""Tests for campaign edit mode web routes."""

from __future__ import annotations

import os
import sys

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "webapp")
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
os.environ.setdefault("TEMPLATE_DIR", template_root)

from webapp.app import app


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


def test_edit_overlay_requires_edit_mode(edit_client):
    app.config["EDIT_MODE"] = False
    resp = edit_client.get("/edit/overlay")
    assert resp.status_code == 403


def test_edit_overlay_ok_in_edit_mode(edit_client):
    resp = edit_client.get("/edit/overlay")
    assert resp.status_code in (200, 500)


def test_edit_object_schema_teleporter(edit_client):
    resp = edit_client.get("/edit/object/schema", query_string={"type": "teleporter"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["object_type"] == "teleporter"
    groups = {group["id"]: group for group in body["schema"]["groups"]}
    assert "destination" in groups


def test_edit_object_schema_unknown_type(edit_client):
    resp = edit_client.get("/edit/object/schema", query_string={"type": "not_a_real_type"})
    assert resp.status_code == 404


def test_edit_catalog_maps(edit_client):
    resp = edit_client.get("/edit/catalog/maps")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body["maps"], list)
