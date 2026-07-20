"""Diagnose cross-package model -> material -> texture links in The Sims 4.

Drop any numbered FullBuild package onto diagnose_linked_packs.bat.  The script
finds sibling packages with the same prefix (for example ClientFullBuild0,
ClientFullBuild1, ClientFullBuild2), builds a global TGI index, then follows:

    CATALOG/OBJD -> MODL/MLOD -> RCOL MTST/MATD -> texture TGI

It does not extract meshes or textures.  Its purpose is to show whether an
object's texture references resolve locally or in another part of the same DLC.
"""
from __future__ import annotations

import gc
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# The file is intended to sit next to the s4extract package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s4extract.dbpf import DBPF
from s4extract import resource_types as rt
from s4extract.objd import discover_objects, ObjectInfo
from s4extract.rcol import RCOL, material_variants


@dataclass(frozen=True)
class Location:
    package: str
    type_id: int
    group_id: int
    instance: int
    mem_size: int

    @property
    def tgi(self) -> tuple[int, int, int]:
        return (self.type_id, self.group_id, self.instance)


def _fullbuild_family(input_path: str) -> list[str]:
    """Find numbered sibling packages belonging to one FullBuild family.

    ClientFullBuild0.package -> ClientFullBuild0/1/2.package
    FullBuild0.package       -> FullBuild0/1/2.package

    If the input does not have a trailing number, or no siblings are found,
    only the supplied package is returned.  This prevents an accidental scan
    of every .package in a normal Mods directory.
    """
    p = Path(input_path).resolve()
    match = re.match(r"^(.*?)(\d+)$", p.stem, flags=re.IGNORECASE)
    if not match:
        return [str(p)]

    prefix = match.group(1)
    numbered = re.compile(r"^" + re.escape(prefix) + r"\d+$", re.IGNORECASE)
    candidates = [
        child for child in p.parent.iterdir()
        if child.is_file() and child.suffix.lower() == ".package"
        and numbered.match(child.stem)
    ]
    if len(candidates) < 2:
        return [str(p)]

    def natural_number(path: Path) -> int:
        m = re.search(r"(\d+)$", path.stem)
        return int(m.group(1)) if m else -1

    return [str(child) for child in sorted(candidates, key=natural_number)]


def _short_tgi(key: tuple[int, int, int]) -> str:
    return f"{key[0]:08X}_{key[1]:08X}_{key[2]:016X}"


def _material_refs(rcol: RCOL) -> list[int]:
    """Read only material references from the MODL/MLOD group records.

    Unlike parse_object_mesh(), this deliberately does not decode vertex or
    index buffers.  It keeps this diagnostic fast even for a full expansion
    pack containing thousands of models.
    """
    indices = rcol.find_chunks_by_sig(b"MLOD")
    if not indices:
        indices = rcol.find_chunks_by_sig(b"MODL")
    if not indices:
        return []

    blob = rcol.chunk_bytes(indices[0])
    if len(blob) < 12:
        return []
    group_count = int.from_bytes(blob[8:12], "little")
    pos = 12
    refs: list[int] = []

    for _ in range(group_count):
        if pos + 12 > len(blob):
            break
        subset_bytes = int.from_bytes(blob[pos:pos + 4], "little")
        next_group = pos + 4 + subset_bytes
        if subset_bytes < 8 or next_group > len(blob):
            break
        # after subset_bytes and name_hash is the MTST/MATD chunk reference
        mat_ref = int.from_bytes(blob[pos + 8:pos + 12], "little")
        if mat_ref not in refs:
            refs.append(mat_ref)
        pos = next_group
    return refs


