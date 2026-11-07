"""Load LVGL v9 ".bin" fonts into a usable `lvgl.font_t` without `lvgl.binfont_create()`.

Why this exists
----------------
`lvgl.binfont_create()` isn't available in this firmware's lvgl binding (the
loader C module wasn't compiled in). This module re-implements the file
parsing side of lv_binfont_loader.c in pure MicroPython, then builds the
native `lv_font_fmt_txt_dsc_t` + friends by hand in a bytearray using
`uctypes.addressof()` to wire up the internal pointers.

The one trick that makes this work at all: the MicroPython binding
(lv_binding_micropython's gen_mpy.py) can only auto-wrap a struct's
function-pointer field as a *callable Python callback* when the callback's
first argument type matches the struct the field lives on (so it has
somewhere - that struct's `user_data` - to stash the Python callable).
`lv_font_t.get_glyph_bitmap`'s first argument is `lv_font_glyph_dsc_t`, not
`lv_font_t`, so that mechanism doesn't apply to it, and assigning a plain
Python function there raises `SyntaxError: Cannot convert 'function' to
pointer!`.

But every bitmap font lvgl ships with uses the *same two native C functions*
for `get_glyph_dsc`/`get_glyph_bitmap` (`lv_font_get_glyph_dsc_fmt_txt`
and `lv_font_get_bitmap_fmt_txt` - this is exactly what `lv_binfont_create()`
itself wires up in C). The binding exposes a struct's function-pointer field,
when read back, as a small wrapper object that implements the buffer
protocol over the real native pointer. That means we can *copy* those two
field values from any already-loaded bitmap font (e.g. `lvgl.font_montserrat_14`)
onto our new font object, and the assignment goes through fine because it's
just a pointer-sized buffer being copied - no Python callback involved.
So: borrow the native engine's function pointers, and hand it a correctly
laid out `font.dsc` to chew on. No C code, no recompiling the firmware.

Assumptions (true for a standard ESP32-S3 / Xtensa-GCC build, which is what
this badge uses)
  - 32-bit pointers, little-endian.
  - `LV_FONT_FMT_TXT_LARGE` is 0 (the default): glyph_dsc entries are 8 bytes
    (20-bit bitmap_index + 12-bit adv_w packed into one uint32, then plain
    uint8 box_w/box_h and int8 ofs_x/ofs_y).
  - GCC's standard little-endian bitfield packing (first-declared member in
    the low bits).
  - Enums are `int`-sized (4 bytes) - the default unless the firmware was
    built with `-fshort-enums`.

If glyph rendering comes out garbled on real hardware, one of these
assumptions is the first thing to check.

Usage
-----
    import lvgl
    from lib.binfont import load_binfont

    my_font = load_binfont("/data/marker_felt.bin")
    label.set_style_text_font(my_font, 0)

`load_binfont()` keeps every buffer it allocates alive for the lifetime of
the returned font (stashed in a module-level registry), since `font.dsc`
only stores a raw pointer with nothing else holding a reference.
"""
import struct

import lvgl
import uctypes

# --------------------------------------------------------------------------
# Binary format constants (see lv_font_fmt_txt.h / lv_binfont_loader.c)
# --------------------------------------------------------------------------

CMAP_FORMAT0_FULL = 0
CMAP_SPARSE_FULL = 1
CMAP_FORMAT0_TINY = 2
CMAP_SPARSE_TINY = 3

BITMAP_PLAIN = 0
BITMAP_COMPRESSED = 1
BITMAP_COMPRESSED_NO_PREFILTER = 2

_HEADER_FMT = "<IHHHhHhHhhHHBBBBBBBBBBhH"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_HEADER_KEYS = (
    "version", "tables_count", "font_size", "ascent", "descent",
    "typo_ascent", "typo_descent", "typo_line_gap", "min_y", "max_y",
    "default_advance_width", "kerning_scale", "index_to_loc_format",
    "glyph_id_format", "advance_width_format", "bits_per_pixel",
    "xy_bits", "wh_bits", "advance_width_bits", "compression_id",
    "subpixels_mode", "padding", "underline_position", "underline_thickness",
)

_CMAP_TABLE_FMT = "<IIHHHBB"
_CMAP_TABLE_SIZE = struct.calcsize(_CMAP_TABLE_FMT)

