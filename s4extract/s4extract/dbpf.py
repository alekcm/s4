"""DBPF 2.0 reader for The Sims 4 .package files.

Spec: Kuree/Sims4Tools wiki "DBPF Format" + Niotso wiki.

Header (96 bytes):
    char[4] magic = "DBPF"
    uint32  major = 2
    uint32  minor = 0
    byte[24] unknown
    uint32  index_entry_count
    byte[4]  unknown (low index entry count in <2.0)
    uint32  index_size (bytes)
    byte[12] unknown
    uint32  index_version (always 3 here, but we read 'index type flags' at index start)
    uint32  index_offset (absolute)
    byte[28] unknown

Index (DBPF 2.0): begins with a uint32 "index type" bit flag. The set bits
mark which of the 8 DWORD fields are CONSTANT (stored once in the index header)
versus stored per-entry.

The 8 logical fields per entry, in order:
    0 ResourceType
    1 ResourceGroup
    2 InstanceHi
    3 InstanceLo
    4 ChunkOffset (absolute)
    5 FileSize (low 31 bits) | compressed-flag-ish (high bit)
    6 MemSize (uncompressed)
    7 Compressed(low word) | Unknown(high word)   compressed == 0xFFFF/0x5A42
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import refpack
from .resource_types import type_name


@dataclass
class IndexEntry:
    type_id: int
    group_id: int
    instance_hi: int
    instance_lo: int
    offset: int
    file_size: int          # bytes on disk (compressed if compressed)
    mem_size: int           # uncompressed size
    compressed: bool

    @property
    def instance(self) -> int:
        return (self.instance_hi << 32) | self.instance_lo

    @property
    def tgi(self) -> str:
        return f"{self.type_id:08X}_{self.group_id:08X}_{self.instance:016X}"

    def describe(self) -> str:
        return f"{type_name(self.type_id)} {self.tgi} ({self.mem_size} bytes)"


class DBPF:
    MAGIC = b"DBPF"

    def __init__(self, data: bytes):
        self.data = data
        self.entries: list[IndexEntry] = []
        self._parse()

    @classmethod
    def from_file(cls, path: str) -> "DBPF":
        with open(path, "rb") as f:
            return cls(f.read())

    def _parse(self) -> None:
        d = self.data
        if d[:4] != self.MAGIC:
            raise ValueError("Not a DBPF package (bad magic)")
        major, minor = struct.unpack_from("<II", d, 4)
        if major != 2:
            raise ValueError(f"Unsupported DBPF major version {major} (only 2.x supported)")

        index_entry_count = struct.unpack_from("<I", d, 36)[0]
        index_size = struct.unpack_from("<I", d, 44)[0]
        index_offset = struct.unpack_from("<I", d, 64)[0]

        if index_entry_count == 0 or index_offset == 0:
            return

        self._read_index(index_offset, index_entry_count)

    def _read_index(self, offset: int, count: int) -> None:
        d = self.data
        pos = offset
        index_type = struct.unpack_from("<I", d, pos)[0]
        pos += 4

        # Header-constant values for bits that are set.
        const = {}
        for bit in range(8):
            if index_type & (1 << bit):
                const[bit] = struct.unpack_from("<I", d, pos)[0]
                pos += 4

        for _ in range(count):
            fields = []
            for bit in range(8):
                if bit in const:
                    fields.append(const[bit])
                else:
                    fields.append(struct.unpack_from("<I", d, pos)[0])
                    pos += 4

            type_id, group_id, inst_hi, inst_lo, chunk_off, file_size_raw, mem_size, comp_raw = fields
            file_size = file_size_raw & 0x7FFFFFFF
            compressed = (comp_raw & 0xFFFF) in (0xFFFF, 0x5A42)

            self.entries.append(IndexEntry(
                type_id=type_id,
                group_id=group_id,
                instance_hi=inst_hi,
                instance_lo=inst_lo,
                offset=chunk_off,
                file_size=file_size,
                mem_size=mem_size,
                compressed=compressed,
            ))

    def read_resource(self, entry: IndexEntry) -> bytes:
        raw = self.data[entry.offset:entry.offset + entry.file_size]
        if not entry.compressed:
            # Even if the flag says uncompressed, some files store zlib anyway.
            if _looks_zlib(raw):
                dec = _try_zlib(raw, entry.mem_size)
                if dec is not None:
                    return dec
            return raw
        return decompress_chunk(raw, entry.mem_size)

    def find(self, type_id: int) -> list[IndexEntry]:
        return [e for e in self.entries if e.type_id == type_id]


# ---------------------------------------------------------------------------
# Multi-format chunk decompression: zlib / raw-deflate / RefPack(QFS) / raw.
# Modern Sims 4 packages use zlib (header 0x78 0x9C / 0x78 0xDA / 0x78 0x01).
# Older ones use RefPack/QFS (xx FB). We auto-detect by signature.
# ---------------------------------------------------------------------------
import zlib as _zlib


def _looks_zlib(data: bytes) -> bool:
    if len(data) < 2:
        return False
    cmf, flg = data[0], data[1]
    # zlib: CMF low nibble == 8 (deflate) and (cmf*256+flg) % 31 == 0
    if (cmf & 0x0F) == 8 and ((cmf << 8) | flg) % 31 == 0:
        return True
    return False


def _looks_refpack(data: bytes) -> bool:
    # RefPack/QFS magic: second byte 0xFB (e.g. 0x10FB, 0x50FB, 0x80FB)
    if len(data) >= 2 and data[1] == 0xFB:
        return True
    if len(data) >= 6 and data[5] == 0xFB:
        return True
    return False


def _try_zlib(data: bytes, expected: int | None) -> bytes | None:
    # Standard zlib stream
    try:
        out = _zlib.decompress(data)
        if not expected or len(out) == expected or abs(len(out) - (expected or 0)) < 16:
            return out
        return out
    except Exception:
        pass
    # Raw DEFLATE (no zlib header)
    try:
        return _zlib.decompress(data, -15)
    except Exception:
        return None


def decompress_chunk(raw: bytes, expected_size: int | None = None) -> bytes:
    """Decompress a DBPF chunk using whatever scheme it actually uses."""
    if not raw:
        return raw

    # 1) zlib (most common in modern TS4)
    if _looks_zlib(raw):
        dec = _try_zlib(raw, expected_size)
        if dec is not None:
            return dec

    # 2) RefPack / QFS (older TS4)
    if _looks_refpack(raw):
        try:
            return refpack.decompress(raw, expected_size=expected_size)
        except refpack.RefPackError:
            pass

    # 3) try zlib anyway (some flags lie)
    dec = _try_zlib(raw, expected_size)
    if dec is not None:
        return dec

    # 4) try refpack anyway
    try:
        return refpack.decompress(raw, expected_size=expected_size)
    except Exception:
        pass

    # 5) give up — return raw bytes
    return raw
