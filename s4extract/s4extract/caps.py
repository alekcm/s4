"""Close open mesh boundaries with simple textured cap geometry.

This is an intentionally lightweight, dependency-free cap generator for the
PARTS export path. The goal is not CAD-perfect fracture surfaces; it is to
avoid obviously empty / transparent break areas by creating a coarse interior
surface that reuses the existing material.

Approach:
- detect boundary edges (edges used by only one triangle)
- walk them into closed loops
- duplicate the loop vertices so the cap can have its own normals
- add a center vertex with averaged UV
- triangulate as a fan from the center
- optionally add reversed triangles too, so the cap is visible from both sides

UVs are inherited from the boundary and averaged at the center, which gives a
reasonable "continued from the main texture" result for game use even if it is
not artist-perfect.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .geom import GeomMesh


def _edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _compute_vertex_normals(positions, faces):
    if not positions:
        return []
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in faces:
        try:
            ax, ay, az = positions[a]
            bx, by, bz = positions[b]
            cx, cy, cz = positions[c]
        except Exception:
            continue
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        for i in (a, b, c):
            acc[i][0] += nx
            acc[i][1] += ny
            acc[i][2] += nz
    out = []
    for nx, ny, nz in acc:
        l = math.sqrt(nx * nx + ny * ny + nz * nz)
        if l > 1e-12:
            out.append((nx / l, ny / l, nz / l))
        else:
            out.append((0.0, 0.0, 1.0))
    return out


def _loop_normal(loop, positions):
    nx = ny = nz = 0.0
    if len(loop) < 3:
        return (0.0, 0.0, 1.0)
    for i, vi in enumerate(loop):
        vj = loop[(i + 1) % len(loop)]
        xi, yi, zi = positions[vi]
        xj, yj, zj = positions[vj]
        nx += (yi - yj) * (zi + zj)
        ny += (zi - zj) * (xi + xj)
        nz += (xi - xj) * (yi + yj)
    l = math.sqrt(nx * nx + ny * ny + nz * nz)
    if l <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / l, ny / l, nz / l)


def _find_boundary_loops(positions, faces):
    counts = defaultdict(int)
    for a, b, c in faces:
        counts[_edge_key(a, b)] += 1
        counts[_edge_key(b, c)] += 1
        counts[_edge_key(c, a)] += 1

    adjacency = defaultdict(set)
    boundary_edges = []
    for (a, b), cnt in counts.items():
        if cnt == 1:
            adjacency[a].add(b)
            adjacency[b].add(a)
            boundary_edges.append((a, b))

    visited = set()
    loops = []
    for a, b in boundary_edges:
        ek = _edge_key(a, b)
        if ek in visited:
            continue
        loop = [a, b]
        visited.add(ek)
        prev, curr = a, b
        ok = True
        while True:
            candidates = [n for n in adjacency[curr] if n != prev]
            if not candidates:
                ok = False
                break
            # Prefer closing the loop when possible, otherwise follow an unused edge.
            nxt = None
            if loop[0] in candidates and len(loop) >= 3:
                nxt = loop[0]
            else:
                for cand in candidates:
                    if _edge_key(curr, cand) not in visited:
                        nxt = cand
                        break
                if nxt is None:
                    nxt = candidates[0]
            ek2 = _edge_key(curr, nxt)
            if nxt == loop[0]:
                visited.add(ek2)
                break
            if ek2 in visited:
                ok = False
                break
            loop.append(nxt)
            visited.add(ek2)
            prev, curr = curr, nxt
        if ok and len(loop) >= 3:
            loops.append(loop)
    return loops


def close_open_boundaries(mesh: GeomMesh, double_sided: bool = True) -> GeomMesh:
    """Return a copy of ``mesh`` with coarse cap polygons over open boundaries."""
    positions = list(mesh.positions)
    faces = list(mesh.faces)
    if not mesh.normals or len(mesh.normals) != len(mesh.positions):
        normals = _compute_vertex_normals(positions, faces)
    else:
        normals = list(mesh.normals)
    if mesh.uvs and len(mesh.uvs) == len(mesh.positions):
        uvs = list(mesh.uvs)
    else:
        uvs = [(0.0, 0.0) for _ in positions]

    loops = _find_boundary_loops(positions, faces)
    if not loops:
        return GeomMesh(name=mesh.name, positions=positions, normals=normals, uvs=uvs, faces=faces)

    for loop in loops:
        if len(loop) < 3:
            continue
        cap_normal = _loop_normal(loop, positions)
        cx = sum(positions[i][0] for i in loop) / len(loop)
        cy = sum(positions[i][1] for i in loop) / len(loop)
        cz = sum(positions[i][2] for i in loop) / len(loop)
        cu = sum(uvs[i][0] for i in loop) / len(loop)
        cv = sum(uvs[i][1] for i in loop) / len(loop)

        cap_loop = []
        for vi in loop:
            cap_loop.append(len(positions))
            positions.append(positions[vi])
            uvs.append(uvs[vi])
            normals.append(cap_normal)
        center_idx = len(positions)
        positions.append((cx, cy, cz))
        uvs.append((cu, cv))
        normals.append(cap_normal)

        n = len(cap_loop)
        for i in range(n):
            a = cap_loop[i]
            b = cap_loop[(i + 1) % n]
            faces.append((a, b, center_idx))
            if double_sided:
                faces.append((center_idx, b, a))

    return GeomMesh(name=mesh.name, positions=positions, normals=normals, uvs=uvs, faces=faces)