# Keeps every native buffer a loaded font depends on alive forever, since
# font.dsc is just a raw pointer with nothing else referencing the data.
_LOADED_FONTS = []


class BinfontError(Exception):
    pass


# --------------------------------------------------------------------------
# File parsing (ports lv_binfont_loader.c's bit-level reader byte for byte)
# --------------------------------------------------------------------------

class _BitReader:
    """Mirrors bit_iterator_t / read_bits / read_bits_signed: reads bits
    MSB-first from a file object, one source byte at a time."""

    def __init__(self, f):
        self.f = f
        self.bit_pos = -1
        self.byte_value = 0

    def read_bits(self, n_bits):
        value = 0
        bp = self.bit_pos
        bv = self.byte_value
        f = self.f
        for _ in range(n_bits):
            bv = (bv << 1) & 0xFF
            bp -= 1
            if bp < 0:
                bp = 7
                b = f.read(1)
                if len(b) != 1:
                    raise BinfontError("unexpected EOF while reading bits")
                bv = b[0]
            bit = 1 if (bv & 0x80) else 0
            value = (value << 1) | bit
        self.bit_pos = bp
        self.byte_value = bv
        return value

    def read_bits_signed(self, n_bits):
        if n_bits <= 0:
            return 0
        value = self.read_bits(n_bits)
        if value & (1 << (n_bits - 1)):
            value -= 1 << n_bits
        return value


def _read_label(f, start, label):
    f.seek(start)
    length = struct.unpack("<I", f.read(4))[0]
    tag = f.read(4)
    if tag != label:
        raise BinfontError(
            "expected %r label at offset %d, got %r" % (label, start, tag)
        )
    return length


def _read_header(f):
    header_len = _read_label(f, 0, b"head")
    raw = f.read(_HEADER_SIZE)
    if len(raw) != _HEADER_SIZE:
        raise BinfontError("truncated head table")
    fields = struct.unpack(_HEADER_FMT, raw)
    header = dict(zip(_HEADER_KEYS, fields))
    header["header_len"] = header_len
    return header


def _load_cmaps(f, cmaps_start):
    cmaps_length = _read_label(f, cmaps_start, b"cmap")
    (subtable_count,) = struct.unpack("<I", f.read(4))

    raw_tables = []
    for _ in range(subtable_count):
        raw = f.read(_CMAP_TABLE_SIZE)
        if len(raw) != _CMAP_TABLE_SIZE:
            raise BinfontError("truncated cmap table")
        raw_tables.append(struct.unpack(_CMAP_TABLE_FMT, raw))

    cmaps = []
    for (
        data_offset, range_start, range_length, glyph_id_start,
        data_entries_count, format_type, _padding,
    ) in raw_tables:
        f.seek(cmaps_start + data_offset)
        unicode_list = None
        glyph_id_ofs_list = None
        list_length = range_length

        if format_type == CMAP_FORMAT0_FULL:
            glyph_id_ofs_list = f.read(data_entries_count)
            list_length = range_length
        elif format_type == CMAP_FORMAT0_TINY:
            pass
        elif format_type in (CMAP_SPARSE_FULL, CMAP_SPARSE_TINY):
            raw = f.read(2 * data_entries_count)
            unicode_list = struct.unpack("<%dH" % data_entries_count, raw)
            list_length = data_entries_count
            if format_type == CMAP_SPARSE_FULL:
                raw2 = f.read(2 * list_length)
                glyph_id_ofs_list = struct.unpack("<%dH" % list_length, raw2)
        else:
            raise BinfontError("unknown cmap format_type %d" % format_type)

        cmaps.append({
            "range_start": range_start,
            "range_length": range_length,
            "glyph_id_start": glyph_id_start,
            "type": format_type,
            "unicode_list": unicode_list,
            "glyph_id_ofs_list": glyph_id_ofs_list,
            "list_length": list_length,
        })
    return cmaps, cmaps_length


