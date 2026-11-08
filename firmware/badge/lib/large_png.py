import io
import gc

import deflate
import micropython
from micropython import const

SCREEN_WIDTH = const(960)
SCREEN_HEIGHT = const(672)
WHITE = const(0)
BLACK = const(1)
RED = const(2)
GRAY = const(3)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def show_png(path, black_framebuffer: bytearray, red_framebuffer: bytearray, row: int = 0, col: int = 0, grayscale: bool = False, mixed: bool = False):
    """Draw a PNG; mixed keeps red and marks gray in both software planes."""
    image_width, image_height, kinds, rows, bit_depth = _read_palette_png(path, grayscale, mixed)

    for y, line in enumerate(rows):
        screen_y = row + y
        if screen_y < 0 or screen_y >= SCREEN_HEIGHT:
            continue
        start = -col if col < 0 else 0
        end = SCREEN_WIDTH - col
        if end > image_width:
            end = image_width
        if start < end:
            _blit_span(line, kinds, bit_depth, start, end, col, screen_y, black_framebuffer, red_framebuffer)


@micropython.viper
def _blit_span(line, kinds, bit_depth: int, start: int, end: int, col: int, screen_y: int, black, red):
    row = ptr8(line)
    kind = ptr8(kinds)
    black_buf = ptr8(black)
    red_buf = ptr8(red)
    depth = bit_depth
    mask = (1 << depth) - 1
    y_base = screen_y * SCREEN_WIDTH
    for x in range(start, end):
        sample = 0
        if depth == 8:
            sample = row[x]
        else:
            bit_index = x * depth
            shift = 8 - depth - (bit_index & 7)
            sample = row[bit_index >> 3]
            sample = (sample >> shift) & mask
        pixel = kind[sample]
        i = y_base + col + x
        byte_index = i >> 3
        bit = 128 >> (i & 7)
        cleared = 255 ^ bit
        if pixel == GRAY:
            black_buf[byte_index] = black_buf[byte_index] | bit
            red_buf[byte_index] = red_buf[byte_index] | bit
        elif pixel == RED:
            red_buf[byte_index] = red_buf[byte_index] | bit
            black_buf[byte_index] = black_buf[byte_index] & cleared
        elif pixel == BLACK:
            black_buf[byte_index] = black_buf[byte_index] | bit
            red_buf[byte_index] = red_buf[byte_index] & cleared
        else:
            black_buf[byte_index] = black_buf[byte_index] & cleared
            red_buf[byte_index] = red_buf[byte_index] & cleared


@micropython.viper
def _color_kind(red_value: int, green_value: int, blue_value: int) -> int:
    if red_value >= 128 and green_value < 128 and blue_value < 128:
        return RED
    if red_value < 128 and green_value < 128 and blue_value < 128:
        return BLACK
    return WHITE


@micropython.viper
def _undo_filter(filter_type: int, row, previous):
    if filter_type == 0:
        return
    if filter_type < 1 or filter_type > 4:
        raise ValueError("unreadable png filter")
    data = ptr8(row)
    prev = ptr8(previous)
    count = int(len(row))
    for i in range(count):
        left = 0
        up_left = 0
        if i != 0:
            left = data[i - 1]
            up_left = prev[i - 1]
        up = prev[i]
        extra = 0
        if filter_type == 1:
            extra = left
        elif filter_type == 2:
            extra = up
        elif filter_type == 3:
            extra = (left + up) >> 1
        else:
            estimate = left + up - up_left
            dist_left = estimate - left
            if dist_left < 0:
                dist_left = -dist_left
            dist_up = estimate - up
            if dist_up < 0:
                dist_up = -dist_up
            dist_up_left = estimate - up_left
            if dist_up_left < 0:
                dist_up_left = -dist_up_left
            if dist_left <= dist_up and dist_left <= dist_up_left:
                extra = left
            elif dist_up <= dist_up_left:
                extra = up
            else:
                extra = up_left
        data[i] = (data[i] + extra) & 255


def _read_palette_png(path, grayscale=False, mixed=False):
    if grayscale and mixed:
        raise ValueError("Choose grayscale or mixed mode")
    if grayscale or mixed:
        from lib.epd_grayscale import grayscale_kind, mixed_kind
    with open(path, "rb") as image_file:
        data = image_file.read()
    if data[:8] != _PNG_SIGNATURE:
        raise ValueError("not a png: " + path)

    palette = None
    compressed = bytearray()
    image_width = image_height = bit_depth = color_type = interlaced = 0
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        name = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if name == b"IHDR":
            image_width = int.from_bytes(chunk[0:4], "big")
            image_height = int.from_bytes(chunk[4:8], "big")
            bit_depth = chunk[8]
            color_type = chunk[9]
            interlaced = chunk[12]
        elif name == b"PLTE":
            palette = [
                (chunk[i], chunk[i + 1], chunk[i + 2])
                for i in range(0, len(chunk), 3)
            ]
        elif name == b"IDAT":
            compressed.extend(chunk)
        elif name == b"IEND":
            break

    if interlaced:
        raise ValueError("turn off interlacing when saving the png")
    if color_type == 0 and bit_depth in (1, 2, 4, 8):
        kinds = bytearray(1 << bit_depth)
        if grayscale or mixed:
            maximum = (1 << bit_depth) - 1
            for sample in range(len(kinds)):
                kind = grayscale_kind(sample * 255 // maximum)
                kinds[sample] = GRAY if mixed and kind == RED else kind
        else:
            # Preserve the original B/W mapping: only sample 0 is black.
            kinds[0] = BLACK
    elif color_type != 3 or palette is None:
        raise ValueError("save the png as a palette image")
    else:
        count = 1 << bit_depth if bit_depth in (1, 2, 4, 8) else len(palette)
        kinds = bytearray(count)
        for index, color in enumerate(palette):
            if mixed:
                kinds[index] = mixed_kind(color[0], color[1], color[2])
            else:
                kinds[index] = (grayscale_kind(color[1]) if grayscale
                                else _color_kind(color[0], color[1], color[2]))

    data = None
    gc.collect()
    rows = _rows(compressed, image_width, image_height, bit_depth)
    return image_width, image_height, kinds, rows, bit_depth


def _rows(compressed, width, height, bit_depth):
    row_bytes = (width * bit_depth + 7) // 8
    previous = bytearray(row_bytes)
    stream = deflate.DeflateIO(io.BytesIO(compressed), deflate.ZLIB)
    try:
        for _ in range(height):
            filter_type = stream.read(1)[0]
            row = bytearray(stream.read(row_bytes))
            _undo_filter(filter_type, row, previous)
            yield row
            previous = row
    finally:
        stream.close()
