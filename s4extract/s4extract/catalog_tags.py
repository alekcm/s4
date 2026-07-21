"""Read Sims 4 COBJ catalog tags and keep only AI-useful classification tags.

COBJ contains a common catalog block. Since common block v11, its tag list is a
counted array of uint32 values. The same COBJ instance as a CATALOG swatch
contains buy/category/function/color tags; all swatches of one model are merged
by ``objd.discover_objects``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct


_REGISTRY_FILE = Path(__file__).with_name("catalog_tag_registry.json")
_registry_cache: dict[int, str] | None = None

# ProductStyle resources use a TGI rather than a regular tag value. These
# canonical EA instances are stable across the older and current catalog data.
# Unknown instances remain diagnostic-only instead of receiving a guessed name.
_PRODUCT_STYLE_NAMES = {
    0x000000000000270B: "shotgun",
    0x0000000000005367: "tudor",
    0x0000000000005368: "modern",
    0x0000000000005369: "mission",
    0x0000000000005C03: "basics",
    0x0000000000005C04: "contemporary",
    0x0000000000009609: "queen_anne",
    0x0000000000009615: "french_country",
    0x000000000000A673: "gothic_farmhouse",
    0x000000000000A674: "cosmolux",
    0x000000000000E596: "suburban_contempo",
    0x000000000000115C: "double_gallery",
    0x000000000001DEF2: "garden",
    0x00000000000465D9: "industrial",
    0x000000000004BE5B: "shabby",
    0x000000000004B328: "vintage",
}
_PRODUCT_STYLE_TYPE = 0x9F5CFF10


@dataclass
class CobjMetadata:
    common_version: int
    name_hash: int
    tags: list[int]
    product_styles: list[tuple[int, int, int]]  # (type, group, instance)


def tag_registry() -> dict[int, str]:
    """Load the bundled public EA tag registry once.

    The registry covers canonical Buy/Build tags such as ``BuyCatSS_Bed`` and
    ``BuyCatSS_BedDouble``. Newer unknown IDs remain visible in diagnostics but
    are deliberately not mistaken for semantic tags.
    """
    global _registry_cache
    if _registry_cache is None:
        try:
            raw = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            _registry_cache = {int(key): str(value) for key, value in raw.items()}
        except Exception:
            _registry_cache = {}
    return _registry_cache


def tag_name(tag_id: int) -> str | None:
    return tag_registry().get(int(tag_id))


def _read_style_tgis(data: bytes, pos: int, count: int):
    if count < 0 or count > 255:
        return None
    styles = []
    for _ in range(count):
        if pos + 16 > len(data):
            return None
        instance = struct.unpack_from("<Q", data, pos)[0]
        type_id, group_id = struct.unpack_from("<II", data, pos + 8)
        styles.append((type_id, group_id, instance))
        pos += 16
    return styles, pos


def _parse_modern_cobj(data: bytes) -> CobjMetadata | None:
    """COBJ v0x1A-style resource: outer version then CatalogCommon."""
    try:
        if len(data) < 37:
            return None
        pos = 4
        common_version = struct.unpack_from("<I", data, pos)[0]; pos += 4
        # Modern catalog common blocks observed in current game data are v10+.
        if common_version < 10 or common_version > 64:
            return None
        name_hash = struct.unpack_from("<I", data, pos)[0]; pos += 4
        pos += 4  # description hash
        pos += 4  # price
        pos += 8  # thumbnail hash
        pos += 4  # dev category flags
        style_data = _read_style_tgis(data, pos + 1, data[pos])
        if style_data is None:
            return None
        styles, pos = style_data
        # PackId (int16), PackFlags (byte), nine reserved bytes.
        if pos + 12 > len(data):
            return None
        pos += 12
        if pos + 4 > len(data):
            return None
        count = struct.unpack_from("<i", data, pos)[0]; pos += 4
        if count < 0 or count > 4096 or pos + count * 4 > len(data):
            return None
        tags = list(struct.unpack_from("<" + "I" * count, data, pos))
        return CobjMetadata(common_version, name_hash, tags, styles)
    except (struct.error, ValueError, IndexError):
        return None


def _parse_legacy_cobj(data: bytes) -> CobjMetadata | None:
    """Pre-CommonBlock-v10 COBJ layout used by older Stuff Packs such as SP01.

    These files start directly with the COBJ resource version, then catalog
    version/name/description/price and three legacy uint32 fields. Their tag
    array is uint16, rather than the modern uint32 CatalogTagList.
    """
    try:
        if len(data) < 41:
            return None
        pos = 0
        _resource_version = struct.unpack_from("<I", data, pos)[0]; pos += 4
        common_version = struct.unpack_from("<I", data, pos)[0]; pos += 4
        if common_version < 1 or common_version > 64:
            return None
        name_hash = struct.unpack_from("<I", data, pos)[0]; pos += 4
        pos += 4  # description hash
        pos += 4  # price
        pos += 4  # unknown1
        pos += 4  # unknown2
        pos += 4  # dev category/unknown3
        style_data = _read_style_tgis(data, pos + 1, data[pos])
        if style_data is None:
            return None
        styles, pos = style_data
        # Legacy unknown4 (uint16), then uint32 number of uint16 tags.
        if pos + 6 > len(data):
            return None
        pos += 2
        count = struct.unpack_from("<i", data, pos)[0]; pos += 4
        if count < 0 or count > 4096 or pos + count * 2 > len(data):
            return None
        tags = list(struct.unpack_from("<" + "H" * count, data, pos))
        return CobjMetadata(common_version, name_hash, tags, styles)
    except (struct.error, ValueError, IndexError):
        return None


def parse_cobj_metadata(data: bytes) -> CobjMetadata | None:
    """Parse modern or legacy COBJ common blocks through their tag arrays."""
    # Try the current v0x1A+ COBJ first, then early COBJ layouts. SP01 uses
    # the latter; treating its first two uint32s as a modern CommonBlock caused
    # all 147 COBJ resources to be silently skipped.
    return _parse_modern_cobj(data) or _parse_legacy_cobj(data)


def merge_tag_ids(groups) -> list[int]:
    return sorted({int(tag) for group in groups for tag in (group or [])})


def merge_style_tgis(groups) -> list[tuple[int, int, int]]:
    return sorted({tuple(style) for group in groups for style in (group or [])})


def _camel_to_snake(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return value.replace("-", "_").lower()


def ai_variant_color_tags(tag_ids) -> list[str]:
    """Extract COBJ Color_* labels for one individual catalog swatch."""
    result = []
    for tag_id in tag_ids or []:
        name = tag_name(tag_id)
        if not name or not name.startswith("Color_"):
            continue
        value = _camel_to_snake(name[len("Color_"):])
        if value and value not in result:
            result.append(value)
    return result


def ai_semantic_tags(tag_ids) -> list[str]:
    """Return category/type tags; discard functions and colour/filter noise.

    ``Func_*`` and newer ``Funk_*`` tags describe interactions (sleep,
    hide-and-seek, etc.) and are intentionally ignored. Colour tags are also
    excluded here because swatch colour data is tracked separately. Buy/Build
    prefixes are removed so the AI receives ``bed`` and ``bed_double`` rather
    than opaque internal values such as ``BuyCatSS_BedDouble``.
    """
    selected = []
    buy_prefixes = ("BuyCatSS_", "BuyCatPA_", "BuyCatLD_", "BuyCatEE_", "BuyCatMAG_", "BuyCat_")
    for tag_id in tag_ids or []:
        name = tag_name(tag_id)
        if not name:
            continue
        if name.startswith(("Func_", "Funk_", "Color_", "Interaction_", "Trait_")):
            continue
        if name.startswith("BuyCat") and name != "BuyCat_Shareable":
            suffix = next((name[len(prefix):] for prefix in buy_prefixes if name.startswith(prefix)), name)
            value = _camel_to_snake(suffix)
        elif name.startswith(("Style_", "Build_Style")):
            value = _camel_to_snake(name)
        elif name.startswith("Build_") and name != "Build_Buy_World_Objects":
            # Doors, windows, arches, fences and roof elements are placement
            # categories, not gameplay functions. They are useful to an AI
            # that needs to compose an actual room/building.
            value = _camel_to_snake(name[len("Build_"):])
        else:
            continue
        if value and value not in selected:
            selected.append(value)
    return selected


def product_style_keys(product_styles) -> list[str]:
    """Canonical TGI strings for non-empty COBJ ProductStyle references."""
    result = []
    for type_id, group_id, instance in product_styles or []:
        if int(type_id) != _PRODUCT_STYLE_TYPE or int(instance) == 0:
            continue
        key = f"{int(type_id):08X}_{int(group_id):08X}_{int(instance):016X}"
        if key not in result:
            result.append(key)
    return result


def product_style_tag_for_key(key: str) -> str | None:
    """Return a known normalized style name for a canonical ProductStyle TGI."""
    try:
        type_hex, _group_hex, instance_hex = key.split("_", 2)
        if int(type_hex, 16) != _PRODUCT_STYLE_TYPE:
            return None
        return _PRODUCT_STYLE_NAMES.get(int(instance_hex, 16))
    except (ValueError, AttributeError):
        return None


def ai_product_style_tags(product_styles) -> list[str]:
    """Translate known COBJ ProductStyle TGIs into compact style tags."""
    result = []
    for key in product_style_keys(product_styles):
        name = product_style_tag_for_key(key)
        if name and name not in result:
            result.append(name)
    return result
