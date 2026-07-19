"""High-level extraction pipeline."""
from __future__ import annotations

import os
import math
import colorsys
from collections import deque, Counter
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
    raw: bool = False
    obj: bool = True
    fbx: bool = True
    png: bool = True
    unity_mat: bool = True
    mat_pipeline: str = "builtin"
    colliders: bool = True
    prefab: bool = False
    dynamic: bool = True
    max_hulls: int = 128
    merge_convex_neighbors: bool = True
    merge_max_inflation: float = 0.03
    merge_contact_epsilon: float = 0.002
    merge_max_deviation_ratio: float = 0.005
    max_verts_per_hull: int = 64
    all_lods: bool = True   # по умолчанию извлекаем все LOD-уровни
    no_cas: bool = True     # по умолчанию пропускаем CAS (одежда/волосы)
    extract_geom: bool = False  # по умолчанию не извлекаем GEOM (создаёт мусор "default")
    concavity_threshold: float = 0.20  # порог вогнутости для рекурсивного разбиения (0.0=все convex, 1.0=почти любой)
    per_object: bool = True  # извлекать каждый объект из мульти-пакета в отдельную папку


@dataclass
class _ObjectContext:
    """Internal: per-object extraction context for multi-object packages."""
    pkg: DBPF
    obj_name: str
    stbl_name: str
    id_str: str
    out_root: str
    modl_tgi_filter: set      # set of (type, group, instance) for MODL/MLOD
    desc_str: str = ""


import json
import struct

def get_or_create_catalog_entry(pkg: DBPF, package_path: str, base_name: str, db_path: str = "catalog_database.json") -> tuple[str, str, str]:
    db = []
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = []
            
    filename = os.path.basename(package_path)
    entry = None
    for item in db:
        if item.get("filename") == filename:
            entry = item
            break
            
    if entry is None:
        existing_ids = [int(item.get("id", 0)) for item in db if item.get("id")]
        next_id = max(existing_ids) + 1 if existing_ids else 1
        id_str = f"{next_id:04d}"
        
        name_str = base_name
        desc_str = ""
        stbl_entries = pkg.find(0x220557DA)
        
        stbl_data = None
        for e in stbl_entries:
            lang_byte = e.instance >> 56
            if lang_byte == 0x12: # Russian
                stbl_data = pkg.read_resource(e)
                break
        if stbl_data is None and stbl_entries:
            for e in stbl_entries:
                lang_byte = e.instance >> 56
                if lang_byte == 0x00: # English (US)
                    stbl_data = pkg.read_resource(e)
                    break
        if stbl_data is None and stbl_entries:
            stbl_data = pkg.read_resource(stbl_entries[0])
            
        if stbl_data:
            strings = {}
            if len(stbl_data) >= 21 and stbl_data[:4] == b"STBL":
                string_count = struct.unpack_from("<Q", stbl_data, 7)[0]
                pos = 21
                for _ in range(string_count):
                    if pos + 7 > len(stbl_data): break
                    key = struct.unpack_from("<I", stbl_data, pos)[0]
                    length = struct.unpack_from("<H", stbl_data, pos+5)[0]
                    pos += 7
                    if pos + length > len(stbl_data): break
                    string_bytes = stbl_data[pos:pos+length]
                    try:
                        val = string_bytes.decode("utf-8")
                    except Exception:
                        val = string_bytes.decode("latin1", errors="replace")
                    strings[key] = val
                    pos += length
                    
            if strings:
                sorted_strings = sorted(strings.values(), key=lambda s: len(s))
                if sorted_strings:
                    name_str = sorted_strings[0]
                    if len(sorted_strings) > 1:
                        desc_str = sorted_strings[1]
                        
        entry = {
            "id": id_str,
            "filename": filename,
            "name": name_str,
            "description": desc_str,
            "colors": []
        }
        db.append(entry)
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
            
    return entry["id"], entry["name"], entry["description"]


