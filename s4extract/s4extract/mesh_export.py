"""Export GeomMesh to OBJ and ASCII FBX (no external dependencies)."""
from __future__ import annotations

from .geom import GeomMesh


def write_obj(mesh: GeomMesh, path: str, mtl_name: str | None = None) -> None:
    lines = [f"# Exported by s4extract — {mesh.name}", f"o {mesh.name}"]
    if mtl_name:
        lines.append(f"mtllib {mtl_name}.mtl")
    for (x, y, z) in mesh.positions:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for (u, v) in mesh.uvs:
        lines.append(f"vt {u:.6f} {1.0 - v:.6f}")  # flip V for OBJ convention
    for (nx, ny, nz) in mesh.normals:
        lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")
    if mtl_name:
        lines.append(f"usemtl {mesh.name}")

    has_uv = len(mesh.uvs) == len(mesh.positions) and len(mesh.uvs) > 0
    has_n = len(mesh.normals) == len(mesh.positions) and len(mesh.normals) > 0
    for (a, b, c) in mesh.faces:
        ia, ib, ic = a + 1, b + 1, c + 1
        if has_uv and has_n:
            lines.append(f"f {ia}/{ia}/{ia} {ib}/{ib}/{ib} {ic}/{ic}/{ic}")
        elif has_n:
            lines.append(f"f {ia}//{ia} {ib}//{ib} {ic}//{ic}")
        elif has_uv:
            lines.append(f"f {ia}/{ia} {ib}/{ib} {ic}/{ic}")
        else:
            lines.append(f"f {ia} {ib} {ic}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if mtl_name:
        with open(path.rsplit(".", 1)[0] + ".mtl", "w", encoding="utf-8") as f:
            f.write(f"newmtl {mesh.name}\n")
            f.write("Ka 1.0 1.0 1.0\nKd 1.0 1.0 1.0\nKs 0.0 0.0 0.0\nd 1.0\nillum 2\n")
            f.write(f"map_Kd {mtl_name}_diffuse.png\n")


# ---------------------------------------------------------------------------
# ASCII FBX 7.4 writer. Unity & Blender both import ASCII FBX fine.
# ---------------------------------------------------------------------------
def write_fbx(mesh: GeomMesh, path: str, texture_name: str | None = None) -> None:
    name = mesh.name

    verts_flat = []
    for (x, y, z) in mesh.positions:
        verts_flat.extend((x, y, z))

    # Polygon vertex index: last index of each polygon is XOR'd with -1 (FBX rule)
    poly_idx = []
    for (a, b, c) in mesh.faces:
        poly_idx.extend((a, b, (-c - 1)))

    def fmt_floats(vals):
        return ",".join(f"{v:.6f}" for v in vals)

    def fmt_ints(vals):
        return ",".join(str(v) for v in vals)

    normals_flat = []
    for (a, b, c) in mesh.faces:
        for idx in (a, b, c):
            if idx < len(mesh.normals):
                normals_flat.extend(mesh.normals[idx])
            else:
                normals_flat.extend((0.0, 0.0, 1.0))

    uv_flat = []
    uv_index = []
    if mesh.uvs:
        for (u, v) in mesh.uvs:
            uv_flat.extend((u, 1.0 - v))
        for (a, b, c) in mesh.faces:
            uv_index.extend((a, b, c))

    geo_id = 1000000
    model_id = 2000000
    mat_id = 3000000

    out = []
    out.append("; FBX 7.4.0 project file")
    out.append("; Exported by s4extract")
    out.append("")
    out.append("FBXHeaderExtension:  {")
    out.append("\tFBXHeaderVersion: 1003")
    out.append("\tFBXVersion: 7400")
    out.append("\tCreator: \"s4extract\"")
    out.append("}")
    out.append("GlobalSettings:  {")
    out.append("\tVersion: 1000")
    out.append("\tProperties70:  {")
    out.append("\t\tP: \"UpAxis\", \"int\", \"Integer\", \"\",1")
    out.append("\t\tP: \"UpAxisSign\", \"int\", \"Integer\", \"\",1")
    out.append("\t\tP: \"FrontAxis\", \"int\", \"Integer\", \"\",2")
    out.append("\t\tP: \"FrontAxisSign\", \"int\", \"Integer\", \"\",1")
    out.append("\t\tP: \"CoordAxis\", \"int\", \"Integer\", \"\",0")
    out.append("\t\tP: \"CoordAxisSign\", \"int\", \"Integer\", \"\",1")
    out.append("\t\tP: \"UnitScaleFactor\", \"double\", \"Number\", \"\",1")
    out.append("\t}")
    out.append("}")

    # Definitions
    out.append("Definitions:  {")
    out.append("\tVersion: 100")
    out.append("\tCount: 3")
    out.append("\tObjectType: \"Geometry\" {\n\t\tCount: 1\n\t}")
    out.append("\tObjectType: \"Model\" {\n\t\tCount: 1\n\t}")
    out.append("\tObjectType: \"Material\" {\n\t\tCount: 1\n\t}")
    out.append("}")

    # Objects
    out.append("Objects:  {")
    out.append(f"\tGeometry: {geo_id}, \"Geometry::{name}\", \"Mesh\" {{")
    out.append(f"\t\tVertices: *{len(verts_flat)} {{")
    out.append(f"\t\t\ta: {fmt_floats(verts_flat)}")
    out.append("\t\t}")
    out.append(f"\t\tPolygonVertexIndex: *{len(poly_idx)} {{")
    out.append(f"\t\t\ta: {fmt_ints(poly_idx)}")
    out.append("\t\t}")

    if normals_flat:
        out.append("\t\tLayerElementNormal: 0 {")
        out.append("\t\t\tVersion: 101")
        out.append("\t\t\tName: \"\"")
        out.append("\t\t\tMappingInformationType: \"ByPolygonVertex\"")
        out.append("\t\t\tReferenceInformationType: \"Direct\"")
        out.append(f"\t\t\tNormals: *{len(normals_flat)} {{")
        out.append(f"\t\t\t\ta: {fmt_floats(normals_flat)}")
        out.append("\t\t\t}")
        out.append("\t\t}")

    if uv_flat:
        out.append("\t\tLayerElementUV: 0 {")
        out.append("\t\t\tVersion: 101")
        out.append("\t\t\tName: \"map1\"")
        out.append("\t\t\tMappingInformationType: \"ByPolygonVertex\"")
        out.append("\t\t\tReferenceInformationType: \"IndexToDirect\"")
        out.append(f"\t\t\tUV: *{len(uv_flat)} {{")
        out.append(f"\t\t\t\ta: {fmt_floats(uv_flat)}")
        out.append("\t\t\t}")
        out.append(f"\t\t\tUVIndex: *{len(uv_index)} {{")
        out.append(f"\t\t\t\ta: {fmt_ints(uv_index)}")
        out.append("\t\t\t}")
        out.append("\t\t}")

    out.append("\t\tLayerElementMaterial: 0 {")
    out.append("\t\t\tVersion: 101")
    out.append("\t\t\tName: \"\"")
    out.append("\t\t\tMappingInformationType: \"AllSame\"")
    out.append("\t\t\tReferenceInformationType: \"IndexToDirect\"")
    out.append("\t\t\tMaterials: *1 {\n\t\t\t\ta: 0\n\t\t\t}")
    out.append("\t\t}")

    out.append("\t\tLayer: 0 {")
    out.append("\t\t\tVersion: 100")
    if normals_flat:
        out.append("\t\t\tLayerElement:  {\n\t\t\t\tType: \"LayerElementNormal\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}")
    if uv_flat:
        out.append("\t\t\tLayerElement:  {\n\t\t\t\tType: \"LayerElementUV\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}")
    out.append("\t\t\tLayerElement:  {\n\t\t\t\tType: \"LayerElementMaterial\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}")
    out.append("\t\t}")
    out.append("\t}")

    # Model
    out.append(f"\tModel: {model_id}, \"Model::{name}\", \"Mesh\" {{")
    out.append("\t\tVersion: 232")
    out.append("\t\tProperties70:  {")
    out.append("\t\t\tP: \"DefaultAttributeIndex\", \"int\", \"Integer\", \"\",0")
    out.append("\t\t}")
    out.append("\t}")

    # Material
    out.append(f"\tMaterial: {mat_id}, \"Material::{name}\", \"\" {{")
    out.append("\t\tVersion: 102")
    out.append("\t\tShadingModel: \"phong\"")
    out.append("\t\tProperties70:  {")
    out.append("\t\t\tP: \"DiffuseColor\", \"Color\", \"\", \"A\",1,1,1")
    out.append("\t\t}")
    out.append("\t}")
    out.append("}")

    # Connections
    out.append("Connections:  {")
    out.append(f"\tC: \"OO\",{model_id},0")
    out.append(f"\tC: \"OO\",{geo_id},{model_id}")
    out.append(f"\tC: \"OO\",{mat_id},{model_id}")
    out.append("}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
