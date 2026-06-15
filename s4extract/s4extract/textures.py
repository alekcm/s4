"""Texture handling for The Sims 4: RLE/DST -> DDS -> PNG.

Sims 4 stores textures as:
  * plain DDS (DXT1/DXT3/DXT5/uncompressed)            -> decode directly
  * "RLES"/"RLE2" packed DDS (a custom run-length form) -> unpack to DDS first

We decode DXT (BC1/BC2/BC3) ourselves so we don't need a native DDS plugin.
numpy is used when available (fast path); without it we fall back to a pure
Python decoder. Pillow is used to *write* the PNG; without Pillow we save the
decoded RGBA as a raw .dds/.tga-less blob and tell the user.

Importing this module never fails even if numpy / Pillow are missing.
"""
from __future__ import annotations

import struct

try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover - environment without numpy
    np = None
    HAVE_NUMPY = False

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:  # pragma: no cover - environment without Pillow
    Image = None
    HAVE_PIL = False

# DDS pixel format flags
DDPF_FOURCC = 0x4

DDS_MAGIC = b"DDS "


def have_image_support() -> bool:
    """True if we can actually write PNGs (Pillow present)."""
    return HAVE_PIL


def missing_deps() -> list[str]:
    miss = []
    if not HAVE_PIL:
        miss.append("Pillow")
    if not HAVE_NUMPY:
        miss.append("numpy")
    return miss


def _looks_like_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def _looks_like_jpg(data: bytes) -> bool:
    return data[:2] == b"\xff\xd8"


def _looks_like_dds(data: bytes) -> bool:
    return data[:4] == DDS_MAGIC


# ---------------------------------------------------------------------------
# RLE / DST unpacking  (Sims 4 "RLES" texture container -> raw DDS)
# ---------------------------------------------------------------------------
def unpack_rle_to_dds(data: bytes) -> bytes | None:
    """Best-effort unpack of S4 RLE2/RLES container into a standard DDS blob."""
    if _looks_like_dds(data):
        return _unshuffle_dst_to_dxt(data)
    idx = data.find(DDS_MAGIC)
    if 0 < idx < 64:
        return _unshuffle_dst_to_dxt(data[idx:])
    return None


# Sims 4 _IMG resources often use custom FourCCs DST1/DST5. They are *not*
# just DXT1/DXT5 with a different tag: the compressed block stream is
# "shuffled" by component (S4PI calls this DST). A normal DDS decoder will show
# a partly-correct image followed by colourful garbage/noise unless we
# unshuffle the stream first. The logic below is a Python port of the S4PI /
# Sims4Toolkit DstResource unshuffle routine.
_DST_TO_DXT = {b"DST1": b"DXT1", b"DST3": b"DXT3", b"DST5": b"DXT5"}


def _unshuffle_dst_to_dxt(data: bytes) -> bytes:
    """Convert Sims 4 DST1/DST5 DDS data into standard DXT1/DXT5 DDS data."""
    if len(data) < 128:
        return data

    fourcc = data[84:88]
    if fourcc not in _DST_TO_DXT:
        return data

    # DST3 exists in headers but is uncommon and not supported by the original
    # S4TK implementation either. Tagging it as DXT3 would produce broken output,
    # so leave it untouched and let the decoder/report handle it.
    if fourcc == b"DST3":
        return data

    header = bytearray(data[:128])
    header[84:88] = _DST_TO_DXT[fourcc]
    src = data[128:]
    size = len(src)
    out = bytearray()

    if fourcc == b"DST1":
        # Shuffled layout: [all first 4 bytes of each 8-byte DXT1 block]
        #                  [all last  4 bytes of each 8-byte DXT1 block]
        half = size >> 1
        count = half // 4
        out = bytearray(size)
        w = 0
        o0, o1 = 0, half
        for _ in range(count):
            out[w:w + 4] = src[o0:o0 + 4]
            out[w + 4:w + 8] = src[o1:o1 + 4]
            w += 8
            o0 += 4
            o1 += 4
        if w < size:
            out[w:] = src[w:]

    elif fourcc == b"DST5":
        # Standard DXT5 block is 16 bytes:
        #   alpha endpoints (2), alpha indices (6), color endpoints (4), color indices (4)
        # Sims DST5 stores component planes in this order:
        #   endpoints(2) for all blocks, color endpoints(4), alpha indices(6), color indices(4)
        off0 = 0
        off2 = off0 + (size >> 3)          # 2 bytes per 16-byte block
        off1 = off2 + (size >> 2)          # 4 bytes per block
        off3 = off1 + ((6 * size) >> 4)    # 6 bytes per block
        count = (off2 - off0) // 2
        out = bytearray(size)
        w = 0
        p0, p1, p2, p3 = off0, off1, off2, off3
        for _ in range(count):
            out[w:w + 2] = src[p0:p0 + 2]
            out[w + 2:w + 8] = src[p1:p1 + 6]
            out[w + 8:w + 12] = src[p2:p2 + 4]
            out[w + 12:w + 16] = src[p3:p3 + 4]
            w += 16
            p0 += 2
            p1 += 6
            p2 += 4
            p3 += 4
        if w < size:
            out[w:] = src[w:]

    return bytes(header) + bytes(out)


