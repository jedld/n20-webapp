"""Tests for webapp.model_convert (STL / Unity asset bundle -> GLB).

The converter lives in the webapp package (it serves the 3D VTT upload
pipeline only). Uses the HeroForge demo fixtures under
``n20-webapp/tests/fixtures/``.
"""
import json
import os
import struct
import sys
import zipfile
from pathlib import Path

import pytest

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from webapp.model_convert import (
    convert_stl_to_glb,
    convert_unity_to_glb,
    is_unity_bundle,
    parse_stl,
)
from webapp.model_convert.unity import _decode_dxt_block, _decode_block_compressed

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
PALADIN_ZIP = FIXTURES / 'Hero_Forge_Paladin_Demo.zip'
EXPLORER_ZIP = FIXTURES / 'Hero_Forge_Explorer_Demo_3D_Tabletop.zip'


def _glb_json(glb: bytes) -> dict:
    assert glb[:4] == b'glTF'
    version, total = struct.unpack_from('<II', glb, 4)
    assert version == 2
    assert total == len(glb)
    off = 12
    gltf = None
    while off + 8 <= len(glb):
        clen, ctype = struct.unpack_from('<I4s', glb, off)
        if ctype == b'JSON':
            gltf = json.loads(glb[off + 8:off + 8 + clen])
        off += 8 + clen
    assert gltf is not None
    return gltf


def _position_accessor(gltf: dict) -> dict:
    prim = gltf['meshes'][0]['primitives'][0]
    return gltf['accessors'][prim['attributes']['POSITION']]


@pytest.mark.skipif(not PALADIN_ZIP.exists(), reason='HeroForge fixture missing')
def test_stl_to_glb():
    with zipfile.ZipFile(PALADIN_ZIP) as zf:
        stl = zf.read('Hero_Forge_Paladin_Demo.stl')

    meshes = parse_stl(stl)
    assert len(meshes) == 1
    mesh = meshes[0]
    # deduplicated: ~118k triangles collapse to far fewer shared vertices
    assert len(mesh.positions) > 10000
    assert len(mesh.positions) < len(stl) // 50 * 3
    assert mesh.indices and len(mesh.indices) % 3 == 0

    glb = convert_stl_to_glb(stl, color=(0.6, 0.7, 0.9, 1.0))
    gltf = _glb_json(glb)
    acc = _position_accessor(gltf)
    lo, hi = acc['min'], acc['max']
    height = hi[1] - lo[1]
    # the 40 mm HeroForge figure stands upright on the Y axis in the output
    assert 30.0 < height < 50.0
    assert hi[1] > hi[2]  # taller than wide
    material = gltf['materials'][0]['pbrMetallicRoughness']
    assert material['baseColorFactor'] == [0.6, 0.7, 0.9, 1.0]
    assert 'NORMAL' in gltf['meshes'][0]['primitives'][0]['attributes']


@pytest.mark.skipif(not EXPLORER_ZIP.exists(), reason='HeroForge fixture missing')
def test_unity_bundle_detection():
    with zipfile.ZipFile(EXPLORER_ZIP) as zf:
        data = zf.read('Hero_Forge_Explorer_Demo.unity3d')
    assert is_unity_bundle(data)
    assert not is_unity_bundle(b'glTF\x02\x00\x00\x00')


@pytest.mark.skipif(not EXPLORER_ZIP.exists(), reason='HeroForge fixture missing')
def test_unity_to_glb():
    with zipfile.ZipFile(EXPLORER_ZIP) as zf:
        data = zf.read('Hero_Forge_Explorer_Demo.unity3d')

    glb = convert_unity_to_glb(data)
    gltf = _glb_json(glb)

    assert len(gltf['meshes']) == 1
    prim = gltf['meshes'][0]['primitives'][0]
    for attr in ('POSITION', 'NORMAL', 'TEXCOORD_0'):
        assert attr in prim['attributes']
    acc = gltf['accessors'][prim['attributes']['POSITION']]
    assert acc['count'] == 40159
    idx = gltf['accessors'][prim['indices']]
    assert idx['count'] == 175839

    # the figure stands upright on the Y axis
    lo, hi = acc['min'], acc['max']
    assert hi[1] - lo[1] > 30.0

    # diffuse texture is embedded and referenced by the material
    assert len(gltf['images']) == 1
    assert gltf['images'][0]['mimeType'] == 'image/png'
    assert gltf['materials'][0]['pbrMetallicRoughness']['baseColorTexture']


