"""RefPack / QFS decompression as used by DBPF (The Sims 4).

Reference: ModTheSims DBPF/Compression and SimsWiki.

The Sims 4 chunks use a 9-... actually a variable header. For DBPF 2.0 the
compressed blob carried in the package starts directly with the 2-byte
compression magic (0x10FB style, big-endian-ish) followed by a 3-byte
uncompressed size. We support both the classic 9-byte/5-byte preambles by
detecting the magic.
"""
from __future__ import annotations


class RefPackError(Exception):
    pass


def _find_magic(data: bytes) -> int:
    """Return offset where the QFS stream (magic byte 0x10/0x50/... 0xFB) starts."""
    # Common layouts:
    #   [magic(2)][unc_size(3 BE)] ...                       -> start at 0
    #   [comp_size(4)][magic(2)][unc_size(3 BE)] ...         -> start at 4
    for off in (0, 4, 5, 9):
        if off + 2 <= len(data) and data[off + 1] == 0xFB:
            return off
    # Fallback: scan a small window.
    for off in range(0, min(16, len(data) - 1)):
        if data[off + 1] == 0xFB:
            return off
    raise RefPackError("QFS magic (xx FB) not found")


def decompress(data: bytes, expected_size: int | None = None) -> bytes:
    """Decompress a RefPack/QFS stream. ``data`` is the raw chunk bytes."""
    start = _find_magic(data)
    i = start
    flags = data[i]
    # i+1 == 0xFB
    i += 2

    # uncompressed size: 3 bytes big-endian
    unc_size = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
    i += 3

    # If the "large" flag bit (0x80) is set, size field is 4 bytes — rare in S4.
    if flags & 0x01:  # has a leading compressed-size field already consumed by some tools
        pass

    out = bytearray(unc_size if unc_size else (expected_size or 0))
    out_pos = 0
    n = len(data)

    def ensure(size_needed: int):
        nonlocal out
        if size_needed > len(out):
            out.extend(b"\x00" * (size_needed - len(out)))

    while i < n:
        ctrl = data[i]
        i += 1

        if ctrl < 0x80:
            # 2-byte command
            b1 = data[i]; i += 1
            plain = ctrl & 0x03
            copy = ((ctrl & 0x1C) >> 2) + 3
            offset = ((ctrl & 0x60) << 3) + b1 + 1
        elif ctrl < 0xC0:
            # 3-byte command
            b1 = data[i]; b2 = data[i + 1]; i += 2
            plain = (b1 >> 6) & 0x03
            copy = (ctrl & 0x3F) + 4
            offset = ((b1 & 0x3F) << 8) + b2 + 1
        elif ctrl < 0xE0:
            # 4-byte command
            b1 = data[i]; b2 = data[i + 1]; b3 = data[i + 2]; i += 3
            plain = ctrl & 0x03
            copy = ((ctrl & 0x0C) << 6) + b3 + 5
            offset = ((ctrl & 0x10) << 12) + (b1 << 8) + b2 + 1
        else:
            # 1-byte command: only plain bytes (4..112)
            plain = ((ctrl & 0x1F) + 1) << 2
            copy = 0
            offset = 0

        # Copy literal plain bytes
        if plain:
            ensure(out_pos + plain)
            out[out_pos:out_pos + plain] = data[i:i + plain]
            out_pos += plain
            i += plain

        # Back-reference copy (byte-by-byte, may overlap)
        if copy:
            ensure(out_pos + copy)
            src = out_pos - offset
            if src < 0:
                raise RefPackError(f"bad back-reference offset {offset} at out_pos {out_pos}")
            for _ in range(copy):
                out[out_pos] = out[src]
                out_pos += 1
                src += 1

        if ctrl >= 0xFC:
            # terminal-ish; the >=0xE0 branch handles small tails. Stop if done.
            if unc_size and out_pos >= unc_size:
                break

    result = bytes(out[:unc_size]) if unc_size else bytes(out[:out_pos])
    if expected_size and len(result) != expected_size and unc_size == 0:
        result = result[:expected_size]
    return result
