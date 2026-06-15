"""Generate collider data for Unity from a mesh.

For a role-play game where furniture is dynamic (Rigidbody) and must support
cavities (cup inside a drawer), a single mesh collider is not viable in PhysX:
  - non-convex MeshCollider cannot be used with a moving Rigidbody
  - convex MeshCollider seals cavities

The robust approach is a COMPOUND collider: one Rigidbody + several convex
parts (approximate convex decomposition, V-HACD). This module produces those
convex hulls as lists of vertices/faces, plus a tight bounding box fallback.
"""
from __future__ import annotations

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
    method: str = "none"     # "vhacd" | "convexhull" | "box"


def _compute_bbox(positions):
    if not positions:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def build_colliders(positions, faces,
                    max_hulls: int = 16,
                    max_verts_per_hull: int = 64) -> ColliderSet:
    """Return a ColliderSet. Tries V-HACD, falls back to convex hull, then box."""
    cs = ColliderSet()
    cs.bbox_min, cs.bbox_max = _compute_bbox(positions)

    if not positions or not faces:
        cs.method = "box"
        return cs

    # Try trimesh + V-HACD
    try:
        import numpy as np
        import trimesh

        verts = np.asarray(positions, dtype=np.float64)
        tris = np.asarray(faces, dtype=np.int64)
        # process=True cleans/merges vertices, which V-HACD needs to behave.
        mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=True)
        try:
            mesh.merge_vertices()
        except Exception:
            pass

        try:
            parts = mesh.convex_decomposition(maxConvexHulls=max_hulls)
        except TypeError:
            parts = mesh.convex_decomposition()
        if not isinstance(parts, list):
            parts = [parts]

        good = []
        for p in parts:
            if hasattr(p, "vertices") and len(p.vertices) >= 4:
                good.append(ConvexPart(
                    vertices=[tuple(map(float, v)) for v in p.vertices],
                    faces=[tuple(map(int, f)) for f in p.faces],
                ))
        if good:
            cs.convex_parts = good
            cs.method = "vhacd"
            return cs
    except Exception:
        pass

    # Fallback 1: single convex hull via scipy
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

    # Fallback 2: box only
    cs.method = "box"
    return cs
