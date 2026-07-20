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
    kind: str = "convex"     # "convex" | "box" | "lathe" (direct primitive fit)


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


def _convex_intersection_volume(a_vertices, b_vertices) -> float:
    """Return the volume shared by two convex hulls.

    Both hulls are represented as intersections of their face half-spaces.  A
    Chebyshev-centre LP finds a point strictly inside the intersection, then
    ``HalfspaceIntersection`` reconstructs its vertices.  Touching faces/edges
    correctly have zero volume.
    """
    import numpy as np
    from scipy.optimize import linprog
    from scipy.spatial import ConvexHull, HalfspaceIntersection

    ah = ConvexHull(np.asarray(a_vertices, dtype=np.float64))
    bh = ConvexHull(np.asarray(b_vertices, dtype=np.float64))
    halfspaces = np.vstack((ah.equations, bh.equations))
    normals = halfspaces[:, :3]
    offsets = halfspaces[:, 3]
    normal_lengths = np.linalg.norm(normals, axis=1)

    # Maximise radius r subject to n.x + b + |n|r <= 0.
    c = np.array([0.0, 0.0, 0.0, -1.0], dtype=np.float64)
    aub = np.hstack((normals, normal_lengths[:, None]))
    result = linprog(c, A_ub=aub, b_ub=-offsets,
                     bounds=[(None, None), (None, None), (None, None), (0.0, None)],
                     method="highs")
    if not result.success or result.x[3] <= 1e-9:
        return 0.0

    points = HalfspaceIntersection(halfspaces, result.x[:3]).intersections
    if len(points) < 4:
        return 0.0
    return float(ConvexHull(points).volume)


def _point_triangle_distance(p, a, b, c) -> float:
    """Distance from a point to a triangle (Real-Time Collision Detection)."""
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    ap = tuple(p[i] - a[i] for i in range(3))
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return math.sqrt(_dot(ap, ap))
    bp = tuple(p[i] - b[i] for i in range(3))
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return math.sqrt(_dot(bp, bp))
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        q = tuple(a[i] + v * ab[i] for i in range(3))
        return math.dist(p, q)
    cp = tuple(p[i] - c[i] for i in range(3))
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return math.sqrt(_dot(cp, cp))
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        q = tuple(a[i] + w * ac[i] for i in range(3))
        return math.dist(p, q)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        q = tuple(b[i] + w * (c[i] - b[i]) for i in range(3))
        return math.dist(p, q)
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    q = tuple(a[i] + ab[i] * v + ac[i] * w for i in range(3))
    return math.dist(p, q)


def _point_to_convex_solid_distance(point, part: ConvexPart, equations) -> float:
    """Distance to a convex solid; zero for points inside or on its boundary."""
    if all(eq[0] * point[0] + eq[1] * point[1] + eq[2] * point[2] + eq[3] <= 1e-8
           for eq in equations):
        return 0.0
    best = float("inf")
    for ia, ib, ic in part.faces:
        best = min(best, _point_triangle_distance(
            point, part.vertices[ia], part.vertices[ib], part.vertices[ic]))
    return best


def _merge_surface_deviation(merged: ConvexPart, a: ConvexPart, b: ConvexPart) -> float:
    """Measure the largest sampled bridge created by the merged convex hull."""
    import numpy as np
    from scipy.spatial import ConvexHull
    ae = ConvexHull(np.asarray(a.vertices, dtype=np.float64)).equations
    be = ConvexHull(np.asarray(b.vertices, dtype=np.float64)).equations
    worst = 0.0
    for ia, ib, ic in merged.faces:
        va, vb, vc = merged.vertices[ia], merged.vertices[ib], merged.vertices[ic]
        samples = [
            tuple((va[k] + vb[k] + vc[k]) / 3.0 for k in range(3)),
            tuple((va[k] + vb[k]) * 0.5 for k in range(3)),
            tuple((vb[k] + vc[k]) * 0.5 for k in range(3)),
            tuple((vc[k] + va[k]) * 0.5 for k in range(3)),
        ]
        for point in samples:
            distance = min(_point_to_convex_solid_distance(point, a, ae),
                           _point_to_convex_solid_distance(point, b, be))
            worst = max(worst, distance)
    return worst