def _inventory(paths: list[str]):
    """Create a global TGI -> package index without keeping packages in RAM."""
    resource_index: dict[tuple[int, int, int], list[Location]] = defaultdict(list)
    package_entries: dict[str, list[Location]] = {}
    package_counts: dict[str, Counter] = {}

    print("=== Linked FullBuild diagnostic ===")
    print("Companion packages:")
    for path in paths:
        print(f"  - {path}")
    print()

    for number, path in enumerate(paths, 1):
        print(f"[{number}/{len(paths)}] Indexing {os.path.basename(path)} ...")
        pkg = DBPF.from_file(path)
        counts = Counter(rt.type_name(entry.type_id) for entry in pkg.entries)
        locations = []
        for entry in pkg.entries:
            loc = Location(
                package=os.path.basename(path),
                type_id=entry.type_id,
                group_id=entry.group_id,
                instance=entry.instance,
                mem_size=entry.mem_size,
            )
            locations.append(loc)
            resource_index[loc.tgi].append(loc)
        package_entries[path] = locations
        package_counts[path] = counts

        print(f"    entries={len(pkg.entries)}  MODL={len(pkg.find(rt.MODL))}  "
              f"MLOD={len(pkg.find(rt.MLOD))}  images="
              f"{sum(1 for e in pkg.entries if e.type_id in rt.IMAGE_TYPES)}")
        # DBPF.from_file reads a large file at once. Release it before opening
        # the next companion package.
        del pkg
        gc.collect()

    print("\n--- Package inventory ---")
    for path in paths:
        print(f"{os.path.basename(path)}")
        for name, count in sorted(package_counts[path].items()):
            print(f"  {name}: {count}")

    duplicates = {key: locs for key, locs in resource_index.items() if len(locs) > 1}
    print(f"\nUnique TGI resources across family: {len(resource_index)}")
    print(f"Duplicate TGI keys across family: {len(duplicates)}")
    if duplicates:
        print("  (The extractor must apply a documented priority rule for these.)")

    return resource_index, package_entries


def _select_location(locations: list[Location], preferred_package: str) -> Location:
    """Use local resource first; otherwise deterministic FullBuild order."""
    for loc in locations:
        if loc.package == preferred_package:
            return loc
    return locations[0]


def _pseudo_objects(pkg: DBPF) -> list[ObjectInfo]:
    """Fallback for packs that have models but catalog data that we cannot name.

    Some stripped/test packages contain only MLOD (no MODL or CATALOG), so
    group every available model resource by instance rather than assuming MODL
    is present.
    """
    by_instance: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for e in pkg.entries:
        if e.type_id in (rt.MODL, rt.MLOD):
            by_instance[e.instance].append((e.type_id, e.group_id, e.instance))

    items = []
    for instance, tgis in sorted(by_instance.items()):
        items.append(ObjectInfo(
            name=f"unnamed_{instance:016X}",
            model_tgis=tgis,
            source_type=tgis[0][0],
            source_instance=instance,
        ))
    return items


