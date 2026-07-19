"""Discover individual objects inside a Sims 4 .package file.

Sims 4 packages that contain multiple furniture items store an OBJD (Object
Definition) or COBJ (Catalog Object) entry for each object.  These entries
hold the object's internal name and TGI references to the MODL/MLOD resources
that make up its 3D model.

This module scans those catalog entries, extracts the name + model TGI keys,
and returns a list of ObjectInfo records that the extractor can use to split
a multi-object package into per-object output folders.

Format notes
------------
OBJD  (0x6C3C6A89):  TGI reference block near the end of the resource with
  format:  uint32 count  +  count × (uint32 type, uint32 group, uint64 instance).
  Inline object name is stored earlier in the resource.

CATALOG (0xC0DB5AE7):  TGI references are embedded inline (not a single
  block at the end).  Each TGI entry is 16 bytes in the order:
  instance_hi(4) instance_lo(4) type(4) group(4).
  The object name is a length-prefixed string near the start of the resource.

COBJ  (0x319E4F1D):  Does NOT store model TGI references directly in a
  discoverable block.  Instead, each COBJ has a companion CATALOG entry
  (same instance ID) that does contain the model TGI.  We match them up.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .dbpf import DBPF
from . import resource_types as rt

# ---------------------------------------------------------------------------
# Known catalog / object-definition type IDs
# ---------------------------------------------------------------------------
OBJD = rt.OBJD       # 0x6C3C6A89  Object Definition
COBJ = rt.COBJ       # 0x319E4F1D  Catalog Object
CATALOG = 0xC0DB5AE7  # Catalog resource (contains names + TGI refs)

# Type IDs we already know are NOT catalog entries – skip them during the
# discovery scan to avoid wasting time on textures, meshes, etc.
KNOWN_NON_CATALOG = {
    rt.GEOM, rt.MODL, rt.MLOD,
    *rt.IMAGE_TYPES,
    0x220557DA,  # STBL
    0x01D0E723,  # VRTF
    0x01D0E6FB,  # VBUF
    0x01D0E70F,  # IBUF
    0x01D0E75D,  # MATD
    0x03B4C61D,  # LITE
    0x4F726BBE,  # FTPT
    0x545AC67A,  # DATA
    0x3C1AF1F2,  # PNG thumb
    0x5B282D45,  # PNG thumb (alt)
}

# Sims 4 type IDs that are typical model / texture references found inside
# a catalog TGI block.  Used to validate candidate TGI blocks.
_MODEL_TYPES = {rt.MODL, rt.MLOD}


@dataclass
class ObjectInfo:
    """One discovered object inside a package."""
    name: str                       # internal name (e.g. "bedExam_EP01GEN_set1")
    stbl_name: str = ""             # display name from STBL (may be empty)
    model_tgis: list[tuple[int, int, int]] = field(default_factory=list)
    source_type: int = 0            # type_id of the source entry
    source_instance: int = 0        # instance of the source entry


# ---------------------------------------------------------------------------
# CATALOG parser
# ---------------------------------------------------------------------------

def _parse_catalog_tgis(data: bytes) -> list[tuple[int, int, int]]:
    """Extract MODL/MLOD TGI references from a CATALOG resource.

    CATALOG entries store TGI references inline in the order:
    instance_hi(4) instance_lo(4) type(4) group(4) — 16 bytes each.

    We search for known model type IDs (MODL, MLOD) and extract the
    surrounding 16-byte TGI entry.
    """
    modl_bytes = struct.pack('<I', rt.MODL)
    mlod_bytes = struct.pack('<I', rt.MLOD)
    results = []

    for model_bytes in (modl_bytes, mlod_bytes):
        pos = 0
        while True:
            idx = data.find(model_bytes, pos)
            if idx < 0:
                break
            # The type field is at offset +8 within the 16-byte TGI entry
            # TGI layout: instance_hi(4) instance_lo(4) type(4) group(4)
            # So the entry starts at idx - 8
            entry_start = idx - 8
            if entry_start < 0:
                pos = idx + 1
                continue

            inst_hi = struct.unpack_from('<I', data, entry_start)[0]
            inst_lo = struct.unpack_from('<I', data, entry_start + 4)[0]
            type_id = struct.unpack_from('<I', data, entry_start + 8)[0]
            group_id = struct.unpack_from('<I', data, entry_start + 12)[0]

            # Sanity check: type should be MODL or MLOD
            if type_id in _MODEL_TYPES:
                instance = (inst_hi << 32) | inst_lo
                tgi = (type_id, group_id, instance)
                if tgi not in results:
                    results.append(tgi)

            pos = idx + 4

    return results


def _parse_catalog_name(data: bytes) -> str:
    """Extract the object name from a CATALOG resource.

    CATALOG entries store two length-prefixed strings near the start:
    1. A long package/swatch name (e.g. "Alex_sitDiningQA_..._set1")
    2. A short internal object name (e.g. "object_sitDiningQA_01T")

    We prefer the short object name (second string) as it's cleaner for
    folder naming. Falls back to the first string if needed.
    """
    # Find all length-prefixed strings (uint32 prefix)
    found_strings: list[tuple[int, str]] = []
    pos = 0
    while pos < min(200, len(data) - 4):
        str_len = struct.unpack_from('<I', data, pos)[0]
        if 3 <= str_len <= 256:
            str_start = pos + 4
            str_end = str_start + str_len
            if str_end <= len(data):
                try:
                    text = data[str_start:str_end].decode('ascii')
                    if all(c.isalnum() or c in '_-.' for c in text) and '_' in text:
                        found_strings.append((pos, text))
                        pos = str_end
                        continue
                except (UnicodeDecodeError, ValueError):
                    pass
        pos += 1

    # Prefer the second string (short internal object name)
    if len(found_strings) >= 2:
        return found_strings[1][1]
    if len(found_strings) >= 1:
        return found_strings[0][1]

    # Fallback: scan for readable strings
    in_string = False
    start = 0
    for i in range(min(200, len(data))):
        b = data[i]
        if 32 <= b < 127:
            if not in_string:
                in_string = True
                start = i
        else:
            if in_string:
                length = i - start
                if length >= 5:
                    try:
                        text = data[start:i].decode('ascii')
                        if '_' in text and all(c.isalnum() or c in '_-.' for c in text):
                            return text
                    except (UnicodeDecodeError, ValueError):
                        pass
                in_string = False
    return ""


# ---------------------------------------------------------------------------
# OBJD parser
# ---------------------------------------------------------------------------

def _extract_tgi_block(data: bytes) -> list[tuple[int, int, int]]:
    """Try to locate and parse a TGI reference block in OBJD data.

    The TGI block is a uint32 *count* followed by *count* entries of
    (uint32 type, uint32 group, uint64 instance).  We scan backwards from
    the end of *data* looking for a plausible count whose entries contain
    at least one known model type ID.
    """
    n = len(data)
    if n < 20:
        return []

    best: list[tuple[int, int, int]] = []

    # Scan backwards; the TGI block is usually within the last ~200 bytes.
    start = max(0, n - 400)
    for pos in range(n - 4, start, -1):
        count = struct.unpack_from('<I', data, pos)[0]
        if count == 0 or count > 64:
            continue
        entry_start = pos + 4
        if entry_start + count * 16 > n:
            continue

        entries: list[tuple[int, int, int]] = []
        has_model = False
        for i in range(count):
            off = entry_start + i * 16
            type_id = struct.unpack_from('<I', data, off)[0]
            group_id = struct.unpack_from('<I', data, off + 4)[0]
            instance = struct.unpack_from('<Q', data, off + 8)[0]
            entries.append((type_id, group_id, instance))

            if type_id in _MODEL_TYPES:
                has_model = True

        if has_model:
            model_entries = [e for e in entries if e[0] in _MODEL_TYPES]
            if model_entries:
                best = entries
                break

    return best


def _extract_model_tgis_objd(data: bytes) -> list[tuple[int, int, int]]:
    """Return only the MODL/MLOD TGI references from an OBJD entry."""
    tgis = _extract_tgi_block(data)
    return [(t, g, i) for t, g, i in tgis if t in _MODEL_TYPES]


def _extract_inline_name(data: bytes) -> str:
    """Try to find an inline object name string in OBJD data."""
    # Pattern 1: length-prefixed string (uint32 length + ASCII chars)
    for pos in range(4, min(512, len(data) - 4)):
        str_len = struct.unpack_from('<I', data, pos)[0]
        if str_len == 0 or str_len > 128:
            continue
        end = pos + 4 + str_len
        if end > len(data):
            continue
        candidate = data[pos + 4:end]
        try:
            text = candidate.decode('ascii')
        except (UnicodeDecodeError, ValueError):
            continue
        if all(c.isalnum() or c in '_-' for c in text) and len(text) >= 3:
            if '_' in text or text[0].isalpha():
                return text

    # Pattern 2: null-terminated string
    in_string = False
    start = 0
    for i in range(min(512, len(data))):
        b = data[i]
        if 32 <= b < 127:
            if not in_string:
                in_string = True
                start = i
        else:
            if in_string:
                length = i - start
                if length >= 5:
                    try:
                        text = data[start:i].decode('ascii')
                        if '_' in text and all(c.isalnum() or c in '_-' for c in text):
                            return text
                    except (UnicodeDecodeError, ValueError):
                        pass
                in_string = False

    return ""


# ---------------------------------------------------------------------------
# STBL name lookup
# ---------------------------------------------------------------------------

def _lookup_stbl_name(pkg: DBPF, name_hash: int) -> str:
    """Look up a name hash in the package's STBL resources."""
    stbl_entries = pkg.find(0x220557DA)
    for e in stbl_entries:
        try:
            data = pkg.read_resource(e)
        except Exception:
            continue
        if len(data) < 21 or data[:4] != b'STBL':
            continue
        try:
            string_count = struct.unpack_from('<I', data, 16)[0]
            pos = 21
            for _ in range(string_count):
                if pos + 12 > len(data):
                    break
                key = struct.unpack_from('<I', data, pos)[0]
                pos += 4
                pos += 4  # skip unknown bytes
                length = struct.unpack_from('<I', data, pos)[0]
                pos += 4
                if pos + length > len(data):
                    break
                string_bytes = data[pos:pos + length]
                pos += length
                if key == (name_hash & 0xFFFFFFFF):
                    try:
                        return string_bytes.decode('utf-8')
                    except Exception:
                        try:
                            return string_bytes.decode('latin1', errors='replace')
                        except Exception:
                            pass
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Main discovery function
# ---------------------------------------------------------------------------

