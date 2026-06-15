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
        return _fix_dst_fourcc(data)
    idx = data.find(DDS_MAGIC)
    if 0 < idx < 64:
        return _fix_dst_fourcc(data[idx:])
    return None


# Sims 4 uses custom FourCCs DST1/DST3/DST5 which are byte-compatible with the
# standard DXT1/DXT3/DXT5 block formats — only the FourCC tag differs. We patch
# the tag so the standard DDS decoder (Pillow) accepts them.
_DST_MAP = {b"DST1": b"DXT1", b"DST3": b"DXT3", b"DST5": b"DXT5"}


def _fix_dst_fourcc(data: bytes) -> bytes:
    if len(data) < 88:
        return data
    fourcc = data[84:88]
    if fourcc in _DST_MAP:
        data = bytearray(data)
        data[84:88] = _DST_MAP[fourcc]
        return bytes(data)
    return data


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
        return "decoded DDS -> PNG (builtin)"
    except Exception as e:
        dds_path = out_path.rsplit(".", 1)[0] + ".dds"
        with open(dds_path, "wb") as f:
            f.write(dds)
        return f"DDS saved (decode failed: {e}); open with texconv/GIMP"
