"""D&D Beyond character import helpers for the web character builder."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - optional for offline tests
    requests = None  # type: ignore

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


def _resolve_beyond_importer_scripts() -> Path:
    """Locate engine ``scripts/`` (beyond_importer.py) for standalone or submodule layout."""
    webapp_repo = Path(__file__).resolve().parent.parent
    for candidate in (
        webapp_repo / "scripts",
        webapp_repo.parent / "scripts",  # natural_20.py engine when n20-webapp is a submodule
    ):
        if (candidate / "beyond_importer.py").is_file():
            return candidate
    return webapp_repo / "scripts"


SCRIPTS = _resolve_beyond_importer_scripts()
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from beyond_importer import (  # noqa: E402
    BeyondImporter,
    extract_avatar_urls,
    parse_character_id_from_url,
)

__all__ = [
    "parse_character_id_from_url",
    "import_character_from_dndbeyond",
    "prepare_imported_pc_dict",
    "download_ddb_character_portraits",
]


def _fetch_image_bytes(url: str, *, cobalt_token: Optional[str] = None) -> bytes:
    if requests is None:
        raise RuntimeError("requests is required to download D&D Beyond portraits")
    headers = {
        "User-Agent": "natural20.py-importer/1.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    cookies = {"CobaltSession": cobalt_token} if cobalt_token else None
    response = requests.get(url, headers=headers, cookies=cookies, timeout=20.0)
    response.raise_for_status()
    return response.content


def download_ddb_character_portraits(
    character_data: Dict[str, Any],
    *,
    entity_uid: str,
    assets_dir: str,
    cobalt_token: Optional[str] = None,
) -> List[str]:
    """Download portrait/token images from D&D Beyond and save campaign assets.

    Returns non-fatal warning strings when URLs are missing or download fails.
    """
    warnings: List[str] = []
    portrait_url, token_url = extract_avatar_urls(character_data)
    if not portrait_url and not token_url:
        warnings.append(
            "No portrait or token image found in D&D Beyond data "
            "(character may use the default race art only)."
        )
        return warnings

    if Image is None:
        warnings.append("Pillow is not installed; skipped D&D Beyond portrait download.")
        return warnings

    from webapp.blueprints.helpers.character_builder_utils import _save_character_images

    profile_pil = None
    token_pil = None

    if portrait_url:
        try:
            profile_pil = Image.open(io.BytesIO(_fetch_image_bytes(portrait_url, cobalt_token=cobalt_token)))
        except Exception as exc:
            warnings.append(f"Could not download D&D Beyond portrait: {exc}")

    if token_url and token_url != portrait_url:
        try:
            token_pil = Image.open(io.BytesIO(_fetch_image_bytes(token_url, cobalt_token=cobalt_token)))
        except Exception as exc:
            warnings.append(f"Could not download D&D Beyond token image: {exc}")

    if profile_pil is None and token_pil is None:
        return warnings

    try:
        _save_character_images(entity_uid, assets_dir, profile_pil=profile_pil, token_pil=token_pil)
    except Exception as exc:
        warnings.append(f"Failed to save imported portrait images: {exc}")
    return warnings


def import_character_from_dndbeyond(
    character_id: int,
    *,
    cobalt_token: Optional[str] = None,
    input_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """Fetch (or load) a D&D Beyond character and convert it to engine YAML fields.

    Returns ``(yaml_dict, warnings, raw_character_data)``. The raw payload is
    needed to download portrait/token URLs that are not stored in YAML.
    """
    importer = BeyondImporter()
    if input_path:
        data = importer.load_character(input_path)
    else:
        data = importer.fetch_character(character_id, cobalt_token=cobalt_token)
    return importer.convert_to_yaml(data), list(importer.warnings), data


def prepare_imported_pc_dict(pc: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Ensure imported YAML has filenames/paths the webapp expects."""
    name = (pc.get("name") or "Unnamed").strip() or "Unnamed"
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    entity_uid = (pc.get("entity_uid") or safe_name).lower()
    entity_uid = re.sub(r"[^a-zA-Z0-9_\-]", "_", entity_uid)

    pc = dict(pc)
    pc["name"] = name
    pc["entity_uid"] = entity_uid
    pc.setdefault("hit_die", "inherit")
    pc.setdefault("group", "a")
    token_letter = (name.strip()[:1] or "P").upper()
    pc.setdefault("token", [token_letter])
    pc.setdefault("token_image", f"token_{entity_uid}.png")
    pc.setdefault("profile_image", f"characters/{entity_uid}.png")
    return pc, safe_name