def discover_objects(pkg: DBPF) -> list[ObjectInfo]:
    """Discover individual objects inside a Sims 4 package.

    Scans CATALOG, OBJD, and COBJ entries to build a list of ObjectInfo
    records.  Each record contains the object's name and the TGI keys of
    its MODL/MLOD resources.

    Priority order for discovery:
    1. CATALOG entries (contain name + model TGI, most reliable)
    2. OBJD entries (contain name + model TGI block)
    3. COBJ entries (may need CATALOG/STBL lookup)

    Returns a list of ObjectInfo, deduplicated by model TGI.
    """
    objects: list[ObjectInfo] = []

    # Build a set of MODL TGI keys present in this package for validation
    modl_tgis_in_pkg: set[tuple[int, int, int]] = set()
    for e in pkg.find(rt.MODL):
        modl_tgis_in_pkg.add((e.type_id, e.group_id, e.instance))
    mlod_tgis_in_pkg: set[tuple[int, int, int]] = set()
    for e in pkg.find(rt.MLOD):
        mlod_tgis_in_pkg.add((e.type_id, e.group_id, e.instance))
    model_tgis_in_pkg = modl_tgis_in_pkg | mlod_tgis_in_pkg

    # Also build a set of (group, instance) for relaxed matching
    modl_gi_set = {(e.group_id, e.instance) for e in pkg.find(rt.MODL)}
    mlod_gi_set = {(e.group_id, e.instance) for e in pkg.find(rt.MLOD)}
    model_gi_set = modl_gi_set | mlod_gi_set

    # ------------------------------------------------------------------
    # Phase 1: CATALOG entries (most reliable source)
    # ------------------------------------------------------------------
    catalog_entries = pkg.find(CATALOG)
    for e in catalog_entries:
        try:
            data = pkg.read_resource(e)
        except Exception:
            continue

        model_tgis = _parse_catalog_tgis(data)
        if not model_tgis:
            continue

        # Validate: at least one model TGI should exist in the package
        valid_model_tgis = [t for t in model_tgis if t in model_tgis_in_pkg]
        if not valid_model_tgis:
            # Try relaxing: check (group, instance) match
            valid_model_tgis = [t for t in model_tgis
                                if (t[1], t[2]) in model_gi_set]
        if not valid_model_tgis:
            continue

        name = _parse_catalog_name(data)
        if not name:
            name = f"object_{e.instance:016X}"

        objects.append(ObjectInfo(
            name=name,
            stbl_name="",
            model_tgis=valid_model_tgis,
            source_type=e.type_id,
            source_instance=e.instance,
        ))

    # If CATALOG found objects, use those (they're the most reliable)
    if objects:
        return _deduplicate_objects(objects)

    # ------------------------------------------------------------------
    # Phase 2: OBJD entries
    # ------------------------------------------------------------------
    objd_entries = pkg.find(OBJD)
    for e in objd_entries:
        try:
            data = pkg.read_resource(e)
        except Exception:
            continue

        model_tgis = _extract_model_tgis_objd(data)
        if not model_tgis:
            continue

        # Validate
        valid_model_tgis = [t for t in model_tgis if t in model_tgis_in_pkg]
        if not valid_model_tgis:
            valid_model_tgis = [t for t in model_tgis
                                if (t[1], t[2]) in model_gi_set]
        if not valid_model_tgis:
            continue

        name = _extract_inline_name(data)
        stbl_name = ""
        if not name:
            stbl_name = _lookup_stbl_name(pkg, e.instance)
            name = stbl_name or f"object_{e.instance:016X}"

        objects.append(ObjectInfo(
            name=name,
            stbl_name=stbl_name,
            model_tgis=valid_model_tgis,
            source_type=e.type_id,
            source_instance=e.instance,
        ))

    if objects:
        return _deduplicate_objects(objects)

    # ------------------------------------------------------------------
    # Phase 3: COBJ entries (fallback, less reliable)
    # ------------------------------------------------------------------
    cobj_entries = pkg.find(COBJ)
    for e in cobj_entries:
        try:
            data = pkg.read_resource(e)
        except Exception:
            continue

        # Try to extract model TGI from COBJ data using TGI block scan
        model_tgis = _extract_model_tgis_objd(data)
        if not model_tgis:
            continue

        valid_model_tgis = [t for t in model_tgis if t in model_tgis_in_pkg]
        if not valid_model_tgis:
            valid_model_tgis = [t for t in model_tgis
                                if (t[1], t[2]) in model_gi_set]
        if not valid_model_tgis:
            continue

        name = _extract_inline_name(data)
        stbl_name = ""
        if not name:
            stbl_name = _lookup_stbl_name(pkg, e.instance)
            name = stbl_name or f"object_{e.instance:016X}"

        objects.append(ObjectInfo(
            name=name,
            stbl_name=stbl_name,
            model_tgis=valid_model_tgis,
            source_type=e.type_id,
            source_instance=e.instance,
        ))

    return _deduplicate_objects(objects)


def _deduplicate_objects(objects: list[ObjectInfo]) -> list[ObjectInfo]:
    """Deduplicate objects: multiple swatch entries may reference the same MODL.

    Group by the primary model TGI (first in the list) and keep the
    first name found for each unique model.
    """
    seen_modl: dict[tuple[int, int, int], ObjectInfo] = {}
    deduped: list[ObjectInfo] = []
    for obj in objects:
        primary_key = obj.model_tgis[0] if obj.model_tgis else None
        if primary_key and primary_key in seen_modl:
            continue
        if primary_key:
            seen_modl[primary_key] = obj
        deduped.append(obj)
    return deduped


def build_modl_to_object_map(objects: list[ObjectInfo]) -> dict[tuple[int, int, int], ObjectInfo]:
    """Build a mapping from MODL TGI → ObjectInfo.

    Multiple MODL TGIs may map to the same ObjectInfo (e.g. different
    LOD levels of the same object).
    """
    mapping: dict[tuple[int, int, int], ObjectInfo] = {}
    for obj in objects:
        for tgi in obj.model_tgis:
            if tgi not in mapping:
                mapping[tgi] = obj
    return mapping
