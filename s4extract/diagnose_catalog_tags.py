"""Diagnose Build/Buy COBJ catalog tags in a Sims 4 package.

Usage: drag a .package onto diagnose-catalog-tags.bat. The report groups tags
by discovered object and gives an aggregate inventory useful for extending the
bundled registry with newer EA tags.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s4extract.dbpf import DBPF
from s4extract.objd import discover_objects
from s4extract.catalog_tags import ai_semantic_tags, ai_product_style_tags, tag_name


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path or not os.path.isfile(path):
        print(f"File not found: {path}")
        return 1

    pkg = DBPF.from_file(path)
    objects = discover_objects(pkg)
    print(f"=== COBJ catalog-tag diagnostic: {os.path.basename(path)} ===")
    print(f"Resources: {len(pkg.entries)}")
    print(f"Discovered objects: {len(objects)}")
    print()

    all_tags = Counter()
    unknown = Counter()
    semantic = Counter()
    styles = Counter()
    for index, obj in enumerate(objects):
        raw = sorted(set(obj.catalog_tags))
        useful = ai_semantic_tags(raw)
        named = [(value, tag_name(value)) for value in raw if tag_name(value)]
        unrecognized = [value for value in raw if tag_name(value) is None]
        for value in raw:
            all_tags[value] += 1
        for value in unrecognized:
            unknown[value] += 1
        for value in useful:
            semantic[value] += 1
        for style in obj.product_styles:
            styles[style] += 1

        print(f"[{index}] {obj.name}")
        print(f"  semantic: {', '.join(useful) if useful else '-'}")
        print("  known raw: " + (", ".join(
            f"0x{value:04X}={name}" for value, name in named) if named else "-"))
        print("  unknown raw: " + (", ".join(f"0x{value:04X}" for value in unrecognized)
                                     if unrecognized else "-"))
        style_names = ai_product_style_tags(obj.product_styles)
        raw_styles = ", ".join(
            f"{type_id:08X}_{group_id:08X}_{instance:016X}"
            for type_id, group_id, instance in obj.product_styles)
        print("  product styles: " + ((", ".join(style_names) + " | " if style_names else "") + raw_styles
                                     if obj.product_styles else "-"))
        print()

    print("--- Aggregate tag inventory ---")
    for value, count in all_tags.most_common():
        mapped = tag_name(value)
        name = mapped or "UNKNOWN"
        kind = ("semantic" if mapped and ai_semantic_tags([value])
                else "ignored/functional" if mapped else "unknown")
        print(f"  0x{value:04X}  x{count:<5}  {kind:<19} {name}")

    print("\n--- Semantic tags retained for AI ---")
    for name, count in semantic.most_common():
        print(f"  {name}: {count}")

    print("\n--- Unknown tag IDs (need current tag registry to name) ---")
    if unknown:
        for value, count in unknown.most_common():
            print(f"  0x{value:04X}: {count}")
    else:
        print("  none")

    print("\n--- ProductStyle TGIs ---")
    if styles:
        for (type_id, group_id, instance), count in styles.most_common():
            print(f"  {type_id:08X}_{group_id:08X}_{instance:016X}: {count}")
    else:
        print("  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
