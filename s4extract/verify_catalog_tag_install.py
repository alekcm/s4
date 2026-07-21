"""Verify COBJ tag support and expose raw COBJ parsing statistics."""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import s4extract
from s4extract.dbpf import DBPF
from s4extract import resource_types as rt
from s4extract.objd import discover_objects, ObjectInfo
from s4extract.catalog_tags import ai_semantic_tags, ai_product_style_tags, parse_cobj_metadata


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    print("=== s4extract COBJ tag-install verification v2 ===")
    print("Loaded module paths:")
    import s4extract.objd as objd
    import s4extract.extractor as extractor
    import s4extract.catalog_tags as catalog_tags
    print("  package:      " + os.path.abspath(s4extract.__file__))
    print("  objd:         " + os.path.abspath(objd.__file__))
    print("  extractor:    " + os.path.abspath(extractor.__file__))
    print("  catalog_tags: " + os.path.abspath(catalog_tags.__file__))
    print("Feature checks:")
    fields = getattr(ObjectInfo, "__dataclass_fields__", {})
    print("  catalog_swatch_tags field: " + str("catalog_swatch_tags" in fields))
    print("  known bed mapping:          " + repr(ai_semantic_tags([0x00E1, 0x0392])))
    print("  known style mapping:        " + repr(ai_product_style_tags([(0x9F5CFF10, 0, 0x00009609)])))

    if not path or not os.path.isfile(path):
        print("\nNo valid .package supplied. Drag the same source package onto this .bat.")
        return 1

    pkg = DBPF.from_file(path)
    cobj_entries = pkg.find(rt.COBJ)
    catalog_entries = pkg.find(0xC0DB5AE7)
    metadata = {}
    common_versions = Counter()
    nonempty_tag_resources = 0
    parse_failures = []
    for entry in cobj_entries:
        try:
            item = parse_cobj_metadata(pkg.read_resource(entry))
        except Exception as exc:
            item = None
            parse_failures.append(f"{entry.tgi}: {exc}")
        if item is None:
            if len(parse_failures) < 10:
                parse_failures.append(f"{entry.tgi}: parser returned no metadata")
            continue
        metadata[entry.instance] = item
        common_versions[item.common_version] += 1
        if item.tags:
            nonempty_tag_resources += 1

    matched = set(metadata) & {entry.instance for entry in catalog_entries}
    print(f"\nPackage: {path}")
    print(f"COBJ resources: {len(cobj_entries)}")
    print(f"CATALOG resources: {len(catalog_entries)}")
    print(f"COBJ parsed: {len(metadata)}")
    print(f"COBJ with non-empty tags: {nonempty_tag_resources}")
    print(f"COBJ/CATALOG instance matches: {len(matched)}")
    print("COBJ common versions: " + repr(dict(common_versions)))
    if parse_failures:
        print("COBJ parse warnings:")
        for warning in parse_failures[:10]:
            print("  " + warning)

    print("\nFirst parsed COBJ metadata:")
    for index, (instance, item) in enumerate(sorted(metadata.items())[:5]):
        print(f"  [{index}] instance={instance:016X} common_v={item.common_version}")
        print("    raw tags: " + repr([f"0x{tag:04X}" for tag in item.tags]))
        print("    semantic: " + repr(ai_semantic_tags(item.tags)))
        print("    styles: " + repr(ai_product_style_tags(item.product_styles)))

    objects = discover_objects(pkg)
    print(f"\nDiscovered objects: {len(objects)}")
    tagged = 0
    for index, obj in enumerate(objects[:20]):
        tags = ai_semantic_tags(obj.catalog_tags)
        styles = ai_product_style_tags(obj.product_styles)
        if tags or styles:
            tagged += 1
        print(f"  [{index}] {obj.name}")
        print(f"    tags={tags}")
        print(f"    styles={styles}")
        print(f"    swatches={len(obj.catalog_swatch_tags)}")
    if len(objects) > 20:
        print("  ... first 20 shown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
