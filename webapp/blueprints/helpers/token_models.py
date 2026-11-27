"""Campaign 3D token-model library: uploads, conversions, and assignments.

HeroForge and similar figures are stored per-campaign under
``<campaign>/assets/models/tokens/`` and mapped to entities through the
``tokens:`` section of ``<campaign>/vtt3d.yml`` (see
:func:`natural20.web.vtt3d_catalog.load_vtt3d_tokens`).

Uploads that the Three.js client cannot render directly are converted to GLB
on the way in using :mod:`webapp.model_convert`:

- ``.stl`` (HeroForge "print ready") -> tinted indexed GLB
- ``.unity3d`` asset bundles (legacy HeroForge downloads) -> textured GLB
- ``.zip`` archives -> extracted (model/textures only), converted per member

Mono-colour STL figures can also stay unconverted; the client tints them via
the assignment ``color`` key. Converting just makes the payload smaller.
"""
from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path
from typing import Any

import yaml

from webapp.model_convert import convert_stl_to_glb, convert_unity_to_glb
from natural20.web.vtt3d_catalog import (
    TOKEN_ENTRY_EXTS,
    _norm_token_spec,
    load_vtt3d_tokens,
)

#: directory (relative to the campaign root) holding token model files
TOKENS_SUBDIR = 'assets/models/tokens'

#: extensions accepted by the upload endpoint
UPLOAD_EXTS = frozenset({'.glb', '.gltf', '.obj', '.stl', '.unity3d', '.zip'})

#: extensions extracted out of uploaded zip archives
EXTRACT_EXTS = frozenset({'.glb', '.gltf', '.obj', '.mtl', '.stl', '.unity3d',
                          '.png', '.jpg', '.jpeg'})

#: formats that render in the 3D client without conversion
RENDER_EXTS = frozenset({'.glb', '.gltf', '.obj'})

_SAFE_NAME = re.compile(r'[^A-Za-z0-9._-]+')


def sanitize_model_filename(name: str) -> str:
    """Reduce an uploaded filename to a safe flat basename."""
    name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = _SAFE_NAME.sub('_', name).strip('._-')
    return name[:80]


def parse_hex_color(value: Any) -> tuple[float, float, float, float] | None:
    """``#rgb`` / ``#rrggbb`` -> rgba floats for the converter, else None."""
    text = str(value or '').strip().lstrip('#')
    if len(text) == 3:
        text = ''.join(ch * 2 for ch in text)
    if len(text) != 6 or not re.fullmatch(r'[0-9a-fA-F]{6}', text):
        return None
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)


def tokens_dir(session, create: bool = False) -> Path | None:
    root = getattr(session, 'root_path', None)
    if not root:
        return None
    path = Path(root) / TOKENS_SUBDIR
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _inside(dir_path: Path, candidate: Path) -> bool:
    try:
        return str(candidate.resolve()).startswith(str(dir_path.resolve()))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# vtt3d.yml assignments (preserves every other section of the file)
# ---------------------------------------------------------------------------

def _vtt3d_path(session) -> Path | None:
    root = getattr(session, 'root_path', None)
    if not root:
        return None
    return Path(root) / 'vtt3d.yml'


def read_vtt3d_doc(session) -> dict:
    path = _vtt3d_path(session)
    if path is None or not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_vtt3d_doc(session, doc: dict) -> bool:
    path = _vtt3d_path(session)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
        encoding='utf-8',
    )
    return True


def get_assignments(session) -> dict[str, dict]:
    """Current ``tokens:`` assignments (normalized, campaign file only)."""
    return load_vtt3d_tokens(session)


def set_assignment(session, key: str, spec: dict) -> dict | None:
    """Store one normalized token spec under ``key``; returns it or None."""
    key = str(key or '').strip()
    normalized = _norm_token_spec(spec)
    if not key or not normalized or not normalized.get('src'):
        return None
    doc = read_vtt3d_doc(session)
    tokens = doc.get('tokens')
    if not isinstance(tokens, dict):
        tokens = {}
    tokens[key] = normalized
    doc['tokens'] = tokens
    write_vtt3d_doc(session, doc)
    return normalized


def remove_assignment(session, key: str) -> bool:
    key = str(key or '').strip()
    doc = read_vtt3d_doc(session)
    tokens = doc.get('tokens')
    if not isinstance(tokens, dict) or key not in tokens:
        return False
    del tokens[key]
    if not tokens:
        del doc['tokens']
    write_vtt3d_doc(session, doc)
    return True


# ---------------------------------------------------------------------------
# library listing
# ---------------------------------------------------------------------------

def list_library(session) -> list[dict]:
    """Model files available for token assignment (sorted by name)."""
    directory = tokens_dir(session)
    if directory is None or not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.rglob('*')):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in TOKEN_ENTRY_EXTS and ext not in ('.mtl', '.unity3d'):
            continue
        rel = path.relative_to(directory.parent.parent)  # under <campaign>/assets
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            'name': path.name,
            'src': rel.as_posix(),
            'ext': ext,
            'bytes': stat.st_size,
            'modified': int(stat.st_mtime),
            'renderable': ext in RENDER_EXTS or ext == '.stl',
        })
    return entries


