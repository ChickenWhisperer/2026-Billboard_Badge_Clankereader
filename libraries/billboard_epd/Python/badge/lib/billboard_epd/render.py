"""RGB565-to-three-tone conversion using green-channel thresholds 96 and 204."""
try:
    from micropython import viper
except ImportError:
    from .decay import viper, ptr8


@viper
def _convert(source, black, gray, x1: int, y1: int, width: int, height: int, stride: int):
    src = ptr8(source)
    b = ptr8(black)
    g = ptr8(gray)
    for row in range(height):
        for col in range(width):
            offset = row * stride + col * 2
            color = int(src[offset]) | (int(src[offset + 1]) << 8)
            level = ((color >> 5) & 63) * 255 // 63
            pixel = (y1 + row) * 960 + x1 + col
            index = pixel >> 3
            mask = 128 >> (pixel & 7)
            clear = 255 ^ mask
            b[index] = int(b[index]) & clear
            g[index] = int(g[index]) & clear
            if level < 96:
                b[index] = int(b[index]) | mask
            elif level < 204:
                g[index] = int(g[index]) | mask


def rgb565_to_grayscale(source, black, gray, x1, y1, width, height, stride):
    if (x1 < 0 or y1 < 0 or width < 0 or height < 0 or x1 + width > 960
            or y1 + height > 672 or stride < width * 2
            or len(source) < stride * height or len(black) != 80640 or len(gray) != 80640):
        raise ValueError("invalid RGB565 area, stride, or plane size")
    _convert(source, black, gray, x1, y1, width, height, stride)