def _load_loca(f, loca_start, index_to_loc_format):
    loca_length = _read_label(f, loca_start, b"loca")
    (loca_count,) = struct.unpack("<I", f.read(4))
    if index_to_loc_format == 0:
        raw = f.read(2 * loca_count)
        offsets = list(struct.unpack("<%dH" % loca_count, raw))
    elif index_to_loc_format == 1:
        raw = f.read(4 * loca_count)
        offsets = list(struct.unpack("<%dI" % loca_count, raw))
    else:
        raise BinfontError("unknown index_to_loc_format %d" % index_to_loc_format)
    return offsets, loca_length, loca_count


def _load_glyphs(f, glyph_start, glyph_offsets, loca_count, header):
    glyph_length = _read_label(f, glyph_start, b"glyf")

    adv_bits = header["advance_width_bits"]
    xy_bits = header["xy_bits"]
    wh_bits = header["wh_bits"]
    nbits = adv_bits + 2 * xy_bits + 2 * wh_bits

    glyphs = [None] * loca_count
    bmp_sizes = [0] * loca_count
    cur_bmp_size = 0

    # Pass 1: glyph metrics + bitmap_index bookkeeping.
    for i in range(loca_count):
        f.seek(glyph_start + glyph_offsets[i])
        br = _BitReader(f)

        if adv_bits == 0:
            adv_w = header["default_advance_width"]
        else:
            adv_w = br.read_bits(adv_bits)
        if header["advance_width_format"] == 0:
            adv_w *= 16

        ofs_x = br.read_bits_signed(xy_bits)
        ofs_y = br.read_bits_signed(xy_bits)
        box_w = br.read_bits(wh_bits)
        box_h = br.read_bits(wh_bits)

        next_offset = glyph_offsets[i + 1] if i < loca_count - 1 else glyph_length
        bmp_size = next_offset - glyph_offsets[i] - nbits // 8

        if i == 0:
            adv_w = 0
            box_w = 0
            box_h = 0
            ofs_x = 0
            ofs_y = 0

        bitmap_index = cur_bmp_size
        if box_w * box_h != 0:
            cur_bmp_size += bmp_size

        glyphs[i] = {
            "adv_w": adv_w, "ofs_x": ofs_x, "ofs_y": ofs_y,
            "box_w": box_w, "box_h": box_h, "bitmap_index": bitmap_index,
        }
        bmp_sizes[i] = bmp_size

    # Pass 2: copy the raw (possibly compressed/bit-packed) bitmap bytes
    # verbatim - unpacking by bpp happens later, natively, inside the
    # borrowed get_glyph_bitmap function.
    bitmap = bytearray(cur_bmp_size)
    cur_bmp_size = 0
    for i in range(1, loca_count):
        g = glyphs[i]
        if g["box_w"] * g["box_h"] == 0:
            continue

        f.seek(glyph_start + glyph_offsets[i])
        br = _BitReader(f)
        br.read_bits(nbits)  # skip the per-glyph metrics header

        bmp_size = bmp_sizes[i]
        if nbits % 8 == 0:
            chunk = f.read(bmp_size)
            if len(chunk) != bmp_size:
                raise BinfontError("truncated glyph bitmap for glyph %d" % i)
            bitmap[cur_bmp_size: cur_bmp_size + bmp_size] = chunk
        else:
            for k in range(bmp_size - 1):
                bitmap[cur_bmp_size + k] = br.read_bits(8)
            last = br.read_bits(8 - nbits % 8)
            bitmap[cur_bmp_size + bmp_size - 1] = (last << (nbits % 8)) & 0xFF

        cur_bmp_size += bmp_size

    return glyphs, bytes(bitmap), glyph_length


def _load_kern(f, kern_start, glyph_id_format):
    kern_length = _read_label(f, kern_start, b"kern")
    kern_format_type = f.read(1)[0]
    f.read(3)  # padding

    if kern_format_type == 0:
        (pair_cnt,) = struct.unpack("<I", f.read(4))
        elem_fmt = "B" if glyph_id_format == 0 else "H"
        raw_ids = f.read(pair_cnt * 2 * struct.calcsize(elem_fmt))
        flat = struct.unpack("<%d%s" % (2 * pair_cnt, elem_fmt), raw_ids)
        pairs = list(zip(flat[0::2], flat[1::2]))
        values = list(struct.unpack("<%db" % pair_cnt, f.read(pair_cnt)))
        return {
            "classes": False,
            "glyph_id_format": glyph_id_format,
            "pairs": pairs,
            "values": values,
        }, kern_length
    elif kern_format_type == 3:
        mapping_len, rows, cols = struct.unpack("<HBB", f.read(4))
        left = f.read(mapping_len)
        right = f.read(mapping_len)
        values = list(struct.unpack("<%db" % (rows * cols), f.read(rows * cols)))
        return {
            "classes": True,
            "left_class_mapping": left,
            "right_class_mapping": right,
            "left_class_cnt": rows,
            "right_class_cnt": cols,
            "class_pair_values": values,
        }, kern_length
    else:
        raise BinfontError("unknown kern_format_type %d" % kern_format_type)


