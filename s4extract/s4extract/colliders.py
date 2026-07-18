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

When a connected component is still significantly concave (e.g. a chair whose
legs and seat form one continuous mesh), a recursive convex decomposition step
splits it along a plane through the deepest concavity, producing separate colliders
for each leg and the seat.
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


def _bbox_distance(a: ConvexPart, b: ConvexPart) -> float:
    """Shortest distance between axis-aligned bounds of two convex parts."""
    amin, amax = _compute_bbox(a.vertices)
    bmin, bmax = _compute_bbox(b.vertices)
    squared = 0.0
    for axis in range(3):
        if amax[axis] < bmin[axis]:
            delta = bmin[axis] - amax[axis]
        elif bmax[axis] < amin[axis]:
            delta = amin[axis] - bmax[axis]
        else:
            delta = 0.0
        squared += delta * delta
    return math.sqrt(squared)


def _merge_nearly_convex_neighbors(parts: list[ConvexPart],
                                   max_inflation: float = 0.05,
                                   contact_epsilon: float = 0.002) -> list[ConvexPart]:
    """Greedily merge touching hulls only when their union stays near-convex.

    This reduces duplicate/UV-seam fragments without bridging a physical gap.
    A pair must first have touching (or nearly touching) AABBs, and the convex
    hull of their union may add at most ``max_inflation`` empty volume.
    """
    if len(parts) < 2:
        return parts
    try:
        import numpy as np
        from scipy.spatial import ConvexHull
    except ImportError:
        return parts

    def hull_for(vertices):
        pts = np.asarray(vertices, dtype=np.float64)
        hull = ConvexHull(pts)
        indices = sorted(set(int(i) for i in hull.vertices))
        remap = {old: new for new, old in enumerate(indices)}
        return (ConvexPart(
            vertices=[tuple(map(float, pts[i])) for i in indices],
            faces=[tuple(remap[int(i)] for i in simplex) for simplex in hull.simplices],
        ), float(hull.volume))

    work = []
    for part in parts:
        try:
            hull, volume = hull_for(part.vertices)
            work.append((hull, volume))
        except Exception:
            work.append((part, None))

    while True:
        best = None  # (inflation, i, j, union_part, union_volume)
        for i in range(len(work)):
            a, avol = work[i]
            if avol is None or avol <= 1e-9:
                continue
            for j in range(i + 1, len(work)):
                b, bvol = work[j]
                if bvol is None or bvol <= 1e-9:
                    continue
                if _bbox_distance(a, b) > contact_epsilon:
                    continue
                try:
                    merged, mvol = hull_for(a.vertices + b.vertices)
                except Exception:
                    continue
                inflation = mvol / (avol + bvol) - 1.0
                if inflation <= max_inflation:
                    candidate = (inflation, i, j, merged, mvol)
                    if best is None or candidate[0] < best[0]:
                        best = candidate
        if best is None:
            break
        _, i, j, merged, volume = best
        work[i] = (merged, volume)
        del work[j]

    return [part for part, _ in work]


# ---------------------------------------------------------------------------
# Recursive convex decomposition (simplified V-HACD)
# ---------------------------------------------------------------------------

def _compute_hull_concavity(positions: list) -> float:
    """Estimate concavity of a set of 3D points.

    Returns the fraction of vertices that lie *inside* the convex hull
    (not on the hull surface).  Values near 0 = almost convex,
    values > 0.3 = significantly concave.
    """
    import numpy as np
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        return 0.0

    pts = np.asarray(positions, dtype=np.float64)
    if len(pts) < 4:
        return 0.0
    try:
        hull = ConvexHull(pts)
    except Exception:
        return 0.0

    hull_vert_set = set(int(v) for v in hull.vertices)
    interior = 0
    for i, p in enumerate(pts):
        if i in hull_vert_set:
            continue
        # Check if point is strictly inside all half-spaces
        inside = True
        for eq in hull.equations:
            if eq[0] * p[0] + eq[1] * p[1] + eq[2] * p[2] + eq[3] > 1e-6:
                inside = False
                break
        if inside:
            interior += 1

    return interior / len(pts) if len(pts) > 0 else 0.0


def _split_faces_by_plane(positions, faces, face_ids, axis: int, position: float):
    """Split face_ids into two lists: those whose centroid <= position and > position."""
    left = []
    right = []
    for fi in face_ids:
        a, b, c = faces[fi]
        mid = (positions[a][axis] + positions[b][axis] + positions[c][axis]) / 3.0
        if mid <= position:
            left.append(fi)
        else:
            right.append(fi)
    return left, right