def _merge_nearly_convex_neighbors(parts: list[ConvexPart],
                                   max_inflation: float = 0.03,
                                   contact_epsilon: float = 0.002,
                                   max_deviation: float = 0.01,
                                   max_verts_per_hull: int = 64) -> list[ConvexPart]:
    """Greedily merge duplicate/overlapping/touching near-convex hulls.

    Unlike the old ``merged_volume / (a_volume + b_volume)`` test, this uses
    the actual union volume, so overlap is not counted twice.  A sampled
    surface-deviation guard also rejects thin T/Y-shaped bridges which can look
    cheap by volume alone.  A sweep on the X bounds avoids testing every pair.
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
        best = None  # (inflation, deviation, i, j, merged, merged_volume)
        bounds = [(*_compute_bbox(part.vertices), idx) for idx, (part, _) in enumerate(work)]
        bounds.sort(key=lambda item: item[0][0])
        for order_i, (amin, amax, i) in enumerate(bounds):
            a, avol = work[i]
            if avol is None or avol <= 1e-9:
                continue
            for bmin, bmax, j in bounds[order_i + 1:]:
                if bmin[0] - amax[0] > contact_epsilon:
                    break
                b, bvol = work[j]
                if bvol is None or bvol <= 1e-9 or _bbox_distance(a, b) > contact_epsilon:
                    continue
                try:
                    merged, mvol = hull_for(a.vertices + b.vertices)
                except Exception:
                    continue
                if max_verts_per_hull > 0 and len(merged.vertices) > max_verts_per_hull:
                    continue

                # This cheap lower bound is exact for non-overlapping hulls.
                lower_bound = mvol / (avol + bvol) - 1.0
                if lower_bound > max_inflation:
                    continue
                try:
                    intersection = _convex_intersection_volume(a.vertices, b.vertices)
                except Exception:
                    intersection = 0.0
                union_volume = avol + bvol - min(intersection, min(avol, bvol))
                if union_volume <= 1e-9:
                    continue
                inflation = max(0.0, (mvol - union_volume) / union_volume)
                if inflation > max_inflation:
                    continue
                try:
                    deviation = _merge_surface_deviation(merged, a, b)
                except Exception:
                    deviation = float("inf")
                if deviation > max_deviation:
                    continue
                candidate = (inflation, deviation, i, j, merged, mvol)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
        if best is None:
            break
        _, _, i, j, merged, volume = best
        if i > j:
            i, j = j, i
        work[i] = (merged, volume)
        del work[j]

    return [part for part, _ in work]


def _split_at_concave_edges(positions, faces, face_ids, vertex_map,
                            relative_epsilon=1e-6):
    """Split a manifold component at inward (concave) seams.

    For consistently wound render meshes, every point of a convex body lies on
    or behind each outward face plane. If the opposite triangle's third vertex
    lies in front of a face plane, their shared edge is concave. Cutting only
    those seams separates a leg from a seat/back without exploding a convex
    box into six planar faces as a generic sharp-edge split would.
    """
    if len(face_ids) < 8:
        return [face_ids]
    bbox_points = [positions[v] for fi in face_ids for v in faces[fi]]
    mn, mx = _compute_bbox(bbox_points)
    epsilon = max(math.dist(mn, mx) * relative_epsilon, 1e-8)

    normals = {}
    for fi in face_ids:
        ia, ib, ic = faces[fi]
        a, b, c = positions[ia], positions[ib], positions[ic]
        ab = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
        ac = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        n = (ab[1]*ac[2]-ab[2]*ac[1],
             ab[2]*ac[0]-ab[0]*ac[2],
             ab[0]*ac[1]-ab[1]*ac[0])
        length = math.sqrt(_dot(n, n))
        normals[fi] = tuple(x/length for x in n) if length > 1e-12 else (0.0,0.0,0.0)

    edge_faces = {}
    face_welded = {}
    for fi in face_ids:
        tri = faces[fi]
        welded = tuple(vertex_map[v] for v in tri)
        face_welded[fi] = welded
        for i in range(3):
            u, v = welded[i], welded[(i+1)%3]
            if u == v:
                continue
            edge_faces.setdefault(tuple(sorted((u,v))), []).append(fi)

    blocked = set()
    for edge, adjacent in edge_faces.items():
        if len(adjacent) != 2:
            continue
        fa, fb = adjacent
        wa, wb = face_welded[fa], face_welded[fb]
        shared = set(edge)
        a_third_pos = next((positions[faces[fa][i]] for i,w in enumerate(wa) if w not in shared), None)
        b_third_pos = next((positions[faces[fb][i]] for i,w in enumerate(wb) if w not in shared), None)
        if a_third_pos is None or b_third_pos is None:
            continue
        a0 = positions[faces[fa][0]]
        b0 = positions[faces[fb][0]]
        da = _dot(normals[fa], (b_third_pos[0]-a0[0], b_third_pos[1]-a0[1], b_third_pos[2]-a0[2]))
        db = _dot(normals[fb], (a_third_pos[0]-b0[0], a_third_pos[1]-b0[1], a_third_pos[2]-b0[2]))
        # FIXED: было строго AND, теперь мягче OR - разделяет ножки соединённые планкой
        # даже при несогласованном winding
        if (da > epsilon or db > epsilon) and (da > -epsilon*0.5 or db > -epsilon*0.5):
            blocked.add((min(fa,fb), max(fa,fb)))

    if not blocked:
        return [face_ids]
    adjacency = {fi: [] for fi in face_ids}
    for adjacent in edge_faces.values():
        if len(adjacent) != 2:
            continue
        a, b = adjacent
        if (min(a,b), max(a,b)) in blocked:
            continue
        adjacency[a].append(b)
        adjacency[b].append(a)
    groups = []
    seen = set()
    for start in face_ids:
        if start in seen:
            continue
        q = [start]
        seen.add(start)
        group = []
        while q:
            fi = q.pop()
            group.append(fi)
            for other in adjacency[fi]:
                if other not in seen:
                    seen.add(other)
                    q.append(other)
        groups.append(group)
    # Ignore pathological cuts into isolated triangles; the recursive fallback
    # is safer in that case.
    meaningful = [g for g in groups if len(g) >= 4]
    return groups if len(meaningful) >= 2 and len(meaningful) == len(groups) else [face_ids]



def _split_by_spatial_gaps(positions, faces, face_ids, min_gap=0.06, min_gap_ratio=0.12, min_faces_per_side=4):
    """FIX: ищет большие пустые промежутки в X и Z и режет по ним.
    Для chair.package: gap 0.08м вокруг X=0 разделяет левые/правые ножки,
    gap 0.08м по Z разделяет передние/задние.
    """
    if len(face_ids) < 6:
        return [face_ids]
    centroids = []
    for fi in face_ids:
        a,b,c = faces[fi]
        cx = (positions[a][0]+positions[b][0]+positions[c][0])/3.0
        cy = (positions[a][1]+positions[b][1]+positions[c][1])/3.0
        cz = (positions[a][2]+positions[b][2]+positions[c][2])/3.0
        centroids.append((cx,cy,cz,fi))
    best_gap = 0
    best_split = None
    for axis in (0, 2):  # X,Z
        sorted_c = sorted(centroids, key=lambda x: x[axis])
        span = sorted_c[-1][axis] - sorted_c[0][axis]
        if span < 0.05:
            continue
        max_gap = 0
        max_gap_idx = -1
        for i in range(1, len(sorted_c)):
            gap = sorted_c[i][axis] - sorted_c[i-1][axis]
            if gap > max_gap:
                max_gap = gap
                max_gap_idx = i
        if max_gap > min_gap and max_gap > span * min_gap_ratio:
            left = max_gap_idx
            right = len(sorted_c) - max_gap_idx
            if left >= min_faces_per_side and right >= min_faces_per_side:
                if max_gap_idx > len(sorted_c)*0.1 and max_gap_idx < len(sorted_c)*0.9:
                    if max_gap > best_gap:
                        best_gap = max_gap
                        left_ids = [c[3] for c in sorted_c[:max_gap_idx]]
                        right_ids = [c[3] for c in sorted_c[max_gap_idx:]]
                        best_split = (left_ids, right_ids)
    if best_split:
        return list(best_split)
    return [face_ids]


def _recursive_gap_split(positions, faces, face_ids, depth=0, max_depth=4, min_gap=0.06):
    """Рекурсивно применяет gap-split чтобы разделить 4 ножки стула"""
    if depth >= max_depth or len(face_ids) < 10:
        return [face_ids]
    splits = _split_by_spatial_gaps(positions, faces, face_ids, min_gap=min_gap)
    if len(splits) == 1:
        return [face_ids]
    result = []
    for sub in splits:
        result.extend(_recursive_gap_split(positions, faces, sub, depth+1, max_depth, min_gap))
    return result


# ---------------------------------------------------------------------------
# Direct primitive fitting on the unsplit render component
# ---------------------------------------------------------------------------
def _part_from_scipy_hull(points, kind="convex"):
    """Build a compact ConvexPart from points using scipy's hull."""
    import numpy as np
    from scipy.spatial import ConvexHull
    pts = np.asarray(points, dtype=np.float64)
    hull = ConvexHull(pts)
    indices = sorted(set(int(i) for i in hull.vertices))
    remap = {old: new for new, old in enumerate(indices)}
    return ConvexPart(
        vertices=[tuple(map(float, pts[i])) for i in indices],
        faces=[tuple(remap[int(i)] for i in tri) for tri in hull.simplices],
        kind=kind,
    ), float(hull.volume)