def parse_binfont(path):
    """Parse an lvgl v9 ".bin" font file into plain Python data structures."""
    f = open(path, "rb")
    try:
        header = _read_header(f)
        cmaps_start = header["header_len"]
        cmaps, cmaps_length = _load_cmaps(f, cmaps_start)

        loca_start = cmaps_start + cmaps_length
        loca_offsets, loca_length, loca_count = _load_loca(
            f, loca_start, header["index_to_loc_format"]
        )

        glyph_start = loca_start + loca_length
        glyphs, bitmap, glyph_length = _load_glyphs(
            f, glyph_start, loca_offsets, loca_count, header
        )

        kern = None
        if header["tables_count"] >= 4:
            kern_start = glyph_start + glyph_length
            kern, _kern_length = _load_kern(f, kern_start, header["glyph_id_format"])

        return {
            "header": header,
            "cmaps": cmaps,
            "glyphs": glyphs,
            "bitmap": bitmap,
            "kern": kern,
        }
    finally:
        f.close()


# --------------------------------------------------------------------------
# Native struct assembly (mirrors lv_font_fmt_txt_private.h's layouts)
# --------------------------------------------------------------------------

def _addr(buf):
    return uctypes.addressof(buf) if buf is not None else 0


def _build_glyph_dsc_array(glyphs):
    buf = bytearray(8 * len(glyphs))
    for i, g in enumerate(glyphs):
        word0 = (g["bitmap_index"] & 0xFFFFF) | ((g["adv_w"] & 0xFFF) << 20)
        struct.pack_into(
            "<IBBBB", buf, i * 8,
            word0,
            g["box_w"] & 0xFF,
            g["box_h"] & 0xFF,
            g["ofs_x"] & 0xFF,
            g["ofs_y"] & 0xFF,
        )
    return buf


def _build_cmaps(cmaps):
    """Returns (cmaps_array_buf, [child_buffers...])."""
    keepalive = []
    entries = bytearray(24 * len(cmaps))
    for i, c in enumerate(cmaps):
        unicode_buf = None
        if c["unicode_list"] is not None:
            n = len(c["unicode_list"])
            unicode_buf = bytearray(struct.pack("<%dH" % n, *c["unicode_list"]))
            keepalive.append(unicode_buf)

        ofs_buf = None
        if c["glyph_id_ofs_list"] is not None:
            if c["type"] == CMAP_FORMAT0_FULL:
                ofs_buf = bytearray(c["glyph_id_ofs_list"])  # already uint8 bytes
            else:
                n = len(c["glyph_id_ofs_list"])
                ofs_buf = bytearray(
                    struct.pack("<%dH" % n, *c["glyph_id_ofs_list"])
                )
            keepalive.append(ofs_buf)

        struct.pack_into(
            "<IHHIIHHI", entries, i * 24,
            c["range_start"],
            c["range_length"],
            c["glyph_id_start"],
            _addr(unicode_buf),
            _addr(ofs_buf),
            c["list_length"],
            0,  # 2 bytes padding before the (int-sized) enum field
            c["type"],
        )
    return entries, keepalive


