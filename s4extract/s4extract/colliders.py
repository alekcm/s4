"""Generate collider data for Unity from a mesh using loose parts (connected components) decomposition.

For a role-play game where furniture is dynamic (Rigidbody) and must support
cavities (cup inside a drawer), a single mesh collider is not viable in PhysX:
  - non-convex MeshCollider cannot be used with a moving Rigidbody
  - convex MeshCollider seals cavities

The robust approach is a COMPOUND collider: one Rigidbody + several convex
parts (one for each loose/connected component of the mesh). This module
splits the mesh into its disconnected topological components while taking surface
normals into account to prevent perpendicular connected pieces (like a vertical pole
meeting a flat base plate) from being welded together, producing extremely accurate
hulls.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class ConvexPart:
    vertices: list           # list of (x,y,z)
    faces: list              # list of (a,b,c)


@dataclass
class ColliderSet:
    bbox_min: tuple = (0.0, 0.0, 0.0)
    bbox_max: tuple = (0.0, 0.0, 0.0)
    convex_parts: list = field(default_factory=list)  # list[ConvexPart]
    method: str = "none"     # "loose_parts" | "convexhull" | "box"


def _compute_bbox(positions):
    if not positions:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _dot(v1, v2):
    return v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]


def _thicken_vertices(vertices: list[tuple[float, float, float]], min_thickness: float = 0.02) -> list[tuple[float, float, float]]:
    """Thicken a flat 2D shape along its thinnest dimension to make it a valid 3D shape with volume."""
    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    zs = [p[2] for p in vertices]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    
    min_val = min(dx, dy, dz)
    if min_val >= min_thickness:
        return vertices
        
    thickened = [list(p) for p in vertices]
    half_thick = min_thickness * 0.5
    
    if dx == min_val:
        mid_x = (min(xs) + max(xs)) * 0.5
        if dx < 1e-5:
            for i in range(len(thickened)):
                thickened[i][0] += -half_thick if (i % 2 == 0) else half_thick
        else:
            for i in range(len(thickened)):
                thickened[i][0] = (mid_x - half_thick) if (thickened[i][0] <= mid_x) else (mid_x + half_thick)
    elif dy == min_val:
        mid_y = (min(ys) + max(ys)) * 0.5
        if dy < 1e-5:
            for i in range(len(thickened)):
                thickened[i][1] += -half_thick if (i % 2 == 0) else half_thick
        else:
            for i in range(len(thickened)):
                thickened[i][1] = (mid_y - half_thick) if (thickened[i][1] <= mid_y) else (mid_y + half_thick)
    else:
        mid_z = (min(zs) + max(zs)) * 0.5
        if dz < 1e-5:
            for i in range(len(thickened)):
                thickened[i][2] += -half_thick if (i % 2 == 0) else half_thick
        else:
            for i in range(len(thickened)):
                thickened[i][2] = (mid_z - half_thick) if (thickened[i][2] <= mid_z) else (mid_z + half_thick)
                
    return [tuple(p) for p in thickened]


def build_colliders(positions, faces,
                    normals: list[tuple[float, float, float]] | None = None,
                    max_hulls: int = 128,
                    max_verts_per_hull: int = 64) -> ColliderSet:
    """Return a ColliderSet split by connected components (loose parts).

    Tries to find connected components of the mesh (loose parts) with surface-normal
    filtering (to keep perpendicular parts like table poles vs bases separate),
    then builds convex hulls for each part via scipy.spatial.ConvexHull.
    """
    cs = ColliderSet()
    cs.bbox_min, cs.bbox_max = _compute_bbox(positions)

    if not positions or not faces:
        cs.method = "box"
        return cs

    try:
        # Step 1: Weld duplicate vertex positions.
        # If normals are provided, we only weld vertices if their normals are close
        # (normal_threshold_cos = 0.15 representing ~80 degrees).
        # This keeps perpendicular touching surfaces (like vertical cylinders on flat bases) separated.
        use_normals = normals is not None and len(normals) == len(positions)
        normal_threshold_cos = 0.15
        decimals = 5
        
        unique_pos_and_normal_to_idx = []  # list of (rounded_pos, normal, index)
        vertex_map = []
        
        for i, p in enumerate(positions):
            rounded_p = (round(p[0], decimals), round(p[1], decimals), round(p[2], decimals))
            n = normals[i] if use_normals else (0.0, 1.0, 0.0)
            
            found_idx = None
            if use_normals:
                for up, un, uidx in unique_pos_and_normal_to_idx:
                    if up == rounded_p:
                        if _dot(un, n) >= normal_threshold_cos:
                            found_idx = uidx
                            break
            else:
                # Fallback to position-only mapping
                for up, un, uidx in unique_pos_and_normal_to_idx:
                    if up == rounded_p:
                        found_idx = uidx
                        break
                        
            if found_idx is None:
                found_idx = len(unique_pos_and_normal_to_idx)
                unique_pos_and_normal_to_idx.append((rounded_p, n, found_idx))
                
            vertex_map.append(found_idx)

        # Build adjacency: vertex index to faces
        vert_to_faces = [[] for _ in range(len(unique_pos_and_normal_to_idx))]
        for fi, (a, b, c) in enumerate(faces):
            wa = vertex_map[a]
            wb = vertex_map[b]
            wc = vertex_map[c]
            for vi in (wa, wb, wc):
                if 0 <= vi < len(unique_pos_and_normal_to_idx):
                    vert_to_faces[vi].append(fi)

        # Step 2: Find connected components of faces (loose parts) using BFS
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
                wa, wb, wc = vertex_map[a], vertex_map[b], vertex_map[c]
                for vi in (wa, wb, wc):
                    if not (0 <= vi < len(vert_to_faces)):
                        continue
                    for nfi in vert_to_faces[vi]:
                        if not seen[nfi]:
                            seen[nfi] = True
                            q.append(nfi)
                            
            if comp_faces:
                components.append(comp_faces)

        # Step 3: Rebuild local component lists and filter/merge tiny pieces.
        # We merge "tiny" components (fewer than min_verts vertices, like screws/decorations)
        # into the nearest larger component to avoid cluttering PhysX with too many colliders.
        min_verts = 12
        comp_list = []
        for comp_faces in components:
            used_verts = sorted(list(set(vi for fi in comp_faces for vi in faces[fi])))
            comp_pts = [positions[i] for i in used_verts]
            xs = [pt[0] for pt in comp_pts]
            ys = [pt[1] for pt in comp_pts]
            zs = [pt[2] for pt in comp_pts]
            centroid = (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
            comp_list.append({
                'faces': comp_faces,
                'verts': used_verts,
                'centroid': centroid,
                'vert_count': len(used_verts)
            })

        large_comps = [c for c in comp_list if c['vert_count'] >= min_verts]
        small_comps = [c for c in comp_list if c['vert_count'] < min_verts]

        if large_comps:
            for sm in small_comps:
                sx, sy, sz = sm['centroid']
                best_lg = None
                best_dist = float('inf')
                for lg in large_comps:
                    lx, ly, lz = lg['centroid']
                    dist = math.sqrt((sx - lx)**2 + (sy - ly)**2 + (sz - lz)**2)
                    if dist < best_dist:
                        best_dist = dist
                        best_lg = lg
                best_lg['faces'].extend(sm['faces'])
                best_lg['verts'] = sorted(list(set(best_lg['verts'] + sm['verts'])))
            final_comps = large_comps
        else:
            final_comps = comp_list

        # Step 4: For each remaining component, build a convex hull
        good_parts = []
        import numpy as np
        has_scipy = False
        try:
            from scipy.spatial import ConvexHull
            has_scipy = True
        except ImportError:
            pass

        for comp in final_comps:
            comp_faces = comp['faces']
            used_verts = comp['verts']
            if len(used_verts) < 4:
                continue  # skip components with < 4 vertices (cannot cook a 3D convex hull)
                
            comp_pts = [positions[i] for i in used_verts]
            xs = [pt[0] for pt in comp_pts]
            ys = [pt[1] for pt in comp_pts]
            zs = [pt[2] for pt in comp_pts]
            dx = max(xs) - min(xs)
            dy = max(ys) - min(ys)
            dz = max(zs) - min(zs)
            min_dim = min(dx, dy, dz)
            min_y = min(ys)
            
            # 1. Skip if it is a floor shadow plane (completely flat and sitting on the ground)
            if min_y < 0.015 and dy < 0.005:
                continue
                
            # 2. Skip if it is a small flat AO card / decorative helper
            # (less than 8 vertices and very flat)
            if len(used_verts) < 8 and min_dim < 0.01:
                continue
                
            # 3. Do not fabricate a 2 cm solid from a one-sided, zero-thickness
            # render card. Sims objects contain many such helper/decal faces. A
            # convex hull of one creates the conspicuous "phantom" wedges in
            # Unity, often far larger than the visible face. Real shelves and
            # glass normally have front/back faces and physical thickness, so
            # they reach this point with a non-zero min_dim and are retained.
            #
            # Use geometric triangle normals here rather than imported vertex
            # normals: vertex normals are split at UV seams and are unsuitable
            # for deciding whether the component is a single render surface.
            flat_epsilon = 1e-4
            if min_dim < flat_epsilon:
                normal_sum = [0.0, 0.0, 0.0]
                normal_count = 0
                for ia, ib, ic in (faces[fi] for fi in comp_faces):
                    ax, ay, az = positions[ia]
                    bx, by, bz = positions[ib]
                    cx, cy, cz = positions[ic]
                    ux, uy, uz = bx - ax, by - ay, bz - az
                    vx, vy, vz = cx - ax, cy - ay, cz - az
                    nx = uy * vz - uz * vy
                    ny = uz * vx - ux * vz
                    nz = ux * vy - uy * vx
                    length = math.sqrt(nx * nx + ny * ny + nz * nz)
                    if length > 1e-10:
                        normal_sum[0] += nx / length
                        normal_sum[1] += ny / length
                        normal_sum[2] += nz / length
                        normal_count += 1
                if normal_count:
                    coherence = math.sqrt(sum(v * v for v in normal_sum)) / normal_count
                    # A value near 1 means every triangle faces the same way:
                    # this is an open render card, not a solid object part.
                    if coherence > 0.95:
                        continue

            # A non-flat thin surface is an actual piece of geometry. Give it
            # the minimum volume required by Unity's convex MeshCollider.
            if min_dim < 0.02:
                comp_pts = _thicken_vertices(comp_pts, min_thickness=0.02)
                
            remap = {old: new for new, old in enumerate(used_verts)}
            comp_tris = [tuple(remap[vi] for vi in faces[fi]) for fi in comp_faces]
            
            hull_built = False
            if has_scipy and len(comp_pts) >= 4:
                try:
                    pts = np.asarray(comp_pts, dtype=np.float64)
                    hull = ConvexHull(pts)
                    hv_idx = sorted(set(int(i) for i in hull.vertices))
                    hremap = {old: new for new, old in enumerate(hv_idx)}
                    hverts = [tuple(map(float, pts[i])) for i in hv_idx]
                    hfaces = []
                    for simplex in hull.simplices:
                        tri = tuple(hremap[int(i)] for i in simplex)
                        hfaces.append(tri)
                    good_parts.append(ConvexPart(vertices=hverts, faces=hfaces))
                    hull_built = True
                except Exception:
                    pass
            
            if not hull_built:
                good_parts.append(ConvexPart(vertices=comp_pts, faces=comp_tris))
                
        if good_parts:
            if len(good_parts) > max_hulls:
                good_parts.sort(key=lambda p: len(p.vertices), reverse=True)
                good_parts = good_parts[:max_hulls]
            cs.convex_parts = good_parts
            cs.method = "loose_parts"
            return cs

    except Exception:
        pass

    # Fallback to single convex hull
    try:
        import numpy as np
        from scipy.spatial import ConvexHull
        pts = np.asarray(positions, dtype=np.float64)
        hull = ConvexHull(pts)
        hv_idx = sorted(set(int(i) for i in hull.vertices))
        remap = {old: new for new, old in enumerate(hv_idx)}
        hverts = [tuple(map(float, pts[i])) for i in hv_idx]
        hfaces = []
        for simplex in hull.simplices:
            tri = tuple(remap[int(i)] for i in simplex)
            hfaces.append(tri)
        cs.convex_parts = [ConvexPart(vertices=hverts, faces=hfaces)]
        cs.method = "convexhull"
        return cs
    except Exception:
        pass

    cs.method = "box"
    return cs