def _analyse_model_package(
    path: str,
    resource_index: dict[tuple[int, int, int], list[Location]],
    package_entries: dict[str, list[Location]],
):
    """Trace material texture references for all catalog objects in one pack."""
    pkg = DBPF.from_file(path)
    source_name = os.path.basename(path)
    objects = discover_objects(pkg)
    if not objects:
        objects = _pseudo_objects(pkg)

    by_instance: dict[int, list] = defaultdict(list)
    for entry in pkg.entries:
        if entry.type_id in (rt.MODL, rt.MLOD):
            by_instance[entry.instance].append(entry)

    model_seen: set[tuple[int, int, int]] = set()
    texture_seen: set[tuple[int, int, int]] = set()
    role_counts = Counter()
    resolved_by_package = Counter()
    missing: list[tuple[str, str, tuple[int, int, int]]] = []
    external_examples: list[tuple[str, str, tuple[int, int, int], str]] = []
    parse_errors: list[str] = []
    unnamed_families = 0

    for obj in objects:
        object_name = obj.name or f"unnamed_{obj.source_instance:016X}"
        instances = {tgi[2] for tgi in obj.model_tgis}
        if not instances:
            continue

        entries = []
        for instance in instances:
            entries.extend(by_instance.get(instance, []))
        if not entries:
            unnamed_families += 1
            continue

        for entry in entries:
            model_key = (entry.type_id, entry.group_id, entry.instance)
            if model_key in model_seen:
                continue
            model_seen.add(model_key)
            try:
                rcol = RCOL(pkg.read_resource(entry))
                for mat_ref in _material_refs(rcol):
                    variants = material_variants(rcol, mat_ref)
                    for variant in variants:
                        for role, key in (
                            ("diffuse", variant.diffuse_key),
                            ("normal", variant.normal_key),
                            ("specular", variant.specular_key),
                            ("emission", variant.emission_key),
                        ):
                            if not key:
                                continue
                            role_counts[role] += 1
                            texture_seen.add(key)
                            locations = resource_index.get(key, [])
                            if not locations:
                                missing.append((object_name, role, key))
                                continue
                            chosen = _select_location(locations, source_name)
                            resolved_by_package[chosen.package] += 1
                            if chosen.package != source_name and len(external_examples) < 40:
                                external_examples.append((object_name, role, key, chosen.package))
            except Exception as exc:
                if len(parse_errors) < 30:
                    parse_errors.append(f"{entry.tgi}: {exc}")

    print(f"\n=== Material link results: {source_name} ===")
    print(f"Catalog objects analysed: {len(objects)}")
    print(f"Distinct MODL/MLOD resources traced: {len(model_seen)}")
    if unnamed_families:
        print(f"Catalog model families not found in this package: {unnamed_families}")
    print(f"Distinct texture TGIs referenced by MATD: {len(texture_seen)}")
    print("References by material role:")
    for role in ("diffuse", "normal", "specular", "emission"):
        print(f"  {role}: {role_counts[role]}")

    print("Resolved texture references by source package:")
    if resolved_by_package:
        for name, count in resolved_by_package.most_common():
            tag = " (LOCAL)" if name == source_name else " (EXTERNAL COMPANION)"
            print(f"  {name}: {count}{tag}")
    else:
        print("  none (no readable MATD texture references were found)")

    print(f"Missing texture references: {len(missing)}")
    for obj_name, role, key in missing[:20]:
        print(f"  MISSING  {obj_name} | {role:8} | {_short_tgi(key)}")
    if len(missing) > 20:
        print(f"  ... plus {len(missing) - 20} more")

    print(f"External-link examples: {len(external_examples)}")
    for obj_name, role, key, package in external_examples:
        print(f"  {obj_name} | {role:8} | {_short_tgi(key)} -> {package}")

    if parse_errors:
        print(f"RCOL/MATD parse warnings: {len(parse_errors)}")
        for line in parse_errors:
            print(f"  ! {line}")

    del pkg
    gc.collect()

    return {
        "source": source_name,
        "objects": len(objects),
        "models": len(model_seen),
        "unique_textures": len(texture_seen),
        "external_refs": sum(
            count for package, count in resolved_by_package.items()
            if package != source_name),
        "missing": len(missing),
    }


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path or not os.path.isfile(path):
        print(f"File not found: {path}")
        return 1
    if not path.lower().endswith(".package"):
        print("Drop a .package file onto this diagnostic.")
        return 1

    companions = _fullbuild_family(path)
    resource_index, package_entries = _inventory(companions)

    results = []
    for companion in companions:
        entries = package_entries[companion]
        has_models = any(loc.type_id in (rt.MODL, rt.MLOD) for loc in entries)
        if not has_models:
            continue
        print(f"\nTracing material links in {os.path.basename(companion)} ...")
        results.append(_analyse_model_package(companion, resource_index, package_entries))

    print("\n=== Conclusion ===")
    if not results:
        print("No MODL/MLOD resources were found in the selected package family.")
        return 0

    total_external = sum(item["external_refs"] for item in results)
    total_missing = sum(item["missing"] for item in results)
    if total_external:
        print("CROSS-PACK LINKS CONFIRMED.")
        print(f"Material references resolved from companion packages: {total_external}")
        print("The exporter needs a shared TGI resolver: it should read textures "
              "from the reported companion package, but write them into the "
              "output folder of the object whose MODL/MLOD uses them.")
    else:
        print("No external material texture links were detected in readable MATD data.")
        print("This can mean that textures are local, or that this game build uses "
              "a material/resource format the current MATD parser does not yet cover.")

    if total_missing:
        print(f"Unresolved links: {total_missing}. They may live in a DeltaBuild, "
              "a base-game FullBuild family, or use an unsupported resource type.")
    else:
        print("No missing texture TGI references were found.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