def _get_or_create_object_id(db_path: str, package_path: str, obj_name: str,
                              obj_stbl_name: str, source_instance: int) -> str:
    """Get or create a catalog ID for a specific discovered object."""
    db = []
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = []

    # Composite key to uniquely identify this object within the package
    obj_key = f"{os.path.basename(package_path)}::{obj_name}::{source_instance:016X}"

    for item in db:
        if item.get("_obj_key") == obj_key:
            return item.get("id", "0001")

    existing_ids = [int(item.get("id", 0)) for item in db if item.get("id")]
    next_id = max(existing_ids) + 1 if existing_ids else 1
    id_str = f"{next_id:04d}"

    entry = {
        "id": id_str,
        "filename": os.path.basename(package_path),
        "name": obj_name,
        "description": obj_stbl_name or "",
        "colors": [],
        "_obj_key": obj_key,
    }
    db.append(entry)

    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

    return id_str


def _classify_texture_role(name: str) -> str:
    n = name.lower()
    if "norm" in n or "_n_" in n or n.endswith("_n"):
        return "normal"
    if "spec" in n or "_s_" in n or "mask" in n or "_rough" in n:
        return "specular"
    return "diffuse"


def _is_opaque_square_palette_png(path: str) -> bool:
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
    for p in png_files:
        if os.path.exists(p):
            return [p]
    return []


def _to_common_mesh(m, rcol_obj=None):
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


def _compute_component_bbox(positions, faces, face_ids):
    used_verts = set()
    for fi in face_ids:
        for vi in faces[fi]:
            used_verts.add(vi)
    if not used_verts:
        return None
    xs = [positions[v][0] for v in used_verts]
    ys = [positions[v][1] for v in used_verts]
    zs = [positions[v][2] for v in used_verts]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _compute_component_centroid(positions, faces, face_ids):
    used_verts = set()
    for fi in face_ids:
        for vi in faces[fi]:
            used_verts.add(vi)
    if not used_verts:
        return (0, 0, 0)
    cx = sum(positions[v][0] for v in used_verts) / len(used_verts)
    cy = sum(positions[v][1] for v in used_verts) / len(used_verts)
    cz = sum(positions[v][2] for v in used_verts) / len(used_verts)
    return (cx, cy, cz)


def _extract_per_object(pkg: DBPF, opt: Options, base: str,
                        package_path: str, objects: list) -> dict:
    """Extract each discovered object in a multi-object package into its own folder.

    Called by ``extract_package()`` when ``per_object`` mode is enabled and more
    than one object is found.  Each object gets its own output subfolder named
    after its internal Sims 4 name (from OBJD/COBJ).
    """
    os.makedirs(opt.out_dir, exist_ok=True)
    db_path = os.path.join(opt.out_dir, "catalog_database.json")

    all_reports = []
    for obj_index, obj_info in enumerate(objects):
        safe_name = "".join(c if c not in '<>:"/\\|?*' else '_' for c in obj_info.name).strip()
        if not safe_name:
            safe_name = f"object_{obj_index:04d}"

        # Get or create a catalog ID for this specific object
        id_str = _get_or_create_object_id(
            db_path, package_path, safe_name,
            obj_info.stbl_name, obj_info.source_instance)

        out_root = os.path.join(opt.out_dir, f"[{id_str}] {safe_name}")

        ctx = _ObjectContext(
            pkg=pkg,
            obj_name=safe_name,
            stbl_name=obj_info.stbl_name,
            id_str=id_str,
            out_root=out_root,
            modl_tgi_filter=set(obj_info.model_tgis),
            desc_str=obj_info.stbl_name or "",
        )

        try:
            obj_report = extract_package(package_path, opt, _obj_ctx=ctx)
            all_reports.append(obj_report)
        except Exception as e:
            all_reports.append({
                "package": package_path,
                "out_dir": out_root,
                "object_name": safe_name,
                "total_resources": 0,
                "meshes": [],
                "textures": [],
                "materials": [],
                "colliders": [],
                "prefabs": [],
                "raw": [],
                "errors": [f"object {safe_name}: {e}"],
            })

    # Combine per-object reports into one top-level report
    combined = {
        "package": package_path,
        "out_dir": opt.out_dir,
        "total_resources": len(pkg.entries),
        "objects": len(objects),
        "meshes": [],
        "textures": [],
        "materials": [],
        "colliders": [],
        "prefabs": [],
        "raw": [],
        "errors": [],
    }
    for r in all_reports:
        for key in ("meshes", "textures", "materials", "colliders", "prefabs", "raw", "errors"):
            combined[key].extend(r.get(key, []))

    return combined