def _upper_radius_at_t(polygon, t):
    """Upper intersection of a convex 2D polygon with the vertical line x=t."""
    values = []
    for i, p in enumerate(polygon):
        q = polygon[(i + 1) % len(polygon)]
        lo, hi = min(p[0], q[0]), max(p[0], q[0])
        if t < lo - 1e-9 or t > hi + 1e-9:
            continue
        dx = q[0] - p[0]
        if abs(dx) < 1e-12:
            values.extend((p[1], q[1]))
        else:
            u = (t - p[0]) / dx
            if -1e-9 <= u <= 1.0 + 1e-9:
                values.append(p[1] + (q[1] - p[1]) * u)
    return max(values) if values else 0.0


def _try_fit_direct_primitive(points,
                              box_min_fill=0.72,
                              lathe_min_fill=0.68,
                              radial_aspect_limit=1.45,
                              radial_segments=16,
                              profile_levels=8):
    """Fit a primitive directly to an unsplit connected render component.

    Two general primitive families cover most furniture props:
      * oriented box (also rectangular frusta are left to residual hulls);
      * convex body of revolution (cylinder/cone/frustum/sphere/hemisphere).

    The fit encloses the source point hull and is accepted only when little
    empty volume is introduced.  It therefore cannot bridge a chair cavity or
    turn a C/U-shaped component into a solid collider merely because its AABB
    happens to look simple.
    """
    try:
        import numpy as np
        from scipy.spatial import ConvexHull
    except ImportError:
        return None
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 4:
        return None
    try:
        source_hull = ConvexHull(pts)
    except Exception:
        return None
    source_volume = float(source_hull.volume)
    if source_volume <= 1e-9:
        return None

    mean = pts.mean(axis=0)
    centered = pts - mean
    covariance = centered.T @ centered
    eigenvalues, basis = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    basis = basis[:, order]
    # Keep the frame right-handed so exported faces/transforms stay stable.
    if np.linalg.det(basis) < 0:
        basis[:, 2] *= -1.0
    local = centered @ basis

    candidates = []  # (empty_fraction, priority, part)

    # Pyramids, wedges and rectangular frusta are already handled exactly by
    # the normal one-hull path; replacing them here would not reduce collider
    # count and could stop duplicate/tiny-island merging. Direct fitting is
    # reserved for shapes where it changes representation (box/lathe).

    # Oriented box. Eight corners are enough; scipy supplies consistent faces.
    mn, mx = local.min(axis=0), local.max(axis=0)
    box_volume = float(np.prod(mx - mn))
    if box_volume > 1e-9:
        fill = source_volume / box_volume
        if fill >= box_min_fill:
            corners_local = np.asarray([
                (x, y, z)
                for x in (mn[0], mx[0])
                for y in (mn[1], mx[1])
                for z in (mn[2], mx[2])
            ], dtype=np.float64)
            corners = corners_local @ basis.T + mean
            try:
                part, _ = _part_from_scipy_hull(corners, kind="box")
                candidates.append((max(0.0, 1.0 - fill), 1, part))
            except Exception:
                pass

    # Try every PCA axis. This matters for hemispheres and squat lamp bases:
    # their symmetry axis is often not the largest-variance axis.
    for axis in range(3):
        transverse = [i for i in range(3) if i != axis]
        t = local[:, axis]
        u, v = local[:, transverse[0]], local[:, transverse[1]]
        eu = math.sqrt(max(float((u * u).mean()), 1e-12))
        ev = math.sqrt(max(float((v * v).mean()), 1e-12))
        radial_aspect = max(eu, ev) / min(eu, ev)
        if radial_aspect > radial_aspect_limit:
            continue
        radial = np.sqrt(u * u + v * v)
        meridian = np.column_stack((
            np.concatenate((t, t)),
            np.concatenate((radial, -radial)),
        ))
        try:
            meridian_hull = ConvexHull(meridian)
        except Exception:
            continue
        polygon = meridian[meridian_hull.vertices]
        tmin, tmax = float(t.min()), float(t.max())
        if tmax - tmin <= 1e-6:
            continue
        # Preserve exact upper-profile breakpoints whenever possible. Uniform
        # levels joined by chords badly under-approximate a hemisphere near its
        # pole and would require inflating the whole body to compensate.
        upper_by_t = {}
        for px, py in polygon:
            if py >= -1e-9:
                key = round(float(px), 10)
                upper_by_t[key] = max(upper_by_t.get(key, 0.0), float(py))
        upper = sorted((x, r) for x, r in upper_by_t.items())
        if 2 <= len(upper) <= profile_levels:
            levels = np.asarray([x for x, _ in upper], dtype=np.float64)
            radii = np.asarray([max(0.0, r) for _, r in upper], dtype=np.float64)
        else:
            levels = np.linspace(tmin, tmax, profile_levels)
            radii = np.asarray([max(0.0, _upper_radius_at_t(polygon, float(x)))
                                for x in levels], dtype=np.float64)

        # Circumscribe rather than inscribe the sampled circular cross-section.
        # Then enlarge once more if interpolation misses any source point.
        radii *= 1.0 / math.cos(math.pi / radial_segments)
        predicted = np.interp(t, levels, radii)
        valid = predicted > 1e-9
        if not valid.any():
            continue
        scale = max(1.0, float(np.max(radial[valid] / predicted[valid])))
        radii *= scale

        generated_local = []
        for level, radius in zip(levels, radii):
            if radius <= 1e-7:
                q = [0.0, 0.0, 0.0]
                q[axis] = float(level)
                generated_local.append(q)
                continue
            for segment in range(radial_segments):
                angle = 2.0 * math.pi * segment / radial_segments
                q = [0.0, 0.0, 0.0]
                q[axis] = float(level)
                q[transverse[0]] = float(radius * math.cos(angle))
                q[transverse[1]] = float(radius * math.sin(angle))
                generated_local.append(q)
        generated = np.asarray(generated_local) @ basis.T + mean
        try:
            part, candidate_volume = _part_from_scipy_hull(generated, kind="lathe")
        except Exception:
            continue
        if len(part.faces) > 255 or candidate_volume <= 1e-9:
            continue
        fill = min(1.0, source_volume / candidate_volume)
        if fill >= lathe_min_fill:
            # Prefer a rotational primitive over a box at essentially equal
            # error: it gives smoother contacts on cups, lamps and tapered legs.
            candidates.append((max(0.0, 1.0 - fill), 0, part))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], len(item[2].vertices)))
    return candidates[0][2]


