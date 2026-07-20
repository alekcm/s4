"""Command-line interface for s4extract.

Usage:
    python -m s4extract path/to/file.package [more.package ...] [options]
    python -m s4extract path/to/folder            # processes every .package inside

Options:
    -o, --out DIR     output directory (default: ./extracted)
    --no-obj          do not write .obj
    --no-fbx          do not write .fbx
    --no-png          do not extract textures to .png
    --no-unity        do not generate Unity .mat
    --raw             also dump every raw resource
    --pipeline NAME   unity render pipeline (builtin|urp|hdrp), default builtin
    --no-colliders    do not generate convex collider meshes
    --prefab          generate legacy YAML .prefab files
    --static          make prefab static (no Rigidbody)
    --max-hulls N     max convex parts per object (default 128)
    --all-lods        export every LOD (default: only the highest-detail one)
    --no-cas          skip CAS resources (clothing, hair, human body)
    --geom            also extract GEOM meshes (off by default — creates 'default' objects)
    -q, --quiet       less output
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .extractor import Options, extract_package


def _gather_packages(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            out.extend(sorted(glob.glob(os.path.join(p, "**", "*.package"), recursive=True)))
        elif p.lower().endswith(".package"):
            out.append(p)
        else:
            print(f"! skipping non-package: {p}", file=sys.stderr)
    return out


class _ConsoleProgress:
    """Line-oriented progress output that remains readable in .bat log files."""

    def __init__(self):
        self._last_index_bucket = -1

    @staticmethod
    def _bar(fraction: float, width: int = 20) -> str:
        fraction = max(0.0, min(1.0, fraction))
        filled = int(round(fraction * width))
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    @staticmethod
    def _short(value: str, limit: int = 68) -> str:
        if len(value) <= limit:
            return value
        return value[:limit - 3] + "..."

    def __call__(self, event: str, **info) -> None:
        current = int(info.get("current") or 0)
        total = int(info.get("total") or 0)
        name = self._short(str(info.get("name") or ""))
        package = os.path.basename(str(info.get("package") or ""))

        if event == "linked_family":
            family = ", ".join(info.get("family") or [])
            print(f"[linked] Найдены связанные пакеты: {family}", flush=True)
            return

        if event == "resource_index_started":
            print("[index] Индексация Build/DeltaBuild ресурсов установленной игры...", flush=True)
            self._last_index_bucket = -1
            return
        if event == "resource_index_progress":
            # A package-by-package line is useful, but avoid flooding the log
            # when a large installation has hundreds of build archives.
            bucket = int((current * 20 / total)) if total else 20
            if current != 1 and current != total and bucket == self._last_index_bucket:
                return
            self._last_index_bucket = bucket
            frac = current / total if total else 1.0
            indexed = os.path.basename(str(info.get("indexed_package") or ""))
            print(f"[index] {self._bar(frac)} {frac * 100:5.1f}%  "
                  f"{current}/{total}  {indexed}", flush=True)
            return
        if event == "resource_index_done":
            print(f"[index] Готово: индексы {info.get('indexed_packages', 0)} пакетов.", flush=True)
            return

        if event == "object_list":
            print(f"[objects] {package}: найдено объектов: {total}.", flush=True)
            return

        if total:
            if event in ("object_done", "object_skipped"):
                fraction = current / total
            else:
                fraction = (current - 1) / total
            prefix = (f"{self._bar(fraction)} {fraction * 100:5.1f}%  "
                      f"{current}/{total}")
        else:
            prefix = "[--------------------]   0.0%"

        if event == "object_started":
            text = "обработка"
        elif event == "object_stage":
            text = str(info.get("stage") or "обработка")
        elif event == "object_skipped":
            text = "пропуск: объект уже полностью экспортирован"
        elif event == "object_done":
            text = (f"готово: meshes={info.get('meshes', 0)}, "
                    f"textures={info.get('textures', 0)}")
        elif event == "object_error":
            text = "ОШИБКА: " + self._short(str(info.get("error") or ""), 100)
        elif event == "package_done":
            print(f"[done] {package}: экспорт завершён.", flush=True)
            return
        else:
            return
        print(f"{prefix}  {name} — {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="s4extract",
        description="Extract The Sims 4 .package files into .obj/.fbx + .png + Unity .mat")
    ap.add_argument("inputs", nargs="*", help=".package file(s) or folder(s)")
    ap.add_argument("-o", "--out", default="extracted", help="output directory")
    ap.add_argument("--no-obj", action="store_true")
    ap.add_argument("--no-fbx", action="store_true")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--no-unity", action="store_true")
    ap.add_argument("--raw", action="store_true", help="also dump raw resources")
    ap.add_argument("--pipeline", default="builtin", choices=["builtin", "urp", "hdrp"],
                    help="Unity render pipeline for .mat (default: builtin)")
    ap.add_argument("--no-colliders", action="store_true",
                    help="do not generate convex collider meshes")
    ap.add_argument("--prefab", action="store_true",
                    help="generate legacy YAML .prefab files")
    ap.add_argument("--static", action="store_true",
                    help="make prefab static (no Rigidbody)")
    ap.add_argument("--max-hulls", type=int, default=128,
                    help="target convex-part budget; reports over-budget instead of deleting geometry (default 128)")
    ap.add_argument("--no-convex-merge", action="store_false", dest="merge_convex_neighbors",
                    help="keep every generated convex fragment; disable safe near-convex merging")
    ap.add_argument("--merge-max-inflation", type=float, default=0.03,
                    help="maximum empty-volume fraction introduced by a convex merge (default 0.03)")
    ap.add_argument("--merge-contact-epsilon", type=float, default=0.002,
                    help="maximum gap between merge candidates in model units (default 0.002)")
    ap.add_argument("--merge-max-deviation-ratio", type=float, default=0.005,
                    help="maximum convex bridge thickness relative to object diagonal (default 0.005)")
    ap.add_argument("--max-verts-per-hull", type=int, default=64,
                    help="maximum vertices allowed in a merged convex hull (default 64)")
    ap.add_argument("--concavity-threshold", type=float, default=0.20,
                    help="Concavity threshold for recursive decomposition (0.0=always split, 1.0=never split). "
                         "Lower values produce more, smaller collider parts. Default 0.20.")
    # Defaults: all_lods=True (extract all LODs), no_cas=True (skip CAS), extract_geom=False
    ap.add_argument("--all-lods", action="store_true", default=True,
                    help="(default on) extract all LOD levels")
    ap.add_argument("--no-lods", action="store_false", dest="all_lods",
                    help="export only the highest-detail LOD")
    ap.add_argument("--no-cas", action="store_true", default=True,
                    help="(default on) skip CAS resources (clothing, hair, human body)")
    ap.add_argument("--include-cas", action="store_false", dest="no_cas",
                    help="include CAS resources (clothing, hair)")
    ap.add_argument("--geom", action="store_true",
                    help="also extract GEOM meshes (creates extra 'default' objects)")
    ap.add_argument("--per-object", action="store_true", default=True,
                    help="(default on) extract each object in a multi-object package into its own folder")
    ap.add_argument("--no-per-object", action="store_false", dest="per_object",
                    help="extract everything into one folder (legacy mode)")
    ap.add_argument("--no-linked-fullbuilds", action="store_false", dest="linked_fullbuilds",
                    default=True,
                    help="do not automatically link numbered ClientFullBuild/DeltaBuild siblings")
    ap.add_argument("--no-game-resource-search", action="store_false", dest="game_resource_fallback",
                    default=True,
                    help="do not search installed Build/DeltaBuild packages for missing linked TGIs")
    ap.add_argument("--no-resume", action="store_false", dest="resume", default=True,
                    help="re-export all objects; ignore completed-object resume manifests")
    ap.add_argument("--progress", action="store_true",
                    help="show object-level progress and resume/skip status")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--json", action="store_true", help="print JSON report")
    ap.add_argument("--inspect", action="store_true",
                    help="diagnostic mode: print what is inside each package "
                         "(types, sizes, magic signatures) and exit")
    args = ap.parse_args(argv)

    packages = _gather_packages(args.inputs) if args.inputs else []
    if not packages:
        print("No .package files found. Drag a .package file onto the .bat or specify a path.", file=sys.stderr)
        return 1

    if args.inspect:
        from .inspect import inspect_package
        for pkg in packages:
            print(inspect_package(pkg))
            print("\n" + "=" * 70 + "\n")
        return 0

    progress_reporter = _ConsoleProgress() if args.progress else None
    opt = Options(
        out_dir=args.out,
        raw=args.raw,
        obj=not args.no_obj,
        fbx=not args.no_fbx,
        png=not args.no_png,
        unity_mat=not args.no_unity,
        mat_pipeline=args.pipeline,
        colliders=not args.no_colliders,
        prefab=args.prefab,
        dynamic=not args.static,
        max_hulls=args.max_hulls,
        max_verts_per_hull=args.max_verts_per_hull,
        merge_convex_neighbors=args.merge_convex_neighbors,
        merge_max_inflation=args.merge_max_inflation,
        merge_contact_epsilon=args.merge_contact_epsilon,
        merge_max_deviation_ratio=args.merge_max_deviation_ratio,
        concavity_threshold=args.concavity_threshold,
        all_lods=args.all_lods,
        no_cas=args.no_cas,
        extract_geom=args.geom,
        per_object=args.per_object,
        linked_fullbuilds=args.linked_fullbuilds,
        game_resource_fallback=args.game_resource_fallback,
        resume=args.resume,
        progress_callback=progress_reporter,
    )

    all_reports = []
    for pkg in packages:
        if not args.quiet:
            print(f"\n=== {pkg} ===")
        try:
            rep = extract_package(pkg, opt)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        all_reports.append(rep)
        if progress_reporter is not None:
            progress_reporter("package_done", package=pkg)
        if args.quiet:
            continue
        print(f"  -> {rep['out_dir']}")
        print(f"  resources: {rep['total_resources']}")
        linked = rep.get("linked_resources")
        if linked:
            family = ", ".join(linked.get("family_packages", []))
            print(f"  linked FullBuilds: {family}")
            print(f"  linked TGI index: {linked.get('indexed_tgis', 0)} resources "
                  f"in {linked.get('indexed_packages', 0)} packages; "
                  f"duplicates={linked.get('duplicate_tgis', 0)}")
            reads = linked.get("resources_read") or {}
            if reads:
                print("  linked texture reads: " + ", ".join(
                    f"{name}={count}" for name, count in sorted(reads.items())))
            for warning in linked.get("warnings") or []:
                print(f"  linked warning: {warning}")
        for m in rep["meshes"]:
            print(f"  mesh  {m['name']}: {m['verts']} verts, {m['faces']} faces -> {', '.join(m['files'])}")
        for t in rep["textures"]:
            print(f"  tex   {t['file']}: {t['status']}")
        for mat in rep["materials"]:
            print(f"  mat   {mat['file']} [{mat.get('pipeline','?')}] (diffuse={mat['diffuse']}, normal={mat['normal']})")
        for collider in rep.get("colliders", []):
            budget_note = (f", OVER BUDGET {collider['target_budget']}"
                           if collider.get("over_budget") else "")
            kinds = collider.get("kinds") or {}
            kind_note = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
            if kind_note:
                kind_note = "; " + kind_note
            print(f"  collider {collider['name']}: {collider['parts']} parts "
                  f"[{collider['method']}{budget_note}{kind_note}]")
        for pf in rep.get("prefabs", []):
            print(f"  prefab {pf['file']}: collider={pf['collider_method']} "
                  f"({pf['collider_parts']} parts), dynamic={pf['dynamic']}")
        if rep["errors"]:
            print(f"  errors ({len(rep['errors'])}):")
            for err in rep["errors"]:
                print(f"    ! {err}")

    if args.json:
        print(json.dumps(all_reports, indent=2))

    total_meshes = sum(len(r["meshes"]) for r in all_reports)
    total_tex = sum(len(r["textures"]) for r in all_reports)
    if not args.quiet or args.progress:
        print(f"\nDone. {len(all_reports)} package(s), {total_meshes} mesh(es), {total_tex} texture(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
