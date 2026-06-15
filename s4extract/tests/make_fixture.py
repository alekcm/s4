"""Build a synthetic Sims4-style .package for testing the extractor.

Creates a DBPF 2.0 file with:
  * one GEOM (a simple quad: 4 verts, 2 tris) — stored uncompressed
  * one DXT5 DDS texture (8x8 red) — stored uncompressed

This exercises the DBPF index reader, GEOM parser, FBX/OBJ export, and the
DDS->PNG decoder. (Compression path is unit-tested separately.)
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_geom():
    out = bytearray()
    out += b"GEOM"
    out += struct.pack("<I", 0x0C)      # version
    out += struct.pack("<I", 0)         # tgi offset
    out += struct.pack("<I", 0)         # tgi size
    out += struct.pack("<I", 0)         # embedded id (none)
    out += struct.pack("<I", 0)         # mergeGroup
    out += struct.pack("<I", 0)         # sortOrder

    verts = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
    normals = [(0, 0, 1)] * 4
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    faces = [0, 1, 2, 0, 2, 3]

    out += struct.pack("<I", len(verts))   # NumVerts
    out += struct.pack("<I", 3)            # FCount (3 elements)
    # element: position
    out += struct.pack("<I", 1); out += struct.pack("<I", 1); out += bytes([12])
    # element: normal
    out += struct.pack("<I", 2); out += struct.pack("<I", 1); out += bytes([12])
    # element: uv
    out += struct.pack("<I", 3); out += struct.pack("<I", 1); out += bytes([8])

    for i in range(len(verts)):
        out += struct.pack("<fff", *verts[i])
        out += struct.pack("<fff", *normals[i])
        out += struct.pack("<ff", *uvs[i])

    out += struct.pack("<I", 1)   # ItemCount
    out += bytes([2])             # BytesPerFacePoint
    out += struct.pack("<I", len(faces))  # NumFacePoints
    for idx in faces:
        out += struct.pack("<H", idx)

    out += struct.pack("<I", 0)   # boneCount
    return bytes(out)


def build_dds_dxt5_red(w=8, h=8):
    # DDS header (128 bytes) for DXT5
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<I", hdr, 4, 124)          # dwSize
    struct.pack_into("<I", hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)  # flags
    struct.pack_into("<I", hdr, 12, h)
    struct.pack_into("<I", hdr, 16, w)
    struct.pack_into("<I", hdr, 76, 32)          # pf size
    struct.pack_into("<I", hdr, 80, 0x4)         # DDPF_FOURCC
    hdr[84:88] = b"DXT5"

    # one DXT5 block = 16 bytes; red opaque
    # alpha block: a0=255,a1=255 then 6 bytes index 0
    alpha = bytes([255, 255, 0, 0, 0, 0, 0, 0])
    # color: c0 = red 565 = 0xF800, c1=0, indices all 0
    color = struct.pack("<HHI", 0xF800, 0x0000, 0)
    block = alpha + color
    blocks_x = (w + 3) // 4
    blocks_y = (h + 3) // 4
    data = block * (blocks_x * blocks_y)
    return bytes(hdr) + data


def build_package(path):
    geom = build_geom()
    dds = build_dds_dxt5_red()

    resources = [
        (0x015A1849, 0x00000000, 0x0, 0x11111111, geom),   # GEOM
        (0x00B2D882, 0x00000000, 0x0, 0x22222222, dds),    # DDS _IMG
    ]

    # Layout: 96-byte header, then chunk data, then index.
    header = bytearray(96)
    header[0:4] = b"DBPF"
    struct.pack_into("<I", header, 4, 2)   # major
    struct.pack_into("<I", header, 8, 0)   # minor

    body = bytearray()
    offsets = []
    pos = 96
    for (_, _, _, _, data) in resources:
        offsets.append(pos)
        body += data
        pos += len(data)

    index_offset = pos
    # index type = 0 (nothing constant) -> all 8 dwords per entry
    index = bytearray()
    index += struct.pack("<I", 0)  # index type flags
    for i, (tid, gid, ihi, ilo, data) in enumerate(resources):
        index += struct.pack("<I", tid)
        index += struct.pack("<I", gid)
        index += struct.pack("<I", ihi)
        index += struct.pack("<I", ilo)
        index += struct.pack("<I", offsets[i])
        index += struct.pack("<I", len(data))   # file size (uncompressed, high bit 0)
        index += struct.pack("<I", len(data))   # mem size
        index += struct.pack("<I", 0)           # compressed=0

    struct.pack_into("<I", header, 36, len(resources))  # index entry count
    struct.pack_into("<I", header, 44, len(index))      # index size
    struct.pack_into("<I", header, 60, 3)               # index version
    struct.pack_into("<I", header, 64, index_offset)    # index offset

    with open(path, "wb") as f:
        f.write(header)
        f.write(body)
        f.write(index)

    print(f"wrote fixture: {path} ({pos + len(index)} bytes)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "test_fixture.package"
    build_package(out)