def extract_package(package_path: str, opt: Options,
                    _obj_ctx: _ObjectContext | None = None) -> dict:
    # Open package (use provided DBPF in per-object mode to avoid re-reading)
    if _obj_ctx is not None:
        pkg = _obj_ctx.pkg
    else:
        pkg = DBPF.from_file(package_path)
    base = os.path.splitext(os.path.basename(package_path))[0]

    # --- Per-object discovery (only at the top level, not for recursive calls) ---
    if _obj_ctx is None and opt.per_object:
        from .objd import discover_objects
        objects = discover_objects(pkg)
        if len(objects) > 1:
            return _extract_per_object(pkg, opt, base, package_path, objects)

    # Create the output directory first so the catalog can be saved inside it
    os.makedirs(opt.out_dir, exist_ok=True)

    # --- Determine name, ID, and output folder ---
    if _obj_ctx is not None:
        # Per-object mode: use the context-provided values
        safe_obj_name = _obj_ctx.obj_name
        id_str = _obj_ctx.id_str
        desc_str = _obj_ctx.desc_str
        out_root = _obj_ctx.out_root
        modl_tgi_filter = _obj_ctx.modl_tgi_filter
    else:
        # Single-folder mode: original STBL-based name logic
        db_path = os.path.join(opt.out_dir, "catalog_database.json")
        id_str, name_str, desc_str = get_or_create_catalog_entry(pkg, package_path, base, db_path)
        obj_name = name_str if name_str else base
        safe_obj_name = "".join(c if c not in '<>:"/\\|?*' else '_' for c in obj_name).strip()
        if not safe_obj_name:
            safe_obj_name = base
        out_root = os.path.join(opt.out_dir, f"[{id_str}] {safe_obj_name}")
        modl_tgi_filter = None
    os.makedirs(out_root, exist_ok=True)

    # PARTS/breakable exports are no longer part of the pipeline. Remove stale
    # generated assets when reusing an output directory so Unity cannot import
    # old part/broken meshes left by an earlier version.
    import glob as _glob_cleanup
    for pattern in ("*_part*.obj", "*_brokenA.obj", "*_brokenB.obj"):
        for stale_obj in _glob_cleanup.glob(os.path.join(out_root, pattern)):
            for stale in (stale_obj, stale_obj + ".meta"):
                try:
                    if os.path.exists(stale):
                        os.remove(stale)
                except OSError:
                    pass

    report = {
        "package": package_path,
        "out_dir": out_root,
        "total_resources": len(pkg.entries),
        "meshes": [],
        "textures": [],
        "materials": [],
        "colliders": [],
        "prefabs": [],
        "raw": [],
        "errors": [],
    }

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

    collected = []

    if opt.extract_geom:
        for i, e in enumerate(pkg.find(rt.GEOM)):
            try:
                data = pkg.read_resource(e)
                m = parse_geom(data, name=f"{safe_obj_name}_geom{i:02d}")
                if m.vertex_count and m.face_count:
                    collected.append(_to_common_mesh(m))
            except Exception as ex:
                report["errors"].append(f"GEOM {e.tgi}: {ex}")

    lod_candidates = []
    for type_id in (rt.MODL, rt.MLOD):
        priority = 0 if type_id == rt.MODL else 1
        for i, e in enumerate(pkg.find(type_id)):
            # Per-object: skip MODL/MLOD entries that don't belong to this object
            if modl_tgi_filter is not None:
                if (e.type_id, e.group_id, e.instance) not in modl_tgi_filter:
                    continue
            try:
                data = pkg.read_resource(e)
                rcol = RCOL(data)
                label = f"{safe_obj_name}_{rt.type_name(type_id).lower()}{i:02d}"
                groups = parse_object_mesh(rcol, name=label, no_cas=opt.no_cas)
                groups = [g for g in groups if g.vertex_count and g.face_count]
                if groups:
                    total = sum(g.vertex_count for g in groups)
                    lod_candidates.append((priority, total, groups, label, rcol))
            except Exception as ex:
                report["errors"].append(f"{rt.type_name(type_id)} {e.tgi}: {ex}")

    # A DBPF's physical index order and MODL/MLOD type are *not* LOD numbers.
    # In particular, an object commonly has MODL[0] and MLOD[0]; naming both
    # resources "...lod00" made Unity put both renderers into LOD 0.  Give every
    # successfully decoded model one global, deterministic LOD number instead:
    # the most detailed mesh is LOD 0, followed by decreasing vertex count.
    # This also deliberately excludes empty/shadow-only resources.
    best_lod_label = ""
    if lod_candidates:
        lod_candidates.sort(key=lambda c: (-c[1], c[0], c[3]))
        if not opt.all_lods:
            lod_candidates = [lod_candidates[0]]

        for lod_index, (_, _, groups, old_label, rcol_obj) in enumerate(lod_candidates):
            lod_label = f"{safe_obj_name}_lod{lod_index:02d}"
            if lod_index == 0:
                best_lod_label = lod_label
            for group_index, gm in enumerate(groups):
                # parse_object_mesh has already named it <old_label>_gNN.  Do
                # not derive a level from the per-type enumerate() index: each
                # type starts at zero, which was the original LOD0-only bug.
                suffix = gm.name[len(old_label):] if gm.name.startswith(old_label) else f"_g{group_index:02d}"
                gm.name = lod_label + suffix
                collected.append(_to_common_mesh(gm, rcol_obj=rcol_obj))

    mesh_records = []
    # Only LOD0 is used for breakable-part/collider geometry. Other LODs
    # remain intact visual meshes.
    def _lod_index(mesh_name):
        marker = "_lod"
        at = mesh_name.lower().rfind(marker)
        if at < 0: return -1
        digits = mesh_name[at + len(marker):]
        digits = digits.split("_", 1)[0]
        return int(digits) if digits.isdigit() else -1
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

        mesh_records.append({
            "name": name,
            "fbx": fbx_path,
            "positions": positions,
            "normals": normals,
            "faces": faces,
            "material_ref": rec.get("material_ref"),
            "rcol": rec.get("rcol"),
        })

    png_files = []
    texture_by_key = {}
    if opt.png:
        # Collect referenced texture TGI keys from material variants
        # (needed for per-object texture filtering to avoid extracting thousands
        #  of unrelated textures from multi-object packages)
        texture_tgi_filter = None
        if modl_tgi_filter is not None:
            texture_tgi_filter = set()
            for rec in mesh_records:
                if rec.get("rcol") is not None and rec.get("material_ref") is not None:
                    try:
                        variants = material_variants(rec["rcol"], rec["material_ref"])
                        for mv in variants:
                            if mv.diffuse_key:
                                texture_tgi_filter.add(mv.diffuse_key)
                            if mv.normal_key:
                                texture_tgi_filter.add(mv.normal_key)
                            if mv.specular_key:
                                texture_tgi_filter.add(mv.specular_key)
                    except Exception:
                        pass

        tex_entries = [e for e in pkg.entries if e.type_id in rt.IMAGE_TYPES]
        if texture_tgi_filter is not None:
            tex_entries = [e for e in tex_entries
                          if (e.type_id, e.group_id, e.instance) in texture_tgi_filter]
        for i, e in enumerate(tex_entries):
            try:
                data = pkg.read_resource(e)
                name = f"{safe_obj_name}_tex{i:02d}"
                p = os.path.join(out_root, name + ".png")
                status = textures.save_as_png(data, p)
                png_files.append(p)
                texture_by_key[(e.type_id, e.group_id, e.instance)] = p
                report["textures"].append({"name": name, "status": status,
                                           "file": os.path.basename(p)})
            except Exception as ex:
                report["errors"].append(f"TEX {e.tgi}: {ex}")

    material_guid = None
    mesh_material_guid_by_name = {}
    mesh_material_name_by_name = {}
    part_asset_material_pairs = []
    breakable_specs_with_material = []
    # Must exist even when PNG/material export is disabled.
    material_texture_pairs = []
    if opt.unity_mat and png_files:
        for p in png_files:
            if os.path.exists(p):
                unity.write_texture_meta(p)

        material_texture_pairs = []

        families = {}
        for rec in mesh_records:
            if rec.get("rcol") is None or rec.get("material_ref") is None:
                continue
            fam_key = rec["material_ref"]
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
            fam_key = rec.get("material_ref")
            if fam_key in family_guids:
                mesh_material_guid_by_name[rec["name"]] = family_guids[fam_key][0]
                mesh_material_name_by_name[rec["name"]] = family_names[fam_key][0]

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
                mat_name = f"{safe_obj_name}_{suffix}_material"
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

        if material_texture_pairs:
            # === Новая batch-архитектура: JSON + единый Editor-скрипт ===
            export_data = {
                "folderName": os.path.basename(out_root),
                "id": id_str,
                "assetName": safe_obj_name,
                "materials": [],
                "meshNames": [rec["name"] for rec in mesh_records],
                "colliderAssets": [],
                "meshMaterials": [{"meshName": mn, "materialName": matn}
                                   for mn, matn in mesh_material_name_by_name.items()],
            }
            
            for mat_name, albedo_file, normal_file, mask_file in material_texture_pairs:
                export_data["materials"].append({
                    "materialName": mat_name,
                    "albedoName": os.path.splitext(albedo_file)[0] if albedo_file else "",
                    "normalName": os.path.splitext(normal_file)[0] if normal_file else "",
                    "maskName": os.path.splitext(mask_file)[0] if mask_file else "",
                })
            
            json_path = unity.write_export_json(out_root, export_data, opt.out_dir)
            batch_script = unity.write_batch_editor_script(opt.out_dir, opt.mat_pipeline)
            
            report["materials"].append({
                "name": "S4ExtractBatchFixer",
                "pipeline": opt.mat_pipeline,
                "file": os.path.relpath(batch_script, out_root).replace(os.sep, "/"),
                "data_file": os.path.relpath(json_path, out_root).replace(os.sep, "/"),
                "diffuse": None,
                "normal": None,
                "specular": None,
            })

    # Update catalog entry colors in the database with automatic dominant color detection
    if material_texture_pairs:
        db_path = os.path.join(opt.out_dir, "catalog_database.json")
        if os.path.exists(db_path):
            try:
                with open(db_path, "r", encoding="utf-8") as f:
                    db = json.load(f)
                # In per-object mode, match by id; otherwise by filename
                lookup_key = id_str if modl_tgi_filter is not None else os.path.basename(package_path)
                for item in db:
                    match = False
                    if modl_tgi_filter is not None:
                        match = item.get("id") == lookup_key
                    else:
                        match = item.get("filename") == lookup_key
                    if not match:
                        continue
                    swatches_found = []
                    seen_mat_names = set()

                    COLOR_NAMES = {
                        "Black": (30, 30, 30),
                        "White": (240, 240, 240),
                        "Grey": (128, 128, 128),
                        "Dark Grey": (70, 70, 70),
                        "Light Grey": (190, 190, 190),
                        "Red": (200, 30, 30),
                        "Dark Red": (130, 20, 20),
                        "Orange": (220, 120, 30),
                        "Yellow": (220, 220, 30),
                        "Green": (30, 150, 30),
                        "Dark Green": (20, 80, 20),
                        "Blue": (30, 30, 200),
                        "Dark Blue": (15, 30, 80),
                        "Light Blue": (150, 200, 240),
                        "Purple": (120, 30, 120),
                        "Pink": (240, 150, 180),
                        "Brown": (100, 60, 30),
                        "Dark Brown": (60, 35, 15),
                        "Beige": (220, 200, 170),
                        "Cream": (255, 250, 220),
                        "Teal": (0, 128, 128),
                        "Burgundy": (128, 0, 32),
                        "Olive": (100, 110, 30),
                        "Coral": (255, 127, 80),
                        "Navy": (20, 20, 80),
                        "Turquoise": (64, 200, 200),
                        "Gold": (200, 170, 50),
                        "Silver": (180, 185, 190),
                        "Tan": (210, 180, 140),
                    }

                    def get_image_dominant_color_hex(png_path):
                        try:
                            from PIL import Image as _PILImage
                            im = _PILImage.open(png_path).convert("RGB")
                            im = im.resize((64, 64))

                            quantized = im.quantize(colors=16, method=2)
                            palette = quantized.getpalette()
                            color_counts = Counter(quantized.getdata())

                            scored = []
                            for idx, count in color_counts.items():
                                r = palette[idx * 3]
                                g = palette[idx * 3 + 1]
                                b = palette[idx * 3 + 2]
                                if r > 220 and g > 220 and b > 220:
                                    continue
                                if r < 30 and g < 30 and b < 30:
                                    continue
                                _h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                                score = count * (0.3 + 0.7 * s)
                                scored.append((score, r, g, b))

                            if scored:
                                scored.sort(reverse=True)
                                _, r, g, b = scored[0]
                                return f"#{r:02x}{g:02x}{b:02x}"

                            most_idx = color_counts.most_common(1)[0][0]
                            r = palette[most_idx * 3]
                            g = palette[most_idx * 3 + 1]
                            b = palette[most_idx * 3 + 2]
                            return f"#{r:02x}{g:02x}{b:02x}"
                        except Exception:
                            return "#FFFFFF"

                    def get_closest_color_name(hex_str):
                        h = hex_str.lstrip('#')
                        r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
                        best_name = "Unknown"
                        best_dist = float('inf')
                        for name, rgb in COLOR_NAMES.items():
                            dist = (r - rgb[0])**2 + (g - rgb[1])**2 + (b - rgb[2])**2
                            if dist < best_dist:
                                best_dist = dist
                                best_name = name
                        return best_name

                    for mat_name, albedo_file, _, _ in material_texture_pairs:
                        if mat_name in seen_mat_names:
                            continue
                        seen_mat_names.add(mat_name)

                        parts = mat_name.split("_")
                        swatch_part = None
                        for p in parts:
                            if p.startswith("swatch"):
                                swatch_part = p
                                break
                            if not swatch_part:
                                swatch_part = mat_name

                            hex_color = "#FFFFFF"
                            color_name = "White"
                            if albedo_file:
                                full_albedo_path = os.path.join(out_root, albedo_file)
                                if os.path.exists(full_albedo_path):
                                    hex_color = get_image_dominant_color_hex(full_albedo_path)
                                    color_name = get_closest_color_name(hex_color)

                            swatches_found.append({
                                "swatch": swatch_part,
                                "hex": hex_color,
                                "name": color_name
                            })

                        item["colors"] = sorted(swatches_found, key=lambda x: x["swatch"])
                        break
                with open(db_path, "w", encoding="utf-8") as f:
                    json.dump(db, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    for rec in mesh_records:
        if rec.get("fbx"):
            unity.write_fbx_meta(rec["fbx"], mesh_material_guid_by_name.get(rec["name"], material_guid))

    if (opt.colliders or opt.prefab):
        lod0_records = [rec for rec in mesh_records if _lod_index(rec["name"]) == 0]
        if lod0_records:
            owner = lod0_records[0]
            name = owner["name"]
            agg_positions = []
            agg_normals = []
            agg_faces = []
            normals_complete = True
            for rec in lod0_records:
                offset = len(agg_positions)
                positions = rec["positions"]
                normals = rec.get("normals") or []
                agg_positions.extend(positions)
                agg_faces.extend(tuple(offset + vi for vi in face) for face in rec["faces"])
                if len(normals) == len(positions):
                    agg_normals.extend(normals)
                else:
                    normals_complete = False
            if not normals_complete:
                agg_normals = []

            cset = None
            collider_guids = []
            collider_asset_names = []
            if opt.colliders:
                import glob as _glob
                for old_rec in lod0_records:
                    pattern = os.path.join(out_root, old_rec["name"] + "_collider*.obj")
                    for old_obj in _glob.glob(pattern):
                        for stale in (old_obj, old_obj + ".meta"):
                            try:
                                if os.path.exists(stale):
                                    os.remove(stale)
                            except OSError:
                                pass

                cset = col.build_colliders(
                    agg_positions, agg_faces,
                    normals=agg_normals if agg_normals else None,
                    max_hulls=opt.max_hulls,
                    max_verts_per_hull=opt.max_verts_per_hull,
                    merge_convex_neighbors=opt.merge_convex_neighbors,
                    merge_max_inflation=opt.merge_max_inflation,
                    merge_contact_epsilon=opt.merge_contact_epsilon,
                    merge_max_deviation_ratio=opt.merge_max_deviation_ratio,
                    concavity_threshold=opt.concavity_threshold)
                for ci, part in enumerate(cset.convex_parts):
                    if not part or not part.vertices or not part.faces:
                        continue
                    asset_name = f"{safe_obj_name}_collider{ci:02d}"
                    cobj = os.path.join(out_root, asset_name + ".obj")
                    cguid = unity.write_collider_obj(cobj, part)
                    if cguid:
                        collider_guids.append(cguid)
                        collider_asset_names.append(asset_name)

                data_path = os.path.join(
                    opt.out_dir, "S4Extract_Data", os.path.basename(out_root) + ".json")
                if os.path.exists(data_path):
                    try:
                        with open(data_path, "r", encoding="utf-8") as f:
                            export_payload = json.load(f)
                        export_payload["colliderAssets"] = collider_asset_names
                        with open(data_path, "w", encoding="utf-8") as f:
                            json.dump(export_payload, f, indent=2, ensure_ascii=False)
                    except Exception as ex:
                        report["errors"].append(f"collider JSON update: {ex}")

                kind_counts = {}
                for part in cset.convex_parts:
                    if not part or not part.vertices or not part.faces:
                        continue
                    kind = getattr(part, "kind", "convex")
                    kind_counts[kind] = kind_counts.get(kind, 0) + 1
                report["colliders"].append({
                    "name": safe_obj_name,
                    "source_groups": len(lod0_records),
                    "method": cset.method,
                    "parts": len(collider_guids),
                    "kinds": kind_counts,
                    "target_budget": opt.max_hulls,
                    "over_budget": opt.max_hulls > 0 and len(collider_guids) > opt.max_hulls,
                })

            if opt.prefab and owner["fbx"]:
                rec_mat_guid = mesh_material_guid_by_name.get(name, material_guid)
                fbx_guid = unity.write_fbx_meta(owner["fbx"], rec_mat_guid)
                prefab_path = os.path.join(out_root, safe_obj_name + ".prefab")
                unity.write_prefab(
                    prefab_path, safe_obj_name, fbx_guid, rec_mat_guid,
                    cset, collider_guids, dynamic=opt.dynamic)
                report["prefabs"].append({
                    "name": safe_obj_name,
                    "file": os.path.basename(prefab_path),
                    "collider_method": cset.method if cset else "none",
                    "collider_parts": len(collider_guids),
                    "dynamic": opt.dynamic,
                })

    return report
