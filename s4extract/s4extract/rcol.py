"""Parser for Sims 4 MODL/MLOD object resources (furniture) inside RCOL.

Important real-world findings:

* MLOD version 0x0205 group records are variable-length; we use ``subset_bytes``
  (the first DWORD of each group) to jump reliably to the next group.
* Real Sims 4 object VRTF layouts use the **Sims 4 / s4pi semantic enum**, not
  the D3DDECLUSAGE values some older reverse-engineering notes assumed.
  In common furniture files that means:

    - usage 0 = Position
    - usage 1 = Normal
    - usage 2 = UV (channel selected by ``usage_index``)
    - usage 3 = BlendIndex
    - usage 4 = BlendWeight
    - usage 5 = Tangent
    - usage 6 = Colour

  Older builds in this repo treated usage 5 as TEXCOORD and therefore read the
  tangent bytes as UVs. That is the root cause of the "material scale does not
  match the mesh" problem the user reported.
* VBUF header = 16 bytes (sig+ver+flags+swizzleRef), then interleaved verts.
* In the common TS4 object layout, UVs are stored as **Short2** and must be
  multiplied by the material ``UVScales`` parameter (or 1/32767 fallback).
* IBUF version 2 has a 16-byte header and stores **delta-encoded** 16-bit
  indices (each value is a signed delta added to a running accumulator).
* Chunk references in the group record are resolved by verifying the target
  chunk's 4-byte signature (the raw ref numbering varies between files).

We keep a small compatibility path for the repo's older synthetic fixture,
which used a legacy usage/format mapping.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Real Sims 4 / s4pi object VRTF semantic usage codes
# ---------------------------------------------------------------------------
S4_USAGE_POSITION = 0
S4_USAGE_NORMAL = 1
S4_USAGE_UV = 2
S4_USAGE_BLENDINDEX = 3
S4_USAGE_BLENDWEIGHT = 4
S4_USAGE_TANGENT = 5
S4_USAGE_COLOR = 6

# Real Sims 4 / s4pi object VRTF element formats
S4_FMT_FLOAT1 = 0
S4_FMT_FLOAT2 = 1
S4_FMT_FLOAT3 = 2
S4_FMT_FLOAT4 = 3
S4_FMT_UBYTE4 = 4
S4_FMT_COLOR_UBYTE4 = 5
S4_FMT_SHORT2 = 6
S4_FMT_SHORT4 = 7
S4_FMT_UBYTE4N = 8
S4_FMT_SHORT2N = 9
S4_FMT_SHORT4N = 10
S4_FMT_USHORT2N = 11
S4_FMT_USHORT4N = 12
S4_FMT_DEC3N = 13
S4_FMT_UDEC3N = 14
S4_FMT_FLOAT16_2 = 15
S4_FMT_FLOAT16_4 = 16

# ---------------------------------------------------------------------------
# Sims 4 shader IDs
# ---------------------------------------------------------------------------
# Drop-shadow / shadow-map helper shaders (already filtered in parse_object_mesh)
SHADER_DROP_SHADOW = 0xC09C7582
SHADER_SHADOW_MAP = 0x21FE207D

# CAS shaders — human skin / hair / clothing materials
CAS_SHADER_HUMAN_SKIN = 0x37CE2622
CAS_SHADER_HUMAN_HAIR = 0x7647A8B3
CAS_SHADER_MATERIAL = 0xA282F69F
CAS_SHADER_HAIR = 0x6B7D2B2D
CAS_SHADER_SKIN = 0x1D29B0C3

CAS_SHADERS = frozenset({
    CAS_SHADER_HUMAN_SKIN,
    CAS_SHADER_HUMAN_HAIR,
    CAS_SHADER_MATERIAL,
    CAS_SHADER_HAIR,
    CAS_SHADER_SKIN,
})


def is_cas_shader(shader_id: int | None) -> bool:
    """Return True if the shader is a CAS (human body/hair/clothing) shader.
    
    Sims 4 uses distinct shader IDs for CAS resources vs object/build resources.
    This function checks against the known CAS shader list.
    """
    if shader_id is None:
        return False
    return shader_id in CAS_SHADERS


# ---------------------------------------------------------------------------
# Legacy compatibility path used by this repo's old synthetic fixture
# ---------------------------------------------------------------------------
LEGACY_USAGE_POSITION = 1
LEGACY_USAGE_NORMAL = 2
LEGACY_USAGE_UV = 3

# VRTF data format -> byte size. For real TS4 object files the values match the
# s4pi VRTF.ElementFormat enum above. We keep a couple of legacy entries so the
# synthetic fixture built for an older parser still loads.
VRTF_FORMAT_SIZE = {
    # real TS4 / s4pi layout
    S4_FMT_FLOAT1: 4,
    S4_FMT_FLOAT2: 8,
    S4_FMT_FLOAT3: 12,
    S4_FMT_FLOAT4: 16,
    S4_FMT_UBYTE4: 4,
    S4_FMT_COLOR_UBYTE4: 4,
    S4_FMT_SHORT2: 4,
    S4_FMT_SHORT4: 8,
    S4_FMT_UBYTE4N: 4,
    S4_FMT_SHORT2N: 4,
    S4_FMT_SHORT4N: 8,
    S4_FMT_USHORT2N: 4,
    S4_FMT_USHORT4N: 8,
    S4_FMT_DEC3N: 4,
    S4_FMT_UDEC3N: 4,
    S4_FMT_FLOAT16_2: 4,
    S4_FMT_FLOAT16_4: 8,
    # legacy synthetic-fixture aliases
    1: 4,
    2: 8,
    3: 12,
    4: 4,
    5: 4,
    6: 4,
    7: 8,
    8: 4,
    9: 4,
    10: 8,
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
    material_ref: int | None = None

    @property
    def vertex_count(self):
        return len(self.positions)

    @property
    def face_count(self):
        return len(self.faces)


@dataclass
class MaterialVariant:
    variant_id: int = 0
    shader: int | None = None
    diffuse_key: tuple[int, int, int] | None = None   # (type, group, instance)
    normal_key: tuple[int, int, int] | None = None
    specular_key: tuple[int, int, int] | None = None
    emission_key: tuple[int, int, int] | None = None
    uv_scale: float | None = None


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


def _vrtf_scheme(vrtf: VRTF) -> str:
    usages = {el.usage for el in vrtf.elements}
    return "s4" if S4_USAGE_POSITION in usages else "legacy"


def _read_position(buf, base, el, scheme: str):
    off = base + el.offset

    # Legacy synthetic fixture path used by old tests in this repo.
    if scheme == "legacy":
        if el.fmt == 3 or el.size >= 12:
            return struct.unpack_from("<3f", buf, off)
        if el.size >= 8:
            sx, sy, sz, sw = struct.unpack_from("<4h", buf, off)
            w = sw / 32767.0
            scale = (1.0 / w) if abs(w) > 1e-6 else 1.0
            return (sx / 32767.0 * scale,
                    sy / 32767.0 * scale,
                    sz / 32767.0 * scale)
        if el.size >= 6:
            x = _read_half(buf, off)
            y = _read_half(buf, off + 2)
            z = _read_half(buf, off + 4)
            return (float(x), float(y), float(z))
        x = _read_half(buf, off)
        y = _read_half(buf, off + 2)
        return (float(x), float(y), 0.0)

    # Real TS4 object meshes: common position format is Short4 with the 4th
    # component acting as a scalar. This matches Sims4Tools / s4pi.
    if el.fmt in (S4_FMT_SHORT4, S4_FMT_USHORT4N) and el.size >= 8:
        sx, sy, sz, sw = struct.unpack_from("<4h", buf, off)
        scalar = float(sw)
        if abs(scalar) < 1e-6:
            scalar = 32767.0
        return (sx / scalar, sy / scalar, sz / scalar)

    if el.fmt == S4_FMT_FLOAT3 and el.size >= 12:
        return struct.unpack_from("<3f", buf, off)

    if el.fmt == S4_FMT_FLOAT16_4 and el.size >= 8:
        x = _read_half(buf, off)
        y = _read_half(buf, off + 2)
        z = _read_half(buf, off + 4)
        return (float(x), float(y), float(z))

    # Best-effort fallback.
    if el.size >= 12:
        return struct.unpack_from("<3f", buf, off)
    if el.size >= 8:
        sx, sy, sz, sw = struct.unpack_from("<4h", buf, off)
        scalar = float(sw) if abs(float(sw)) > 1e-6 else 32767.0
        return (sx / scalar, sy / scalar, sz / scalar)
    return (0.0, 0.0, 0.0)


def _read_uv(buf, base, el, uv_scale: float, scheme: str):
    off = base + el.offset
    try:
        if scheme == "legacy":
            if el.size >= 8:
                u, v = struct.unpack_from("<2f", buf, off)
                return (float(u), float(v))
            if el.fmt in (4, 8, 9) and el.size == 4:
                u, v = struct.unpack_from("<HH", buf, off)
                return (u / 65535.0, v / 65535.0)
            u = _read_half(buf, off)
            v = _read_half(buf, off + 2)
            if u != u or v != v:
                raise ValueError
            return (float(u), float(v))

        # Real TS4 object meshes: UVs are usually Short2 * UVScales.
        if el.fmt == S4_FMT_SHORT2 and el.size >= 4:
            u, v = struct.unpack_from("<2h", buf, off)
            return (u * uv_scale, v * uv_scale)
        if el.fmt == S4_FMT_SHORT2N and el.size >= 4:
            u, v = struct.unpack_from("<2h", buf, off)
            return (u / 32767.0, v / 32767.0)
        if el.fmt == S4_FMT_USHORT2N and el.size >= 4:
            u, v = struct.unpack_from("<2H", buf, off)
            return (u / 65535.0, v / 65535.0)
        if el.fmt == S4_FMT_FLOAT2 and el.size >= 8:
            u, v = struct.unpack_from("<2f", buf, off)
            return (float(u), float(v))
        if el.fmt == S4_FMT_FLOAT16_2 and el.size >= 4:
            u = _read_half(buf, off)
            v = _read_half(buf, off + 2)
            return (float(u), float(v))

        # Best-effort fallback.
        if el.size >= 8:
            u, v = struct.unpack_from("<2f", buf, off)
            return (float(u), float(v))
        if el.size >= 4:
            u, v = struct.unpack_from("<2h", buf, off)
            return (u * uv_scale, v * uv_scale)
        return (0.0, 0.0)
    except Exception:
        return (0.0, 0.0)


def _compute_vertex_normals(positions, faces):
    if not positions:
        return []
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in faces:
        try:
            ax, ay, az = positions[a]
            bx, by, bz = positions[b]
            cx, cy, cz = positions[c]
        except Exception:
            continue
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        for i in (a, b, c):
            acc[i][0] += nx
            acc[i][1] += ny
            acc[i][2] += nz
    out = []
    for nx, ny, nz in acc:
        l = math.sqrt(nx * nx + ny * ny + nz * nz)
        if l > 1e-12:
            out.append((nx / l, ny / l, nz / l))
        else:
            out.append((0.0, 0.0, 1.0))
    return out


def _decode_ibuf(ibuf: bytes, count_indices: int, start_index: int):
    """Decode an IBUF chunk. v2 uses 16-byte header + delta-encoded 16-bit."""
    if ibuf[0:4] != b"IBUF":
        raise ValueError("not IBUF")
    version = struct.unpack_from("<I", ibuf, 4)[0]
    header = 16 if version >= 2 else 12
    total = (len(ibuf) - header) // 2
    raw = struct.unpack_from(f"<{total}h", ibuf, header)
    decoded = []
    acc = 0
    for d in raw:
        acc += d
        decoded.append(acc)
    return decoded[start_index:start_index + count_indices]


def _resolve_index(rcol: RCOL, ref: int, sig: bytes, fallback_search: bool = True):
    primary = ref & 0x0FFFFFFF
    n = len(rcol.internal_chunks)
    for idx in (primary, primary + 1, primary - 1):
        if 0 <= idx < n and rcol.chunk_sig(idx) == sig:
            return idx
    if fallback_search:
        found = rcol.find_chunks_by_sig(sig)
        if found:
            found.sort(key=lambda i: len(rcol.chunk_bytes(i)), reverse=True)
            return found[0]
    raise ValueError(f"cannot resolve {sig!r}")


def _resolve(rcol, *refs_and_sig, fallback_search: bool = True):
    """Resolve an RCOL chunk reference and verify its signature."""
    *refs, sig = refs_and_sig
    for ref in refs:
        try:
            idx = _resolve_index(rcol, ref, sig, fallback_search=fallback_search)
            return rcol.chunk_bytes(idx)
        except Exception:
            pass
    raise ValueError(f"cannot resolve {sig!r}")


def _parse_mtst_default_matd_ref(mtst: bytes) -> int | None:
    if mtst[:4] != b"MTST" or len(mtst) < 20:
        return None
    version = struct.unpack_from("<I", mtst, 4)[0]
    pos = 16  # tag+ver+nameHash+index
    count = struct.unpack_from("<I", mtst, pos)[0]
    pos += 4
    choice = None
    if version < 768:
        for _ in range(count):
            matd_ref, state = struct.unpack_from("<II", mtst, pos)
            pos += 8
            if state == 0:
                return matd_ref
            if choice is None:
                choice = matd_ref
    else:
        for _ in range(count):
            matd_ref, state, variant = struct.unpack_from("<III", mtst, pos)
            pos += 12
            if state == 0 and variant == 0:
                return matd_ref
            if state == 0 and choice is None:
                choice = matd_ref
            elif choice is None:
                choice = matd_ref
    return choice


def _parse_matd_uv_scale(matd: bytes) -> float | None:
    """Read FieldType.UVScales (0x420520E9) from a MATD, if present."""
    if matd[:4] != b"MATD" or len(matd) < 44:
        return None
    version = struct.unpack_from("<I", matd, 4)[0]
    if version >= 0x103:
        mtrl_start = 28
        pos = 40  # count field inside MTRL, after tag/u32/u16/u16
    else:
        mtrl_start = 20
        pos = 32
    count = struct.unpack_from("<I", matd, pos)[0]
    pos += 4
    for _ in range(count):
        field, dtype, dwords, offset = struct.unpack_from("<4I", matd, pos)
        pos += 16
        if field != 0x420520E9 or dtype != 1 or dwords < 2:
            continue
        raw = matd[mtrl_start + offset:mtrl_start + offset + dwords * 4]
        if len(raw) < dwords * 4:
            continue
        vals = struct.unpack("<" + "f" * dwords, raw)
        for v in vals[:3]:
            if isinstance(v, float) and abs(v) > 1e-12:
                return float(v)
    return None


def _parse_matd_variant(matd: bytes, variant_id: int = 0) -> MaterialVariant:
    mv = MaterialVariant(variant_id=variant_id)
    if matd[:4] != b"MATD" or len(matd) < 44:
        return mv

    version = struct.unpack_from("<I", matd, 4)[0]
    mv.shader = struct.unpack_from("<I", matd, 12)[0]
    if version >= 0x103:
        mtrl_start = 28
        pos = 40
    else:
        mtrl_start = 20
        pos = 32
    count = struct.unpack_from("<I", matd, pos)[0]
    pos += 4

    for _ in range(count):
        field, dtype, dwords, offset = struct.unpack_from("<4I", matd, pos)
        pos += 16
        raw = matd[mtrl_start + offset:mtrl_start + offset + dwords * 4]
        if len(raw) < dwords * 4:
            continue

        if field == 0x420520E9 and dtype == 1 and dwords >= 2:
            vals = struct.unpack("<" + "f" * dwords, raw)
            for v in vals[:3]:
                if abs(v) > 1e-12:
                    mv.uv_scale = float(v)
                    break
        elif dtype in (4, 0x10004) and dwords >= 4:
            inst = struct.unpack_from("<Q", raw, 0)[0]
            tid = struct.unpack_from("<I", raw, 8)[0]
            gid = struct.unpack_from("<I", raw, 12)[0]
            key = (tid, gid, inst)
            if field == 0x6CC0FD85:
                mv.diffuse_key = key
            elif field == 0x6E56548A:
                mv.normal_key = key
            elif field == 0xAD528A60:
                mv.specular_key = key
            elif field == 0xF303D152:
                mv.emission_key = key

    return mv


def _resolve_default_matd_bytes(rcol: RCOL, mat_ref: int) -> bytes | None:
    try:
        sig = None
        idx = None
        # Prefer MTST over a nearby scale-offset MATD. Some object groups place
        # a tiny helper MATD right next to the real MTST reference.
        for cand_sig in (b"MTST", b"MATD"):
            try:
                idx = _resolve_index(rcol, mat_ref, cand_sig, fallback_search=False)
                sig = cand_sig
                break
            except Exception:
                pass
        if idx is None:
            return None
        block = rcol.chunk_bytes(idx)
        if sig == b"MATD":
            return block
        mtst_ref = _parse_mtst_default_matd_ref(block)
        if mtst_ref is None:
            return None
        matd_idx = _resolve_index(rcol, mtst_ref, b"MATD", fallback_search=False)
        return rcol.chunk_bytes(matd_idx)
    except Exception:
        return None


def _material_shader(rcol: RCOL, mat_ref: int) -> int | None:
    matd = _resolve_default_matd_bytes(rcol, mat_ref)
    if not matd or len(matd) < 16 or matd[:4] != b"MATD":
        return None
    return struct.unpack_from("<I", matd, 12)[0]


def _material_uv_scale(rcol: RCOL, mat_ref: int) -> float:
    # Default used by Sims4Tools when UVScales is absent.
    default = 1.0 / 32767.0
    try:
        matd = _resolve_default_matd_bytes(rcol, mat_ref)
        if not matd:
            return default
        scale = _parse_matd_uv_scale(matd)
        return scale if scale else default
    except Exception:
        return default


def material_variants(rcol: RCOL, mat_ref: int) -> list[MaterialVariant]:
    """Return the default-state material variants for a mesh material reference.

    For MTST this yields one entry per visible swatch/variant in file order.
    For direct MATD references it yields a single variant.
    """
    try:
        idx = None
        sig = None
        # Prefer MTST over a nearby scale-offset MATD helper.
        for cand_sig in (b"MTST", b"MATD"):
            try:
                idx = _resolve_index(rcol, mat_ref, cand_sig, fallback_search=False)
                sig = cand_sig
                break
            except Exception:
                pass
        if idx is None:
            return []
        block = rcol.chunk_bytes(idx)
        if sig == b"MATD":
            return [_parse_matd_variant(block, variant_id=0)]

        version = struct.unpack_from("<I", block, 4)[0]
        pos = 16
        count = struct.unpack_from("<I", block, pos)[0]
        pos += 4
        out = []
        for _ in range(count):
            if version < 768:
                matd_ref, state = struct.unpack_from("<II", block, pos)
                variant_id = 0
                pos += 8
            else:
                matd_ref, state, variant_id = struct.unpack_from("<III", block, pos)
                pos += 12
            # state 0 = default, non-burnt visual material
            if state != 0:
                continue
            try:
                matd_idx = _resolve_index(rcol, matd_ref, b"MATD", fallback_search=False)
            except Exception:
                continue
            out.append(_parse_matd_variant(rcol.chunk_bytes(matd_idx), variant_id=variant_id))
        return out
    except Exception:
        return []


def parse_object_mesh(rcol: RCOL, name: str = "object", no_cas: bool = False) -> list:
    mlod_idx = rcol.find_chunks_by_sig(b"MLOD")
    if not mlod_idx:
        mlod_idx = rcol.find_chunks_by_sig(b"MODL")
    if not mlod_idx:
        raise ValueError("no MLOD/MODL chunk")
    mlod = rcol.chunk_bytes(mlod_idx[0])

    pos = 4
    version = struct.unpack_from("<I", mlod, pos)[0]; pos += 4
    group_count = struct.unpack_from("<I", mlod, pos)[0]; pos += 4

    # When filtering CAS objects, peek at the first non-shadow group's shader.
    # If it is a CAS shader (skin/hair/clothing), skip the entire MLOD.
    # This works because Sims 4 objects are uniformly CAS or uniformly Objects.
    if no_cas and group_count > 0:
        try:
            scan_pos = pos + 4
            for _ in range(group_count):
                grp_start = scan_pos
                subset_bytes = struct.unpack_from("<I", mlod, scan_pos)[0]
                scan_next = grp_start + 4 + subset_bytes
                sp = scan_pos + 4 + 4  # skip name_hash, get mat_ref
                mat_ref = struct.unpack_from("<I", mlod, sp)[0]
                shader = _material_shader(rcol, mat_ref)
                scan_pos = scan_next
                if shader in (SHADER_DROP_SHADOW, SHADER_SHADOW_MAP):
                    continue
                if is_cas_shader(shader):
                    return []
                break
        except Exception:
            pass  # on any parse failure, proceed normally (don't block)

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

            shader = _material_shader(rcol, mat_ref)
            # Skip helper-only shadow meshes. These are not part of the visible
            # furniture model the user expects to export.
            if shader in (0xC09C7582, 0x21FE207D):  # DropShadow / ShadowMap
                pos = next_group
                continue

            mesh = _build_group_mesh(
                rcol, f"{name}_g{g:02d}",
                mat_ref, vrtf_ref, vbuf_ref, ibuf_ref,
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


def _build_group_mesh(rcol, name, mat_ref, vrtf_ref, vbuf_ref, ibuf_ref,
                      start_vertex, vertex_count, start_index, primitive_count,
                      stream_offset) -> ObjMesh:
    vrtf_b = _resolve(rcol, vrtf_ref, vbuf_ref, ibuf_ref, b"VRTF")
    vbuf_b = _resolve(rcol, vbuf_ref, ibuf_ref, vrtf_ref, b"VBUF")
    ibuf_b = _resolve(rcol, ibuf_ref, vbuf_ref, vrtf_ref, b"IBUF")

    vrtf = parse_vrtf(vrtf_b)
    scheme = _vrtf_scheme(vrtf)
    uv_scale = _material_uv_scale(rcol, mat_ref) if scheme == "s4" else 1.0
    mesh = ObjMesh(name=name, material_ref=mat_ref)

    stride = vrtf.stride
    vbuf_data = 16  # sig+ver+flags+swizzle

    pos_el = norm_el = uv_el = None
    for el in vrtf.elements:
        if scheme == "s4":
            if el.usage == S4_USAGE_POSITION and pos_el is None:
                pos_el = el
            elif el.usage == S4_USAGE_NORMAL and norm_el is None:
                norm_el = el
            elif el.usage == S4_USAGE_UV and el.usage_index == 0 and uv_el is None:
                uv_el = el
        else:
            if el.usage == LEGACY_USAGE_POSITION and pos_el is None:
                pos_el = el
            elif el.usage == LEGACY_USAGE_NORMAL and norm_el is None:
                norm_el = el
            elif el.usage == LEGACY_USAGE_UV and uv_el is None:
                uv_el = el

    base0 = vbuf_data + stream_offset + start_vertex * stride
    for vi in range(vertex_count):
        base = base0 + vi * stride
        if base + stride > len(vbuf_b):
            break
        if pos_el is not None:
            mesh.positions.append(_read_position(vbuf_b, base, pos_el, scheme))
        else:
            mesh.positions.append((0.0, 0.0, 0.0))
        if uv_el is not None:
            mesh.uvs.append(_read_uv(vbuf_b, base, uv_el, uv_scale, scheme))

    num_indices = primitive_count * 3
    indices = _decode_ibuf(ibuf_b, num_indices, start_index)

    vc = len(mesh.positions)
    for i in range(0, len(indices) - 2, 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if 0 <= a < vc and 0 <= b < vc and 0 <= c < vc:
            mesh.faces.append((a, b, c))

    if mesh.uvs and all(u == (0.0, 0.0) for u in mesh.uvs):
        mesh.uvs = []

    # If we failed to decode explicit normals, compute smooth vertex normals.
    if not mesh.normals or len(mesh.normals) != len(mesh.positions):
        mesh.normals = _compute_vertex_normals(mesh.positions, mesh.faces)

    return mesh
