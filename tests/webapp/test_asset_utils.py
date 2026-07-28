import os
from pathlib import Path

from webapp.blueprints.helpers import asset_utils


def test_asset_url_prefers_minified_when_enabled(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'engine.js').write_text('console.log("dev");', encoding='utf-8')
    (static / 'engine.min.js').write_text('console.log("min");', encoding='utf-8')
    manifest = {
        'buildVersion': 'abc123',
        'files': {'engine.js': 'engine.min.js'},
        'versions': {'engine.min.js': 'deadbeef01'},
    }
    (static / 'manifest.assets.json').write_text(
        __import__('json').dumps(manifest),
        encoding='utf-8',
    )

    monkeypatch.setattr(asset_utils, '_STATIC_ROOT', static)
    monkeypatch.setattr(asset_utils, '_MANIFEST_PATH', static / 'manifest.assets.json')
    asset_utils._load_manifest.cache_clear()
    monkeypatch.setenv('N20_USE_MINIFIED_ASSETS', '1')

    assert asset_utils.asset_url('engine.js') == '/engine.min.js?v=deadbeef01'


def test_asset_url_uses_source_in_dev_mode(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    static.mkdir()
    src = static / 'utils.js'
    src.write_text('x = 1;', encoding='utf-8')
    (static / 'utils.min.js').write_text('x=1', encoding='utf-8')

    monkeypatch.setattr(asset_utils, '_STATIC_ROOT', static)
    monkeypatch.setattr(asset_utils, '_MANIFEST_PATH', static / 'manifest.assets.json')
    asset_utils._load_manifest.cache_clear()
    monkeypatch.delenv('N20_USE_MINIFIED_ASSETS', raising=False)
    monkeypatch.delenv('FLASK_ENV', raising=False)
    monkeypatch.delenv('FLASK_DEBUG', raising=False)

    url = asset_utils.asset_url('utils.js')
    assert url.startswith('/utils.js?v=')
    assert 'min' not in url.split('?')[0]


def test_resolve_asset_category_file_finds_bundled_items(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    items_dir = static / 'assets' / 'items'
    items_dir.mkdir(parents=True)
    icon = items_dir / 'chain_mail.png'
    icon.write_bytes(b'png')

    monkeypatch.setattr(asset_utils, '_STATIC_ROOT', static)
    session = type('Session', (), {'root_path': str(tmp_path / 'campaign')})()

    file_path, fallback = asset_utils.resolve_asset_category_file(session, 'items', 'chain_mail.png')
    assert file_path == icon
    assert fallback is None


def test_resolve_editor_asset_prefers_campaign_then_templates(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    bundled_dir = static / 'assets' / 'editor'
    bundled_dir.mkdir(parents=True)
    bundled = bundled_dir / 'barrel.png'
    bundled.write_bytes(b'bundled')

    templates = tmp_path / 'templates'
    templates_editor = templates / 'assets' / 'editor'
    templates_editor.mkdir(parents=True)
    template_icon = templates_editor / 'barrel.png'
    template_icon.write_bytes(b'template')

    campaign = tmp_path / 'campaign'
    campaign_editor = campaign / 'assets' / 'editor'
    campaign_editor.mkdir(parents=True)
    campaign_icon = campaign_editor / 'barrel.png'
    campaign_icon.write_bytes(b'campaign')

    monkeypatch.setattr(asset_utils, '_STATIC_ROOT', static)

    import natural20.yaml_loader as yaml_loader

    monkeypatch.setattr(yaml_loader, 'templates_root', lambda: templates)

    session = type('Session', (), {'root_path': str(campaign)})()
    file_path, fallback = asset_utils.resolve_asset_category_file(session, 'editor', 'barrel.png')
    assert file_path == campaign_icon
    assert fallback is None

    session_other = type('Session', (), {'root_path': str(tmp_path / 'other')})()
    file_path, fallback = asset_utils.resolve_asset_category_file(session_other, 'editor', 'barrel.png')
    assert file_path == template_icon
    assert fallback is None

    session_none = type('Session', (), {'root_path': None})()
    file_path, fallback = asset_utils.resolve_asset_category_file(session_none, 'editor', 'barrel.png')
    assert file_path == template_icon
    assert fallback is None

    (templates_editor / 'barrel.png').unlink()
    file_path, fallback = asset_utils.resolve_asset_category_file(session_none, 'editor', 'barrel.png')
    assert file_path == bundled
    assert fallback is None


def test_resolve_editor_asset_uses_corner_wall_name_aliases(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    bundled_dir = static / 'assets' / 'editor'
    bundled_dir.mkdir(parents=True)
    alias_icon = bundled_dir / 'stone_wall_br_wall.png'
    alias_icon.write_bytes(b'br')

    templates = tmp_path / 'templates'
    templates_editor = templates / 'assets' / 'editor'
    templates_editor.mkdir(parents=True)

    monkeypatch.setattr(asset_utils, '_STATIC_ROOT', static)

    import natural20.yaml_loader as yaml_loader

    monkeypatch.setattr(yaml_loader, 'templates_root', lambda: templates)

    session = type('Session', (), {'root_path': str(tmp_path / 'campaign')})()
    file_path, fallback = asset_utils.resolve_asset_category_file(session, 'editor', 'stone_wall_rb.png')
    assert file_path == alias_icon
    assert fallback is None
