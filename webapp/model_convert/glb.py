"""Minimal glTF 2.0 (GLB) writer for converted token models.

Stdlib-only. Supports multiple mesh groups, PBR metallic-roughness materials,
and embedded PNG/JPEG textures. Coordinates are written as-is (Y-up,
right-handed); callers are responsible for axis conventions.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field


@dataclass
class Material:
    color: tuple = (0.8, 0.8, 0.8, 1.0)          # rgba 0..1
    metallic: float = 0.0
    roughness: float = 0.8
    texture: tuple = None                        # (mime_type, image_bytes) or None


@dataclass
class MeshData:
    """One mesh group: interleaved-free triangle soup or indexed mesh."""
    name: str
    positions: list                            # [(x, y, z), ...]
    indices: list = None                       # [int, ...] or None (flat triples)
    normals: list = None                       # [(nx, ny, nz), ...] matching positions
    uvs: list = None                           # [(u, v), ...] matching positions
    material: Material = field(default_factory=Material)

    def triangle_count(self) -> int:
        if self.indices is not None:
            return len(self.indices) // 3
        return len(self.positions) // 3


def _default_normals(positions, indices=None) -> list:
    normals = [(0.0, 0.0, 0.0)] * len(positions)
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    if indices is not None:
        tris = range(0, len(indices) - 2, 3)

        def tri(i):
            return indices[i], indices[i + 1], indices[i + 2]
    else:
        tris = range(0, len(positions) - 2, 3)

        def tri(i):
            return i, i + 1, i + 2
    for i in tris:
        ia, ib, ic = tri(i)
        ax, ay, az = positions[ia]
        bx, by, bz = positions[ib]
        cx, cy, cz = positions[ic]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        for j in (ia, ib, ic):
            acc[j][0] += nx
            acc[j][1] += ny
            acc[j][2] += nz
    out = []
    for vx, vy, vz in acc:
        ln = math.sqrt(vx * vx + vy * vy + vz * vz)
        if ln < 1e-12:
            out.append((0.0, 1.0, 0.0))
        else:
            out.append((vx / ln, vy / ln, vz / ln))
    return out


def _pad4(n: int) -> int:
    return (n + 3) & ~3


class GLBBuilder:
    def __init__(self):
        self._bin = bytearray()
        self.bufferViews = []
        self.accessors = []
        self.meshes = []
        self.nodes = []
        self.materials = []
        self.textures = []
        self.images = []
        self._material_index = {}
        self._image_index = {}

    def _add_buffer_view(self, data: bytes, target: int = None) -> int:
        while len(self._bin) % 4:
            self._bin.append(0)
        offset = len(self._bin)
        self._bin += data
        view = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)}
        if target:
            view['target'] = target
        self.bufferViews.append(view)
        return len(self.bufferViews) - 1

    def _add_positions(self, positions) -> int:
        blob = b''.join(struct.pack('<fff', *p) for p in positions)
        view = self._add_buffer_view(blob, 34962)  # ARRAY_BUFFER
        mn = [min(p[i] for p in positions) for i in range(3)]
        mx = [max(p[i] for p in positions) for i in range(3)]
        self.accessors.append({
            'bufferView': view, 'componentType': 5126, 'count': len(positions),
            'type': 'VEC3', 'min': mn, 'max': mx,
        })
        return len(self.accessors) - 1

    def _add_vec3(self, vectors) -> int:
        blob = b''.join(struct.pack('<fff', *v) for v in vectors)
        view = self._add_buffer_view(blob, 34962)
        self.accessors.append({
            'bufferView': view, 'componentType': 5126, 'count': len(vectors), 'type': 'VEC3',
        })
        return len(self.accessors) - 1

    def _add_vec2(self, vectors) -> int:
        blob = b''.join(struct.pack('<ff', *v) for v in vectors)
        view = self._add_buffer_view(blob, 34962)
        self.accessors.append({
            'bufferView': view, 'componentType': 5126, 'count': len(vectors), 'type': 'VEC2',
        })
        return len(self.accessors) - 1

    def _add_indices(self, indices, vertex_count: int) -> int:
        comp = 5123 if vertex_count < 65536 else 5125  # UNSIGNED_SHORT / UNSIGNED_INT
        fmt = '<H' if comp == 5123 else '<I'
        blob = b''.join(struct.pack(fmt, i) for i in indices)
        view = self._add_buffer_view(blob, 34963)  # ELEMENT_ARRAY_BUFFER
        self.accessors.append({
            'bufferView': view, 'componentType': comp, 'count': len(indices), 'type': 'SCALAR',
        })
        return len(self.accessors) - 1

    def _add_image(self, mime: str, data: bytes) -> int:
        key = (mime, id(data), len(data))
        if key in self._image_index:
            return self._image_index[key]
        view = self._add_buffer_view(data)
        self.images.append({'mimeType': mime, 'bufferView': view})
        index = len(self.images) - 1
        self._image_index[key] = index
        return index

    def _add_material(self, mat: Material) -> int:
        key = (mat.color, mat.metallic, mat.roughness,
               mat.texture[0] if mat.texture else None,
               len(mat.texture[1]) if mat.texture else 0)
        if key in self._material_index:
            return self._material_index[key]
        entry = {
            'pbrMetallicRoughness': {
                'baseColorFactor': [float(c) for c in mat.color],
                'metallicFactor': float(mat.metallic),
                'roughnessFactor': float(mat.roughness),
            },
            'doubleSided': False,
        }
        if mat.texture:
            mime, data = mat.texture
            if mime in ('image/png', 'image/jpeg'):
                image = self._add_image(mime, data)
                self.textures.append({'sampler': 0, 'source': image})
                entry['pbrMetallicRoughness']['baseColorTexture'] = {
                    'index': len(self.textures) - 1,
                }
        self.materials.append(entry)
        index = len(self.materials) - 1
        self._material_index[key] = index
        return index

    def add_mesh(self, mesh: MeshData, *, name: str = None):
        positions = list(mesh.positions)
        if not positions:
            return
        attributes = {'POSITION': self._add_positions(positions)}
        normals = mesh.normals
        if normals is None:
            normals = _default_normals(positions, mesh.indices)
        if normals is not None and len(normals) == len(positions):
            attributes['NORMAL'] = self._add_vec3(normals)
        if mesh.uvs is not None and len(mesh.uvs) == len(positions):
            attributes['TEXCOORD_0'] = self._add_vec2(mesh.uvs)
        primitive = {'attributes': attributes}
        if mesh.indices is not None:
            primitive['indices'] = self._add_indices(list(mesh.indices), len(positions))
        primitive['material'] = self._add_material(mesh.material)
        mesh_name = name or mesh.name or f'mesh{len(self.meshes)}'
        self.meshes.append({'name': mesh_name, 'primitives': [primitive]})
        self.nodes.append({'mesh': len(self.meshes) - 1, 'name': mesh_name})

    def build(self) -> bytes:
        sampler = {'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497}
        gltf = {
            'asset': {'version': '2.0', 'generator': 'webapp.model_convert'},
            'scene': 0,
            'scenes': [{'nodes': list(range(len(self.nodes)))}],
            'nodes': self.nodes,
            'meshes': self.meshes,
            'materials': self.materials,
            'textures': self.textures,
            'images': self.images,
            'samplers': [sampler],
            'accessors': self.accessors,
            'bufferViews': self.bufferViews,
            'buffers': [{'byteLength': len(self._bin)}],
        }
        json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
        while len(json_bytes) % 4:
            json_bytes += b' '
        bin_bytes = bytes(self._bin)
        while len(bin_bytes) % 4:
            bin_bytes += b'\x00'
        total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
        out = bytearray()
        out += struct.pack('<III', 0x46546C67, 2, total)          # glTF, version, length
        out += struct.pack('<II', len(json_bytes), 0x4E4F534A)    # JSON chunk
        out += json_bytes
        out += struct.pack('<II', len(bin_bytes), 0x004E4942)     # BIN chunk
        out += bin_bytes
        return bytes(out)


def build_glb(meshes, *, default_color=None, metallic=None, roughness=None) -> bytes:
    builder = GLBBuilder()
    for i, mesh in enumerate(meshes):
        if mesh.material.color == (0.8, 0.8, 0.8, 1.0) and default_color:
            mesh.material.color = default_color
        if metallic is not None and mesh.material.metallic == 0.0:
            mesh.material.metallic = metallic
        if roughness is not None and mesh.material.roughness == 0.8:
            mesh.material.roughness = roughness
        builder.add_mesh(mesh)
    return builder.build()
