"""UnityFS asset-bundle reader for HeroForge legacy "3D Tabletop" samples.

Best-effort converter: extracts Mesh / SkinnedMeshRenderer assets with their
material textures and writes a GLB (rest pose: bind-pose baked into the
vertices). Stdlib-only (``lzma`` plus a small LZ4 block decoder). Handles
SerializedFile formats 7-21 that carry inline type trees (Unity 5 - 2020),
which covers the HeroForge sample bundles.

Texture formats decoded: RGBA32/ARGB32/BGRA32/RGB24/BGR24/RGBA4444/ARGB4444/
RGB565/Alpha8, DXT1/DXT3/DXT5 (BC1-3), plus embedded PNG/JPEG streams.
"""
from __future__ import annotations

import struct
from math import sqrt

from .glb import Material, MeshData


class UnityConvertError(ValueError):
    pass


# ---------------------------------------------------------------------------
# decompression
# ---------------------------------------------------------------------------

def lz4_block_decompress(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        token = data[i]
        i += 1
        length = token >> 4
        if length == 15:
            while i < n:
                b = data[i]
                i += 1
                length += b
                if b != 255:
                    break
        if i + length > n:
            raise UnityConvertError('lz4 literal overflow')
        out += data[i:i + length]
        i += length
        if i >= n:
            break
        if i + 2 > n:
            raise UnityConvertError('lz4 truncated offset')
        offset = struct.unpack('<H', data[i:i + 2])[0]
        i += 2
        if offset == 0 or offset > len(out):
            raise UnityConvertError('lz4 bad offset')
        match_len = token & 0x0F
        if match_len == 15:
            while i < n:
                b = data[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4
        start = len(out) - offset
        if start + match_len <= len(out):
            out += out[start:start + match_len]
        else:
            for k in range(match_len):
                out.append(out[start + k])
    return bytes(out)


def _decompress_lzma(data: bytes) -> bytes:
    """raw LZMA1; Unity prefixes a props byte + little-endian dict size."""
    props = data[0]
    dictsize = struct.unpack('<I', data[1:5])[0] or (1 << 24)
    pb = props // 45
    rem = props % 45
    lc = rem // 9
    lp = rem % 9
    import lzma
    for params in ((lc, lp, pb), (3, 0, 2)):
        try:
            dec = lzma.LZMADecompressor(
                format=lzma.FORMAT_RAW,
                filters=[{'id': lzma.FILTER_LZMA1, 'lc': params[0], 'lp': params[1],
                          'pb': params[2], 'dict_size': max(dictsize, 1 << 16)}],
            )
            return dec.decompress(data[5:])
        except Exception:
            continue
    raise UnityConvertError('unsupported LZMA block parameters')


def _decompress_block(data: bytes, comp: int) -> bytes:
    """Decompress one bundle block.

    Some exporters (HeroForge's included) mislabel LZMA blocks as LZ4, so on
    a decoder mismatch we retry the other supported codec before failing.
    """
    if comp == 0:
        return data
    if comp in (1, 3):  # LZ4 / LZ4HC share the block bitstream
        try:
            return lz4_block_decompress(data)
        except UnityConvertError:
            if data[:2] == b'\x5d\x00':  # LZMA props suggest raw LZMA1
                return _decompress_lzma(data)
            raise
    if comp == 2:
        return _decompress_lzma(data)
    raise UnityConvertError(f'unsupported block compression {comp}')


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b'\x00', pos)
    return data[pos:end].decode('utf-8', 'replace'), end + 1


def read_bundle(data: bytes) -> dict[str, bytes]:
    """Return the (decompressed) files in a Unity bundle keyed by node name."""
    if not data.startswith(b'Unity'):
        raise UnityConvertError('not a Unity bundle')
    pos = 0
    signature, pos = _cstr(data, pos)
    if signature != 'UnityFS':
        raise UnityConvertError(f'unsupported bundle signature {signature!r}')
    (fmt,) = struct.unpack_from('>I', data, pos)
    pos += 4
    _player, pos = _cstr(data, pos)
    _engine, pos = _cstr(data, pos)
    pos += 8  # total size
    csize, usize, flags = struct.unpack_from('>III', data, pos)
    pos += 12
    bi_comp = flags & 0x3F
    bi_at_end = bool(flags & 0x80)
    blocks_info_start = (len(data) - csize) if bi_at_end else pos
    bi_raw = data[blocks_info_start:blocks_info_start + csize]
    bi = _decompress_block(bi_raw, bi_comp) if bi_comp else bi_raw
    off = 16  # skip hash
    (nb,) = struct.unpack_from('>i', bi, off)
    off += 4
    blocks = []
    for _ in range(nb):
        usize_b, csize_b = struct.unpack_from('>II', bi, off)
        (bflags,) = struct.unpack_from('>H', bi, off + 8)
        off += 10
        blocks.append((usize_b, csize_b, bflags & 0x3F))
    (nd,) = struct.unpack_from('>i', bi, off)
    off += 4
    nodes = []
    for _ in range(nd):
        offset, size = struct.unpack_from('>QQ', bi, off)
        name, off2 = _cstr(bi, off + 16 + 4)
        off = off2
        nodes.append((offset, size, name))
    if bi_at_end:
        data_start = pos
    else:
        data_start = pos + csize
        if fmt >= 6 and flags & 0x200:
            data_start = (data_start + 15) & ~15
    stream = bytearray()
    boff = data_start
    for _usize_b, csize_b, comp in blocks:
        stream += _decompress_block(data[boff:boff + csize_b], comp)
        boff += csize_b
    files = {name: bytes(stream[offset:offset + size]) for offset, size, name in nodes}
    if not files:
        raise UnityConvertError('bundle contains no files')
    return files


# ---------------------------------------------------------------------------
# canonical Unity CommonString table (type-tree string buffer references)
# ---------------------------------------------------------------------------

COMMON_STRINGS = {
    0: 'AABB', 5: 'AnimationClip', 19: 'AnimationCurve', 34: 'AnimationState',
    49: 'Array', 55: 'Base', 60: 'BitField', 69: 'bitset', 76: 'bool', 81: 'char',
    86: 'ColorRGBA', 96: 'Component', 106: 'data', 111: 'deque', 117: 'double',
    124: 'dynamic_array', 138: 'FastPropertyName', 155: 'first', 161: 'float',
    167: 'Font', 172: 'GameObject', 183: 'Generic Mono', 196: 'GradientNEW',
    208: 'GUID', 213: 'GUIStyle', 222: 'int', 226: 'list', 231: 'long long',
    241: 'map', 245: 'Matrix4x4f', 256: 'MdFour', 263: 'MonoBehaviour',
    277: 'MonoScript', 288: 'm_ByteSize', 299: 'm_Curve', 307: 'm_EditorClassIdentifier',
    331: 'm_EditorHideFlags', 349: 'm_Enabled', 359: 'm_ExtensionPtr',
    374: 'm_GameObject', 387: 'm_Index', 395: 'm_IsArray', 405: 'm_IsStatic',
    416: 'm_MetaFlag', 427: 'm_Name', 434: 'm_ObjectHideFlags', 452: 'm_PrefabInternal',
    469: 'm_PrefabParentObject', 490: 'm_Script', 499: 'm_StaticEditorFlags',
    519: 'm_Type', 526: 'm_Version', 536: 'Object', 543: 'pair', 548: 'PPtr<Component>',
    564: 'PPtr<GameObject>', 581: 'PPtr<Material>', 596: 'PPtr<MonoBehaviour>',
    616: 'PPtr<MonoScript>', 633: 'PPtr<Object>', 646: 'PPtr<Prefab>',
    659: 'PPtr<Sprite>', 672: 'PPtr<TextAsset>', 688: 'PPtr<Texture>',
    702: 'PPtr<Texture2D>', 718: 'PPtr<Transform>', 734: 'Prefab',
    741: 'Quaternionf', 753: 'Rectf', 759: 'RectInt', 767: 'RectOffset',
    778: 'second', 785: 'set', 789: 'short', 795: 'size', 800: 'SInt16',
    807: 'SInt32', 814: 'SInt64', 821: 'SInt8', 827: 'staticvector', 840: 'string',
    847: 'TextAsset', 857: 'TextMesh', 866: 'Texture', 874: 'Texture2D',
    884: 'Transform', 894: 'TypelessData', 907: 'UInt16', 914: 'UInt32',
    921: 'UInt64', 928: 'UInt8', 934: 'unsigned int', 947: 'unsigned long long',
    966: 'unsigned short', 981: 'vector', 988: 'Vector2f', 997: 'Vector3f',
    1006: 'Vector4f', 1015: 'm_ScriptingClassIdentifier', 1042: 'Gradient',
    1051: 'Type*', 1057: 'int2_storage', 1070: 'int3_storage', 1083: 'BoundsInt',
    1093: 'm_CorrespondingSourceObject', 1121: 'm_PrefabInstance', 1138: 'm_PrefabAsset',
    1152: 'FileSize', 1161: 'Hash128', 1169: 'RenderingLayerMask',
    1188: 'fixed_array', 1200: 'EntityId', 1209: 'LoadableObjectId',
    1226: 'LoadableSceneId',
}


class _Node:
    __slots__ = ('level', 'type', 'name', 'byte_size', 'meta', 'children')

    def __init__(self, level, type_str, name, byte_size, meta):
        self.level = level
        self.type = type_str
        self.name = name
        self.byte_size = byte_size
        self.meta = meta
        self.children = []

    def child(self, name: str):
        for ch in self.children:
            if ch.name == name:
                return ch
        return None


def _read_typetree_nodes(blob: bytes, node_count: int, endian: str, version: int) -> _Node:
    """Build the type tree from the node array + trailing string buffer."""
    fmt = f'{endian}hBBIIiii' + ('Q' if version >= 19 else '')
    stride = struct.calcsize(fmt)
    strbuf = blob[node_count * stride:]

    def read_str(val: int) -> str:
        if val & 0x80000000:
            return COMMON_STRINGS.get(val & 0x7FFFFFFF, f'x{val & 0x7FFFFFFF}')
        end = strbuf.index(b'\x00', val)
        return strbuf[val:end].decode('utf-8', 'replace')

    root = None
    stack: list[_Node] = []
    for i in range(node_count):
        raw = struct.unpack_from(fmt, blob, i * stride)
        _ver, level, _flags, type_off, name_off, byte_size, _index, meta = raw[:8]
        node = _Node(level, read_str(type_off), read_str(name_off), byte_size, meta)
        if level == 0:
            root = node
            stack = [node]
            continue
        while stack and stack[-1].level >= level:
            stack.pop()
        if not stack:
            raise UnityConvertError('broken type tree')
        stack[-1].children.append(node)
        stack.append(node)
    if root is None:
        raise UnityConvertError('empty type tree')
    return root


# ---------------------------------------------------------------------------
# SerializedFile
# ---------------------------------------------------------------------------

_PRIMS = {
    'bool': ('?', 1), 'char': ('b', 1), 'SInt8': ('b', 1), 'UInt8': ('B', 1),
    'short': ('h', 2), 'SInt16': ('h', 2), 'UInt16': ('H', 2), 'unsigned short': ('H', 2),
    'int': ('i', 4), 'SInt32': ('i', 4), 'UInt32': ('I', 4), 'unsigned int': ('I', 4),
    'long long': ('q', 8), 'SInt64': ('q', 8), 'UInt64': ('Q', 8), 'unsigned long long': ('Q', 8),
    'float': ('f', 4), 'double': ('d', 8),
}


class _RawArray:
    """Element array kept as raw bytes (vertex/index buffers)."""

    __slots__ = ('data', 'elem_size')

    def __init__(self, data: bytes, elem_size: int):
        self.data = data
        self.elem_size = elem_size


class _Reader:
    def __init__(self, data: bytes, endian: str = '<'):
        self.data = data
        self.pos = 0
        self.endian = endian

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise UnityConvertError('read past end of object')
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt: str):
        full = self.endian + fmt
        return struct.unpack(full, self.read(struct.calcsize(full)))

    def align(self, n: int = 4) -> None:
        pad = (-self.pos) % n
        if pad:
            self.read(pad)