def _circular_coverage_ok(angles, max_gap=math.pi * 0.60):
    if len(angles) < 4:
        return False
    values = sorted((float(a) % (2.0 * math.pi)) for a in angles)
    gaps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    gaps.append(values[0] + 2.0 * math.pi - values[-1])
    return max(gaps) <= max_gap


def _try_fit_hollow_lathe(points, radial_segments=12, max_profile_levels=6):
    """Recognise a hollow body of revolution and return convex ring sectors.

    Intended for cups, bowls, cylindrical shades and tubes.  The cavity remains
    open because every angular wedge covers only the wall thickness.  A solid
    bottom plug is added when the inner surface starts above the outer bottom.
    Detection is deliberately strict; ambiguous geometry falls back to normal
    convex decomposition rather than being accidentally hollowed/sealed.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 24:
        return None
    mean = pts.mean(axis=0)
    centered = pts - mean
    eigenvalues, basis = np.linalg.eigh(centered.T @ centered)
    order = np.argsort(eigenvalues)[::-1]
    basis = basis[:, order]
    if np.linalg.det(basis) < 0:
        basis[:, 2] *= -1.0
    local = centered @ basis
    best = None

    for axis in range(3):
        transverse = [i for i in range(3) if i != axis]
        t = local[:, axis]
        u, v = local[:, transverse[0]], local[:, transverse[1]]
        eu = math.sqrt(max(float((u * u).mean()), 1e-12))
        ev = math.sqrt(max(float((v * v).mean()), 1e-12))
        if max(eu, ev) / min(eu, ev) > 1.35:
            continue
        span = float(t.max() - t.min())
        if span <= 1e-5:
            continue
        radial = np.sqrt(u * u + v * v)
        angle = np.arctan2(v, u)

        # Bin nearly coplanar vertex rings without assuming exact coordinates.
        order_idx = np.argsort(t)
        tolerance = max(span * 0.0125, 1e-5)
        bins = []
        current = [int(order_idx[0])]
        anchor = float(t[order_idx[0]])
        for raw_idx in order_idx[1:]:
            idx = int(raw_idx)
            if float(t[idx]) - anchor <= tolerance:
                current.append(idx)
            else:
                bins.append(current)
                current = [idx]
                anchor = float(t[idx])
        bins.append(current)

        hollow_levels = []
        outer_levels = []
        for ids in bins:
            rs_all = radial[ids]
            level_t = float(np.mean(t[ids]))
            outer = float(np.max(rs_all))
            if outer <= 1e-6:
                continue
            outer_levels.append((level_t, outer))
            # Ignore centre vertices used only to triangulate a cap.
            usable = [(idx, float(radial[idx])) for idx in ids
                      if radial[idx] >= outer * 0.15]
            if len(usable) < 8:
                continue
            values = np.asarray([r for _, r in usable], dtype=np.float64)
            c0, c1 = float(values.min()), float(values.max())
            if c1 - c0 <= outer * 0.04:
                continue
            for _ in range(12):
                d0 = np.abs(values - c0)
                d1 = np.abs(values - c1)
                mask = d0 <= d1
                if not mask.any() or mask.all():
                    break
                n0, n1 = float(values[mask].mean()), float(values[~mask].mean())
                if abs(n0 - c0) + abs(n1 - c1) < 1e-8:
                    break
                c0, c1 = n0, n1
            inner, outer_fit = sorted((c0, c1))
            inner_ids = [usable[i][0] for i in range(len(usable)) if mask[i]]
            outer_ids = [usable[i][0] for i in range(len(usable)) if not mask[i]]
            if inner / max(outer_fit, 1e-9) < 0.25:
                continue
            if (outer_fit - inner) / outer_fit > 0.45:
                continue
            if not (_circular_coverage_ok(angle[inner_ids]) and
                    _circular_coverage_ok(angle[outer_ids])):
                continue
            hollow_levels.append((level_t, inner, outer_fit))

        if len(hollow_levels) < 2:
            continue
        hollow_levels.sort()
        hollow_depth = hollow_levels[-1][0] - hollow_levels[0][0]
        if hollow_depth < span * 0.20:
            continue
        if len(hollow_levels) > max_profile_levels:
            picks = np.linspace(0, len(hollow_levels) - 1,
                                max_profile_levels).round().astype(int)
            hollow_levels = [hollow_levels[int(i)] for i in sorted(set(picks))]

        parts = []
        for segment in range(radial_segments):
            a0 = 2.0 * math.pi * segment / radial_segments
            a1 = 2.0 * math.pi * (segment + 1) / radial_segments
            generated_local = []
            for level, inner, outer in hollow_levels:
                # Slightly circumscribe the outer wall; slightly inscribe the
                # inner wall so no radial gap appears between visual wall and
                # collision wall while the central cavity remains open.
                outer *= 1.0 / math.cos(math.pi / radial_segments)
                inner *= math.cos(math.pi / radial_segments)
                for radius in (inner, outer):
                    for a in (a0, a1):
                        q = [0.0, 0.0, 0.0]
                        q[axis] = float(level)
                        q[transverse[0]] = float(radius * math.cos(a))
                        q[transverse[1]] = float(radius * math.sin(a))
                        generated_local.append(q)
            generated = np.asarray(generated_local) @ basis.T + mean
            try:
                part, _ = _part_from_scipy_hull(generated, kind="hollow_lathe")
            except Exception:
                parts = []
                break
            if len(part.faces) > 255:
                parts = []
                break
            parts.append(part)
        if not parts:
            continue

        # Closed cup/bowl: add one solid bottom plug up to the first detected
        # inner ring. Open tubes have no axial gap and therefore no plug.
        outer_levels.sort()
        bottom_t = outer_levels[0][0] if outer_levels else float(t.min())
        inner_start = hollow_levels[0][0]
        if inner_start - bottom_t > span * 0.025:
            bottom_outer = outer_levels[0][1]
            top_outer = min(outer_levels, key=lambda x: abs(x[0] - inner_start))[1]
            ring_points = []
            for level, radius in ((bottom_t, bottom_outer), (inner_start, top_outer)):
                radius *= 1.0 / math.cos(math.pi / radial_segments)
                for segment in range(radial_segments):
                    a = 2.0 * math.pi * segment / radial_segments
                    q = [0.0, 0.0, 0.0]
                    q[axis] = float(level)
                    q[transverse[0]] = float(radius * math.cos(a))
                    q[transverse[1]] = float(radius * math.sin(a))
                    ring_points.append(q)
            ring_world = np.asarray(ring_points) @ basis.T + mean
            try:
                bottom, _ = _part_from_scipy_hull(ring_world, kind="hollow_lathe")
                parts.append(bottom)
            except Exception:
                pass

        score = len(parts)
        if best is None or score < best[0]:
            best = (score, parts)

    return best[1] if best else None


# ---------------------------------------------------------------------------
# Recursive approximate convex decomposition
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
    bbox_span = np.ptp(pts, axis=0)
    strict_epsilon = max(float(np.linalg.norm(bbox_span)) * 1e-6, 1e-8)
    for i, p in enumerate(pts):
        if i in hull_vert_set:
            continue
        # A triangulated convex surface often has many vertices in the middle
        # of a planar face. They are not listed in hull.vertices, but they are
        # still ON the hull and must not be counted as concavity. A point is
        # truly interior only when it lies a meaningful distance behind every
        # hull plane.
        max_plane_distance = max(
            eq[0] * p[0] + eq[1] * p[1] + eq[2] * p[2] + eq[3]
            for eq in hull.equations)
        if max_plane_distance < -strict_epsilon:
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
                         max_depth: int = 5,
                         min_faces: int = 10,
                         concavity_threshold: float = 0.15) -> list:
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
        # FIX: даже если concavity низкая, пробуем gap-split для рамок стула с дыркой
        gap_split = _split_by_spatial_gaps(positions, faces, face_ids)
        if len(gap_split) >= 2:
            result = []
            for sub in gap_split:
                result.extend(_recursive_decompose(positions, faces, sub, depth+1, max_depth, min_faces, concavity_threshold))
            return result
        return [face_ids]

    # FIX: сначала пробуем gap-based split (наиболее эффективен для ножек)
    gap_split = _split_by_spatial_gaps(positions, faces, face_ids)
    if len(gap_split) >= 2:
        if all(len(g) >= min_faces for g in gap_split):
            result = []
            for sub in gap_split:
                result.extend(_recursive_decompose(positions, faces, sub, depth+1, max_depth, min_faces, concavity_threshold))
            return result

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
# Fast coarse collider mode for very high-poly environment objects
# ---------------------------------------------------------------------------

def _box_part_from_bounds(bounds_min, bounds_max, min_thickness=0.02):
    """Create one valid convex box from axis-aligned bounds without scipy."""
    mn = list(bounds_min)
    mx = list(bounds_max)
    for axis in range(3):
        if mx[axis] - mn[axis] < min_thickness:
            middle = (mx[axis] + mn[axis]) * 0.5
            mn[axis] = middle - min_thickness * 0.5
            mx[axis] = middle + min_thickness * 0.5
    vertices = [
        (mn[0], mn[1], mn[2]), (mx[0], mn[1], mn[2]),
        (mx[0], mx[1], mn[2]), (mn[0], mx[1], mn[2]),
        (mn[0], mn[1], mx[2]), (mx[0], mn[1], mx[2]),
        (mx[0], mx[1], mx[2]), (mn[0], mx[1], mx[2]),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),  # bottom
        (4, 5, 6), (4, 6, 7),  # top
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7),
    ]
    return ConvexPart(vertices=vertices, faces=faces, kind="box")


def _build_coarse_box_colliders(positions, faces, max_parts=12,
                                 target_faces_per_part=15000) -> ColliderSet:
    """Build a deliberately coarse compound collider in one linear face pass.

    This mode is for bridges, buildings and other environment-scale meshes for
    which recursive concavity analysis creates thousands of tiny hulls and can
    take minutes. Faces are divided into large spatial blocks along the longest
    horizontal span; tall assets receive two broad elevation layers when the
    part budget permits it. Each non-empty block becomes an 8-vertex box.

    It intentionally favours a small, stable collision approximation over
    detail: there is no per-edge segmentation, convex-hull calculation, scipy,
    recursive decomposition or neighbour merging in this path.
    """
    cs = ColliderSet()
    cs.bbox_min, cs.bbox_max = _compute_bbox(positions)
    if not positions or not faces:
        cs.method = "coarse_boxes"
        return cs

    min_x, min_y, min_z = cs.bbox_min
    max_x, max_y, max_z = cs.bbox_max
    spans = (max_x - min_x, max_y - min_y, max_z - min_z)
    # Bridges normally run along X/Z. Prefer the longer horizontal axis even
    # when a tall tower makes Y numerically larger.
    primary_axis = 0 if spans[0] >= spans[2] else 2
    horizontal_span = spans[primary_axis]
    vertical_span = spans[1]

    requested = max(1, int(math.ceil(len(faces) / max(1, target_faces_per_part))))
    # A landmark can be geometrically huge without reaching the triangle
    # target (this EP04 bridge is ~103 m long but only ~8.8k triangles). One
    # AABB would seal the whole underpass, so reserve a few *large* blocks by
    # world span, not by tiny-detail density.
    if horizontal_span >= 40.0:
        requested = max(requested, 6)
    elif horizontal_span >= 15.0:
        requested = max(requested, 4)
    requested = min(max(1, max_parts), requested)

    # Split deck/supports into broad upper/lower bands only when it can do so
    # without exceeding the requested total amount of large collider pieces.
    vertical_layers = 1
    if requested >= 4 and vertical_span > max(horizontal_span * 0.12, 0.5):
        vertical_layers = 2
    longitudinal_bins = max(1, min(max_parts // vertical_layers, requested // vertical_layers))
    if longitudinal_bins < 1:
        longitudinal_bins = 1

    axis_min = (min_x, min_y, min_z)[primary_axis]
    axis_span = max(horizontal_span, 1e-8)
    y_span = max(vertical_span, 1e-8)
    groups = {}  # (longitudinal_bin, vertical_layer) -> [minx,miny,minz,maxx,maxy,maxz,count]

    for face in faces:
        try:
            ia, ib, ic = face
            pa, pb, pc = positions[ia], positions[ib], positions[ic]
        except (IndexError, TypeError, ValueError):
            continue
        center_axis = (pa[primary_axis] + pb[primary_axis] + pc[primary_axis]) / 3.0
        long_bin = min(longitudinal_bins - 1, max(
            0, int((center_axis - axis_min) / axis_span * longitudinal_bins)))
        if vertical_layers == 1:
            layer = 0
        else:
            center_y = (pa[1] + pb[1] + pc[1]) / 3.0
            layer = min(vertical_layers - 1, max(
                0, int((center_y - min_y) / y_span * vertical_layers)))
        key = (long_bin, layer)
        xs = (pa[0], pb[0], pc[0])
        ys = (pa[1], pb[1], pc[1])
        zs = (pa[2], pb[2], pc[2])
        if key not in groups:
            groups[key] = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs), 1]
        else:
            item = groups[key]
            item[0] = min(item[0], min(xs)); item[1] = min(item[1], min(ys)); item[2] = min(item[2], min(zs))
            item[3] = max(item[3], max(xs)); item[4] = max(item[4], max(ys)); item[5] = max(item[5], max(zs))
            item[6] += 1

    # Keep every populated spatial region. A sparse distant support is more
    # useful than spending time joining it to a huge deck AABB.
    object_diagonal = math.dist(cs.bbox_min, cs.bbox_max)
    min_thickness = max(0.02, object_diagonal * 0.001)
    for _key, item in sorted(groups.items()):
        bounds_min = (item[0], item[1], item[2])
        bounds_max = (item[3], item[4], item[5])
        cs.convex_parts.append(_box_part_from_bounds(bounds_min, bounds_max, min_thickness))

    # Degenerate malformed face data can leave no groups; use one overall box
    # rather than falling into the expensive exact path.
    if not cs.convex_parts:
        cs.convex_parts.append(_box_part_from_bounds(cs.bbox_min, cs.bbox_max, min_thickness))
    cs.method = "coarse_boxes"
    return cs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_colliders(positions, faces,
                    normals: list[tuple[float, float, float]] | None = None,
                    max_hulls: int = 128,
                    max_verts_per_hull: int = 64,
                    merge_convex_neighbors: bool = True,
                    merge_max_inflation: float = 0.03,
                    merge_contact_epsilon: float = 0.002,
                    merge_max_deviation_ratio: float = 0.005,
                    concavity_threshold: float = 0.20,
                    coarse_face_threshold: int = 20_000,
                    coarse_vertex_threshold: int = 8_000,
                    coarse_max_parts: int = 12,
                    coarse_target_faces: int = 15_000) -> ColliderSet:
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

    # High-poly environment objects are not worth feeding through the exact
    # recursive concavity path. Its edge/face analysis grows very expensive
    # and produces collision fragments far smaller than gameplay needs.
    #
    # Face count alone is not sufficient: the EP04 elevated freeway has only
    # ~8.8k triangles but ~11.7k split render vertices. The old weld loop was
    # quadratic in exactly that vertex count, which is why it appeared frozen.
    coarse_by_faces = coarse_face_threshold > 0 and len(faces) >= coarse_face_threshold
    coarse_by_vertices = coarse_vertex_threshold > 0 and len(positions) >= coarse_vertex_threshold
    if coarse_by_faces or coarse_by_vertices:
        coarse = _build_coarse_box_colliders(
            positions, faces,
            max_parts=coarse_max_parts,
            target_faces_per_part=coarse_target_faces)
        if max_hulls > 0 and len(coarse.convex_parts) > max_hulls:
            coarse.method = "coarse_boxes_over_budget"
        return coarse

    try:
        # Step 1: Weld duplicate vertex positions.
        use_normals = normals is not None and len(normals) == len(positions)
        normal_threshold_cos = 0.15
        decimals = 5

        unique_pos_and_normal_to_idx = []
        # Old code scanned every previously-seen vertex for every new vertex:
        # O(V^2). On the freeway's 11,676 vertices that is ~136 million Python
        # comparisons before collider decomposition even begins. Bucket only
        # identical rounded positions; a UV-seamed mesh has just a handful of
        # different normals per position, so this preserves behavior at O(V).
        position_buckets = {}
        vertex_map = []

        for i, p in enumerate(positions):
            rounded_p = (round(p[0], decimals), round(p[1], decimals), round(p[2], decimals))
            n = normals[i] if use_normals else (0.0, 1.0, 0.0)
            bucket = position_buckets.setdefault(rounded_p, [])

            found_idx = None
            if use_normals:
                for un, uidx in bucket:
                    if _dot(un, n) >= normal_threshold_cos:
                        found_idx = uidx
                        break
            elif bucket:
                found_idx = bucket[0][1]

            if found_idx is None:
                found_idx = len(unique_pos_and_normal_to_idx)
                unique_pos_and_normal_to_idx.append((rounded_p, n, found_idx))
                bucket.append((n, found_idx))

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

        # Step 2b: FIXED - split at concave seams + spatial gaps
        # direct surface segmentation pass for models whose legs/seat/back were
        # authored as one manifold mesh.
        refined_components = []
        for comp_faces in components:
            # Сначала gap-split (разделяет 4 ножки)
            gap_parts = _recursive_gap_split(positions, faces, comp_faces, max_depth=4)
            for gp in gap_parts:
                refined_components.extend(_split_at_concave_edges(
                    positions, faces, gp, vertex_map))
        components = refined_components

        # Step 3: Rebuild local component lists and filter/merge tiny pieces.
        min_verts = 12
        comp_list = []
        direct_parts = []
        for comp_faces in components:
            used_verts = sorted(list(set(vi for fi in comp_faces for vi in faces[fi])))
            comp_pts = [positions[i] for i in used_verts]

            # Inspect each original topological component before tiny islands
            # are attached to their nearest neighbour. This is the point where
            # a cone, hemisphere or pyramid still exists as one recognisable
            # object rather than as contaminated/fragmented geometry.
            if len(used_verts) >= 4:
                hollow = _try_fit_hollow_lathe(comp_pts)
                if hollow:
                    direct_parts.extend(hollow)
                    continue
                if _compute_hull_concavity(comp_pts) < 0.12:
                    primitive = _try_fit_direct_primitive(comp_pts)
                    if primitive is not None:
                        direct_parts.append(primitive)
                        continue

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
        good_parts = list(direct_parts)

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
            # Direct primitive fit BEFORE any convex cutting.  This preserves
            # the evidence that a complete component is a cone, cylinder,
            # hemisphere, lamp base or rectangular solid. Concave/cavity-rich
            # components are deliberately excluded and continue to the
            # residual decomposition below.
            # --------------------------------------------------------------
            hollow = _try_fit_hollow_lathe(comp_pts)
            if hollow:
                good_parts.extend(hollow)
                continue
            component_concavity = _compute_hull_concavity(comp_pts)
            if component_concavity < 0.12:
                primitive = _try_fit_direct_primitive(comp_pts)
                if primitive is not None:
                    good_parts.append(primitive)
                    continue

            # Residual recursive convex decomposition for geometry that no
            # accepted primitive can represent safely.
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

                # A residual split may itself expose a complete tapered leg or
                # rounded lobe. Fit once more before falling back to its raw
                # point hull; this does not affect the earlier cavity decision.
                if _compute_hull_concavity(group_pts) < 0.08:
                    primitive = _try_fit_direct_primitive(group_pts)
                    if primitive is not None:
                        good_parts.append(primitive)
                        continue

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
                object_diag = math.dist(cs.bbox_min, cs.bbox_max)
                max_deviation = max(merge_contact_epsilon * 2.0,
                                    object_diag * merge_max_deviation_ratio)
                # Bodies of revolution already represent a complete unsplit
                # component. Re-hulling them with neighbours would destroy flat
                # cone/cylinder ends and make the Unity optimizer misclassify
                # them. Boxes, wedges and simple hulls may still participate in
                # the safe merge pass so duplicates are not retained.
                preserved_kinds = {"lathe", "hollow_lathe"}
                direct_parts = [p for p in good_parts if p.kind in preserved_kinds]
                residual_parts = [p for p in good_parts if p.kind not in preserved_kinds]
                residual_parts = _merge_nearly_convex_neighbors(
                    residual_parts,
                    max_inflation=merge_max_inflation,
                    contact_epsilon=merge_contact_epsilon,
                    max_deviation=max_deviation,
                    max_verts_per_hull=max_verts_per_hull,
                )
                good_parts = direct_parts + residual_parts
            # Never satisfy max_hulls by silently deleting the smallest shapes:
            # that creates holes in collision geometry.  The value is now a
            # target for diagnostics/future lossy simplification, not a knife.
            cs.convex_parts = good_parts
            cs.method = ("loose_parts_over_budget"
                         if max_hulls > 0 and len(good_parts) > max_hulls
                         else "loose_parts")
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
