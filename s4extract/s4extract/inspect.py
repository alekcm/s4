"""Diagnostic inspector: dump what is actually inside a .package.

Prints, for every resource: type, TGI, compressed flag, sizes, and the first
bytes (magic signature) of the decompressed data. This tells us the real mesh
and texture formats so the parsers can be fixed for real-world files.
"""
from __future__ import annotations

import struct
import traceback

from .dbpf import DBPF
from . import resource_types as rt


def _hex_head(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def _ascii_head(data: bytes, n: int = 8) -> str:
    out = []
    for b in data[:n]:
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out)


def inspect_package(package_path: str) -> str:
    lines = []
    pkg = DBPF.from_file(package_path)
    lines.append(f"PACKAGE: {package_path}")
    lines.append(f"resources: {len(pkg.entries)}")
    lines.append("")

    # Tally by type
    counts = {}
    for e in pkg.entries:
        counts[e.type_id] = counts.get(e.type_id, 0) + 1
    lines.append("TYPE SUMMARY:")
    for tid, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {rt.type_name(tid):12s} 0x{tid:08X} x{c}")
    lines.append("")

    lines.append("RESOURCES:")
    lines.append(f"{'#':>3}  {'TYPE':12s} {'COMP':4s} {'DISK':>9s} {'MEM':>9s}  "
                 f"{'MAGIC(ascii)':12s}  HEX HEAD")
    for i, e in enumerate(pkg.entries):
        try:
            data = pkg.read_resource(e)
        except Exception as ex:
            lines.append(f"{i:>3}  {rt.type_name(e.type_id):12s} ERR read: {ex}")
            continue
        comp = "yes" if e.compressed else "no"
        magic = _ascii_head(data, 8)
        hexh = _hex_head(data, 16)
        lines.append(f"{i:>3}  {rt.type_name(e.type_id):12s} {comp:4s} "
                     f"{e.file_size:>9d} {e.mem_size:>9d}  {magic:12s}  {hexh}")

        # For DDS images, decode the header and report the real format.
        if e.type_id in rt.IMAGE_TYPES or data[:4] == b"DDS ":
            try:
                _peek_dds(data, lines)
            except Exception as ex:
                lines.append(f"        (dds peek failed: {ex})")

        # For RCOL-like resources, peek deeper at the chunk signatures and try
        # a full mesh parse so we can see WHY meshes fail on real files.
        if e.type_id in (rt.MODL, rt.MLOD):
            try:
                _peek_rcol(data, lines)
            except Exception as ex:
                lines.append(f"        (rcol peek failed: {ex})")
            _peek_mesh_parse(data, lines)
    return "\n".join(lines)


def _peek_dds(data: bytes, lines: list):
    if data[:4] != b"DDS ":
        idx = data.find(b"DDS ")
        if 0 <= idx < 128:
            data = data[idx:]
        else:
            lines.append("        DDS: no 'DDS ' magic (packed RLE/LRLE?)")
            return
    height = struct.unpack_from("<I", data, 12)[0]
    width = struct.unpack_from("<I", data, 16)[0]
    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    bitcount = struct.unpack_from("<I", data, 88)[0]
    fourcc_a = "".join(chr(b) if 32 <= b < 127 else "." for b in fourcc)
    dx10 = ""
    if fourcc == b"DX10":
        dxgi = struct.unpack_from("<I", data, 128)[0]
        dx10 = f" DXGI_format={dxgi}"
    lines.append(f"        DDS: {width}x{height} fourcc='{fourcc_a}' "
                 f"flags=0x{pf_flags:08X} bpp={bitcount}{dx10}")


def _peek_mesh_parse(data: bytes, lines: list):
    try:
        from .rcol import RCOL, parse_object_mesh
        rcol = RCOL(data)
        groups = parse_object_mesh(rcol, name="probe")
        if not groups:
            lines.append("        MESH: parsed RCOL but found 0 usable groups")
        for gi, g in enumerate(groups):
            lines.append(f"        MESH group[{gi}]: {g.vertex_count} verts, "
                         f"{g.face_count} faces, bbox={g.bbox_min}->{g.bbox_max}")
    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        lines.append("        MESH parse ERROR:")
        for t in tb[-4:]:
            lines.append("          " + t)


def _peek_rcol(data: bytes, lines: list):
    pos = 0
    version, public_count, index3, external_count, internal_count = \
        struct.unpack_from("<IIIII", data, pos)
    pos += 20
    lines.append(f"        RCOL v{version} internal={internal_count} external={external_count}")
    internal_tgis = []
    for _ in range(internal_count):
        inst = struct.unpack_from("<Q", data, pos)[0]; pos += 8
        tid = struct.unpack_from("<I", data, pos)[0]; pos += 4
        gid = struct.unpack_from("<I", data, pos)[0]; pos += 4
        internal_tgis.append((inst, tid, gid))
    pos += external_count * 16
    for i in range(internal_count):
        cpos = struct.unpack_from("<I", data, pos)[0]; pos += 4
        csize = struct.unpack_from("<I", data, pos)[0]; pos += 4
        sig = data[cpos:cpos + 4]
        sig_a = _ascii_head(sig, 4)
        inst, tid, gid = internal_tgis[i]
        lines.append(f"          chunk[{i}] sig={sig_a!r} type=0x{tid:08X} "
                     f"pos={cpos} size={csize} head={_hex_head(data[cpos:cpos+24],24)}")
