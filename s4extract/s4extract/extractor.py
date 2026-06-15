"""High-level extraction pipeline."""
from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from .dbpf import DBPF
from .geom import parse_geom, GeomMesh
from .rcol import RCOL, parse_object_mesh, ObjMesh, material_variants
from .mesh_export import write_obj, write_fbx
from .caps import close_open_boundaries
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
    mat_pipeline: str = "builtin"  # builtin | urp | hdrp
    colliders: bool = True         # generate compound convex colliders
    prefab: bool = False           # generate legacy YAML prefab (Unity fixer creates READY prefab instead)
    dynamic: bool = True           # add Rigidbody (dynamic furniture)
    max_hulls: int = 16            # V-HACD: max convex parts per object
    all_lods: bool = False         # export every LOD (default: best only)
    parts_prefab: bool = True      # also generate a split-into-parts prefab via Unity fixer


def _classify_texture_role(name: str) -> str:
    n = name.lower()
    if "norm" in n or "_n_" in n or n.endswith("_n"):
        return "normal"
    if "spec" in n or "_s_" in n or "mask" in n or "_rough" in n:
        return "specular"
    return "diffuse"


def _is_opaque_square_palette_png(path: str) -> bool:
    """Heuristic for Sims object swatches: large square opaque atlas textures.

    Object packages usually contain many color variants as full-size square
    DST1 atlases, plus masks/specular/thumbnail/parts textures with alpha or
    non-square dimensions. The package format does not give friendly names, so
    this mirrors Sims4Studio's practical swatch selection for exported object
    textures.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            if w != h or w < 256:
                return False
            rgba = im.convert("RGBA")
            amin, amax = rgba.getchannel("A").getextrema()
            return amin == 255 and amax == 255
    except Exception:
        return False


def _choose_palette_diffuses(png_files: list[str]) -> list[str]:
    palettes = [p for p in png_files if os.path.exists(p) and _is_opaque_square_palette_png(p)]
    if palettes:
        return palettes
    # Fallback for unusual packages or missing Pillow: keep previous behaviour.
    for p in png_files:
        if os.path.exists(p):
            return [p]
    return []


def _to_common_mesh(m, rcol_obj=None):
    """Normalize GeomMesh / ObjMesh to a common dict."""
    return {
        "positions": m.positions,
        "normals": m.normals,
        "uvs": m.uvs,
        "faces": m.faces,
        "name": m.name,
        "material_ref": getattr(m, "material_ref", None),
        "rcol": rcol_obj,
    }


def _mesh_from_face_ids(mesh_name: str, positions, normals, uvs, faces, face_ids):
    used = []
    used_set = set()
    for fi in face_ids:
        for vi in faces[fi]:
            if vi not in used_set:
                used_set.add(vi)
                used.append(vi)
    remap = {old: new for new, old in enumerate(used)}
    p_positions = [positions[i] for i in used]
    p_normals = [normals[i] for i in used] if normals and len(normals) == len(positions) else []
    p_uvs = [uvs[i] for i in used] if uvs and len(uvs) == len(positions) else []
    p_faces = [tuple(remap[i] for i in faces[fi]) for fi in face_ids]
    return GeomMesh(
        name=mesh_name,
        positions=p_positions,
        normals=p_normals,
        uvs=p_uvs,
        faces=p_faces,
    )


def _split_connected_components(mesh_name: str, positions, normals, uvs, faces):
    """Split a mesh into disconnected vertex-connected islands.

    We split *after* the original Sims mesh groups, so this gives the most
    granular safe decomposition we can derive without semantic guessing.
    """
    if not positions or not faces:
        return []

    vert_to_faces = [[] for _ in range(len(positions))]
    for fi, (a, b, c) in enumerate(faces):
        if 0 <= a < len(positions):
            vert_to_faces[a].append(fi)
        if 0 <= b < len(positions):
            vert_to_faces[b].append(fi)
        if 0 <= c < len(positions):
            vert_to_faces[c].append(fi)

    seen = [False] * len(faces)
    groups = []
    for start_fi in range(len(faces)):
        if seen[start_fi]:
            continue
        q = deque([start_fi])
        seen[start_fi] = True
        comp_faces = []
        while q:
            fi = q.popleft()
            comp_faces.append(fi)
            a, b, c = faces[fi]
            for vi in (a, b, c):
                if not (0 <= vi < len(vert_to_faces)):
                    continue
                for nfi in vert_to_faces[vi]:
                    if not seen[nfi]:
                        seen[nfi] = True
                        q.append(nfi)
        groups.append(comp_faces)

    parts = []
    for pi, face_ids in enumerate(groups):
        parts.append(_mesh_from_face_ids(
            f"{mesh_name}_part{pi:02d}", positions, normals, uvs, faces, face_ids))
    return parts


def _pick_break_axis(positions):
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    ex = max(xs) - min(xs)
    ey = max(ys) - min(ys)
    ez = max(zs) - min(zs)
    # Prefer a horizontal split plane for breakage when possible; this makes
    # mugs / lamps / beds more likely to break into left/right-ish chunks rather
    # than top/bottom slices.
    horiz_axis = 0 if ex >= ez else 2
    horiz_extent = ex if ex >= ez else ez
    if horiz_extent >= ey * 0.45:
        return horiz_axis
    ext = [ex, ey, ez]
    return max(range(3), key=lambda i: ext[i])


def _split_mesh_by_axis(mesh: GeomMesh, axis: int):
    if not mesh.faces:
        return []
    coords = [p[axis] for p in mesh.positions]
    center = (min(coords) + max(coords)) * 0.5
    left_ids = []
    right_ids = []
    for fi, (a, b, c) in enumerate(mesh.faces):
        mid = (mesh.positions[a][axis] + mesh.positions[b][axis] + mesh.positions[c][axis]) / 3.0
        if mid <= center:
            left_ids.append(fi)
        else:
            right_ids.append(fi)
    if not left_ids or not right_ids:
        return []
    left = _mesh_from_face_ids(mesh.name + "_brokenA", mesh.positions, mesh.normals, mesh.uvs, mesh.faces, left_ids)
    right = _mesh_from_face_ids(mesh.name + "_brokenB", mesh.positions, mesh.normals, mesh.uvs, mesh.faces, right_ids)
    return [left, right]


def _fracture_mesh(mesh: GeomMesh):
    """Create a coarse two-piece broken variant for a logical part."""
    if mesh.face_count < 6 or mesh.vertex_count < 6:
        return []
    primary = _pick_break_axis(mesh.positions)
    axes = [primary] + [ax for ax in (0, 1, 2) if ax != primary]
    for axis in axes:
        halves = _split_mesh_by_axis(mesh, axis)
        if len(halves) == 2 and halves[0].face_count and halves[1].face_count:
            return [close_open_boundaries(h) for h in halves]
    return []


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
    collected = []   # list of normalized mesh dicts

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
    lod_candidates = []  # (priority, total_verts, groups, label, rcol)
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
                    lod_candidates.append((priority, total, groups, label, rcol))
            except Exception as ex:
                report["errors"].append(f"{rt.type_name(type_id)} {e.tgi}: {ex}")

    if lod_candidates:
        if not opt.all_lods:
            # The MODL resource and the highest MLOD usually contain the SAME
            # geometry. De-duplicate by total vertex count and keep just one
            # (the most detailed). Prefer MODL (priority 0) on ties.
            lod_candidates.sort(key=lambda c: (-c[1], c[0]))
            best = lod_candidates[0]
            lod_candidates = [best]
        for (_, _, groups, label, rcol_obj) in lod_candidates:
            for gm in groups:
                collected.append(_to_common_mesh(gm, rcol_obj=rcol_obj))

    # ---- export meshes ----
    mesh_records = []
    part_records = []
    break_specs = []
    for idx, rec in enumerate(collected):
        positions = rec["positions"]
        normals = rec["normals"]
        uvs = rec["uvs"]
        faces = rec["faces"]
        name = rec["name"]
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

        # Build a list of part assets for the optional *_PARTS prefab.
        parts = _split_connected_components(name, positions, normals, uvs, faces) if opt.parts_prefab else []
        part_asset_names = []
        if parts and len(parts) > 1:
            for part in parts:
                capped_part = close_open_boundaries(part)
                pobj = os.path.join(out_root, capped_part.name + ".obj")
                write_obj(capped_part, pobj)
                part_asset_names.append(capped_part.name)
                part_records.append({
                    "asset_name": capped_part.name,
                    "mesh_name": name,
                    "positions": capped_part.positions,
                    "faces": capped_part.faces,
                })
                broken = _fracture_mesh(capped_part)
                broken_asset_names = []
                for bp in broken:
                    bp_path = os.path.join(out_root, bp.name + ".obj")
                    write_obj(bp, bp_path)
                    broken_asset_names.append(bp.name)
                if broken_asset_names:
                    break_specs.append({
                        "intact_asset_name": capped_part.name,
                        "mesh_name": name,
                        "broken_asset_names": broken_asset_names,
                    })
        else:
            base_part = close_open_boundaries(gm)
            if opt.obj:
                p = os.path.join(out_root, name + ".obj")
                write_obj(base_part, p)
            part_asset_names.append(name)
            part_records.append({
                "asset_name": name,
                "mesh_name": name,
                "positions": base_part.positions,
                "faces": base_part.faces,
            })
            broken = _fracture_mesh(base_part)
            broken_asset_names = []
            for bp in broken:
                bp_path = os.path.join(out_root, bp.name + ".obj")
                write_obj(bp, bp_path)
                broken_asset_names.append(bp.name)
            if broken_asset_names:
                break_specs.append({
                    "intact_asset_name": name,
                    "mesh_name": name,
                    "broken_asset_names": broken_asset_names,
                })

        mesh_records.append({
            "name": name,
            "fbx": fbx_path,
            "positions": positions,
            "faces": faces,
            "material_ref": rec.get("material_ref"),
            "rcol": rec.get("rcol"),
            "part_asset_names": part_asset_names,
        })

    # ---- textures ----
    png_files = []
    texture_by_key = {}  # (type, group, instance) -> extracted png path
    if opt.png:
        tex_entries = [e for e in pkg.entries if e.type_id in rt.IMAGE_TYPES]
        for i, e in enumerate(tex_entries):
            try:
                data = pkg.read_resource(e)
                name = f"{base}_tex{i:02d}"
                p = os.path.join(out_root, name + ".png")
                status = textures.save_as_png(data, p)
                png_files.append(p)
                texture_by_key[(e.type_id, e.group_id, e.instance)] = p
                report["textures"].append({"name": name, "status": status,
                                           "file": os.path.basename(p)})
            except Exception as ex:
                report["errors"].append(f"TEX {e.tgi}: {ex}")

    # ---- Unity materials (pipeline-aware) ----
    material_guid = None          # first generated material, used as a generic fallback
    mesh_material_guid_by_name = {}
    mesh_material_name_by_name = {}
    part_asset_material_pairs = []
    breakable_specs_with_material = []
    if opt.unity_mat and png_files:
        for p in png_files:
            if os.path.exists(p):
                unity.write_texture_meta(p)

        material_texture_pairs = []

        # Preferred path: use the actual MATD / MTST bindings from each mesh
        # group, so multi-part objects (like beds) get the correct material per
        # part instead of everything receiving the first swatch.
        families = {}
        for rec in mesh_records:
            if rec.get("rcol") is None or rec.get("material_ref") is None:
                continue
            fam_key = (id(rec["rcol"]), rec["material_ref"])
            if fam_key not in families:
                families[fam_key] = {
                    "label": rec["name"],
                    "variants": material_variants(rec["rcol"], rec["material_ref"]),
                }

        family_guids = {}
        family_names = {}
        for fam_key, fam in families.items():
            label = fam["label"]
            guids = []
            names = []
            created_i = 0
            for mv in (fam.get("variants") or []):
                diffuse = texture_by_key.get(mv.diffuse_key) if mv.diffuse_key else None
                normal = texture_by_key.get(mv.normal_key) if mv.normal_key else None
                specular = texture_by_key.get(mv.specular_key) if mv.specular_key else None
                if diffuse is None:
                    continue
                mi = created_i
                created_i += 1
                mat_name = f"{label}_swatch{mi:02d}_material"
                mat_path = os.path.join(out_root, mat_name + ".mat")
                unity.write_material(
                    mat_path, mat_name, pipeline=opt.mat_pipeline,
                    diffuse_png=diffuse, normal_png=normal, specular_png=specular)
                guid = unity._guid_for(os.path.basename(mat_path))
                guids.append(guid)
                names.append(mat_name)
                material_texture_pairs.append((
                    mat_name,
                    os.path.basename(diffuse) if diffuse else "",
                    os.path.basename(normal) if normal else None,
                    os.path.basename(specular) if specular else None))
                if material_guid is None:
                    material_guid = guid
                report["materials"].append({
                    "name": mat_name,
                    "pipeline": opt.mat_pipeline,
                    "swatch": mi,
                    "file": os.path.basename(mat_path),
                    "diffuse": os.path.basename(diffuse) if diffuse else None,
                    "normal": os.path.basename(normal) if normal else None,
                    "specular": os.path.basename(specular) if specular else None})
            if guids:
                family_guids[fam_key] = guids
                family_names[fam_key] = names

        for rec in mesh_records:
            fam_key = (id(rec.get("rcol")), rec.get("material_ref"))
            if fam_key in family_guids:
                mesh_material_guid_by_name[rec["name"]] = family_guids[fam_key][0]
                mesh_material_name_by_name[rec["name"]] = family_names[fam_key][0]

        # Fallback path for unusual packages where direct material parsing did
        # not yield anything useful.
        if not material_texture_pairs:
            normal = specular = None
            for p in png_files:
                if not os.path.exists(p):
                    continue
                role = _classify_texture_role(os.path.basename(p))
                if role == "normal" and normal is None:
                    normal = p
                elif role == "specular" and specular is None:
                    specular = p

            palette_diffuses = _choose_palette_diffuses(png_files)
            if (normal is None or specular is None) and palette_diffuses:
                palette_set = set(palette_diffuses)
                aux = [p for p in png_files if os.path.exists(p) and p not in palette_set]
                if normal is None and aux:
                    normal = aux[0]
                if specular is None and len(aux) > 1:
                    specular = aux[1]

            for mi, diffuse in enumerate(palette_diffuses):
                suffix = f"swatch{mi:02d}"
                mat_name = f"{base}_{suffix}_material"
                mat_path = os.path.join(out_root, mat_name + ".mat")
                unity.write_material(
                    mat_path, mat_name, pipeline=opt.mat_pipeline,
                    diffuse_png=diffuse, normal_png=normal, specular_png=specular)
                guid = unity._guid_for(os.path.basename(mat_path))
                material_texture_pairs.append((
                    mat_name,
                    os.path.basename(diffuse) if diffuse else "",
                    os.path.basename(normal) if normal else None,
                    os.path.basename(specular) if specular else None))
                if material_guid is None:
                    material_guid = guid
                report["materials"].append({
                    "name": mat_name,
                    "pipeline": opt.mat_pipeline,
                    "swatch": mi,
                    "file": os.path.basename(mat_path),
                    "diffuse": os.path.basename(diffuse) if diffuse else None,
                    "normal": os.path.basename(normal) if normal else None,
                    "specular": os.path.basename(specular) if specular else None})
            for rec in mesh_records:
                if material_guid and rec["name"] not in mesh_material_guid_by_name:
                    mesh_material_guid_by_name[rec["name"]] = material_guid
                    mesh_material_name_by_name[rec["name"]] = os.path.splitext(os.path.basename(report["materials"][0]["file"]))[0] if report["materials"] else ""

        if material_guid:
            for rec in mesh_records:
                if rec["name"] not in mesh_material_guid_by_name:
                    mesh_material_guid_by_name[rec["name"]] = material_guid

        # Every disconnected-island part inherits the first swatch material of
        # its parent mesh group.
        for rec in mesh_records:
            mesh_mat_name = mesh_material_name_by_name.get(rec["name"])
            if not mesh_mat_name:
                continue
            for asset_name in rec.get("part_asset_names", []):
                part_asset_material_pairs.append((asset_name, mesh_mat_name))

        for spec in break_specs:
            mesh_mat_name = mesh_material_name_by_name.get(spec["mesh_name"])
            if not mesh_mat_name:
                continue
            breakable_specs_with_material.append((
                spec["intact_asset_name"],
                mesh_mat_name,
                list(spec.get("broken_asset_names", [])),
            ))

        if material_texture_pairs:
            fixer = unity.write_editor_material_fixer(
                out_root, opt.mat_pipeline, material_texture_pairs,
                mesh_names=[rec["name"] for rec in mesh_records],
                mesh_material_pairs=[(mn, mesh_material_name_by_name[mn]) for mn in mesh_material_name_by_name],
                part_asset_material_pairs=part_asset_material_pairs,
                breakable_specs=breakable_specs_with_material)
            report["materials"].append({
                "name": "S4ExtractMaterialFixer",
                "pipeline": opt.mat_pipeline,
                "file": os.path.relpath(fixer, out_root).replace(os.sep, "/"),
                "diffuse": None,
                "normal": None,
                "specular": None})

    # Write FBX importer .meta even when we do not generate legacy prefabs.
    # This prevents Unity's default FBX 0.01 file-scale behaviour and remaps the
    # first material slot to the first generated swatch material.
    for rec in mesh_records:
        if rec.get("fbx"):
            unity.write_fbx_meta(rec["fbx"], mesh_material_guid_by_name.get(rec["name"], material_guid))

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
                rec_mat_guid = mesh_material_guid_by_name.get(name, material_guid)
                fbx_guid = unity.write_fbx_meta(rec["fbx"], rec_mat_guid)
                prefab_path = os.path.join(out_root, name + ".prefab")
                unity.write_prefab(
                    prefab_path, name, fbx_guid, rec_mat_guid,
                    cset, collider_guids, dynamic=opt.dynamic)
                report["prefabs"].append({
                    "name": name,
                    "file": os.path.basename(prefab_path),
                    "collider_method": cset.method if cset else "none",
                    "collider_parts": len(collider_guids),
                    "dynamic": opt.dynamic,
                })

    return report
