"""Convert model uploads (STL / Unity asset bundles) to GLB for the 3D VTT.

The Three.js client natively renders glTF/GLB, OBJ+MTL (HeroForge "web ready"
exports, full color with textures), and STL. To keep the token pipeline fast
and uniform, uploads are converted to GLB when that helps:

- STL (HeroForge "print ready")  -> GLB with a PBR material (optional tint
  color) and indexed, de-duplicated geometry (binary STL is ~3x larger).
- ``.unity3d`` asset bundles (legacy HeroForge "3D Tabletop" samples) -> one
  GLB per bundle with skinned meshes bound to their bind pose, DXT/RGBA
  textures embedded, full color.

Pure Python, stdlib only (``lzma`` for Unity LZMA blocks). GLB output follows
glTF 2.0: a single buffer, one mesh primitive per source group, PBR metallic
roughness materials, embedded PNG/JPEG textures.
"""
from __future__ import annotations

from .glb import GLBBuilder, MeshData, build_glb
from .stl import parse_stl

__all__ = [
    'GLBBuilder',
    'MeshData',
    'build_glb',
    'parse_stl',
    'convert_stl_to_glb',
    'convert_unity_to_glb',
    'is_unity_bundle',
]


def convert_stl_to_glb(stl_bytes: bytes, *, color=None, metallic=0.1, roughness=0.6) -> bytes:
    """Convert binary/ASCII STL bytes to a single-mesh GLB.

    ``color`` (rgba 0..1) tints the single PBR material when given, which is
    how mono STL uploads get a per-token DM-chosen color.
    """
    meshes = parse_stl(stl_bytes)
    if not meshes:
        raise ValueError('STL contains no triangles')
    if color is not None:
        for mesh in meshes:
            mesh.material.color = tuple(color)
    return build_glb(meshes, metallic=metallic, roughness=roughness)


def convert_unity_to_glb(bundle_bytes: bytes) -> bytes:
    """Convert a Unity asset bundle (``UnityFS``) to a GLB.

    Raises ``ValueError`` for bundles this converter cannot handle.
    """
    from .unity import bundle_to_glb

    return bundle_to_glb(bundle_bytes)


def is_unity_bundle(data: bytes) -> bool:
    return data[:8] in (b'UnityFS\x00', b'UnityW\x00\x00', b'UnityR\x00\x00', b'UnityA\x00\x00')
