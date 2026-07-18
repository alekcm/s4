from pathlib import Path

from s4extract.colliders import ConvexPart
from s4extract.unity import write_batch_editor_script, write_collider_obj


def test_fix_all_exports_calls_automatic_collider_optimizer(tmp_path):
    script = Path(write_batch_editor_script(str(tmp_path), "builtin"))
    generated = script.read_text(encoding="utf-8")
    optimizer = script.parent / "S4ColliderOptimizer.cs"

    assert optimizer.exists()
    assert "S4ColliderOptimizer.OptimizeForBatch(root" in generated
    assert "PrefabUtility.SaveAsPrefabAsset(root, prefabPath)" in generated
    assert generated.index("S4ColliderOptimizer.OptimizeForBatch(root") < generated.index(
        "PrefabUtility.SaveAsPrefabAsset(root, prefabPath)")
    assert "DisplayCancelableProgressBar" in generated
    assert "ClearProgressBar" in generated
    assert "CreatePartsPrefab" not in generated
    assert "S4BreakablePart" not in generated


def test_parametric_collider_is_marked_against_destructive_unity_refit(tmp_path):
    part = ConvexPart(
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
        faces=[(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)],
        kind="hollow_lathe",
    )
    path = tmp_path / "cup_collider00.obj"
    write_collider_obj(str(path), part)
    assert "o collider_hollow_parametric_keep" in path.read_text(encoding="utf-8")
