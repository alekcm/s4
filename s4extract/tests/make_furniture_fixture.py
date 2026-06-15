"""Build a synthetic furniture .package: a MLOD object (L-shaped, non-convex)
with VRTF/VBUF/IBUF chunks inside an RCOL, plus a DDS texture.

This exercises the RCOL/MODL path, collider decomposition and prefab output.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from make_fixture import build_dds_dxt5_red


def build_l_shape():
    """Return (vertices[(x,y,z)], faces[(a,b,c)]) of a non-convex L extrusion."""
    # L profile in XY, extruded in Z. 6 outline points -> two prisms.
    # Simple: combine two boxes' corners into a vertex/face soup (with normals/uv).
    import itertools
    boxes = [
        (-1.0, -1.0, 0.0, 1.0, 0.0, 1.0),   # box A: x[-1,1] y[-1,0] z[0,1]
        (-1.0, 0.0, 0.0, 0.0, 1.0, 1.0),    # box B: x[-1,0] y[0,1] z[0,1]
    ]
    verts = []
    faces = []
    for (x0, y0, z0, x1, y1, z1) in boxes:
        base = len(verts)
        corners = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
        ]
        verts.extend(corners)
        quads = [
            (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
            (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4),
        ]
        for (a, b, c, d) in quads:
            faces.append((base + a, base + b, base + c))
            faces.append((base + a, base + c, base + d))
    return verts, faces


def build_vrtf():
    out = bytearray()
    out += b"VRTF"
    out += struct.pack("<I", 2)          # version
    stride = 12 + 12 + 8                  # pos + normal + uv
    out += struct.pack("<I", stride)
    out += struct.pack("<I", 3)          # element count
    out += struct.pack("<I", 0)          # not extended
    # element: usage(byte) usageIndex(byte) format(byte) offset(byte)
    # usage 1=pos fmt 3=Float3 offset 0
    out += bytes([1, 0, 3, 0])
    # usage 2=normal fmt 3=Float3 offset 12
    out += bytes([2, 0, 3, 12])
    # usage 3=uv fmt 2=Float2 offset 24
    out += bytes([3, 0, 2, 24])
    return bytes(out)


def build_vbuf(verts):
    out = bytearray()
    out += b"VBUF"
    out += struct.pack("<I", 0x00000101)  # version
    out += struct.pack("<I", 0)           # flags
    out += struct.pack("<I", 0)           # swizzle ref
    for (x, y, z) in verts:
        out += struct.pack("<fff", x, y, z)       # position
        out += struct.pack("<fff", 0.0, 0.0, 1.0)  # normal
        out += struct.pack("<ff", 0.0, 0.0)        # uv
    return bytes(out)


def build_ibuf(faces):
    out = bytearray()
    out += b"IBUF"
    out += struct.pack("<I", 0x00000101)  # version
    out += struct.pack("<I", 0)           # flags
    for (a, b, c) in faces:
        out += struct.pack("<HHH", a, b, c)
    return bytes(out)


def build_mlod(vrtf_ref, vbuf_ref, ibuf_ref, num_verts, num_faces, bbox):
    out = bytearray()
    out += b"MLOD"
    out += struct.pack("<I", 0x00000201)  # version
    out += struct.pack("<I", 1)           # group_count

    grp = bytearray()
    grp += struct.pack("<I", 0)           # name hash
    grp += struct.pack("<I", 0)           # material ref
    grp += struct.pack("<I", vrtf_ref)    # VRTF ref (1-based)
    grp += struct.pack("<I", vbuf_ref)    # VBUF ref
    grp += struct.pack("<I", ibuf_ref)    # IBUF ref
    grp += struct.pack("<I", 0)           # flags
    grp += struct.pack("<I", 0)           # stream offset
    grp += struct.pack("<I", 0)           # start vertex
    grp += struct.pack("<I", 0)           # start index
    grp += struct.pack("<I", 0)           # min vertex
    grp += struct.pack("<I", num_verts)   # vertex count
    grp += struct.pack("<I", num_faces)   # primitive count
    grp += struct.pack("<6f", *bbox)      # bounding box
    grp += struct.pack("<i", -1)          # skin controller (none)
    grp += struct.pack("<I", 0)           # bone count
    grp += struct.pack("<I", 0)           # mesh material
    grp += struct.pack("<I", 0)           # num prim info

    out += struct.pack("<I", len(grp))    # subset_bytes
    out += grp
    return bytes(out)


def build_rcol(chunks):
    """chunks: list of (sig, type_id, bytes). Returns RCOL resource bytes."""
    header = bytearray()
    header += struct.pack("<I", 3)                 # version
    header += struct.pack("<I", len(chunks))       # public chunk count
    header += struct.pack("<I", 0)                 # index3
    header += struct.pack("<I", 0)                 # external count
    header += struct.pack("<I", len(chunks))       # internal count
    for i, (sig, tid, data) in enumerate(chunks):
        header += struct.pack("<Q", i + 1)         # instance
        header += struct.pack("<I", tid)           # type
        header += struct.pack("<I", 0)             # group

    # chunk data laid out after header + location table
    loc_table_size = len(chunks) * 8
    data_start = len(header) + loc_table_size
    body = bytearray()
    locs = []
    pos = data_start
    for (sig, tid, data) in chunks:
        # pad to 4-byte alignment
        while (pos % 4) != 0:
            body += b"\x00"
            pos += 1
        locs.append((pos, len(data)))
        body += data
        pos += len(data)

    for (cpos, csize) in locs:
        header += struct.pack("<I", cpos)
        header += struct.pack("<I", csize)

    return bytes(header) + bytes(body)


def build_package(path):
    verts, faces = build_l_shape()
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    bbox = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    vrtf = build_vrtf()
    vbuf = build_vbuf(verts)
    ibuf = build_ibuf(faces)
    # chunk order: MLOD(1), VRTF(2), VBUF(3), IBUF(4)  (1-based refs)
    mlod = build_mlod(2, 3, 4, len(verts), len(faces), bbox)

    chunks = [
        (b"MLOD", 0x01D10F34, mlod),
        (b"VRTF", 0x01D0E723, vrtf),
        (b"VBUF", 0x01D0E6FB, vbuf),
        (b"IBUF", 0x01D0E70F, ibuf),
    ]
    rcol = build_rcol(chunks)
    dds = build_dds_dxt5_red()

    resources = [
        (0x01D10F34, 0, 0, 0x33333333, rcol),
        (0x00B2D882, 0, 0, 0x44444444, dds),
    ]

    header = bytearray(96)
    header[0:4] = b"DBPF"
    struct.pack_into("<I", header, 4, 2)

    body = bytearray()
    offs = []
    pos = 96
    for r in resources:
        offs.append(pos)
        body += r[4]
        pos += len(r[4])

    index_offset = pos
    index = bytearray()
    index += struct.pack("<I", 0)
    for i, (tid, gid, ihi, ilo, data) in enumerate(resources):
        index += struct.pack("<I", tid)
        index += struct.pack("<I", gid)
        index += struct.pack("<I", ihi)
        index += struct.pack("<I", ilo)
        index += struct.pack("<I", offs[i])
        index += struct.pack("<I", len(data))
        index += struct.pack("<I", len(data))
        index += struct.pack("<I", 0)

    struct.pack_into("<I", header, 36, len(resources))
    struct.pack_into("<I", header, 44, len(index))
    struct.pack_into("<I", header, 60, 3)
    struct.pack_into("<I", header, 64, index_offset)

    with open(path, "wb") as f:
        f.write(header)
        f.write(body)
        f.write(index)
    print(f"wrote furniture fixture: {path} ({pos + len(index)} bytes), "
          f"{len(verts)} verts {len(faces)} faces")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "furniture.package"
    build_package(out)