def _build_kern(kern):
    """Returns (kern_dsc_buf, kern_classes_flag, [child_buffers...])."""
    if kern is None:
        return None, 0, []

    keepalive = []
    if not kern["classes"]:
        pairs = kern["pairs"]
        values = kern["values"]
        if kern["glyph_id_format"] == 0:
            ids_buf = bytearray(struct.pack("<%dB" % (2 * len(pairs)),
                                             *[v for p in pairs for v in p]))
            glyph_ids_size = 0
        else:
            ids_buf = bytearray(struct.pack("<%dH" % (2 * len(pairs)),
                                             *[v for p in pairs for v in p]))
            glyph_ids_size = 1
        values_buf = bytearray(struct.pack("<%db" % len(values), *values))
        keepalive += [ids_buf, values_buf]

        buf = bytearray(12)
        struct.pack_into(
            "<III", buf, 0,
            _addr(ids_buf),
            _addr(values_buf),
            (len(pairs) & 0x3FFFFFFF) | ((glyph_ids_size & 0x3) << 30),
        )
        return buf, 0, keepalive
    else:
        values_buf = bytearray(
            struct.pack("<%db" % len(kern["class_pair_values"]), *kern["class_pair_values"])
        )
        left_buf = bytearray(kern["left_class_mapping"])
        right_buf = bytearray(kern["right_class_mapping"])
        keepalive += [values_buf, left_buf, right_buf]

        buf = bytearray(16)
        struct.pack_into(
            "<IIIBB", buf, 0,
            _addr(values_buf),
            _addr(left_buf),
            _addr(right_buf),
            kern["left_class_cnt"],
            kern["right_class_cnt"],
        )
        return buf, 1, keepalive


def _build_dsc(parsed):
    header = parsed["header"]
    if header["compression_id"] not in (
        BITMAP_PLAIN, BITMAP_COMPRESSED, BITMAP_COMPRESSED_NO_PREFILTER
    ):
        raise BinfontError("unknown bitmap compression id %d" % header["compression_id"])

    keepalive = []

    glyph_dsc_buf = _build_glyph_dsc_array(parsed["glyphs"])
    keepalive.append(glyph_dsc_buf)

    cmaps_buf, cmaps_children = _build_cmaps(parsed["cmaps"])
    keepalive.append(cmaps_buf)
    keepalive += cmaps_children

    kern_buf, kern_classes, kern_children = _build_kern(parsed["kern"])
    if kern_buf is not None:
        keepalive.append(kern_buf)
        keepalive += kern_children

    bitmap_buf = bytearray(parsed["bitmap"])
    keepalive.append(bitmap_buf)

    dsc = bytearray(24)
    bitfield = (
        (len(parsed["cmaps"]) & 0x1FF)
        | ((header["bits_per_pixel"] & 0xF) << 9)
        | ((kern_classes & 0x1) << 13)
        | ((header["compression_id"] & 0x3) << 14)
    )
    struct.pack_into(
        "<IIIIHHB", dsc, 0,
        _addr(bitmap_buf),
        _addr(glyph_dsc_buf),
        _addr(cmaps_buf),
        _addr(kern_buf),
        header["kerning_scale"],
        bitfield,
        0,  # stride: loaded bin fonts always use 0 (tightly packed rows)
    )
    keepalive.append(dsc)
    return dsc, keepalive


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def load_binfont(path, ref_font=None):
    """Load an lvgl v9 bin-format font from `path` into a usable `lvgl.font_t`.

    `ref_font` supplies the native `get_glyph_dsc`/`get_glyph_bitmap`/
    `release_glyph` function pointers - any already-loaded bitmap font works
    (they all point at the same two engine functions). Defaults to
    `lvgl.font_montserrat_14`, which lvgl enables by default.
    """
    if ref_font is None:
        ref_font = lvgl.font_montserrat_14

    parsed = parse_binfont(path)
    header = parsed["header"]

    dsc, keepalive = _build_dsc(parsed)

    font = lvgl.font_t()
    font.line_height = header["ascent"] - header["descent"]
    font.base_line = -header["descent"]
    font.subpx = header["subpixels_mode"]
    font.underline_position = header["underline_position"]
    font.underline_thickness = header["underline_thickness"]
    font.dsc = dsc

    # The actual trick: borrow the native fmt_txt engine's function pointers
    # from an already-working bitmap font instead of trying (and failing) to
    # implement them as Python callbacks. See module docstring.
    font.get_glyph_dsc = ref_font.get_glyph_dsc
    font.get_glyph_bitmap = ref_font.get_glyph_bitmap
    font.release_glyph = ref_font.release_glyph

    _LOADED_FONTS.append((font, keepalive))
    return font
