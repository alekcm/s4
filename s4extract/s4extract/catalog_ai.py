"""Compact AI-oriented catalog generated from s4extract exports.

The regular ``catalog_database.json`` remains the extractor's durable working
catalog (resume IDs, source identity, colors). This module adds dimensions to
it and writes a separate tiny ``catalog_ai.json`` intended for passing many
objects to an LLM.

AI format, deliberately positional and minified::

    {"v":2,"u":"r1000","t":[...],"s":[...],"c":[...],
     "o":[[id,name,[x,y,z],typeIds,styleIds,[[swatch,colorIds],...]],...]}

``t``, ``s`` and ``c`` are shared dictionaries for type/room tags, style tags,
and colour/material tags. ``u`` is normally ``t10``: dimensions in tenths of
a verified Sims build tile (10 = one tile). Human-entered metadata lives
separately in ``catalog_annotations.json`` and is merged only while generating
the compact AI file, so hand-written labels are never overwritten by the
extractor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .catalog_tags import product_style_tag_for_key


DB_FILENAME = "catalog_database.json"
AI_FILENAME = "catalog_ai.json"
SETTINGS_FILENAME = "catalog_ai_settings.json"
# Verified from the supplied exact reference meshes:
# catCondo_1x1_Med = X 1.000062, Z 1.000062; catCondo_2x1_High = X 2.0, Z 1.0.
# Thus one Sims build tile is one raw export unit on both horizontal axes.
DEFAULT_TILE_SIZE = 1.0
ANNOTATIONS_FILENAME = "catalog_annotations.json"
ANNOTATIONS_README_FILENAME = "catalog_annotations_README.txt"
STYLE_OVERRIDES_FILENAME = "catalog_style_overrides.json"
STYLE_OVERRIDES_README_FILENAME = "catalog_style_overrides_README.txt"


def _db_path(out_dir: str) -> str:
    return os.path.join(out_dir, DB_FILENAME)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, value, *, compact: bool = False) -> None:
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def _normalise_dimensions(dimensions) -> list[float] | None:
    if not isinstance(dimensions, (list, tuple)) or len(dimensions) != 3:
        return None
    try:
        result = [round(max(0.0, float(value)), 6) for value in dimensions]
    except (TypeError, ValueError):
        return None
    return result


def update_dimensions_bulk(out_dir: str, updates: list[tuple[str, list[float]]]) -> int:
    """Store raw LOD0 dimensions in working catalog entries, once per batch."""
    if not updates:
        return 0
    path = _db_path(out_dir)
    database = _load_json(path, [])
    if not isinstance(database, list):
        return 0
    by_id = {str(item.get("id")): item for item in database if isinstance(item, dict)}
    changed = 0
    for catalog_id, dimensions in updates:
        normalised = _normalise_dimensions(dimensions)
        item = by_id.get(str(catalog_id))
        if item is None or normalised is None:
            continue
        if item.get("dimensions") != normalised:
            # Compact positional vector: [x, y, z], in raw exported units.
            item["dimensions"] = normalised
            changed += 1
    if changed:
        _save_json(path, database)
    return changed


def update_catalog_tags_bulk(out_dir: str, updates: list[tuple[str, list[str]]]) -> int:
    """Store already-filtered semantic COBJ tags in working catalog entries."""
    if not updates:
        return 0
    path = _db_path(out_dir)
    database = _load_json(path, [])
    if not isinstance(database, list):
        return 0
    by_id = {str(item.get("id")): item for item in database if isinstance(item, dict)}
    changed = 0
    for catalog_id, tags in updates:
        item = by_id.get(str(catalog_id))
        if item is None:
            continue
        clean = _normalise_tags(tags)
        if item.get("tags") != clean:
            item["tags"] = clean
            changed += 1
    if changed:
        _save_json(path, database)
    return changed


def update_catalog_styles_bulk(out_dir: str, updates: list[tuple[str, list[str]]]) -> int:
    """Store ProductStyle-derived style tags in working catalog entries."""
    if not updates:
        return 0
    path = _db_path(out_dir)
    database = _load_json(path, [])
    if not isinstance(database, list):
        return 0
    by_id = {str(item.get("id")): item for item in database if isinstance(item, dict)}
    changed = 0
    for catalog_id, styles in updates:
        item = by_id.get(str(catalog_id))
        if item is None:
            continue
        clean = _normalise_tags(styles)
        if item.get("styles") != clean:
            item["styles"] = clean
            changed += 1
    if changed:
        _save_json(path, database)
    return changed


def update_catalog_style_refs_bulk(out_dir: str, updates: list[tuple[str, list[str]]]) -> int:
    """Store raw ProductStyle TGIs so unknown future styles are not lost."""
    if not updates:
        return 0
    path = _db_path(out_dir)
    database = _load_json(path, [])
    if not isinstance(database, list):
        return 0
    by_id = {str(item.get("id")): item for item in database if isinstance(item, dict)}
    changed = 0
    for catalog_id, refs in updates:
        item = by_id.get(str(catalog_id))
        if item is None:
            continue
        clean = sorted({str(value) for value in refs if isinstance(value, str)})
        if item.get("style_refs") != clean:
            item["style_refs"] = clean
            changed += 1
    if changed:
        _save_json(path, database)
    return changed


def update_catalog_variants_bulk(out_dir: str, updates: list[tuple[str, list[list]]]) -> int:
    """Store per-COBJ-swatch colour tags as [index, [tags...]] rows."""
    if not updates:
        return 0
    path = _db_path(out_dir)
    database = _load_json(path, [])
    if not isinstance(database, list):
        return 0
    by_id = {str(item.get("id")): item for item in database if isinstance(item, dict)}
    changed = 0
    for catalog_id, variants in updates:
        item = by_id.get(str(catalog_id))
        if item is None:
            continue
        clean = []
        for row in variants or []:
            try:
                index, tags = row
                index = int(index)
            except (TypeError, ValueError):
                continue
            if index < 0:
                continue
            clean.append([index, _normalise_tags(tags)])
        clean.sort(key=lambda row: row[0])
        if item.get("variants") != clean:
            item["variants"] = clean
            changed += 1
    if changed:
        _save_json(path, database)
    return changed


def _object_output_folder(out_dir: str, catalog_id) -> Path | None:
    """Find an exported object by stable numeric prefix, never by its name."""
    prefix = f"[{str(catalog_id).zfill(4)}] "
    try:
        candidates = [child for child in Path(out_dir).iterdir()
                      if child.is_dir() and child.name.startswith(prefix)]
    except OSError:
        return None
    return sorted(candidates)[0] if candidates else None


def _visual_obj_candidates(folder: Path) -> list[Path]:
    all_obj = []
    lod0 = []
    for path in folder.glob("*.obj"):
        name = path.name.lower()
        if any(token in name for token in ("_collider", "_part", "_broken")):
            continue
        all_obj.append(path)
        if "_lod00" in name:
            lod0.append(path)
    return sorted(lod0 or all_obj)


def _dimensions_from_obj_files(paths: list[Path]) -> list[float] | None:
    """Stream OBJ vertices to calculate a combined visual LOD0 bounding box."""
    minimum = [float("inf"), float("inf"), float("inf")]
    maximum = [float("-inf"), float("-inf"), float("-inf")]
    count = 0
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.startswith("v "):
                        continue
                    fields = line.split()
                    if len(fields) < 4:
                        continue
                    try:
                        point = (float(fields[1]), float(fields[2]), float(fields[3]))
                    except ValueError:
                        continue
                    for axis, value in enumerate(point):
                        minimum[axis] = min(minimum[axis], value)
                        maximum[axis] = max(maximum[axis], value)
                    count += 1
        except OSError:
            continue
    if not count:
        return None
    return _normalise_dimensions([maximum[i] - minimum[i] for i in range(3)])


def backfill_dimensions_from_output(out_dir: str, database: list) -> int:
    """One-time compatibility scan for old exports that predate dimensions."""
    changed = 0
    for item in database:
        if not isinstance(item, dict) or _normalise_dimensions(item.get("dimensions")):
            continue
        folder = _object_output_folder(out_dir, item.get("id", ""))
        if folder is None:
            continue
        dimensions = _dimensions_from_obj_files(_visual_obj_candidates(folder))
        if dimensions is not None:
            item["dimensions"] = dimensions
            changed += 1
    return changed


def _tile_size_from_settings(out_dir: str) -> float:
    settings_path = os.path.join(out_dir, SETTINGS_FILENAME)
    settings = _load_json(settings_path, None)
    if not isinstance(settings, dict):
        settings = {}
    try:
        value = float(settings.get("tile_size"))
        if value > 1e-9:
            return value
    except (TypeError, ValueError):
        pass

    # Earlier update versions created tile_size:null while calibration was
    # pending. The two exact reference planes now prove the canonical scale is
    # 1.0 raw unit per build tile, so migrate that old config automatically.
    settings["tile_size"] = DEFAULT_TILE_SIZE
    settings["axis_order"] = ["x", "y", "z"]
    settings["calibration"] = "reference_1x1: 1.000062 x 1.000062; reference_2x1: 2.0 x 1.0"
    settings["note"] = "1 raw export unit = 1 Sims build tile. Override tile_size only for a deliberately rescaled source."
    _save_json(settings_path, settings)
    return DEFAULT_TILE_SIZE


# ---------------------------------------------------------------------------
# Human annotations: names, room/type, style and swatch colour tags
# ---------------------------------------------------------------------------

def _annotations_readme() -> str:
    return """CATALOG ANNOTATIONS\n===================\n\nThis file is optional, human-editable metadata used only for catalog_ai.json.\nThe extractor does not overwrite it. Entries are keyed by catalog ID:\n\n{\n  \"v\": 1,\n  \"o\": {\n    \"42\": {\n      \"n\": \"двухдверный холодильник\",\n      \"t\": [\"kitchen\", \"fridge\"],\n      \"s\": [\"casual\", \"retro\"],\n      \"v\": {\n        \"0\": [\"white\"],\n        \"1\": [\"blue\"],\n        \"2\": [\"black\", \"metal\"]\n      }\n    }\n  }\n}\n\nFields:\n n = manual AI-facing name; omitted -> internal Sims name is used.\n t = room/function/type tags; these extend automatic COBJ Buy/Build tags.\n s = object-level style tags, e.g. casual, futuristic, retro, rustic.\n v = per-swatch colour/material tags. The key is the swatch number from the\n     exported *_swatchNN_material.mat file. A manually specified v entry\n     overrides automatic dominant-colour suggestions for that swatch.\n"""


def _ensure_annotations(out_dir: str) -> dict:
    path = os.path.join(out_dir, ANNOTATIONS_FILENAME)
    annotations = _load_json(path, None)
    if not isinstance(annotations, dict):
        annotations = {"v": 1, "o": {}}
        _save_json(path, annotations)
    if not isinstance(annotations.get("o"), dict):
        annotations["o"] = {}
        _save_json(path, annotations)
    readme_path = os.path.join(out_dir, ANNOTATIONS_README_FILENAME)
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(_annotations_readme())
    return annotations


def _normalise_tags(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip().lower().replace(" ", "_")
        if tag and tag not in result:
            result.append(tag)
    return result


def _swatch_number(value) -> int | None:
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    found = re.search(r"(\d+)$", value.strip().lower())
    return int(found.group(1)) if found else None


def _automatic_variant_tags(item: dict) -> dict[int, list[str]]:
    """Use COBJ swatch colours first, then dominant texture colours as fallback."""
    variants: dict[int, list[str]] = {}
    # COBJ supplies game-authored Color_* filters for an individual swatch.
    for row in item.get("variants") or []:
        try:
            swatch, tags = row
            swatch = int(swatch)
        except (TypeError, ValueError):
            continue
        if swatch >= 0:
            variants[swatch] = _normalise_tags(tags)
    # Older exports may only have the image-derived color record.
    for color in item.get("colors") or []:
        if not isinstance(color, dict):
            continue
        swatch = _swatch_number(color.get("swatch"))
        if swatch is None or swatch in variants:
            continue
        tags = _normalise_tags([color.get("name", "")])
        if tags:
            variants[swatch] = tags
    return variants


def _style_overrides_readme() -> str:
    return """PRODUCT STYLE OVERRIDES\n=======================\n\nCOBJ ProductStyle resources describe an object's visual décor style. Known EA\nstyles are assigned automatically. Unknown IDs found in any exported DLC are\nlisted below with an empty array. Name each new style ONCE, then rebuild\ncatalog_ai.json; no model re-export is required.\n\nExample:\n{\n  \"v\": 1,\n  \"styles\": {\n    \"9F5CFF10_00000000_000000000007CB54\": [\"island\", \"tropical\"]\n  }\n}\n\nThe value may contain one or more normalized tags. These tags are added to all\nobjects that reference the same ProductStyle TGI.\n"""


def _ensure_style_overrides(out_dir: str, database: list) -> dict[str, list[str]]:
    """Preserve unknown style IDs as a small editable, global style dictionary."""
    path = os.path.join(out_dir, STYLE_OVERRIDES_FILENAME)
    document = _load_json(path, None)
    if not isinstance(document, dict):
        document = {"v": 1, "styles": {}}
    if not isinstance(document.get("styles"), dict):
        document["styles"] = {}

    changed = False
    for item in database:
        if not isinstance(item, dict):
            continue
        for key in item.get("style_refs") or []:
            if not isinstance(key, str) or product_style_tag_for_key(key):
                continue
            if key not in document["styles"]:
                document["styles"][key] = []
                changed = True
    if changed or not os.path.exists(path):
        _save_json(path, document)

    readme_path = os.path.join(out_dir, STYLE_OVERRIDES_README_FILENAME)
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(_style_overrides_readme())

    return {key: _normalise_tags(value) for key, value in document["styles"].items()}


def _object_metadata(item: dict, annotations: dict,
                     style_overrides: dict[str, list[str]]) -> tuple[str, list[str], list[str], dict[int, list[str]]]:
    object_map = annotations.get("o") or {}
    # Working catalog IDs are usually zero-padded ("0001"), while humans
    # naturally type "1" in annotations. Accept both spellings.
    raw_id = str(item.get("id"))
    entry = object_map.get(raw_id)
    if entry is None:
        try:
            entry = object_map.get(str(int(raw_id)))
        except (TypeError, ValueError):
            entry = None
    if not isinstance(entry, dict):
        entry = {}
    manual_name = entry.get("n")
    name = manual_name.strip() if isinstance(manual_name, str) and manual_name.strip() else item.get("name", "")
    # In-game Buy/Build category tags (e.g. Bed, BedDouble) are automatic;
    # manual t tags extend them rather than replacing useful game metadata.
    types = _normalise_tags(list(item.get("tags") or []) + list(entry.get("t") or []))
    # Known ProductStyle records provide automatic aesthetics (basics,
    # french_country, gothic_farmhouse). Unknown future style IDs can be named
    # once in catalog_style_overrides.json; manual per-object styles extend both.
    inherited_styles = []
    for key in item.get("style_refs") or []:
        inherited_styles.extend(style_overrides.get(key, []))
    styles = _normalise_tags(list(item.get("styles") or []) + inherited_styles + list(entry.get("s") or []))
    variants = _automatic_variant_tags(item)
    manual_variants = entry.get("v") or {}
    if isinstance(manual_variants, dict):
        for raw_swatch, raw_tags in manual_variants.items():
            swatch = _swatch_number(raw_swatch)
            if swatch is not None:
                # Explicit human entry, including [], deliberately overrides
                # the auto colour suggestion.
                variants[swatch] = _normalise_tags(raw_tags)
    return name, types, styles, variants


def _encode_dictionary(tags: set[str]) -> tuple[list[str], dict[str, int]]:
    values = sorted(tags)
    return values, {value: index for index, value in enumerate(values)}


def build_ai_catalog(out_dir: str, *, backfill: bool = False) -> dict:
    """Generate minified ``catalog_ai.json`` and return a short summary."""
    db_path = _db_path(out_dir)
    database = _load_json(db_path, [])
    if not isinstance(database, list):
        database = []

    backfilled = backfill_dimensions_from_output(out_dir, database) if backfill else 0
    if backfilled:
        _save_json(db_path, database)

    annotations = _ensure_annotations(out_dir)
    style_overrides = _ensure_style_overrides(out_dir, database)
    tile_size = _tile_size_from_settings(out_dir)
    # t10 = tenths of a verified Sims build tile. Integer encoding remains
    # compact while preserving 0.1-tile placement precision for the AI.
    unit = "t10"
    scale = 10.0 / tile_size

    prepared = []
    all_types: set[str] = set()
    all_styles: set[str] = set()
    all_colors: set[str] = set()
    for item in database:
        if not isinstance(item, dict):
            continue
        dimensions = _normalise_dimensions(item.get("dimensions"))
        if dimensions is None:
            continue
        try:
            catalog_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name, types, styles, variants = _object_metadata(item, annotations, style_overrides)
        if not isinstance(name, str) or not name:
            continue
        all_types.update(types)
        all_styles.update(styles)
        for tags in variants.values():
            all_colors.update(tags)
        prepared.append((catalog_id, name, dimensions, types, styles, variants))

    type_values, type_index = _encode_dictionary(all_types)
    style_values, style_index = _encode_dictionary(all_styles)
    color_values, color_index = _encode_dictionary(all_colors)

    rows = []
    for catalog_id, name, dimensions, types, styles, variants in prepared:
        encoded_dimensions = [int(round(value * scale)) for value in dimensions]
        encoded_variants = [
            [swatch, [color_index[tag] for tag in tags if tag in color_index]]
            for swatch, tags in sorted(variants.items())
        ]
        rows.append([
            catalog_id,
            name,
            encoded_dimensions,
            [type_index[tag] for tag in types if tag in type_index],
            [style_index[tag] for tag in styles if tag in style_index],
            encoded_variants,
        ])
    rows.sort(key=lambda row: row[0])

    payload = {
        "v": 2,
        "u": unit,
        "t": type_values,
        "s": style_values,
        "c": color_values,
        "o": rows,
    }
    _save_json(os.path.join(out_dir, AI_FILENAME), payload, compact=True)
    return {"objects": len(rows), "backfilled": backfilled, "unit": unit,
            "path": AI_FILENAME}