def find_assignment_keys_for(session, src: str) -> list[str]:
    src = str(src or '').strip()
    return [
        key for key, spec in get_assignments(session).items()
        if str(spec.get('src') or '').strip() == src
    ]


# ---------------------------------------------------------------------------
# upload + conversion
# ---------------------------------------------------------------------------

def _convert_file(path: Path, color: tuple | None = None) -> Path | None:
    """Convert one uploaded STL/Unity file to a sibling ``.glb``.

    Returns the GLB path, or None when the file is not convertible.
    Raises ValueError when conversion fails.
    """
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == '.stl':
        glb = convert_stl_to_glb(data, color=color)
    elif ext == '.unity3d' or is_unity_bundle(data):
        glb = convert_unity_to_glb(data)
    else:
        return None
    out = path.with_suffix('.glb')
    out.write_bytes(glb)
    return out


def save_upload(session, file_storage, color: str | None = None) -> dict:
    """Persist one uploaded model (or zip of models) into the token library.

    Auto-converts STL/Unity payloads to GLB. Returns
    ``{'files': [src...], 'models': [renderable src...], 'converted': bool}``
    or ``{'error': ...}``. ``src`` values are ``<campaign>/assets``-relative,
    matching the token spec ``src`` convention.
    """
    directory = tokens_dir(session, create=True)
    if directory is None:
        return {'error': 'Campaign has no asset directory'}
    name = sanitize_model_filename(getattr(file_storage, 'filename', '') or '')
    ext = Path(name).suffix.lower()
    if not name or ext not in UPLOAD_EXTS:
        return {'error': f'Unsupported file type (allowed: {", ".join(sorted(UPLOAD_EXTS))})'}

    started = time.monotonic()
    written: list[Path] = []
    models: list[Path] = []
    converted = False
    tint = parse_hex_color(color)

    if ext == '.zip':
        try:
            extracted = _extract_model_zip(file_storage, directory, name)
        except zipfile.BadZipFile:
            return {'error': 'Upload is not a valid zip archive'}
        if not extracted:
            return {'error': 'Zip contains no model files '
                             f'(allowed: {", ".join(sorted(EXTRACT_EXTS))})'}
        written.extend(extracted)
    else:
        target = directory / name
        file_storage.save(target)
        written.append(target)

    for path in list(written):
        path_ext = path.suffix.lower()
        if path_ext in RENDER_EXTS:
            models.append(path)
        elif path_ext in ('.stl', '.unity3d'):
            try:
                glb = _convert_file(path, color=tint)
            except ValueError as exc:
                # keep the original upload; report and let the DM decide
                return {
                    'error': f'Could not convert {path.name}: {exc}',
                    'files': [_rel_src(p, directory) for p in written],
                }
            if glb is not None:
                if path.suffix.lower() == '.unity3d':
                    try:
                        path.unlink()  # raw bundles are not renderable
                        written.remove(path)
                    except OSError:
                        pass
                written.append(glb)
                models.append(glb)
                converted = True
        elif path_ext == '.mtl':
            pass  # companion for a sibling .obj

    # an OBJ without its MTL still renders; a .mtl without OBJ is not a model
    model_srcs = []
    seen = set()
    for path in models:
        src = _rel_src(path, directory)
        if src not in seen:
            seen.add(src)
            model_srcs.append(src)

    return {
        'files': [_rel_src(p, directory) for p in written],
        'models': model_srcs,
        'converted': converted,
        'seconds': round(time.monotonic() - started, 2),
    }


def _extract_model_zip(file_storage, directory: Path, zip_name: str) -> list[Path]:
    """Flatten model/texture members of a zip into ``directory``."""
    stem = sanitize_model_filename(zip_name)
    for suffix in ('.zip',):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
    written: list[Path] = []
    seen_names: set[str] = set()
    with zipfile.ZipFile(file_storage.stream) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = sanitize_model_filename(info.filename)
            ext = Path(member).suffix.lower()
            if ext not in EXTRACT_EXTS:
                continue
            if member.lower().endswith('.zip') or member in seen_names:
                continue
            # prefix members with the archive name to avoid library collisions
            candidate = f'{stem}_{member}' if stem and not member.lower().startswith(stem.lower()) else member
            target = directory / candidate
            if not _inside(directory, target):
                continue
            with archive.open(info) as src, open(target, 'wb') as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            written.append(target)
            seen_names.add(candidate)
    return written


def _rel_src(path: Path, directory: Path) -> str:
    """Path under ``<campaign>/assets`` (the token spec ``src`` form)."""
    return path.relative_to(directory.parent.parent).as_posix()


def delete_model(session, src: str) -> dict:
    """Delete a library file and any assignments that reference it."""
    directory = tokens_dir(session)
    if directory is None:
        return {'error': 'Campaign has no asset directory'}
    src = str(src or '').strip().lstrip('/')
    if src.startswith('assets/'):
        src = src[len('assets/'):]
    assets_root = directory.parent.parent
    target = assets_root / src
    if not _inside(directory, target) or not target.is_file():
        return {'error': 'Model file not found'}
    removed_keys = find_assignment_keys_for(session, src)
    for key in removed_keys:
        remove_assignment(session, key)
    target.unlink()
    return {'deleted': src, 'unassigned': removed_keys}
