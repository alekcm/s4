"""Parser for Sims 4 GEOM resources (0x015A1849) -> mesh data.

Spec: SimsWiki Sims_4:0x015A1849.

We extract positions, normals, UVs and faces. Vertex format is data-driven:
each "vertex element" declares a usage and a datatype, and we read each vertex
according to the declared element list.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


# Vertex element "usage" codes (from S4 pipeline / Blender importers)
USAGE_POSITION = 1
USAGE_NORMAL = 2
USAGE_UV = 3
USAGE_BONE_ASSIGNMENT = 4
USAGE_WEIGHTS = 5
USAGE_TANGENT = 6
USAGE_COLOR = 7
USAGE_VERTEX_ID = 10


@dataclass
class VertexElement:
    usage: int
    datatype: int
    bytes_per: int


@dataclass
class GeomMesh:
    name: str = "mesh"
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def face_count(self) -> int:
        return len(self.faces)


class GeomReader:
    def __init__(self, data: bytes):
        self.d = data
        self.pos = 0

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.pos)[0]
        self.pos += 4
        return v

    def u8(self) -> int:
        v = self.d[self.pos]
        self.pos += 1
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.d, self.pos)[0]
        self.pos += 4
        return v

    def parse(self) -> GeomMesh:
        d = self.d
        tag = d[:4]
        if tag != b"GEOM":
            raise ValueError("Not a GEOM chunk")
        self.pos = 4
        version = self.u32()
        tgi_offset = self.u32()
        tgi_size = self.u32()
        embedded_id = self.u32()
        if embedded_id != 0:
            chunk_size = self.u32()
            # skip embedded MTNF material chunk
            self.pos += chunk_size

        merge_group = self.u32()
        sort_order = self.u32()

        num_verts = self.u32()
        fcount = self.u32()  # number of vertex elements (format)

        elements: list[VertexElement] = []
        for _ in range(fcount):
            usage = self.u32()
            datatype = self.u32()
            bytes_per = self.u8()
            elements.append(VertexElement(usage, datatype, bytes_per))

        mesh = GeomMesh()

        # Read each vertex according to element layout.
        for _ in range(num_verts):
            for el in elements:
                start = self.pos
                if el.usage == USAGE_POSITION and el.bytes_per >= 12:
                    x = self.f32(); y = self.f32(); z = self.f32()
                    mesh.positions.append((x, y, z))
                    self.pos = start + el.bytes_per
                elif el.usage == USAGE_NORMAL and el.bytes_per >= 12:
                    nx = self.f32(); ny = self.f32(); nz = self.f32()
                    mesh.normals.append((nx, ny, nz))
                    self.pos = start + el.bytes_per
                elif el.usage == USAGE_UV and el.bytes_per >= 8:
                    u = self.f32(); v = self.f32()
                    # only keep first UV channel
                    if len(mesh.uvs) < len(mesh.positions):
                        mesh.uvs.append((u, v))
                    self.pos = start + el.bytes_per
                else:
                    # skip any other element (bone weights, tangents, color, id...)
                    self.pos = start + el.bytes_per

        # Faces
        item_count = self.u32()
        bytes_per_face_point = 2
        for _ in range(item_count):
            bytes_per_face_point = self.u8()

        num_face_points = self.u32()
        indices: list[int] = []
        for _ in range(num_face_points):
            if bytes_per_face_point == 2:
                indices.append(struct.unpack_from("<H", self.d, self.pos)[0])
                self.pos += 2
            else:
                indices.append(struct.unpack_from("<I", self.d, self.pos)[0])
                self.pos += bytes_per_face_point

        for i in range(0, len(indices) - 2, 3):
            mesh.faces.append((indices[i], indices[i + 1], indices[i + 2]))

        return mesh


def parse_geom(data: bytes, name: str = "mesh") -> GeomMesh:
    m = GeomReader(data).parse()
    m.name = name
    return m