def _read_value(r: _Reader, node: _Node):
    pair_array = (len(node.children) == 2 and node.children[0].name == 'size'
                  and node.children[1].name == 'data')
    is_array = (node.name == 'Array' or node.type == 'Array'
                or (pair_array and node.byte_size < 0))
    if not is_array and node.byte_size >= 4:
        r.align(4)  # Unity serializes every 4+-byte value (scalar or struct) aligned
    if node.children:
        if is_array:
            elements = node.children
            variable = bool(node.meta & 0x4000)
            if len(elements) == 2 and elements[0].name == 'size':
                # explicit size member: int size; <element> data
                if variable:
                    r.align(4)  # the count field itself is 4-byte aligned
                (count,) = r.unpack('i')
                element = elements[1]
            elif variable:
                r.align(4)
                (count,) = r.unpack('i')
                element = elements[-1]
            else:
                element = elements[0]
                elem_size = element.byte_size or 1
                count = (node.byte_size // elem_size) if elem_size else 0
            if count < 0:
                count = 0
            is_string = (element.type == 'char' and element.name == 'data')
            if is_string:
                raw = r.read(count)
                if count:
                    r.align(4)  # string payloads are padded to 4 bytes
                text = raw.split(b'\x00', 1)[0]
                try:
                    return text.decode('utf-8')
                except UnicodeDecodeError:
                    return text.decode('latin1')
            if element.type in _PRIMS and (count > 8 or node.type == 'TypelessData'):
                size = _PRIMS[element.type][1]
                if count and size >= 4:
                    r.align(4)
                raw = r.read(count * size)
                if count and variable:
                    r.align(4)
                return _RawArray(raw, size)
            values = [_read_value(r, element) for _ in range(count)]
            if count and variable and (element.byte_size or 1) > 1:
                r.align(4)
            return values
        if node.type == 'vector' and len(node.children) == 1:
            # vector<T> wrapper: surface the element list directly
            return _read_value(r, node.children[0])
        if node.type == 'map' and len(node.children) == 1:
            # map<K,V> wrapper: list of {'first':.., 'second':..} entries
            return _read_value(r, node.children[0])
        if (node.type == 'string' or node.type.startswith('basic_string')) \
                and len(node.children) == 1:
            # string wrapper around an Array of char
            return _read_value(r, node.children[0])
        out = {}
        for ch in node.children:
            out[ch.name] = _read_value(r, ch)
        return out
    fmt = _PRIMS.get(node.type)
    if fmt:
        (v,) = r.unpack(fmt[0])
        return v
    raise UnityConvertError(f'unknown primitive type {node.type!r}')


class SerializedFile:
    def __init__(self, data: bytes, resS: bytes = b''):
        self.data = data
        self.resS = resS
        r = _Reader(data, '>')
        meta_size, file_size, version, data_offset = r.unpack('IIII')
        self.version = version
        endian = '>'
        if version >= 9:
            (endian_byte,) = r.unpack('?')
            endian = '>' if endian_byte else '<'
            r.read(3)
            if version >= 22:
                # 64-bit header fields
                meta_size, file_size, data_offset, _unk = r.unpack('qqqq')
        self.endian = endian
        self.unity_version = ''
        if version >= 7:
            end = data.index(b'\x00', r.pos)
            self.unity_version = data[r.pos:end].decode('latin1')
            r.pos = end + 1
        if version >= 8:
            r.read(4)  # target platform
        if version >= 13:
            (has_tree,) = r.unpack('?')
            if not has_tree:
                raise UnityConvertError('no inline type trees (needs a class database)')
        r.endian = endian
        (type_count,) = r.unpack('i')
        self.types: list[dict] = []
        for _ in range(type_count):
            (class_id,) = r.unpack('i')
            if version >= 16:
                r.read(1)  # is_stripped
            if version >= 17:
                r.read(2)  # script type index
            if version >= 13:
                if (version < 16 and class_id < 0) or (version >= 16 and class_id == 114):
                    r.read(16)
                r.read(16)  # old type hash
            tree = None
            if version >= 12 or version == 10:
                (node_count,) = r.unpack('I')
                (str_size,) = r.unpack('I')
                stride = struct.calcsize(f'{endian}hBBIIiii' + ('Q' if version >= 19 else ''))
                tree = _read_typetree_nodes(r.read(node_count * stride + str_size),
                                            node_count, endian, version)
            if version >= 21:
                (dep_count,) = r.unpack('i')
                r.read(4 * dep_count)
            entry = {'class_id': class_id, 'tree': tree}
            entry['type_name'] = tree.type if tree else str(class_id)
            self.types.append(entry)
        big_id = 0
        if 7 <= version < 14:
            (big_id,) = r.unpack('i')
        (obj_count,) = r.unpack('i')
        self.objects: dict[int, dict] = {}
        if version >= 14:
            r.align(8)  # the whole table is aligned; entries themselves are 20 bytes packed
        for _ in range(obj_count):
            if 7 <= version < 14 and not big_id:
                (path_id,) = r.unpack('i')
            else:
                (path_id,) = r.unpack('q')
            (byte_start,) = r.unpack('I')
            (byte_size,) = r.unpack('I')
            (type_id,) = r.unpack('i')
            stype = self.types[type_id] if 0 <= type_id < len(self.types) else None
            if 11 <= version < 17:
                r.read(2)
            if version in (15, 16):
                r.read(1)
            self.objects[path_id] = {
                'path_id': path_id,
                'byte_start': byte_start + data_offset,
                'byte_size': byte_size,
                'type': stype,
            }

    def parse(self, path_id: int) -> dict:
        obj = self.objects[path_id]
        r = _Reader(self.data[obj['byte_start']:obj['byte_start'] + obj['byte_size']], self.endian)
        return _read_value(r, obj['type']['tree'])

    def deref(self, pptr) -> int | None:
        """Resolve a PPtr dict {'m_FileID':..,'m_PathID':..} to a local path_id."""
        if not isinstance(pptr, dict):
            return None
        if pptr.get('m_FileID'):
            return None  # external reference: not resolvable inside one bundle
        return pptr.get('m_PathID') or None

    def stream_data(self, sd: dict) -> bytes:
        """Resolve an m_StreamData block to raw bytes (inline or .resS)."""
        if not isinstance(sd, dict):
            return b''
        size = int(sd.get('size') or 0)
        if not size:
            return b''
        path = sd.get('path') or ''
        offset = int(sd.get('offset') or 0)
        if not path:
            return self.data[offset:offset + size]
        return self.resS[offset:offset + size]


# ---------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------

def _decode_dxt_block(block: bytes, alpha: bool, dxt3: bool = False) -> list[tuple[int, int, int, int]]:
    """Decode one 8-byte DXT1 or 16-byte DXT3/DXT5 block into 16 RGBA
    pixels, row-major (block-local row 0 first, left to right)."""
    if alpha:
        # DXT3/DXT5 put the alpha sub-block first; colour follows at 8..16
        col = block[8:16]
    else:
        col = block[0:8]
    c0, c1 = struct.unpack_from('<HH', col, 0)

    def unpack565(v: int) -> tuple[int, int, int]:
        r = ((v >> 11) & 0x1F) * 255 // 31
        g = ((v >> 5) & 0x3F) * 255 // 63
        b = (v & 0x1F) * 255 // 31
        return r, g, b

    r0, g0, b0 = unpack565(c0)
    r1, g1, b1 = unpack565(c1)
    palette = [(r0, g0, b0), (r1, g1, b1)]
    if alpha or c0 > c1:
        palette.append(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3))
        palette.append(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3))
    else:
        palette.append(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2))
        palette.append((0, 0, 0))
    # colour selector bitstream: bytes 4..8 of the colour sub-block,
    # two bits per pixel, row 0 first, LSB = leftmost pixel
    sel = int.from_bytes(col[4:8], 'little')
    alphas: list[int] = []
    alpha_bits = 0
    if dxt3:
        # DXT3: explicit 4-bit alpha per pixel, low nibble = left pixel
        alpha_nib = block[0:8]
    elif alpha:
        a0, a1 = block[0], block[1]
        alphas = [a0, a1]
        if a0 > a1:
            alphas += [(6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                       (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                       (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7]
        else:
            alphas += [(4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                       (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5, 0, 255]
        alpha_bits = int.from_bytes(block[2:8], 'little')
    pixels = []
    for row in range(4):
        for colx in range(4):
            r, g, b = palette[(sel >> ((row * 4 + colx) * 2)) & 3]
            if dxt3:
                nib = alpha_nib[row * 2 + colx // 2]
                a = (nib & 0xF if colx % 2 == 0 else (nib >> 4) & 0xF) * 17
            elif alpha:
                a = alphas[(alpha_bits >> ((row * 4 + colx) * 3)) & 7]
            else:
                a = 255
            pixels.append((r, g, b, a))
    return pixels


def _decode_block_compressed(data: bytes, w: int, h: int, alpha: bool,
                             dxt3: bool = False) -> list[tuple[int, int, int, int]]:
    """Decode DXT1/DXT3/DXT5 blocks into RGBA8 rows (top row first)."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    stride = 16 if alpha else 8
    out: list[tuple[int, int, int, int]] = []
    for by in range(bh):
        band: list[list[tuple[int, int, int, int]]] = [[], [], [], []]
        for bx in range(bw):
            off = (by * bw + bx) * stride
            pixels = _decode_dxt_block(data[off:off + stride], alpha, dxt3)
            # each block contributes 4 pixels to each of 4 image rows
            for r in range(4):
                band[r].extend(pixels[r * 4:r * 4 + 4])
        for r in range(4):
            if by * 4 + r >= h:
                break
            out.extend(band[r][:w])
    return out[:w * h]


def decode_texture(fmt: int, data: bytes, w: int, h: int) -> bytes:
    """Decode a Unity TextureFormat to RGBA8 rows (top row first, not flipped)."""
    n = w * h
    if fmt == 4:  # RGBA32
        return data[:n * 4]
    if fmt == 5:  # ARGB32 -> RGBA
        out = bytearray(n * 4)
        for i in range(n):
            a, r, g, b = data[i * 4:i * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        return bytes(out)
    if fmt in (64, 65):  # BGRA32 / BGRA32 sRGB? common BGRA order
        out = bytearray(data[:n * 4])
        for i in range(0, n * 4, 4):
            out[i], out[i + 2] = out[i + 2], out[i]
        return bytes(out)
    if fmt == 3:  # RGB24
        out = bytearray(n * 4)
        for i in range(n):
            out[i * 4:i * 4 + 3] = data[i * 3:i * 3 + 3]
            out[i * 4 + 3] = 255
        return bytes(out)
    if fmt == 2:  # ARGB4444
        out = bytearray(n * 4)
        for i in range(n):
            v = struct.unpack_from('<H', data, i * 2)[0]
            a = ((v >> 12) & 0xF) * 17
            r = ((v >> 8) & 0xF) * 17
            g = ((v >> 4) & 0xF) * 17
            b = (v & 0xF) * 17
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        return bytes(out)
    if fmt == 7:  # RGB565
        out = bytearray(n * 4)
        for i in range(n):
            v = struct.unpack_from('<H', data, i * 2)[0]
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x3F) * 255 // 63
            b = (v & 0x1F) * 255 // 31
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
        return bytes(out)
    if fmt == 1:  # Alpha8
        out = bytearray(n * 4)
        for i in range(n):
            out[i * 4:i * 4 + 3] = bytes((255, 255, 255))
            out[i * 4 + 3] = data[i]
        return bytes(out)
    if fmt == 34:  # DXT1
        return _to_rgba_bytes(_decode_block_compressed(data, w, h, False), w, h)
    if fmt == 35:  # DXT3 (explicit 4-bit alpha)
        return _to_rgba_bytes(_decode_block_compressed(data, w, h, True, dxt3=True), w, h)
    if fmt == 37:  # DXT5
        return _to_rgba_bytes(_decode_block_compressed(data, w, h, True), w, h)
    raise UnityConvertError(f'unsupported texture format {fmt}')


def _to_rgba_bytes(pixels, w, h) -> bytes:
    out = bytearray()
    for r, g, b, a in pixels[:w * h]:
        out += bytes((r, g, b, a))
    return bytes(out)


def encode_png(w: int, h: int, rgba: bytes) -> bytes:
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
            + chunk(b'IEND', b''))


def texture_png(sf: SerializedFile, t2d: dict) -> tuple[int, int, bytes]:
    w = int(t2d.get('m_Width') or 0)
    h = int(t2d.get('m_Height') or 0)
    if not w or not h:
        raise UnityConvertError('invalid texture size')
    fmt = int(t2d.get('m_TextureFormat') or t2d.get('m_Format') or 0)
    sd = t2d.get('m_StreamData') or {}
    size = int(sd.get('size') or 0)
    if size:
        raw = sf.stream_data(sd)
    else:
        raise UnityConvertError('inline texture data not supported')
    if fmt not in (1, 2, 3, 4, 5, 7, 34, 35, 37, 64, 65):
        # exporters sometimes write a bogus format tag; infer it from the
        # byte-per-pixel ratio of the (mip-chained) image data
        bpp = size / float(w * h)
        if bpp < 1.0:
            fmt = 34  # DXT1
        elif bpp < 2.0:
            fmt = 37  # DXT5
        elif bpp < 5.0:
            fmt = 4   # RGBA32
        else:
            raise UnityConvertError(f'unsupported texture format {fmt}')
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        return w, h, raw
    if raw[:2] == b'\xff\xd8':
        return w, h, raw
    rgba = decode_texture(fmt, raw, w, h)
    # Unity textures have the origin at the bottom-left; flip to top-left.
    rows = [rgba[y * w * 4:(y + 1) * w * 4] for y in range(h)]
    rows.reverse()
    return w, h, encode_png(w, h, b''.join(rows))


# ---------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------

_VERTEX_FORMATS = {
    0: ('<f', 4),   # Float32
    1: ('<e', 2),   # Float16
    2: ('<B', 1),   # UNorm8
    3: ('<b', 1),   # SNorm8
    4: ('<I', 4),   # UInt32
    5: ('<H', 2),   # UInt16
    6: ('<H', 2),   # UNorm16
    7: ('<B', 1),   # UInt8
    8: ('<h', 2),   # SNorm16
    9: ('<i', 4),   # SInt32
}


def _channel_value(raw: bytes, offset: int, fmt: int, dim: int, vertex: int, vertex_size: int):
    code, size = _VERTEX_FORMATS[fmt]
    base = vertex * vertex_size + offset
    vals = []
    for d in range(dim):
        v = struct.unpack_from(code, raw, base + d * size)[0]
        if fmt == 2:
            v = v / 255.0
        elif fmt == 3:
            v = max(-1.0, v / 127.0)
        elif fmt in (6,):
            v = v / 65535.0
        elif fmt == 8:
            v = max(-1.0, v / 32767.0)
        vals.append(v)
    return vals


def _matrix_from(values) -> list[float]:
    flat = []
    for v in values:
        if isinstance(v, (list, tuple)):
            flat.extend(v)
        else:
            flat.append(float(v))
    if len(flat) != 16:
        raise UnityConvertError('bad bind matrix')
    return flat


def _apply_matrix(m: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
    # Unity Matrix4x4 is column-major: m[col*4+row]
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _matrix_rotation(m: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
    return (m[0] * x + m[4] * y + m[8] * z,
            m[1] * x + m[5] * y + m[9] * z,
            m[2] * x + m[6] * y + m[10] * z)


def _raw_bytes(value) -> bytes:
    if isinstance(value, _RawArray):
        return value.data
    if isinstance(value, str):
        return value.encode('latin1')
    if isinstance(value, list):
        return bytes(int(x) & 0xFF for x in value)
    return b''


# VertexData channel list position == Unity semantic index:
# 0 position, 1 normal, 2 tangent, 3 color, 4..7 texcoord0..3,
# 8/21 blend weights, 9/22 blend indices.
_CHANNEL_SEMANTICS = {
    0: 'POSITION', 1: 'NORMAL', 2: 'TANGENT', 3: 'COLOR',
    4: 'TEXCOORD0', 5: 'TEXCOORD1', 6: 'TEXCOORD2', 7: 'TEXCOORD3',
    8: 'BLENDWEIGHT', 9: 'BLENDINDICES', 21: 'BLENDWEIGHT', 22: 'BLENDINDICES',
}


def _parse_vertex_data(vd: dict, blob: bytes) -> tuple[int, dict]:
    """Unity 2017-2020 VertexData: one packed blob, per-stream interleaved
    channels.  Returns (vertex_count, {SEMANTIC: (stream_raw, offset, fmt,
    dim, stream_stride)})."""
    channels = vd.get('m_Channels') or []
    vcount = int(vd.get('m_VertexCount') or 0)
    if not vcount or not channels:
        raise UnityConvertError('mesh has no vertex data')

    def valid_dim(ch_dim: int) -> int:
        dim = int(ch_dim or 0)
        return min(dim, 4) if 0 < dim <= 4 else (4 if dim > 4 else 0)

    # per-stream stride: channels are interleaved within each stream, and the
    # stream buffers are laid out one after another inside the blob.
    strides: dict[int, int] = {}
    for ch in channels:
        dim = valid_dim(ch.get('dimension'))
        if not dim:
            continue
        stream = int(ch.get('stream') or 0)
        fmt = int(ch.get('format') or 0)
        size = _VERTEX_FORMATS.get(fmt, ('<f', 4))[1]
        end = int(ch.get('offset') or 0) + dim * size
        strides[stream] = max(strides.get(stream, 0), end)
    streams: dict[int, bytes] = {}
    pos = 0
    for stream in sorted(strides):
        span = strides[stream] * vcount
        streams[stream] = blob[pos:pos + span]
        pos += span
    channel_data: dict[str, tuple] = {}
    for idx, ch in enumerate(channels):
        semantic = _CHANNEL_SEMANTICS.get(idx)
        dim = valid_dim(ch.get('dimension'))
        if not semantic or not dim or semantic in channel_data:
            continue
        stream = int(ch.get('stream') or 0)
        raw = streams.get(stream)
        if not raw:
            continue
        channel_data[semantic] = (raw, int(ch.get('offset') or 0),
                                  int(ch.get('format') or 0), dim,
                                  strides[stream])
    return vcount, channel_data


def _parse_vertex_buffers(vd: dict) -> tuple[int, dict]:
    """Newer Unity layout: explicit m_VertexBuffers with per-buffer m_VertexSize."""
    channels = vd.get('m_Channels') or []
    buffers = vd.get('m_VertexBuffers') or []
    vcount = int(vd.get('m_VertexCount') or 0)
    channel_data: dict[str, tuple] = {}
    for ch in channels:
        subtype = str(ch.get('m_SubType') or '').upper()
        if not subtype:
            continue
        stream = int(ch.get('m_Stream') or 0)
        if stream >= len(buffers):
            continue
        buf = buffers[stream]
        raw = _raw_bytes(buf.get('m_Data'))
        vertex_size = int(buf.get('m_VertexSize') or 0)
        if not vertex_size or not raw:
            continue
        vcount = max(vcount, len(raw) // vertex_size)
        channel_data[subtype] = (raw, int(ch.get('m_Offset') or 0),
                                 int(ch.get('m_Format') or 0),
                                 int(ch.get('m_Dimension') or 3), vertex_size)
    return vcount, channel_data


def _orient_outward(positions: list, indices: list | None) -> None:
    """Make front faces CCW-outward for glTF's right-handed convention.

    Unity stores front faces clockwise in left-handed space, and the Z-up
    relabel above is a reflection; either can leave the exported triangle
    soup inward-facing, which renders as a hollow, unlit shell in three.js.
    The signed volume of the triangle soup reveals the orientation (closed
    tabletop figures make this reliable). Flipping the index order keeps the
    (correct, outward) vertex normals aligned.
    """
    if not indices or not positions:
        return
    volume = 0.0
    for i in range(0, len(indices), 3):
        a = positions[indices[i]]
        b = positions[indices[i + 1]]
        c = positions[indices[i + 2]]
        cx = b[1] * c[2] - b[2] * c[1]
        cy = b[2] * c[0] - b[0] * c[2]
        cz = b[0] * c[1] - b[1] * c[0]
        volume += (a[0] * cx + a[1] * cy + a[2] * cz) / 6.0
    if volume >= 0:
        return
    for i in range(0, len(indices), 3):
        indices[i + 1], indices[i + 2] = indices[i + 2], indices[i + 1]


def parse_mesh(mesh: dict, sf: SerializedFile | None = None) -> MeshData:
    vd = mesh.get('m_VertexData') or {}
    if vd.get('m_VertexBuffers') is not None:
        vertex_count, channel_data = _parse_vertex_buffers(vd)
    else:
        blob = _raw_bytes(vd.get('m_DataSize'))
        if not blob and sf is not None:
            blob = sf.stream_data(mesh.get('m_StreamData') or {})
        vertex_count, channel_data = _parse_vertex_data(vd, blob)
    if 'POSITION' not in channel_data or not vertex_count:
        raise UnityConvertError('mesh has no POSITION channel')

    def read_channel(subtype, vertex):
        entry = channel_data.get(subtype)
        if not entry:
            return None
        raw, offset, fmt, dim, vsize = entry
        return _channel_value(raw, offset, fmt, dim, vertex, vsize)

    positions = []
    for v in range(vertex_count):
        p = read_channel('POSITION', v)
        positions.append((p[0], p[1], p[2]))

    bind_pose = [_matrix_from(m) for m in (mesh.get('m_BindPose') or []) if m]
    blend_idx = None
    blend_w = None
    if bind_pose and 'BLENDINDICES' in channel_data and 'BLENDWEIGHT' in channel_data:
        blend_idx = [read_channel('BLENDINDICES', v) for v in range(vertex_count)]
        blend_w = [read_channel('BLENDWEIGHT', v) for v in range(vertex_count)]

    if blend_idx and blend_w and any(blend_idx) and any(blend_w):
        posed = []
        for v in range(vertex_count):
            idx = blend_idx[v] or [0, 0, 0, 0]
            wts = blend_w[v] or [1, 0, 0, 0]
            x, y, z = positions[v]
            px = py = pz = 0.0
            for slot in range(4):
                wi = int(idx[slot]) if slot < len(idx) else 0
                w = float(wts[slot]) if slot < len(wts) else 0.0
                if not w or wi >= len(bind_pose):
                    continue
                m = bind_pose[wi]
                qx, qy, qz = _apply_matrix(m, x, y, z)
                px += w * qx
                py += w * qy
                pz += w * qz
            posed.append((px, py, pz))
        positions = posed

    normals = None
    if 'NORMAL' in channel_data:
        nrm = []
        for v in range(vertex_count):
            n = read_channel('NORMAL', v)
            ln = sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
            nrm.append((n[0] / ln, n[1] / ln, n[2] / ln))
        normals = nrm

    # Battle-forum style exports can stand the figure up on the Z axis.
    # The relabel (x, y, z) -> (x, z, y) is a reflection (det -1): it puts
    # the model upright in glTF's Y-up space but mirrors the geometry.
    # Triangle winding is normalized once below, after the index buffer is
    # parsed (Unity itself stores CW front faces in left-handed space).
    aabb = mesh.get('m_LocalAABB') or {}
    ext = aabb.get('m_Extent') or {}
    try:
        ex, ey, ez = (float(ext.get('x') or 0), float(ext.get('y') or 0),
                      float(ext.get('z') or 0))
    except (TypeError, ValueError):
        ex = ey = ez = 0.0
    if ez > ex and ez > ey:
        positions = [(x, z, y) for (x, y, z) in positions]
        if normals:
            normals = [(nx, nz, ny) for (nx, ny, nz) in normals]

    uvs = None
    uv_name = None
    for cand in ('TEXCOORD0', 'TEXCOORD1', 'TEXCOORD2', 'TEXCOORD3'):
        if cand in channel_data:
            uv_name = cand
            break
    if uv_name:
        uvs = []
        for v in range(vertex_count):
            uv = read_channel(uv_name, v)
            # Unity UV origin is bottom-left; glTF expects top-left.
            uvs.append((uv[0], 1.0 - uv[1]))

    ib_fmt = int(mesh.get('m_IndexFormat') or vd.get('m_IndexFormat') or 0)
    raw_idx = _raw_bytes(mesh.get('m_IndexBuffer')
                         or (vd.get('m_IndexBuffer') or {}).get('m_Data'))
    indices = None
    if raw_idx:
        code, size = {0: ('<H', 2), 1: ('<I', 4), 2: ('<I', 4)}.get(ib_fmt, ('<H', 2))
        count = len(raw_idx) // size
        indices = [struct.unpack_from(code, raw_idx, i * size)[0] for i in range(count)]
        # strip any trailing padding to a multiple of 3
        indices = indices[:len(indices) - (len(indices) % 3)]

    _orient_outward(positions, indices)

    return MeshData(name=str(mesh.get('m_Name') or 'mesh'), positions=positions,
                    indices=indices, normals=normals, uvs=uvs)


# ---------------------------------------------------------------------------
# assembly: renderer -> mesh + material -> texture
# ---------------------------------------------------------------------------

def _texture_from_material(sf: SerializedFile, material: dict) -> bytes | None:
    props = material.get('m_SavedProperties') or {}
    envs = props.get('m_TexEnvs') or []

    def entry_key(entry):
        if isinstance(entry, dict):
            return str(entry.get('first') or entry.get('key') or '')
        if isinstance(entry, list) and entry:
            return str(entry[0] or '')
        return ''

    def entry_pptr(entry):
        if isinstance(entry, dict):
            value = entry.get('second') or entry.get('value')
        elif isinstance(entry, list) and len(entry) > 1:
            value = entry[1]
        else:
            return None
        if isinstance(value, dict) and 'm_Texture' in value:
            value = value.get('m_Texture')  # UnityTexEnv wrapper
        return value

    # diffuse first, then anything else that resolves
    order = sorted(envs, key=lambda e: 0 if 'maintex' in entry_key(e).lower()
                   else (1 if any(k in entry_key(e).lower()
                                  for k in ('diffuse', 'color', 'bump')) else 2))
    for entry in order:
        path_id = sf.deref(entry_pptr(entry))
        if path_id is None:
            continue
        obj = sf.objects.get(path_id)
        if not obj or not obj['type']:
            continue
        if obj['type']['type_name'] != 'Texture2D':
            continue
        try:
            tex = sf.parse(path_id)
            _w, _h, png = texture_png(sf, tex)
            return ('image/png', png)
        except UnityConvertError:
            continue
    return None


def apply_material_hints(material, mat_dict: dict) -> None:
    """Copy m_Colors / m_Floats hints (base color, metallic, smoothness)."""
    props = mat_dict.get('m_SavedProperties') or {}

    def pairs(container):
        out = {}
        for entry in container or []:
            if isinstance(entry, dict):
                out[str(entry.get('first') or '')] = entry.get('second')
            elif isinstance(entry, list) and len(entry) > 1:
                out[str(entry[0] or '')] = entry[1]
        return out

    colors = pairs(props.get('m_Colors'))
    for key in ('_Color', 'm_Color', '_BaseColor'):
        c = colors.get(key)
        if isinstance(c, dict):
            try:
                material.color = (float(c.get('r') or 1), float(c.get('g') or 1),
                                  float(c.get('b') or 1),
                                  float(c.get('a') if c.get('a') is not None else 1.0))
                break
            except (TypeError, ValueError):
                pass
    floats = pairs(props.get('m_Floats'))
    try:
        if floats.get('_Metallic') is not None:
            material.metallic = max(0.0, min(1.0, float(floats['_Metallic'])))
        if floats.get('_Glossiness') is not None:
            material.roughness = max(0.0, min(1.0, 1.0 - float(floats['_Glossiness'])))
    except (TypeError, ValueError):
        pass


def bundle_to_glb(data: bytes) -> bytes:
    from .glb import GLBBuilder

    files = read_bundle(data)
    cab_name = None
    resS = b''
    for name, payload in files.items():
        if name.endswith('.resS') or name.endswith('.resource'):
            resS = payload
        else:
            cab_name = name
    if cab_name is None:
        raise UnityConvertError('no SerializedFile found in bundle')
    sf = SerializedFile(files[cab_name], resS)

    renderers = []
    mesh_filters = []
    skinned = []
    for path_id, obj in sf.objects.items():
        tname = obj['type']['type_name'] if obj['type'] else ''
        if tname not in ('MeshRenderer', 'SkinnedMeshRenderer', 'MeshFilter'):
            continue
        try:
            parsed = sf.parse(path_id)
        except UnityConvertError:
            continue
        if tname == 'MeshRenderer':
            renderers.append(parsed)
        elif tname == 'MeshFilter':
            mesh_filters.append(parsed)
        else:
            skinned.append(parsed)

    # pair MeshFilters with the MeshRenderer on the same GameObject to get
    # (mesh, materials) pairs; SkinnedMeshRenderers carry both themselves
    mats_by_go = {}
    for rend in renderers:
        go = sf.deref(rend.get('m_GameObject'))
        if go is not None:
            mats_by_go[go] = rend.get('m_Materials') or []
    pairs = []  # (mesh_pptr, materials)
    for mf in mesh_filters:
        go = sf.deref(mf.get('m_GameObject'))
        pairs.append((mf.get('m_Mesh'), mats_by_go.get(go) or []))
    for rend in skinned:
        pairs.append((rend.get('m_Mesh'), rend.get('m_Materials') or []))
    if not pairs and renderers:
        # last resort: any mesh + the first renderer's materials
        pairs = [(None, rend.get('m_Materials') or []) for rend in renderers[:1]]

    builder = GLBBuilder()
    exported = set()
    for mesh_pptr, materials in pairs:
        mesh_id = sf.deref(mesh_pptr)
        if mesh_id is None or mesh_id in exported:
            continue
        exported.add(mesh_id)
        try:
            mesh = parse_mesh(sf.parse(mesh_id), sf)
        except UnityConvertError:
            continue
        if not mesh.positions:
            continue
        material = Material(color=(1, 1, 1, 1), metallic=0.0, roughness=0.8)
        for m_pptr in (materials or []):
            mid = sf.deref(m_pptr)
            if mid is None:
                continue
            mobj = sf.objects.get(mid)
            if not mobj or not mobj['type'] or mobj['type']['type_name'] != 'Material':
                continue
            try:
                mat_dict = sf.parse(mid)
            except UnityConvertError:
                continue
            apply_material_hints(material, mat_dict)
            tex = _texture_from_material(sf, mat_dict)
            if tex:
                material.texture = tex
            break
        mesh.material = material
        builder.add_mesh(mesh)

    if not builder.meshes:
        # fallback: export raw Mesh objects with default material
        for path_id, obj in sf.objects.items():
            tname = obj['type']['type_name'] if obj['type'] else ''
            if tname != 'Mesh':
                continue
            try:
                mesh = parse_mesh(sf.parse(path_id), sf)
            except UnityConvertError:
                continue
            if mesh.positions:
                builder.add_mesh(mesh)
    if not builder.meshes:
        raise UnityConvertError('no meshes could be extracted from bundle')
    return builder.build()