def _recursive_decompose(positions, faces, face_ids,
                         depth: int = 0,
                         max_depth: int = 4,
                         min_faces: int = 10,
                         concavity_threshold: float = 0.20) -> list:
    """Recursively split a set of face indices into approximately convex groups.

    Uses the fraction of interior vertices (vertices inside the convex hull
    but not on its surface) as a concavity metric.  When a group exceeds
    ``concavity_threshold`` it is split along the axis + position that
    minimises the average concavity of the children.

    Returns a list of face_id lists (each is one convex fragment).
    """
    if depth >= max_depth or len(face_ids) < min_faces:
        return [face_ids]

    # Collect unique vertex indices for this component
    verts = sorted(set(vi for fi in face_ids for vi in faces[fi]))
    if len(verts) < 8:
        return [face_ids]

    comp_pts = [positions[i] for i in verts]

    # Early exit: if the component is already near-convex, stop recursing
    concavity = _compute_hull_concavity(comp_pts)
    if concavity < concavity_threshold:
        return [face_ids]

    # ---- Find best split ----
    import numpy as np
    pts_np = np.asarray(comp_pts, dtype=np.float64)

    best_score = float('inf')
    best_axis = 0
    best_position = 0.0

    for axis in range(3):
        coords = pts_np[:, axis]
        cmin, cmax = float(coords.min()), float(coords.max())
        span = cmax - cmin
        if span < 0.02:
            continue

        # Try multiple split positions along this axis
        for fraction in (0.30, 0.40, 0.50, 0.60, 0.70):
            pos = cmin + span * fraction
            left_ids, right_ids = _split_faces_by_plane(positions, faces, face_ids, axis, pos)
            if len(left_ids) < min_faces or len(right_ids) < min_faces:
                continue

            # Compute average concavity of the two halves
            left_verts = sorted(set(vi for fi in left_ids for vi in faces[fi]))
            right_verts = sorted(set(vi for fi in right_ids for vi in faces[fi]))
            left_pts = [positions[i] for i in left_verts]
            right_pts = [positions[i] for i in right_verts]

            left_c = _compute_hull_concavity(left_pts)
            right_c = _compute_hull_concavity(right_pts)
            avg_concavity = (left_c + right_c) / 2.0

            # Balance term: prefer splits where both sides have meaningful geometry
            balance = min(len(left_ids), len(right_ids)) / max(len(left_ids), len(right_ids))

            # Score: low concavity + good balance
            score = avg_concavity * (2.0 - balance)
            if score < best_score:
                best_score = score
                best_axis = axis
                best_position = pos

    if best_score >= float('inf') / 2:
        # No valid split found
        return [face_ids]

    # Perform the best split
    left_ids, right_ids = _split_faces_by_plane(positions, faces, face_ids, best_axis, best_position)

    result = []
    result.extend(_recursive_decompose(positions, faces, left_ids,
                                       depth + 1, max_depth, min_faces, concavity_threshold))
    result.extend(_recursive_decompose(positions, faces, right_ids,
                                       depth + 1, max_depth, min_faces, concavity_threshold))
    return result


