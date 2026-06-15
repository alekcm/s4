"""Parser for Sims 4 MODL/MLOD object resources (furniture) inside RCOL.

Reverse-engineered against real Sims 4 packages. Key real-world findings:

* MLOD version 0x0205 group records are variable-length; we use ``subset_bytes``
  (the first DWORD of each group) to jump reliably to the next group.
* VRTF uses D3DDECLUSAGE codes: 0=Position, 2=Normal, 3=UV (NOT the GEOM
  numbering). Positions are commonly Half4 (use xyz).
* VBUF header = 16 bytes (sig+ver+flags+swizzleRef), then interleaved verts.
* IBUF version 2 has a 16-byte header and stores **delta-encoded** 16-bit
  indices (each value is a signed delta added to a running accumulator).
* Chunk references in the group record are resolved by verifying the target
  chunk's 4-byte signature (the raw ref numbering varies between files).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


# D3DDECLUSAGE codes used by VRTF
USAGE_POSITION = 0
USAGE_BLENDWEIGHT = 1
USAGE_BLENDINDICES = 2  # NOTE: some files use 2 for normal; we disambiguate
USAGE_NORMAL = 2
USAGE_UV = 3            # TEXCOORD
USAGE_TANGENT = 6
USAGE_COLOR = 4

# VRTF data format -> byte size. (Half-based formats are the common case in TS4.)
# Sizes are derived from real files; when unknown we infer from element offsets.
VRTF_FORMAT_SIZE = {
    1: 4,    # Float1
    2: 8,    # Float2
    3: 12,   # Float3
    4: 4,    # (UV) packed - 4 bytes in observed files
    5: 4,    # UByte4 / packed
    6: 4,    # ColorUByte4 / packed normal
    7: 8,    # Half4 (position)
    8: 4,    # Short2 / Half2 (4 bytes)
    9: 4,    # Half2
    10: 8,   # Half4
}


@dataclass
class VElement:
    usage: int
    usage_index: int
    fmt: int
    offset: int
    size: int = 0


@dataclass
class VRTF:
    stride: int
    elements: list = field(default_factory=list)


@dataclass
class ObjMesh:
    name: str = "object"
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uvs: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    bbox_min: tuple = (0.0, 0.0, 0.0)
    bbox_max: tuple = (0.0, 0.0, 0.0)

    @property
    def vertex_count(self):
        return len(self.positions)

    @property
    def face_count(self):
        return len(self.faces)


class RCOL:
    def __init__(self, data: bytes):
        self.data = data
        self.internal_chunks = []  # (instance, type, group, pos, size)
        self._parse_header()

    def _parse_header(self):
        d = self.data
        pos = 0
        version, public_count, index3, external_count, internal_count = \
            struct.unpack_from("<IIIII", d, pos)
        pos += 20
        internal_tgis = []
        for _ in range(internal_count):
            inst = struct.unpack_from("<Q", d, pos)[0]; pos += 8
            tid = struct.unpack_from("<I", d, pos)[0]; pos += 4
            gid = struct.unpack_from("<I", d, pos)[0]; pos += 4
            internal_tgis.append((inst, tid, gid))
        pos += external_count * 16
        for i in range(internal_count):
            cpos = struct.unpack_from("<I", d, pos)[0]; pos += 4
            csize = struct.unpack_from("<I", d, pos)[0]; pos += 4
            inst, tid, gid = internal_tgis[i]
            self.internal_chunks.append((inst, tid, gid, cpos, csize))

    def chunk_bytes(self, idx: int) -> bytes:
        _, _, _, cpos, csize = self.internal_chunks[idx]
        return self.data[cpos:cpos + csize]

    def chunk_sig(self, idx: int) -> bytes:
        cpos = self.internal_chunks[idx][3]
        return self.data[cpos:cpos + 4]

    def find_chunks_by_sig(self, sig: bytes) -> list:
        return [i for i in range(len(self.internal_chunks))
                if self.chunk_sig(i) == sig]


def parse_vrtf(data: bytes) -> VRTF:
    if data[0:4] != b"VRTF":
        raise ValueError("not VRTF")
    pos = 4
    version = struct.unpack_from("<I", data, pos)[0]; pos += 4
    stride = struct.unpack_from("<I", data, pos)[0]; pos += 4
    count = struct.unpack_from("<I", data, pos)[0]; pos += 4
    extended = struct.unpack_from("<I", data, pos)[0]; pos += 4
    els = []
    for _ in range(count):
        if extended:
            usage = struct.unpack_from("<I", data, pos)[0]; pos += 4
            uidx = struct.unpack_from("<I", data, pos)[0]; pos += 4
            fmt = struct.unpack_from("<I", data, pos)[0]; pos += 4
            offset = struct.unpack_from("<I", data, pos)[0]; pos += 4
        else:
            usage = data[pos]; uidx = data[pos + 1]
            fmt = data[pos + 2]; offset = data[pos + 3]; pos += 4
        els.append(VElement(usage, uidx, fmt, offset))
    # Infer sizes from consecutive offsets (more reliable than a fixed table).
    els_sorted = sorted(els, key=lambda e: e.offset)
    for i, e in enumerate(els_sorted):
        if i + 1 < len(els_sorted):
            e.size = els_sorted[i + 1].offset - e.offset
        else:
            e.size = stride - e.offset
        if e.size <= 0:
            e.size = VRTF_FORMAT_SIZE.get(e.fmt, 4)
    return VRTF(stride=stride, elements=els)


def _read_half(buf, off):
    return struct.unpack_from("<e", buf, off)[0]


def _read_position(buf, base, el):
    """Decode a vertex position.

    Real Sims 4 object meshes (fmt 7, 8-byte element) store the position as
    **Short4 normalized**: (x, y, z, w) as int16. The actual coordinate is
    ``component / 32767 / w_norm`` where ``w_norm = w/32767`` is a per-vertex
    scale (commonly 0.5 -> overall scale 2). We compute the scale from w so we
    don't hardcode it. Float3 and Half3 are also supported as fallbacks.
    """
    off = base + el.offset

    # Float3 (12 bytes)
    if el.fmt == 3 or el.size >= 12:
        return struct.unpack_from("<3f", buf, off)

    # 8-byte element: Short4 normalized (the real TS4 object format)
    if el.size >= 8:
        sx, sy, sz, sw = struct.unpack_from("<4h", buf, off)
        w = sw / 32767.0
        scale = (1.0 / w) if abs(w) > 1e-6 else 1.0
        return (sx / 32767.0 * scale,
                sy / 32767.0 * scale,
                sz / 32767.0 * scale)

    # 6-byte: Half3
    if el.size >= 6:
        x = _read_half(buf, off)
        y = _read_half(buf, off + 2)
        z = _read_half(buf, off + 4)
        return (float(x), float(y), float(z))

    # 4-byte fallback: Half2
    x = _read_half(buf, off)
    y = _read_half(buf, off + 2)
    return (float(x), float(y), 0.0)


def _read_uv(buf, base, el):
    """UV best-effort: try half2, else short2 normalized."""
    try:
        if el.size >= 8:
            u, v = struct.unpack_from("<2f", buf, base + el.offset)
            return (float(u), float(v))
        u = _read_half(buf, base + el.offset)
        v = _read_half(buf, base + el.offset + 2)
        if u != u or v != v:  # nan
            raise ValueError
        return (float(u), float(v))
    except Exception:
        return (0.0, 0.0)


def _decode_ibuf(ibuf: bytes, count_indices: int, start_index: int):
    """Decode an IBUF chunk. v2 uses 16-byte header + delta-encoded 16-bit."""
    if ibuf[0:4] != b"IBUF":
        raise ValueError("not IBUF")
    version = struct.unpack_from("<I", ibuf, 4)[0]
    # header: sig(4)+ver(4)+flags(4)+displayListUsage(4) for v2; v1 = 12 bytes
    header = 16 if version >= 2 else 12
    # First decode ALL indices (delta), then slice the group's range, because
    # delta accumulator runs across the whole buffer.
    total = (len(ibuf) - header) // 2
    raw = struct.unpack_from(f"<{total}h", ibuf, header)
    decoded = []
    acc = 0
    for d in raw:
        acc += d
        decoded.append(acc)
    return decoded[start_index:start_index + count_indices]


def parse_object_mesh(rcol: RCOL, name: str = "object") -> list:
    mlod_idx = rcol.find_chunks_by_sig(b"MLOD")
    if not mlod_idx:
        mlod_idx = rcol.find_chunks_by_sig(b"MODL")
    if not mlod_idx:
        raise ValueError("no MLOD/MODL chunk")
    mlod = rcol.chunk_bytes(mlod_idx[0])

    pos = 4
    version = struct.unpack_from("<I", mlod, pos)[0]; pos += 4
    group_count = struct.unpack_from("<I", mlod, pos)[0]; pos += 4

    meshes = []
    for g in range(group_count):
        grp_start = pos
        subset_bytes = struct.unpack_from("<I", mlod, pos)[0]
        next_group = grp_start + 4 + subset_bytes
        p = pos + 4
        try:
            name_hash = struct.unpack_from("<I", mlod, p)[0]; p += 4
            mat_ref = struct.unpack_from("<I", mlod, p)[0]; p += 4
            vrtf_ref = struct.unpack_from("<I", mlod, p)[0]; p += 4
            vbuf_ref = struct.unpack_from("<I", mlod, p)[0]; p += 4
            ibuf_ref = struct.unpack_from("<I", mlod, p)[0]; p += 4
            flags = struct.unpack_from("<I", mlod, p)[0]; p += 4
            stream_offset = struct.unpack_from("<I", mlod, p)[0]; p += 4
            start_vertex = struct.unpack_from("<I", mlod, p)[0]; p += 4
            start_index = struct.unpack_from("<I", mlod, p)[0]; p += 4
            min_vertex = struct.unpack_from("<I", mlod, p)[0]; p += 4
            vertex_count = struct.unpack_from("<I", mlod, p)[0]; p += 4
            primitive_count = struct.unpack_from("<I", mlod, p)[0]; p += 4
            bbox = struct.unpack_from("<6f", mlod, p); p += 24

            mesh = _build_group_mesh(
                rcol, f"{name}_g{g:02d}",
                vrtf_ref, vbuf_ref, ibuf_ref,
                start_vertex, vertex_count,
                start_index, primitive_count, stream_offset)
            mesh.bbox_min = (bbox[0], bbox[1], bbox[2])
            mesh.bbox_max = (bbox[3], bbox[4], bbox[5])
            if mesh.vertex_count and mesh.face_count:
                meshes.append(mesh)
        except Exception:
            pass
        pos = next_group

    return meshes


def _resolve(rcol, *refs_and_sig):
    """Resolve an RCOL chunk reference and verify its signature.

    Real Sims 4 references are ``ref & 0x0FFFFFFF`` used as a 0-based index into
    the internal chunk list (the high nibble is a reference-type flag). We try
    that first, then a couple of alternative encodings, and finally fall back to
    a signature search.
    """
    *refs, sig = refs_and_sig
    primary = refs[0] & 0x0FFFFFFF
    n = len(rcol.internal_chunks)

    # The reference's base index can be shifted by ±1 depending on whether a
    # leading MODL chunk is present in this resource. Try the masked index and
    # its near neighbours, accepting only a matching signature.
    for idx in (primary, primary + 1, primary - 1):
        if 0 <= idx < n and rcol.chunk_sig(idx) == sig:
            return rcol.chunk_bytes(idx)

    # Other supplied refs (same neighbour search).
    for ref in refs[1:]:
        base = ref & 0x0FFFFFFF
        for idx in (base, base + 1, base - 1):
            if 0 <= idx < n and rcol.chunk_sig(idx) == sig:
                return rcol.chunk_bytes(idx)

    # Fallback: pick the LARGEST chunk with this signature (the real mesh
    # buffer rather than a tiny dropshadow buffer).
    found = rcol.find_chunks_by_sig(sig)
    if found:
        found.sort(key=lambda i: len(rcol.chunk_bytes(i)), reverse=True)
        return rcol.chunk_bytes(found[0])
    raise ValueError(f"cannot resolve {sig!r}")


def _build_group_mesh(rcol, name, vrtf_ref, vbuf_ref, ibuf_ref,
                      start_vertex, vertex_count, start_index, primitive_count,
                      stream_offset) -> ObjMesh:
    # Resolve by signature: real files offset the refs, so we try the three
    # neighbouring refs and verify against the expected chunk signature.
    vrtf_b = _resolve(rcol, vrtf_ref, vbuf_ref, ibuf_ref, b"VRTF")
    vbuf_b = _resolve(rcol, vbuf_ref, ibuf_ref, vrtf_ref, b"VBUF")
    ibuf_b = _resolve(rcol, ibuf_ref, vbuf_ref, vrtf_ref, b"IBUF")

    vrtf = parse_vrtf(vrtf_b)
    mesh = ObjMesh(name=name)

    stride = vrtf.stride
    vbuf_data = 16  # sig+ver+flags+swizzle

    pos_el = norm_el = uv_el = None
    for el in vrtf.elements:
        if el.usage == USAGE_POSITION and pos_el is None:
            pos_el = el
        elif el.usage == USAGE_UV and uv_el is None:
            uv_el = el
        elif el.usage == 2 and norm_el is None:
            norm_el = el

    base0 = vbuf_data + stream_offset + start_vertex * stride
    for vi in range(vertex_count):
        base = base0 + vi * stride
        if base + stride > len(vbuf_b):
            break
        if pos_el is not None:
            mesh.positions.append(_read_position(vbuf_b, base, pos_el))
        else:
            mesh.positions.append((0.0, 0.0, 0.0))
        if uv_el is not None:
            mesh.uvs.append(_read_uv(vbuf_b, base, uv_el))

    num_indices = primitive_count * 3
    indices = _decode_ibuf(ibuf_b, num_indices, start_index)

    vc = len(mesh.positions)
    for i in range(0, len(indices) - 2, 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if 0 <= a < vc and 0 <= b < vc and 0 <= c < vc:
            mesh.faces.append((a, b, c))

    # drop UVs if they came out all-zero/nan (keeps OBJ/FBX clean)
    if mesh.uvs and all(u == (0.0, 0.0) for u in mesh.uvs):
        mesh.uvs = []

    return mesh
