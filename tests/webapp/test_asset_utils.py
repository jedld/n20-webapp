"""Tests for campaign-first item/object asset serving."""

from types import SimpleNamespace

from webapp.blueprints.helpers.asset_utils import resolve_asset_category_file


def test_resolve_item_asset_prefers_campaign_over_bundled(tmp_path, monkeypatch):
    import webapp.blueprints.helpers.asset_utils as asset_utils

    bundled = tmp_path / "static" / "assets" / "items"
    bundled.mkdir(parents=True)
    (bundled / "skin_pouch.png").write_bytes(b"bundled")
    monkeypatch.setattr(
        asset_utils,
        "bundled_asset_path",
        lambda category, filename: (
            bundled / filename
            if category == "items" and (bundled / filename).is_file()
            else None
        ),
    )

    campaign = tmp_path / "demo"
    (campaign / "assets" / "items").mkdir(parents=True)
    (campaign / "assets" / "items" / "skin_pouch.png").write_bytes(b"campaign")
    session = SimpleNamespace(root_path=str(campaign))

    file_path, fallback = resolve_asset_category_file(session, "items", "skin_pouch.png")
    assert fallback is None
    assert file_path is not None
    assert file_path.read_bytes() == b"campaign"


def test_resolve_item_asset_falls_back_to_bundled(tmp_path, monkeypatch):
    import webapp.blueprints.helpers.asset_utils as asset_utils

    bundled = tmp_path / "static" / "assets" / "items"
    bundled.mkdir(parents=True)
    (bundled / "dagger.png").write_bytes(b"bundled")
    monkeypatch.setattr(
        asset_utils,
        "bundled_asset_path",
        lambda category, filename: (
            bundled / filename
            if category == "items" and (bundled / filename).is_file()
            else None
        ),
    )

    campaign = tmp_path / "demo"
    (campaign / "assets" / "items").mkdir(parents=True)
    session = SimpleNamespace(root_path=str(campaign))

    file_path, fallback = resolve_asset_category_file(session, "items", "dagger.png")
    assert fallback is None
    assert file_path is not None
    assert file_path.read_bytes() == b"bundled"
