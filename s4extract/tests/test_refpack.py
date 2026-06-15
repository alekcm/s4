"""Round-trip test for RefPack decompression using a tiny QFS compressor.

We implement a *minimal* QFS compressor (literal-only + simple matches) just
to validate that our decompressor reads the control-character grammar
correctly, then also test on patterned data with back-references.
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from s4extract import refpack


def qfs_compress_literal_only(data: bytes) -> bytes:
    """Encode data using only literal runs -> valid QFS stream.

    Uses the 1-byte literal command (0xE0..0xFB): plain = ((cc & 0x1F)+1)<<2,
    i.e. multiples of 4 from 4..112. Tail uses 0xFC..0xFF: plain = cc & 0x03.
    """
    out = bytearray()
    out.append(0x10)            # magic byte
    out.append(0xFB)            # 0xFB
    n = len(data)
    out += bytes([(n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])

    i = 0
    while n - i >= 4:
        chunk = min(112, (n - i) // 4 * 4)
        cc = 0xE0 + ((chunk >> 2) - 1)
        out.append(cc)
        out += data[i:i + chunk]
        i += chunk
    # tail
    rem = n - i
    out.append(0xFC + rem)
    out += data[i:i + rem]
    return bytes(out)


def test_literal_roundtrip():
    for payload in [b"", b"A", b"hello world!!", os.urandom(1000), b"x" * 500]:
        comp = qfs_compress_literal_only(payload)
        dec = refpack.decompress(comp, expected_size=len(payload))
        assert dec == payload, f"mismatch len {len(payload)}: got {len(dec)}"
    print("literal roundtrip OK")


def test_backreference():
    """Hand-build a stream that repeats a pattern via a 2-byte copy command."""
    # data: "ABCABC" -> literal "ABC", then copy 3 bytes from offset 3.
    out = bytearray([0x10, 0xFB, 0, 0, 6])
    # 2-byte command: ctrl<0x80. plain=3? max plain in 2-byte cmd is 3.
    # ctrl bits: 0 ppcccoo  -> plain=ctrl&3, copy=((ctrl&0x1C)>>2)+3, off=((ctrl&0x60)<<3)+b1+1
    # want plain=3, copy=3, offset=3 -> b1 = offset-1 - ((ctrl&0x60)<<3)
    plain, copy, offset = 3, 3, 3
    ctrl = (plain & 3) | (((copy - 3) & 0x7) << 2)
    b1 = offset - 1
    out.append(ctrl)
    out.append(b1)
    out += b"ABC"
    # terminator
    out.append(0xFC)
    dec = refpack.decompress(bytes(out), expected_size=6)
    assert dec == b"ABCABC", f"got {dec!r}"
    print("backreference OK")


if __name__ == "__main__":
    test_literal_roundtrip()
    test_backreference()
    print("ALL REFPACK TESTS PASSED")
