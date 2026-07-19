"""Known Sims 4 DBPF resource type IDs (the 32-bit ResourceType field)."""

# Mesh / model
GEOM = 0x015A1849          # Body geometry (the actual editable mesh)
MODL = 0x01661233          # Model (object)
MLOD = 0x01D10F34          # Model LOD container

# Catalog / object definition
OBJD = 0x6C3C6A89          # Object Definition (furniture, objects)
COBJ = 0x319E4F1D          # Catalog Object

# Textures
DDS_RLE2 = 0x3453CF95      # RLES (DST5 packed) used by S4
DDS_RLE = 0xBA856C78       # RLE2/RLE0 packed DDS
DXT = 0x00B2D882           # Plain DDS image (_IMG)
THUMB_PNG = 0x3C1AF1F2     # PNG thumbnails (varies)

DDS_RLE2_TYPE = 0x3453CF95   # RLE2 packed DDS
LRLE = 0x2BC04EDF            # LRLE packed image (newer)

# A friendly map for logging
TYPE_NAMES = {
    GEOM: "GEOM",
    MODL: "MODL",
    MLOD: "MLOD",
    DDS_RLE2: "DDS_RLES",
    DDS_RLE: "DDS_RLE",
    DXT: "DDS_IMG",
    0x3453CF95: "RLE2",
    0x2BC04EDF: "LRLE",
    0x01D0E723: "VRTF",
    0x01D0E6FB: "VBUF",
    0x01D0E70F: "IBUF",
    0x01D0E75D: "MATD",
    0x220557DA: "STBL",
    0x319E4F1D: "COBJ",
    0x6C3C6A89: "OBJD",
    0xC0DB5AE7: "CATALOG",
    0x03B4C61D: "LITE",
    0x4F726BBE: "FTPT",
    0x545AC67A: "DATA",
    0x3C1AF1F2: "PNG",
    0x5B282D45: "PNG",
}

# Resource types we treat as "image-like" and try to turn into PNG.
IMAGE_TYPES = {DDS_RLE2, DDS_RLE, DXT, 0x3453CF95, 0x2BC04EDF}


def type_name(type_id: int) -> str:
    return TYPE_NAMES.get(type_id, f"0x{type_id:08X}")
