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
                    help="Max convex parts per object (default 128)")
    ap.add_argument("--no-convex-merge", action="store_false", dest="merge_convex_neighbors",
                    help="keep every generated convex fragment; disable safe near-convex merging")
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
        merge_convex_neighbors=args.merge_convex_neighbors,
        concavity_threshold=args.concavity_threshold,
        all_lods=args.all_lods,
        no_cas=args.no_cas,
        extract_geom=args.geom,
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
        if args.quiet:
            continue
        print(f"  -> {rep['out_dir']}")
        print(f"  resources: {rep['total_resources']}")
        for m in rep["meshes"]:
            print(f"  mesh  {m['name']}: {m['verts']} verts, {m['faces']} faces -> {', '.join(m['files'])}")
        for t in rep["textures"]:
            print(f"  tex   {t['file']}: {t['status']}")
        for mat in rep["materials"]:
            print(f"  mat   {mat['file']} [{mat.get('pipeline','?')}] (diffuse={mat['diffuse']}, normal={mat['normal']})")
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
    if not args.quiet:
        print(f"\nDone. {len(all_reports)} package(s), {total_meshes} mesh(es), {total_tex} texture(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
