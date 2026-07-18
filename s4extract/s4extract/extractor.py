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
    all_lods: bool = True   # по умолчанию извлекаем все LOD-уровни
    parts_prefab: bool = True
    no_cas: bool = True     # по умолчанию пропускаем CAS (одежда/волосы)
    extract_geom: bool = False  # по умолчанию не извлекаем GEOM (создаёт мусор "default")


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


def _group_group_group_parts_semantically(mesh_name: str, positions, normals, uvs, faces, min_faces_per_part=10):
    """
    Group connected components into semantic parts based on spatial proximity
    and geometric heuristics.
    
    Strategy:
    1. Split into connected components (vertex-connected islands)
    2. Filter out tiny components (< min_faces_per_part faces) - merge into nearest larger component
    3. Cluster remaining components by spatial proximity with strong Y-weight
    4. Detect "objects on top" via vertical gaps and separate them
    4. Filter out debris parts that are too small to be meaningful removable parts
    """
    if not positions or not faces:
        return []

    # Step 1: Get connected components with metadata
    vert_to_faces = [[] for _ in range(len(positions))]
    for fi, (a, b, c) in enumerate(faces):
        if 0 <= a < len(positions):
            vert_to_faces[a].append(fi)
        if 0 <= b < len(positions):
            vert_to_faces[b].append(fi)
        if 0 <= c < len(positions):
            vert_to_faces[c].append(fi)

    seen = [False] * len(faces)
    components = []
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
        if comp_faces:
            bbox = _compute_component_bbox(positions, faces, comp_faces)
            centroid = _compute_component_centroid(positions, faces, comp_faces)
            # Also compute Y-range for vertical gap detection
            ys = [positions[v][1] for fi in comp_faces for v in faces[fi] if 0 <= v < len(positions)]
            min_y = min(ys) if ys else 0
            max_y = max(ys) if ys else 0
            components.append({
                'face_ids': comp_faces,
                'face_count': len(comp_faces),
                'bbox': bbox,
                'centroid': centroid,
                'min_y': min_y,
                'max_y': max_y,
            })

    if not components:
        return []

    # Step 2: Separate large and tiny components
    # Increase threshold: parts with < 20 faces are likely debris/decorations
    DEBRIS_THRESHOLD = max(min_faces_per_part, 20)
    large_comps = [c for c in components if c['face_count'] >= DEBRIS_THRESHOLD]
    tiny_comps = [c for c in components if c['face_count'] < DEBRIS_THRESHOLD]

    # If no large components, fall back to connected components (but merged)
    if not large_comps:
        all_faces = []
        for c in components:
            all_faces.extend(c['face_ids'])
        return [_mesh_from_face_ids(f"{mesh_name}_part00", positions, normals, uvs, faces, all_faces)]

    # Step 3: Merge tiny components into nearest large component
    for tiny in tiny_comps:
        tx, ty, tz = tiny['centroid']
        best_idx = 0
        best_dist = float('inf')
        for i, large in enumerate(large_comps):
            lx, ly, lz = large['centroid']
            # Weight Y MORE for furniture (vertical separation = different objects)
            dist = math.sqrt((tx - lx)**2 + (ty - ly)**2 * 2.0 + (tz - lz)**2)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        large_comps[best_idx]['face_ids'].extend(tiny['face_ids'])
        large_comps[best_idx]['face_count'] += tiny['face_count']
        # Update bbox
        tb = tiny['bbox']
        lb = large_comps[best_idx]['bbox']
        if tb and lb:
            large_comps[best_idx]['bbox'] = (
                min(lb[0], tb[0]), min(lb[1], tb[1]), min(lb[2], tb[2]),
                max(lb[3], tb[3]), max(lb[4], tb[4]), max(lb[5], tb[5])
            )
            # Update Y-range
            large_comps[best_idx]['min_y'] = min(large_comps[best_idx]['min_y'], tiny['min_y'])
            large_comps[best_idx]['max_y'] = max(large_comps[best_idx]['max_y'], tiny['max_y'])

    # Step 4: Detect vertical gaps (objects on top of other objects)
    # Sort by min_y (bottom to top)
    large_comps.sort(key=lambda c: c['min_y'])
    
    # Check for significant Y-gaps between components
    # If there's a gap > 5% of object height, they're likely separate objects stacked vertically
    all_min_y = min(c['min_y'] for c in large_comps)
    all_max_y = max(c['max_y'] for c in large_comps)
    obj_height = all_max_y - all_min_y
    vertical_gap_threshold = max(obj_height * 0.05, 0.05)  # 5% of height or 5cm
    
    # Split into vertical layers where gaps exist
    vertical_layers = []
    current_layer = [0]
    for i in range(1, len(large_comps)):
        prev_max_y = large_comps[i-1]['max_y']
        curr_min_y = large_comps[i]['min_y']
        gap = curr_min_y - prev_max_y
        if gap > vertical_gap_threshold:
            # Significant vertical gap - new layer
            vertical_layers.append(current_layer)
            current_layer = [i]
        else:
            current_layer.append(i)
    vertical_layers.append(current_layer)
    
    # Step 5: Within each vertical layer, cluster horizontally
    final_clusters = []
    for layer in vertical_layers:
        if len(layer) == 1:
            final_clusters.append([layer[0]])
            continue
        
        # Agglomerative clustering within this layer (XZ plane only)
        layer_comps = [large_comps[i] for i in layer]
        all_bboxes = [c['bbox'] for c in layer_comps]
        obj_min_x = min(b[0] for b in all_bboxes)
        obj_min_z = min(b[2] for b in all_bboxes)
        obj_max_x = max(b[3] for b in all_bboxes)
        obj_max_z = max(b[5] for b in all_bboxes)
        obj_diag_xz = math.sqrt((obj_max_x - obj_min_x)**2 + (obj_max_z - obj_min_z)**2)
        cluster_threshold = max(obj_diag_xz * 0.2, 0.15)  # 20% of XZ diagonal or 15cm
        
        clusters = [[i] for i in range(len(layer_comps))]
        changed = True
        while changed and len(clusters) > 1:
            changed = False
            best_merge = None
            best_dist = cluster_threshold
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    # Distance in XZ plane only (ignore Y within layer)
                    ci_centroids = [layer_comps[idx]['centroid'] for idx in clusters[i]]
                    cj_centroids = [layer_comps[idx]['centroid'] for idx in clusters[j]]
                    cx1 = sum(c[0] for c in ci_centroids) / len(ci_centroids)
                    cz1 = sum(c[2] for c in ci_centroids) / len(ci_centroids)
                    cx2 = sum(c[0] for c in cj_centroids) / len(cj_centroids)
                    cz2 = sum(c[2] for c in cj_centroids) / len(cj_centroids)
                    dist = math.sqrt((cx1 - cx2)**2 + (cz1 - cz2)**2)
                    if dist < best_dist:
                        best_dist = dist
                        best_merge = (i, j)
            if best_merge:
                i, j = best_merge
                clusters[i].extend(clusters[j])
                clusters.pop(j)
                changed = True
        
        # Map back to global indices
        for cluster in clusters:
            final_clusters.append([layer[idx] for idx in cluster])

    # Step 6: Filter out clusters that are too small to be meaningful removable parts
    # A removable part should have at least 50 faces (configurable)
    MIN_REMOVABLE_FACES = 50
    filtered_clusters = []
    for cluster in final_clusters:
        total_faces = sum(large_comps[idx]['face_count'] for idx in cluster)
        if total_faces >= MIN_REMOVABLE_FACES:
            filtered_clusters.append(cluster)
        else:
            # Too small - merge into nearest larger cluster
            if filtered_clusters:
                # Find nearest cluster centroid
                cluster_centroid = (
                    sum(large_comps[idx]['centroid'][0] for idx in cluster) / len(cluster),
                    sum(large_comps[idx]['centroid'][1] for idx in cluster) / len(cluster),
                    sum(large_comps[idx]['centroid'][2] for idx in cluster) / len(cluster)
                )
                best_idx = 0
                best_dist = float('inf')
                for fi, fcluster in enumerate(filtered_clusters):
                    f_centroid = (
                        sum(large_comps[idx]['centroid'][0] for idx in fcluster) / len(fcluster),
                        sum(large_comps[idx]['centroid'][1] for idx in fcluster) / len(fcluster),
                        sum(large_comps[idx]['centroid'][2] for idx in fcluster) / len(fcluster)
                    )
                    dist = math.sqrt(
                        (cluster_centroid[0] - f_centroid[0])**2 +
                        (cluster_centroid[1] - f_centroid[1])**2 * 2.0 +
                        (cluster_centroid[2] - f_centroid[2])**2
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = fi
                filtered_clusters[best_idx].extend(cluster)
            else:
                # No other clusters, keep it anyway
                filtered_clusters.append(cluster)

    # Step 7: Build final parts from clusters
    parts = []
    for ci, cluster in enumerate(filtered_clusters):
        all_face_ids = []
        for comp_idx in cluster:
            all_face_ids.extend(large_comps[comp_idx]['face_ids'])
        parts.append(_mesh_from_face_ids(
            f"{mesh_name}_part{ci:02d}", positions, normals, uvs, faces, all_face_ids))

    return parts


# Псевдоним для совместимости
def _group_parts_semantically(mesh_name: str, positions, normals, uvs, faces, min_faces_per_part=10):
    return _group_group_group_parts_semantically(mesh_name, positions, normals, uvs, faces, min_faces_per_part)


def _pick_break_axis(positions):
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    ex = max(xs) - min(xs)
    ey = max(ys) - min(ys)
    ez = max(zs) - min(zs)
    horiz_axis = 0 if ex >= ez else 2
    horiz_extent = ex if ex >= ez else ez
    if horiz_extent >= ey * 0.45:
        return horiz_axis
    ext = [ex, ey, ez]
    return max(range(3), key=lambda i: ext[i])


def _analyze_part_shape(positions):
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    ex = max(xs) - min(xs)
    ey = max(ys) - min(ys)
    ez = max(zs) - min(zs)
    min_y = min(ys)
    max_y = max(ys)
    center_y = (min_y + max_y) * 0.5

    is_flat_horizontal = ey < ex * 0.3 and ey < ez * 0.3
    is_tall_vertical = ey > ex * 2.0 and ey > ez * 2.0
    is_leg_like = ey > ex * 1.5 and ey > ez * 1.5 and ex < 0.3 and ez < 0.3
    is_base_low = center_y < ey * 0.4

    return {
        'extents': (ex, ey, ez),
        'is_flat_horizontal': is_flat_horizontal,
        'is_tall_vertical': is_tall_vertical,
        'is_leg_like': is_leg_like,
        'is_base_low': is_base_low,
        'min_y': min_y,
        'max_y': max_y,
    }


def _pick_semantic_break_axis(positions):
    shape = _analyze_part_shape(positions)
    ex, ey, ez = shape['extents']

    if shape['is_flat_horizontal']:
        return 0 if ex >= ez else 2

    if shape['is_tall_vertical']:
        return 0 if ex <= ez else 2

    if shape['is_leg_like']:
        return 0 if ex <= ez else 2

    if shape['is_base_low']:
        if ex >= ez and ex >= ey * 0.5:
            return 0
        elif ez >= ex and ez >= ey * 0.5:
            return 2

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
    if mesh.face_count < 6 or mesh.vertex_count < 6:
        return []

    shape = _analyze_part_shape(mesh.positions)

    if shape['is_leg_like'] and mesh.face_count < 50:
        return []

    if shape['is_base_low'] and mesh.face_count < 30:
        return []

    primary = _pick_semantic_break_axis(mesh.positions)
    axes = [primary] + [ax for ax in (0, 1, 2) if ax != primary]
    for axis in axes:
        halves = _split_mesh_by_axis(mesh, axis)
        if len(halves) == 2 and halves[0].face_count and halves[1].face_count:
            return [close_open_boundaries(h) for h in halves]
    return []


def extract_package(package_path: str, opt: Options) -> dict:
    pkg = DBPF.from_file(package_path)
    base = os.path.splitext(os.path.basename(package_path))[0]
    
    # Create the output directory first so the catalog can be saved inside it
    os.makedirs(opt.out_dir, exist_ok=True)
    db_path = os.path.join(opt.out_dir, "catalog_database.json")
    id_str, name_str, desc_str = get_or_create_catalog_entry(pkg, package_path, base, db_path)
    
    out_root = os.path.join(opt.out_dir, f"[{id_str}] {base}")
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
                m = parse_geom(data, name=f"{base}_geom{i:02d}")
                if m.vertex_count and m.face_count:
                    collected.append(_to_common_mesh(m))
            except Exception as ex:
                report["errors"].append(f"GEOM {e.tgi}: {ex}")

    lod_candidates = []
    for type_id in (rt.MODL, rt.MLOD):
        priority = 0 if type_id == rt.MODL else 1
        for i, e in enumerate(pkg.find(type_id)):
            try:
                data = pkg.read_resource(e)
                rcol = RCOL(data)
                label = f"{base}_{rt.type_name(type_id).lower()}{i:02d}"
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
            lod_label = f"{base}_lod{lod_index:02d}"
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

        parts = _group_parts_semantically(name, positions, normals, uvs, faces, min_faces_per_part=10) if opt.parts_prefab else []
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
            "normals": normals,
            "faces": faces,
            "material_ref": rec.get("material_ref"),
            "rcol": rec.get("rcol"),
            "part_asset_names": part_asset_names,
        })

    png_files = []
    texture_by_key = {}
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

    material_guid = None
    mesh_material_guid_by_name = {}
    mesh_material_name_by_name = {}
    part_asset_material_pairs = []
    breakable_specs_with_material = []
    if opt.unity_mat and png_files:
        for p in png_files:
            if os.path.exists(p):
                unity.write_texture_meta(p)

        material_texture_pairs = []

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
            # === Новая batch-архитектура: JSON + единый Editor-скрипт ===
            export_data = {
                "folderName": os.path.basename(out_root),
                "id": id_str,
                "assetName": os.path.basename(out_root).split("] ", 1)[-1] if "] " in os.path.basename(out_root) else os.path.basename(out_root),
                "materials": [],
                "meshNames": [rec["name"] for rec in mesh_records],
                "meshMaterials": [{"meshName": mn, "materialName": matn} 
                                   for mn, matn in mesh_material_name_by_name.items()],
                "partAssets": [{"assetName": an, "materialName": matn} 
                              for an, matn in part_asset_material_pairs],
                "breakSpecs": [{"intactAssetName": intact, "materialName": mat, "brokenAssetNames": broken}
                              for intact, mat, broken in breakable_specs_with_material],
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
                filename = os.path.basename(package_path)
                for item in db:
                    if item.get("filename") == filename:
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
                            """Find the dominant *colored* region of a diffuse texture.

                            Sims 4 diffuse textures typically have the actual object UVs
                            occupying only a fraction of the image; the unused UV space
                            is filled with white or near-white.  The old approach of
                            simply taking the most-frequent pixel returned white for
                            almost every texture.  Instead we:
                              1. Quantise the image to a small palette (16 colours).
                              2. Discard near-white and near-black palette entries
                                 (background / borders).
                              3. Rank remaining entries by  count × saturation  so that
                                 a vivid but less-frequent colour beats a pale but
                                 more-frequent one.
                            """
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
                                    # Skip near-white (unused UV / background)
                                    if r > 220 and g > 220 and b > 220:
                                        continue
                                    # Skip near-black (borders / shadows)
                                    if r < 30 and g < 30 and b < 30:
                                        continue
                                    # Weight by saturation so vivid colours win
                                    _h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                                    score = count * (0.3 + 0.7 * s)
                                    scored.append((score, r, g, b))

                                if scored:
                                    scored.sort(reverse=True)
                                    _, r, g, b = scored[0]
                                    return f"#{r:02x}{g:02x}{b:02x}"

                                # Fallback: most frequent colour (no filtering worked)
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
        for rec in mesh_records:
            name = rec["name"]
            cset = None
            collider_guids = []
            if opt.colliders:
                cset = col.build_colliders(rec["positions"], rec["faces"],
                                           normals=rec.get("normals"),
                                           max_hulls=opt.max_hulls,
                                           merge_convex_neighbors=opt.merge_convex_neighbors)
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