def _glb_json_and_bin(glb: bytes) -> tuple[dict, bytes]:
    off = 12
    gltf = None
    bin_chunk = b''
    while off + 8 <= len(glb):
        clen, ctype = struct.unpack_from('<I4s', glb, off)
        if ctype == b'JSON':
            gltf = json.loads(glb[off + 8:off + 8 + clen])
        elif ctype == b'BIN\x00':
            bin_chunk = glb[off + 8:off + 8 + clen]
        off += 8 + clen
    assert gltf is not None
    return gltf, bin_chunk


@pytest.mark.skipif(not EXPLORER_ZIP.exists(), reason='HeroForge fixture missing')
def test_unity_to_glb_front_faces_point_outward():
    """glTF uses CCW front faces; Unity's left-handed CW data (and the Z-up
    reflection) can leave the soup inward-facing, which renders as a hollow
    unlit shell because the renderer culls the actual outer faces."""
    from math import sqrt

    with zipfile.ZipFile(EXPLORER_ZIP) as zf:
        data = zf.read('Hero_Forge_Explorer_Demo.unity3d')
    glb = convert_unity_to_glb(data)
    gltf, bin_chunk = _glb_json_and_bin(glb)

    def read_accessor(index):
        acc = gltf['accessors'][index]
        bv = gltf['bufferViews'][acc['bufferView']]
        comp = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3}[acc['type']]
        size, fmt = {5126: (4, 'f'), 5123: (2, 'H'), 5125: (4, 'I')}[acc['componentType']]
        base = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
        stride = bv.get('byteStride', comp * size)
        return [struct.unpack_from(f'<{comp}{fmt}', bin_chunk, base + i * stride)
                for i in range(acc['count'])]

    prim = gltf['meshes'][0]['primitives'][0]
    positions = read_accessor(prim['attributes']['POSITION'])
    normals = read_accessor(prim['attributes']['NORMAL'])
    indices = [v[0] for v in read_accessor(prim['indices'])]

    volume = 0.0
    cosines = []
    step = max(1, (len(indices) // 3) // 1500)
    for f in range(0, len(indices), 3):
        a = positions[indices[f]]
        b = positions[indices[f + 1]]
        c = positions[indices[f + 2]]
        nx = (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1])
        ny = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
        nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        volume += (a[0] * nx + a[1] * ny + a[2] * nz) / 6.0
        if f % (3 * step) == 0:
            length = sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            va, vb, vc = (normals[indices[f]], normals[indices[f + 1]], normals[indices[f + 2]])
            cosines.append((nx * (va[0] + vb[0] + vc[0])
                            + ny * (va[1] + vb[1] + vc[1])
                            + nz * (va[2] + vb[2] + vc[2])) / (3.0 * length))
    assert volume > 0, 'front faces must point outward (positive signed volume)'
    assert sum(cosines) / len(cosines) > 0.8, 'vertex normals must agree with face winding'


# ---------------------------------------------------------------------------
# DXT block decoding (regression: block rows must not be flattened together,
# and DXT5 colour selectors live AFTER the 8-byte alpha preamble)
# ---------------------------------------------------------------------------

def _rgb565(v: int) -> tuple[int, int, int]:
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return r, g, b


def test_dxt1_block_rows_are_row_major():
    """An 8x8 image of four solid distinct blocks must land on the right rows."""
    colors = [0xF800, 0x07E0, 0x001F, 0b11111_111111_11111]
    blocks = b''.join(struct.pack('<HH', c, 0) + b'\x00' * 4 for c in colors)
    pixels = _decode_block_compressed(blocks, 8, 8, False)
    assert len(pixels) == 64

    def at(x: int, y: int):
        r, g, b, a = pixels[y * 8 + x]
        return (r, g, b), a

    for by in range(2):
        for bx in range(2):
            want = _rgb565(colors[by * 2 + bx])
            for x in (bx * 4, bx * 4 + 3):
                for y in (by * 4, by * 4 + 3):
                    got, a = at(x, y)
                    assert got == want, f'pixel ({x},{y}): {got} != {want}'
                    assert a == 255


def test_dxt5_colour_follows_alpha_preamble():
    block = (
        bytes((255, 200))       # alpha0 > alpha1
        + b'\xff' * 6           # alpha indices all 7 -> (a0+6*a1)//7
        + struct.pack('<HH', 0xF800, 0x0000)  # colour0 red, colour1 black
        + b'\x00' * 4           # colour selectors all 0 -> colour0
    )
    px = _decode_dxt_block(block, alpha=True)
    assert len(px) == 16
    want_a = (255 + 6 * 200) // 7
    for r, g, b, a in px:
        assert (r, g, b) == _rgb565(0xF800)
        assert a == want_a
