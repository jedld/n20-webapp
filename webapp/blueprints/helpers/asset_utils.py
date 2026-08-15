"""Static asset URLs with optional minified bundles and content-hash cache busting."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_STATIC_ROOT = Path(__file__).resolve().parents[2] / 'static'
_MANIFEST_PATH = _STATIC_ROOT / 'manifest.assets.json'


def webapp_static_root() -> Path:
    """Absolute path to ``webapp/static`` (independent of process cwd)."""
    return _STATIC_ROOT


def bundled_asset_path(category: str, filename: str) -> Path | None:
    """Return ``webapp/static/assets/<category>/<filename>`` when the file exists."""
    path = _STATIC_ROOT / 'assets' / category / filename
    return path if path.is_file() else None


def _templates_asset_path(category: str, filename: str) -> Path | None:
    from natural20.yaml_loader import templates_root

    path = templates_root() / 'assets' / category / filename
    return path if path.is_file() else None


def _editor_icon_name_candidates(name: str) -> list[str]:
    """Return alternate editor icon filenames for legacy corner-wall naming."""
    candidates = [name]
    stem = Path(name).stem
    suffix = Path(name).suffix or '.png'
    alias_suffixes = (
        ('_lt', '_tl'),
        ('_rt', '_tr'),
        ('_lb', '_bl'),
        ('_rb', '_br'),
    )
    for old, new in alias_suffixes:
        if stem.endswith(old):
            alt_stem = stem[: -len(old)] + new
            candidates.append(f'{alt_stem}{suffix}')
            candidates.append(f'{alt_stem}_wall{suffix}')
            break
    if not stem.endswith('_wall'):
        candidates.append(f'{stem}_wall{suffix}')
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _resolve_editor_asset(game_session, name: str) -> tuple[Path | None, Path | None]:
    """Editor icons: campaign override, then templates, then bundled static."""
    from natural20.yaml_loader import templates_root

    search_roots: list[Path] = []
    root = getattr(game_session, 'root_path', None)
    if root:
        search_roots.append(Path(root) / 'assets' / 'editor')
    search_roots.append(templates_root() / 'assets' / 'editor')
    search_roots.append(_STATIC_ROOT / 'assets' / 'editor')

    for candidate_name in _editor_icon_name_candidates(name):
        for base in search_roots:
            path = base / candidate_name
            if path.is_file():
                return path, None

    if root:
        return None, Path(root) / 'assets' / 'editor'
    return None, templates_root() / 'assets' / 'editor'


def _resolve_campaign_first_asset(
    game_session,
    category: str,
    name: str,
) -> tuple[Path | None, Path | None]:
    """Campaign / imports / expansion packs, then bundled static, then templates."""
    from natural20.image_gen.item_asset_paths import iter_campaign_asset_roots

    root = getattr(game_session, 'root_path', None)
    if root:
        for asset_root in iter_campaign_asset_roots(root):
            path = asset_root / 'assets' / category / name
            if path.is_file():
                return path, None

    bundled = bundled_asset_path(category, name)
    if bundled is not None:
        return bundled, None

    templates = _templates_asset_path(category, name)
    if templates is not None:
        return templates, None

    if root:
        return None, Path(root) / 'assets' / category
    return None, None


def resolve_asset_category_file(
    game_session,
    category: str,
    filename: str,
    *,
    default_ext: str = '.png',
) -> tuple[Path | None, Path | None]:
    """Resolve a campaign or bundled static asset.

    Returns ``(file_path, fallback_directory)`` — when ``file_path`` is set, serve
    that file; otherwise serve from ``fallback_directory`` (campaign assets).

    Search order:
    - ``editor``: campaign → templates → bundled
    - ``items`` / ``objects``: campaign (then imported campaigns, then expansion
      packs) → bundled → templates. Campaign-only items must live under
      ``<campaign>/assets/items/`` and are never required in bundled static.
    - other categories: bundled → campaign
    """
    name = filename
    if default_ext and not Path(name).suffix:
        name = f'{name}{default_ext}'

    if category == 'editor':
        return _resolve_editor_asset(game_session, name)

    if category in {'items', 'objects'}:
        return _resolve_campaign_first_asset(game_session, category, name)

    bundled = bundled_asset_path(category, name)
    if bundled is not None:
        return bundled, None

    root = getattr(game_session, 'root_path', None)
    if root:
        campaign_dir = Path(root) / 'assets' / category
        if (campaign_dir / name).is_file():
            return campaign_dir / name, None
        return None, campaign_dir

    return None, None


@lru_cache(maxsize=1)
def _load_manifest() -> dict:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        with _MANIFEST_PATH.open(encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def asset_build_version() -> str:
    """Cache-bust token for templates (replaces legacy ``salt``)."""
    manifest = _load_manifest()
    version = manifest.get('buildVersion')
    if version:
        return str(version)
    env_version = os.environ.get('N20_ASSET_VERSION')
    if env_version:
        return env_version
    return 'dev'


def _use_minified_assets() -> bool:
    env = os.environ.get('N20_USE_MINIFIED_ASSETS')
    if env is not None:
        return env.strip().lower() not in ('0', 'false', 'no', 'off', 'disabled')
    flask_env = os.environ.get('FLASK_ENV', '').strip().lower()
    if flask_env == 'production':
        return True
    debug = os.environ.get('FLASK_DEBUG', '').strip().lower()
    if debug in ('0', 'false', 'no', 'off'):
        return True
    return False


def _version_for(relative_path: str) -> str:
    manifest = _load_manifest()
    versions = manifest.get('versions') or {}
    if relative_path in versions:
        return str(versions[relative_path])
    full = _STATIC_ROOT / relative_path
    if full.is_file():
        return str(int(full.stat().st_mtime))
    return asset_build_version()


def _resolve_asset_path(path: str) -> str:
    """Map ``engine.js`` → ``engine.min.js`` when minified builds are enabled."""
    path = path.lstrip('/').replace('\\', '/')
    if not _use_minified_assets():
        return path

    manifest = _load_manifest()
    files = manifest.get('files') or {}
    if path in files:
        min_path = files[path]
        min_full = _STATIC_ROOT / min_path
        src_full = _STATIC_ROOT / path
        if min_full.is_file() and (
            not src_full.is_file()
            or min_full.stat().st_mtime >= src_full.stat().st_mtime
        ):
            return min_path.replace('\\', '/')

    if path.endswith('.js'):
        min_path = path[:-3] + '.min.js'
        min_full = _STATIC_ROOT / min_path
        src_full = _STATIC_ROOT / path
        if min_full.is_file() and (
            not src_full.is_file()
            or min_full.stat().st_mtime >= src_full.stat().st_mtime
        ):
            return min_path
    elif path.endswith('.css'):
        min_path = path[:-4] + '.min.css'
        min_full = _STATIC_ROOT / min_path
        src_full = _STATIC_ROOT / path
        if min_full.is_file() and (
            not src_full.is_file()
            or min_full.stat().st_mtime >= src_full.stat().st_mtime
        ):
            return min_path

    return path


def asset_url(path: str) -> str:
    """Public URL for a static JS/CSS file under ``webapp/static``."""
    resolved = _resolve_asset_path(path)
    version = _version_for(resolved)
    return f'/{resolved}?v={version}'
