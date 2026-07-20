import sys
import os

# argv[2] = directory where the bat lives (the s4extract root)
s4root = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, s4root)

from s4extract.dbpf import DBPF
from s4extract.extractor import _build_stbl_map, _parse_cobj, _parse_objd, _discover_objects
from s4extract import resource_types as rt
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path or not os.path.exists(path):
    print(f"File not found: {path}")
    sys.exit(1)

pkg = DBPF.from_file(path)
base = os.path.splitext(os.path.basename(path))[0]

print(f"=== {base} ===")
print(f"Total entries: {len(pkg.entries)}")

type_counts = Counter(rt.type_name(e.type_id) for e in pkg.entries)
for t, c in sorted(type_counts.items()):
    print(f"  {t}: {c}")

stbl_map = _build_stbl_map(pkg)
print(f"\nSTBL entries: {len(stbl_map)}")
for k, v in sorted(stbl_map.items())[:50]:
    print(f"  0x{k:08X} -> \"{v}\"")
if len(stbl_map) > 50:
    print(f"  ... and {len(stbl_map) - 50} more")

print(f"\n--- COBJ entries ({len(pkg.find(rt.COBJ))}) ---")
seen_hashes = {}
for e in pkg.find(rt.COBJ):
    try:
        data = pkg.read_resource(e)
        info = _parse_cobj(data)
        h = info["name_hash"]
        if h not in seen_hashes:
            seen_hashes[h] = 0
        seen_hashes[h] += 1
    except Exception:
        pass

for h, count in sorted(seen_hashes.items()):
    name = stbl_map.get(h, "???")
    print(f"  name_hash=0x{h:08X} ({count} swatches) -> \"{name}\"")

print(f"\n--- OBJD entries ({len(pkg.find(rt.OBJD))}) ---")
objd_entries = []
for e in pkg.find(rt.OBJD):
    try:
        data = pkg.read_resource(e)
        info = _parse_objd(data)
        objd_entries.append((e, info))
    except Exception:
        pass

by_model = {}
for e, info in objd_entries:
    key = tuple(info["model_keys"]) if info["model_keys"] else ()
    if key not in by_model:
        by_model[key] = []
    by_model[key].append((e, info))

for key, entries in by_model.items():
    first_info = entries[0][1]
    display = first_info["name"]
    for e2, info2 in entries:
        for ce in pkg.find(rt.COBJ):
            if ce.instance == e2.instance:
                try:
                    cd = pkg.read_resource(ce)
                    ci = _parse_cobj(cd)
                    if ci["name_hash"] in stbl_map:
                        display = stbl_map[ci["name_hash"]]
                except Exception:
                    pass
                break
        if display != first_info["name"]:
            break
    print(f'  OBJD name: "{first_info["name"]}"')
    print(f'  STBL name: "{display}"')
    print(f"  Model keys: {key}")
    print(f"  Swatches: {len(entries)}")
    print()

print(f"--- MODL entries ({len(pkg.find(rt.MODL))}) ---")
for e in pkg.find(rt.MODL):
    print(f"  type={e.type_id:08X} group={e.group_id:08X} instance={e.instance:016X}")
print(f"--- MLOD entries ({len(pkg.find(rt.MLOD))}) ---")
for e in pkg.find(rt.MLOD):
    print(f"  type={e.type_id:08X} group={e.group_id:08X} instance={e.instance:016X}")

print(f"\n--- Discovered objects ---")
objects = _discover_objects(pkg, base)
print(f"Count: {len(objects)}")
for i, obj in enumerate(objects):
    print(f'  [{i}] "{obj.display_name}" (internal: "{obj.internal_name}") modl_tgis={obj.modl_tgis}')
