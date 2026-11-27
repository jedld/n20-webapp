"""STL parser (binary and ASCII) for HeroForge print-ready exports."""
from __future__ import annotations

import math
import re

from .glb import Material, MeshData

_ASCII_FACET = re.compile(
    r"facet\s+normal\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+"
    r"outer\s+loop\s+"
    r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+"
    r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+"
    r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+"
    r"endloop\s+endfacet",
    re.IGNORECASE,
)


def _is_binary(data: bytes) -> bool:
    if len(data) >= 84 and data[:5].upper() != b'solid':
        return True
    if b'facet' in data[:2000]:
        return False
    if len(data) >= 84:
        import struct
        try:
            n = struct.unpack('<I', data[80:84])[0]
        except Exception:
            return False
        return len(data) >= 84 + 50 * n
    return False


def _parse_binary(data: bytes) -> MeshData:
    import struct
    n = struct.unpack('<I', data[80:84])[0]
    positions = []
    normals_by_vertex = {}
    off = 84
    need = 50 * n
    if len(data) < off + need:
        n = (len(data) - off) // 50
    for _ in range(n):
        chunk = data[off:off + 50]
        off += 50
        vals = struct.unpack('<12fH', chunk)
        nx, ny, nz = vals[0:3]
        tri = vals[3:12]
        positions.extend((
            (tri[0], tri[1], tri[2]),
            (tri[3], tri[4], tri[5]),
            (tri[6], tri[7], tri[8]),
        ))
        if nx == ny == nz == 0:
            ux, uy, uz = tri[3] - tri[0], tri[4] - tri[1], tri[5] - tri[2]
            wx, wy, wz = tri[6] - tri[0], tri[7] - tri[1], tri[8] - tri[2]
            nx = uy * wz - uz * wy
            ny = uz * wx - ux * wz
            nz = ux * wy - uy * wx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / ln, ny / ln, nz / ln
        for k in range(3):
            normals_by_vertex[positions[-3 + k]] = (nx, ny, nz)
    normals = [normals_by_vertex[p] for p in positions]
    return MeshData(
        name='stl',
        positions=positions,
        normals=normals,
        material=Material(color=(0.82, 0.72, 0.55, 1.0), metallic=0.1, roughness=0.6),
    )


def _parse_ascii(data: bytes) -> MeshData:
    text = data.decode('utf-8', 'replace')
    positions = []
    normals = []
    for m in _ASCII_FACET.finditer(text):
        g = [float(x) for x in m.groups()]
        nx, ny, nz = g[0:3]
        positions.extend((
            (g[3], g[4], g[5]),
            (g[6], g[7], g[8]),
            (g[9], g[10], g[11]),
        ))
        for _ in range(3):
            normals.append((nx, ny, nz))
    return MeshData(
        name='stl',
        positions=positions,
        normals=normals,
        material=Material(color=(0.82, 0.72, 0.55, 1.0), metallic=0.1, roughness=0.6),
    )


def _dedupe(mesh: MeshData) -> None:
    """Index the triangle soup: share vertices at identical positions."""
    seen: dict[tuple, int] = {}
    uniq: list = []
    indices: list = []
    for i, p in enumerate(mesh.positions):
        j = seen.get(p)
        if j is None:
            j = len(uniq)
            seen[p] = j
            uniq.append(p)
        indices.append(j)
    if len(uniq) < len(mesh.positions):
        mesh.positions = uniq
        # STL normals are per-facet and become inconsistent once vertices are
        # shared; drop them so the GLB writer recomputes smooth vertex normals.
        mesh.normals = None
        mesh.indices = indices


def _fix_up_axis(mesh: MeshData) -> None:
    """STLs from CAD/print tooling often stand the model up on Z; glTF is
    Y-up.  The (x, y, z) -> (x, z, y) reflection keeps triangle winding."""
    xs = [p[0] for p in mesh.positions]
    ys = [p[1] for p in mesh.positions]
    zs = [p[2] for p in mesh.positions]
    ex, ey, ez = max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)
    if ez > ex and ez > ey:
        mesh.positions = [(x, z, y) for (x, y, z) in mesh.positions]
        if mesh.normals is not None:
            mesh.normals = [(nx, nz, ny) for (nx, ny, nz) in mesh.normals]


def parse_stl(data: bytes) -> list[MeshData]:
    """Parse an STL file into one indexed MeshData."""
    if not data:
        return []
    if _is_binary(data):
        mesh = _parse_binary(data)
    else:
        mesh = _parse_ascii(data)
    if not mesh.positions:
        return []
    _dedupe(mesh)
    _fix_up_axis(mesh)
    return [mesh]