def _build_part_from_face_ids(positions, faces, face_ids):
    """Build a ConvexPart (convex hull) from a set of face indices."""
    import numpy as np
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        return None

    used_verts = sorted(set(vi for fi in face_ids for vi in faces[fi]))
    if len(used_verts) < 4:
        return None

    comp_pts = [positions[i] for i in used_verts]
    pts = np.asarray(comp_pts, dtype=np.float64)
    try:
        hull = ConvexHull(pts)
    except Exception:
        return None

    hv_idx = sorted(set(int(i) for i in hull.vertices))
    remap = {old: new for new, old in enumerate(hv_idx)}
    hverts = [tuple(map(float, pts[i])) for i in hv_idx]
    hfaces = [tuple(remap[int(i)] for i in simplex) for simplex in hull.simplices]
    return ConvexPart(vertices=hverts, faces=hfaces)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_colliders(positions, faces,
                    normals: list[tuple[float, float, float]] | None = None,
                    max_hulls: int = 128,
                    max_verts_per_hull: int = 64,
                    merge_convex_neighbors: bool = True,
                    concavity_threshold: float = 0.20) -> ColliderSet:
    """Return a ColliderSet split by connected components (loose parts).

    Tries to find connected components of the mesh (loose parts) with surface-normal
    filtering (to keep perpendicular parts like table poles vs bases separate),
    then builds convex hulls for each part via scipy.spatial.ConvexHull.

    Components with significant concavity (e.g. a chair whose legs and seat are
    one continuous mesh) are further split by a recursive convex decomposition
    step that separates them into approximately convex fragments — the colliders
    will follow the actual geometry (each leg separately + the seat) rather than
    forming one giant convex blob.
    """
    cs = ColliderSet()
    cs.bbox_min, cs.bbox_max = _compute_bbox(positions)

    if not positions or not faces:
        cs.method = "box"
        return cs

    try:
        # Step 1: Weld duplicate vertex positions.
        use_normals = normals is not None and len(normals) == len(positions)
        normal_threshold_cos = 0.15
        decimals = 5

        unique_pos_and_normal_to_idx = []
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

        # ------------------------------------------------------------------
        # Step 4: For each remaining component, recursively decompose if
        #         concave, then build convex hulls.
        # ------------------------------------------------------------------
        good_parts = []

        for comp in final_comps:
            comp_faces = comp['faces']
            used_verts = comp['verts']
            if len(used_verts) < 4:
                continue

            comp_pts = [positions[i] for i in used_verts]
            xs = [pt[0] for pt in comp_pts]
            ys = [pt[1] for pt in comp_pts]
            zs = [pt[2] for pt in comp_pts]
            dx = max(xs) - min(xs)
            dy = max(ys) - min(ys)
            dz = max(zs) - min(zs)
            min_dim = min(dx, dy, dz)
            min_y = min(ys)

            # 1. Skip floor shadow plane
            if min_y < 0.015 and dy < 0.005:
                continue

            # 2. Skip small flat AO card
            if len(used_verts) < 8 and min_dim < 0.01:
                continue

            # 3. Skip one-sided zero-thickness render cards
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
                    if coherence > 0.95:
                        continue

            # --------------------------------------------------------------
            # NEW: Recursive convex decomposition for concave components
            # --------------------------------------------------------------
            # Thresholds tuned for furniture: a chair with legs has ~40-60%
            # interior vertices.  We split anything above 20%.
            face_groups = _recursive_decompose(
                positions, faces, comp_faces,
                depth=0,
                max_depth=4,
                min_faces=10,
                concavity_threshold=concavity_threshold,
            )

            for group_faces in face_groups:
                group_verts = sorted(set(vi for fi in group_faces for vi in faces[fi]))
                if len(group_verts) < 4:
                    continue

                group_pts = [positions[i] for i in group_verts]

                # Re-apply thin-surface checks for each fragment
                gxs = [pt[0] for pt in group_pts]
                gys = [pt[1] for pt in group_pts]
                gzs = [pt[2] for pt in group_pts]
                gdx = max(gxs) - min(gxs)
                gdy = max(gys) - min(gys)
                gdz = max(gzs) - min(gzs)
                gmin_dim = min(gdx, gdy, gdz)
                gmin_y = min(gys)

                if gmin_y < 0.015 and gdy < 0.005:
                    continue
                if len(group_verts) < 8 and gmin_dim < 0.01:
                    continue

                if gmin_dim < flat_epsilon:
                    g_normal_sum = [0.0, 0.0, 0.0]
                    g_normal_count = 0
                    for ia, ib, ic in (faces[fi] for fi in group_faces):
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
                            g_normal_sum[0] += nx / length
                            g_normal_sum[1] += ny / length
                            g_normal_sum[2] += nz / length
                            g_normal_count += 1
                    if g_normal_count:
                        coherence = math.sqrt(sum(v * v for v in g_normal_sum)) / g_normal_count
                        if coherence > 0.95:
                            continue

                if gmin_dim < 0.02:
                    group_pts = _thicken_vertices(group_pts, min_thickness=0.02)

                # Build convex hull for this fragment
                remap = {old: new for new, old in enumerate(group_verts)}
                group_tris = [tuple(remap[vi] for vi in faces[fi]) for fi in group_faces]

                hull_built = False
                import numpy as np
                try:
                    from scipy.spatial import ConvexHull
                    if len(group_pts) >= 4:
                        pts = np.asarray(group_pts, dtype=np.float64)
                        hull = ConvexHull(pts)
                        hv_idx = sorted(set(int(i) for i in hull.vertices))
                        hremap = {old: new for new, old in enumerate(hv_idx)}
                        hverts = [tuple(map(float, pts[i])) for i in hv_idx]
                        hfaces = [tuple(hremap[int(i)] for i in simplex) for simplex in hull.simplices]
                        good_parts.append(ConvexPart(vertices=hverts, faces=hfaces))
                        hull_built = True
                except ImportError:
                    pass
                except Exception:
                    pass

                if not hull_built:
                    good_parts.append(ConvexPart(vertices=group_pts, faces=group_tris))

        if good_parts:
            if merge_convex_neighbors:
                good_parts = _merge_nearly_convex_neighbors(good_parts)
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