# ---------------------------------------------------------------------------
# DDS header parsing
# ---------------------------------------------------------------------------
def _parse_dds_header(data: bytes):
    if not _looks_like_dds(data):
        raise ValueError("not a DDS")
    height = struct.unpack_from("<I", data, 12)[0]
    width = struct.unpack_from("<I", data, 16)[0]
    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    rgb_bitcount = struct.unpack_from("<I", data, 88)[0]
    rmask, gmask, bmask, amask = struct.unpack_from("<IIII", data, 92)
    return {
        "width": width,
        "height": height,
        "pf_flags": pf_flags,
        "fourcc": fourcc,
        "bpp": rgb_bitcount,
        "masks": (rmask, gmask, bmask, amask),
        "data_offset": 128,
    }


# ---------------------------------------------------------------------------
# DXT (BC1/BC2/BC3) decoder -> flat RGBA bytearray (pure Python, no numpy)
# ---------------------------------------------------------------------------
def _unpack_565(c):
    r = ((c >> 11) & 0x1F)
    g = ((c >> 5) & 0x3F)
    b = (c & 0x1F)
    return (
        (r << 3) | (r >> 2),
        (g << 2) | (g >> 4),
        (b << 3) | (b >> 2),
    )


def _decode_dxt_flat(data: bytes, width: int, height: int, fmt: str) -> bytearray:
    """Decode DXT into a flat RGBA bytearray of length width*height*4."""
    out = bytearray(width * height * 4)
    # default alpha 255
    for i in range(3, len(out), 4):
        out[i] = 255

    bw = (width + 3) // 4
    bh = (height + 3) // 4
    pos = 0
    block_size = 8 if fmt == "DXT1" else 16

    for by in range(bh):
        for bx in range(bw):
            block = data[pos:pos + block_size]
            pos += block_size
            if len(block) < block_size:
                break

            if fmt == "DXT1":
                color_block = block
                alpha = None
            elif fmt == "DXT3":
                alpha = ("explicit", block[:8])
                color_block = block[8:]
            else:  # DXT5
                alpha = ("interp", block[:8])
                color_block = block[8:]

            c0, c1 = struct.unpack_from("<HH", color_block, 0)
            bits = struct.unpack_from("<I", color_block, 4)[0]
            col = [_unpack_565(c0), _unpack_565(c1)]
            if c0 > c1 or fmt != "DXT1":
                col.append(tuple((2 * col[0][i] + col[1][i]) // 3 for i in range(3)))
                col.append(tuple((col[0][i] + 2 * col[1][i]) // 3 for i in range(3)))
                punch = [255, 255, 255, 255]
            else:
                col.append(tuple((col[0][i] + col[1][i]) // 2 for i in range(3)))
                col.append((0, 0, 0))
                punch = [255, 255, 255, 0]

            adata = None
            if alpha and alpha[0] == "explicit":
                ab = alpha[1]
                avals = []
                for i in range(8):
                    byte = ab[i]
                    avals.append((byte & 0x0F) * 17)
                    avals.append((byte >> 4) * 17)
                adata = avals
            elif alpha and alpha[0] == "interp":
                ab = alpha[1]
                a0, a1 = ab[0], ab[1]
                alut = [a0, a1]
                if a0 > a1:
                    for i in range(1, 7):
                        alut.append(((7 - i) * a0 + i * a1) // 7)
                else:
                    for i in range(1, 5):
                        alut.append(((5 - i) * a0 + i * a1) // 5)
                    alut.extend((0, 255))
                abits = int.from_bytes(ab[2:8], "little")
                adata = [alut[(abits >> (3 * i)) & 0x7] for i in range(16)]

            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x >= width or y >= height:
                        continue
                    idx = (bits >> (2 * (py * 4 + px))) & 0x3
                    r, g, b = col[idx][:3]
                    o = (y * width + x) * 4
                    out[o] = r
                    out[o + 1] = g
                    out[o + 2] = b
                    if adata is not None:
                        out[o + 3] = adata[py * 4 + px]
                    elif fmt == "DXT1":
                        out[o + 3] = punch[idx]
    return out


def dds_to_rgba_flat(data: bytes):
    """Return (width, height, flat_rgba_bytes)."""
    hdr = _parse_dds_header(data)
    w, h = hdr["width"], hdr["height"]
    fourcc = hdr["fourcc"]
    pixels = data[hdr["data_offset"]:]

    if hdr["pf_flags"] & DDPF_FOURCC:
        if fourcc == b"DXT1":
            return w, h, _decode_dxt_flat(pixels, w, h, "DXT1")
        if fourcc == b"DXT3":
            return w, h, _decode_dxt_flat(pixels, w, h, "DXT3")
        if fourcc == b"DXT5":
            return w, h, _decode_dxt_flat(pixels, w, h, "DXT5")
        raise ValueError(f"Unsupported FourCC {fourcc!r}")
    else:
        # Uncompressed 32bpp; handle BGRA vs RGBA
        raw = bytearray(pixels[: w * h * 4])
        if hdr["masks"][0] == 0x00FF0000:  # BGRA -> RGBA
            for o in range(0, len(raw), 4):
                raw[o], raw[o + 2] = raw[o + 2], raw[o]
        return w, h, raw


# ---------------------------------------------------------------------------
# Public: convert any S4 texture resource bytes to a PNG file.
# ---------------------------------------------------------------------------
def save_as_png(data: bytes, out_path: str) -> str:
    """Convert texture resource bytes to a PNG. Returns a status string."""

    # Remember original Sims 4 FourCC so the log can prove that the fixed path
    # was used. Without this, old and fixed builds both only said
    # "decoded DDS -> PNG", which made it hard to notice stale local copies.
    original_fourcc = None
    if _looks_like_dds(data) and len(data) >= 88:
        original_fourcc = data[84:88]
    else:
        idx0 = data.find(DDS_MAGIC)
        if 0 < idx0 < 64 and len(data) >= idx0 + 88:
            original_fourcc = data[idx0 + 84:idx0 + 88]

    if _looks_like_png(data):
        with open(out_path, "wb") as f:
            f.write(data)
        return "copied (already PNG)"

    if _looks_like_jpg(data):
        if HAVE_PIL:
            import io
            Image.open(io.BytesIO(data)).save(out_path)
            return "converted from JPG"
        jpg_path = out_path.rsplit(".", 1)[0] + ".jpg"
        with open(jpg_path, "wb") as f:
            f.write(data)
        return f"JPG saved to {jpg_path} (install Pillow for PNG)"

    dds = unpack_rle_to_dds(data)
    if dds is None:
        raw_path = out_path.rsplit(".", 1)[0] + ".bin"
        with open(raw_path, "wb") as f:
            f.write(data)
        return f"unknown format, raw saved to {raw_path}"

    # If we can't write a PNG at all, just save the DDS for external tools.
    if not HAVE_PIL:
        dds_path = out_path.rsplit(".", 1)[0] + ".dds"
        with open(dds_path, "wb") as f:
            f.write(dds)
        return f"DDS saved to {dds_path} (install Pillow to get PNG)"

    # 1) Primary path: let Pillow's DDS plugin decode it. Pillow 9+ supports
    #    DXT1/3/5, ATI1/ATI2 (BC4/BC5), and DX10 (BC4..BC7) — covers virtually
    #    all Sims 4 _IMG formats (DST1/DST5/ATI2/standard).
    try:
        import io
        img = Image.open(io.BytesIO(dds))
        img.load()
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")
        img.save(out_path)
        if original_fourcc in (b"DST1", b"DST5"):
            return f"unshuffled {original_fourcc.decode('ascii')} -> decoded DDS ({img.mode}) -> PNG via Pillow"
        return f"decoded DDS ({img.mode}) -> PNG via Pillow"
    except Exception as pillow_err:
        pass

    # 2) Fallback: our own DXT1/3/5 decoder.
    try:
        w, h, flat = dds_to_rgba_flat(dds)
        if HAVE_NUMPY:
            arr = np.frombuffer(bytes(flat), dtype=np.uint8).reshape(h, w, 4)
            Image.fromarray(arr, "RGBA").save(out_path)
        else:
            img = Image.frombytes("RGBA", (w, h), bytes(flat))
            img.save(out_path)
        if original_fourcc in (b"DST1", b"DST5"):
            return f"unshuffled {original_fourcc.decode('ascii')} -> decoded DDS -> PNG (builtin)"
        return "decoded DDS -> PNG (builtin)"
    except Exception as e:
        dds_path = out_path.rsplit(".", 1)[0] + ".dds"
        with open(dds_path, "wb") as f:
            f.write(dds)
        return f"DDS saved (decode failed: {e}); open with texconv/GIMP"
