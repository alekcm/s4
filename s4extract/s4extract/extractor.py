"""High-level extraction pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .dbpf import DBPF
from .geom import parse_geom, GeomMesh
from .rcol import RCOL, parse_object_mesh, ObjMesh
from .mesh_export import write_obj, write_fbx
from . import resource_types as rt
from . import textures
from . import unity
from . import colliders as col


@dataclass
class Options:
    out_dir: str
    raw: bool = False        # also dump every raw resource
    obj: bool = True
    fbx: bool = True
    png: bool = True
    unity_mat: bool = True
    mat_pipeline: str = "hdrp"     # hdrp | urp | builtin
    colliders: bool = True         # generate compound convex colliders
    prefab: bool = True            # generate a Unity prefab
    dynamic: bool = True           # add Rigidbody (dynamic furniture)
    max_hulls: int = 16            # V-HACD: max convex parts per object
    all_lods: bool = False         # export every LOD (default: best only)


def _classify_texture_role(name: str) -> str:
    n = name.lower()
    if "norm" in n or "_n_" in n or n.endswith("_n"):
        return "normal"
    if "spec" in n or "_s_" in n or "mask" in n or "_rough" in n:
        return "specular"
    return "diffuse"


def _to_common_mesh(m):
    """Normalize GeomMesh / ObjMesh to a tuple (positions, normals, uvs, faces, name)."""
    return (m.positions, m.normals, m.uvs, m.faces, m.name)


def extract_package(package_path: str, opt: Options) -> dict:
    pkg = DBPF.from_file(package_path)
    base = os.path.splitext(os.path.basename(package_path))[0]
    out_root = os.path.join(opt.out_dir, base)
    os.makedirs(out_root, exist_ok=True)

    report = {
        "package": package_path,
        "out_dir": out_root,
        "total_resources": len(pkg.entries),
        "meshes": [],
        "textures": [],
        "materials": [],
        "prefabs": [],
        "raw": [],
        "errors": [],
    }

    # ---- raw dump ----
    if opt.raw:
        raw_dir = os.path.join(out_root, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        for e in pkg.entries:
            try:
                data = pkg.read_resource(e)
                fn = os.path.join(raw_dir, f"{rt.type_name(e.type_id)}_{e.tgi}.bin")
                with open(fn, "wb") as f:
                    f.write(data)
                report["raw"].append(os.path.basename(fn))
            except Exception as ex:
                report["errors"].append(f"raw {e.tgi}: {ex}")

    # ---- collect meshes from BOTH GEOM (CAS) and MODL/MLOD (furniture) ----
    collected = []   # list of (positions, normals, uvs, faces, name)

    # GEOM (CAS / body) — kept for completeness
    for i, e in enumerate(pkg.find(rt.GEOM)):
        try:
            data = pkg.read_resource(e)
            m = parse_geom(data, name=f"{base}_geom{i:02d}")
            if m.vertex_count and m.face_count:
                collected.append(_to_common_mesh(m))
        except Exception as ex:
            report["errors"].append(f"GEOM {e.tgi}: {ex}")

    # MODL / MLOD (furniture / objects).
    # A package usually has several LOD resources (MODL = highest detail, plus
    # MLOD LOD0..LODn). We parse them all, then keep only the BEST (most
    # detailed) variant per object so we don't export overlapping duplicate
    # meshes. Heuristic: prefer MODL, else the MLOD with the most vertices.
    lod_candidates = []  # (priority, total_verts, groups, label)
    for type_id in (rt.MODL, rt.MLOD):
        priority = 0 if type_id == rt.MODL else 1
        for i, e in enumerate(pkg.find(type_id)):
            try:
                data = pkg.read_resource(e)
                rcol = RCOL(data)
                label = f"{base}_{rt.type_name(type_id).lower()}{i:02d}"
                groups = parse_object_mesh(rcol, name=label)
                groups = [g for g in groups if g.vertex_count and g.face_count]
                if groups:
                    total = sum(g.vertex_count for g in groups)
                    lod_candidates.append((priority, total, groups, label))
            except Exception as ex:
                report["errors"].append(f"{rt.type_name(type_id)} {e.tgi}: {ex}")

    if lod_candidates:
        if not opt.all_lods:
            # The MODL resource and the highest MLOD usually contain the SAME
            # geometry. De-duplicate by total vertex count and keep just one
            # (the most detailed). Prefer MODL (priority 0) on ties.
            lod_candidates.sort(key=lambda c: (-c[1], c[0]))
            best = lod_candidates[0]
            # drop any other candidate with the same vertex total (duplicate LOD)
            lod_candidates = [best]
        for (_, _, groups, label) in lod_candidates:
            for gm in groups:
                collected.append(_to_common_mesh(gm))

    # ---- export meshes ----
    mesh_records = []
    for idx, (positions, normals, uvs, faces, name) in enumerate(collected):
        gm = GeomMesh(name=name, positions=positions, normals=normals,
                      uvs=uvs, faces=faces)
        entry = {"name": name, "verts": gm.vertex_count,
                 "faces": gm.face_count, "files": []}
        fbx_path = None
        if opt.obj:
            p = os.path.join(out_root, name + ".obj")
            write_obj(gm, p)
            entry["files"].append(os.path.basename(p))
        if opt.fbx:
            fbx_path = os.path.join(out_root, name + ".fbx")
            write_fbx(gm, fbx_path)
            entry["files"].append(os.path.basename(fbx_path))
        report["meshes"].append(entry)
        mesh_records.append({"name": name, "fbx": fbx_path,
                             "positions": positions, "faces": faces})

    # ---- textures ----
    png_files = []
    if opt.png:
        tex_entries = [e for e in pkg.entries if e.type_id in rt.IMAGE_TYPES]
        for i, e in enumerate(tex_entries):
            try:
                data = pkg.read_resource(e)
                name = f"{base}_tex{i:02d}"
                p = os.path.join(out_root, name + ".png")
                status = textures.save_as_png(data, p)
                png_files.append(p)
                report["textures"].append({"name": name, "status": status,
                                           "file": os.path.basename(p)})
            except Exception as ex:
                report["errors"].append(f"TEX {e.tgi}: {ex}")

    # ---- Unity material (pipeline-aware) ----
    material_guid = None
    if opt.unity_mat and png_files:
        diffuse = normal = specular = None
        for p in png_files:
            if not os.path.exists(p):
                continue
            role = _classify_texture_role(os.path.basename(p))
            if role == "normal" and normal is None:
                normal = p
            elif role == "specular" and specular is None:
                specular = p
            elif diffuse is None:
                diffuse = p
        if diffuse is None and png_files:
            diffuse = png_files[0]

        for p in png_files:
            if os.path.exists(p):
                unity.write_texture_meta(p)

        mat_name = f"{base}_material"
        mat_path = os.path.join(out_root, mat_name + ".mat")
        unity.write_material(
            mat_path, mat_name, pipeline=opt.mat_pipeline,
            diffuse_png=diffuse, normal_png=normal, specular_png=specular)
        material_guid = unity._guid_for(os.path.basename(mat_path))
        report["materials"].append({
            "name": mat_name, "pipeline": opt.mat_pipeline,
            "file": os.path.basename(mat_path),
            "diffuse": os.path.basename(diffuse) if diffuse else None,
            "normal": os.path.basename(normal) if normal else None,
            "specular": os.path.basename(specular) if specular else None})

    # ---- colliders + prefab per mesh ----
    if (opt.colliders or opt.prefab):
        for rec in mesh_records:
            name = rec["name"]
            cset = None
            collider_guids = []
            if opt.colliders:
                cset = col.build_colliders(rec["positions"], rec["faces"],
                                           max_hulls=opt.max_hulls)
                for ci, part in enumerate(cset.convex_parts):
                    cobj = os.path.join(out_root, f"{name}_collider{ci:02d}.obj")
                    cguid = unity.write_collider_obj(cobj, part)
                    collider_guids.append(cguid)

            if opt.prefab and rec["fbx"]:
                fbx_guid = unity.write_fbx_meta(rec["fbx"], material_guid)
                prefab_path = os.path.join(out_root, name + ".prefab")
                unity.write_prefab(
                    prefab_path, name, fbx_guid, material_guid,
                    cset, collider_guids, dynamic=opt.dynamic)
                report["prefabs"].append({
                    "name": name,
                    "file": os.path.basename(prefab_path),
                    "collider_method": cset.method if cset else "none",
                    "collider_parts": len(collider_guids),
                    "dynamic": opt.dynamic,
                })

    return report
