import math

from s4extract.colliders import (
    ConvexPart,
    _convex_intersection_volume,
    _merge_nearly_convex_neighbors,
    _try_fit_direct_primitive,
    _try_fit_hollow_lathe,
    _compute_hull_concavity,
)


def box(x0, y0, z0, x1, y1, z1):
    v = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    f = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
        (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5),
    ]
    return ConvexPart(v, f)


def test_planar_face_vertices_are_not_mistaken_for_concavity():
    points = box(0, 0, 0, 1, 1, 1).vertices + [
        (0.5, 0.5, 0.0), (0.5, 0.5, 1.0),
        (0.5, 0.0, 0.5), (0.5, 1.0, 0.5),
        (0.0, 0.5, 0.5), (1.0, 0.5, 0.5),
    ]
    assert _compute_hull_concavity(points) == 0.0


def test_intersection_volume_does_not_double_count_overlap():
    a = box(0, 0, 0, 1, 1, 1)
    b = box(0.5, 0, 0, 1.5, 1, 1)
    assert math.isclose(_convex_intersection_volume(a.vertices, b.vertices), 0.5,
                        rel_tol=1e-7, abs_tol=1e-7)


def test_duplicate_hull_is_removed():
    a = box(0, 0, 0, 1, 1, 1)
    b = box(0, 0, 0, 1, 1, 1)
    merged = _merge_nearly_convex_neighbors([a, b])
    assert len(merged) == 1


def test_straight_touching_boxes_merge():
    a = box(0, 0, 0, 1, 0.2, 0.2)
    b = box(1, 0, 0, 2, 0.2, 0.2)
    merged = _merge_nearly_convex_neighbors([a, b])
    assert len(merged) == 1


def test_branching_l_shape_does_not_merge():
    horizontal = box(0, 0, 0, 1, 0.2, 0.2)
    vertical = box(0, 0.2, 0, 0.2, 1, 0.2)
    merged = _merge_nearly_convex_neighbors(
        [horizontal, vertical], max_inflation=0.03, max_deviation=0.01)
    assert len(merged) == 2


def test_visible_gap_does_not_merge():
    a = box(0, 0, 0, 1, 1, 1)
    b = box(1.01, 0, 0, 2.01, 1, 1)
    merged = _merge_nearly_convex_neighbors([a, b], contact_epsilon=0.002)
    assert len(merged) == 2


def test_direct_box_fit_happens_before_decomposition():
    part = _try_fit_direct_primitive(box(0, 0, 0, 1, 2, 0.5).vertices)
    assert part is not None
    assert part.kind == "box"
    assert len(part.vertices) == 8


def test_direct_cone_fit_produces_low_poly_lathe():
    points = []
    segments = 24
    for level, radius in ((0.0, 1.0), (1.0, 0.0)):
        if radius == 0.0:
            points.append((0.0, level, 0.0))
        else:
            for i in range(segments):
                angle = 2.0 * math.pi * i / segments
                points.append((radius * math.cos(angle), level, radius * math.sin(angle)))
    part = _try_fit_direct_primitive(points)
    assert part is not None
    assert part.kind == "lathe"
    assert len(part.faces) <= 255


def test_direct_hemisphere_fit_produces_lathe():
    points = []
    segments = 20
    for ring in range(6):
        phi = (math.pi * 0.5) * ring / 5
        y = math.sin(phi)
        radius = math.cos(phi)
        if radius < 1e-7:
            points.append((0.0, y, 0.0))
        else:
            for i in range(segments):
                angle = 2.0 * math.pi * i / segments
                points.append((radius * math.cos(angle), y, radius * math.sin(angle)))
    part = _try_fit_direct_primitive(points, lathe_min_fill=0.60)
    assert part is not None
    assert part.kind == "lathe"
    assert len(part.faces) <= 255


def test_hollow_cup_keeps_open_cavity_and_adds_bottom():
    points = []
    segments = 24
    # Outer wall rings, including the physical bottom.
    for y in (0.0, 0.2, 1.0):
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            points.append((math.cos(angle), y, math.sin(angle)))
    # Inner wall starts above the outer bottom, so a bottom plug is expected.
    for y in (0.2, 1.0):
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            points.append((0.75 * math.cos(angle), y, 0.75 * math.sin(angle)))
    parts = _try_fit_hollow_lathe(points, radial_segments=12)
    assert parts is not None
    assert len(parts) == 13  # 12 wall sectors + one bottom
    assert all(part.kind == "hollow_lathe" for part in parts)
    assert all(len(part.faces) <= 255 for part in parts)
