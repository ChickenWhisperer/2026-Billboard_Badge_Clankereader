#!/usr/bin/env python3
"""Render the Tetris Tag banner: white text on a red band, saved as a 1-bit
palette PNG that the badge draws with display.draw_png.

Run this on your computer, not on the badge:

    pip install pillow fonttools
    python3 make_banner.py                       # writes tetris_banner.png
    python3 make_banner.py --text "HELLO" --pct 20

then copy the result to the badge:

    mpremote cp tetris_banner.png :data/graphics/tetris_banner.png

The text is set in Helvetica Bold. Helvetica has no hammer and sickle, so any
character it lacks is taken from a fallback font (--symbol-font), scaled to the
height of the capitals. Only the rendered shapes go into the PNG; no font file is
copied anywhere. The PNG must match BANNER_PCT in tetris_tag.py (25% of the
960 x 672 screen is 168 px, the default here).
"""

import argparse
import sys

from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont

WIDTH = 960
HEIGHT = 672
SUPERSAMPLE = 4  # draw big, shrink, then threshold: clean edges on a 1-bit panel

DEFAULT_TEXT = "ТЕТРИС ☭ TAG"  # "ТЕТРИС ☭ TAG"
MAIN_FONT = "/System/Library/Fonts/Helvetica.ttc"
MAIN_FACE = "Helvetica Bold"
SYMBOL_FONTS = (  # tried in order; globs allowed
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/opt/anaconda3/lib/python3*/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf",
)
SYMBOL_BOLD = 0.03  # thicken fallback glyphs by this fraction of the font size


def face_index(path, face):
    """Index of the named face inside a .ttc collection, 0 for a plain font."""
    if not path.lower().endswith(".ttc"):
        return 0
    for i, font in enumerate(TTCollection(path).fonts):
        if font["name"].getDebugName(4) == face:
            return i
    sys.exit(f"{path} has no face called {face!r}")


def covered(path, face, text):
    cmap = (TTCollection(path).fonts[face_index(path, face)] if path.lower().endswith(".ttc")
            else TTFont(path)).getBestCmap()
    return {ch for ch in text if ord(ch) in cmap}


def runs(text, main_has):
    """Split text into (uses_main_font, string) runs."""
    out = []
    for ch in text:
        use_main = ch in main_has
        if out and out[-1][0] == use_main:
            out[-1] = (use_main, out[-1][1] + ch)
        else:
            out.append((use_main, ch))
    return out


def layout(text, size, main_path, main_face, symbol_path, symbol_face, bold=SYMBOL_BOLD):
    """Draw the line big on a scratch canvas; return (image, ink bounding box)."""
    main = ImageFont.truetype(main_path, size, index=face_index(main_path, main_face))
    cap = main.getbbox("H", anchor="ls")  # (l, top, r, bottom) around the baseline
    cap_h = cap[3] - cap[1]
    main_has = covered(main_path, main_face, text)
    canvas = Image.new("L", (size * len(text) * 2, size * 3), 0)
    draw = ImageDraw.Draw(canvas)
    x, base = size, int(size * 2)
    for use_main, chunk in runs(text, main_has):
        if use_main:
            draw.text((x, base), chunk, font=main, fill=255, anchor="ls")
            x += int(main.getlength(chunk))
            continue
        # Fallback glyphs: pick the size whose ink is as tall as the capitals.
        probe = ImageFont.truetype(symbol_path, size, index=face_index(symbol_path, symbol_face))
        top, bottom = probe.getbbox(chunk, anchor="ls")[1], probe.getbbox(chunk, anchor="ls")[3]
        stroke = round(size * bold)  # fallback glyphs are lighter than Helvetica Bold
        scale = max(1, cap_h - 2 * stroke) / max(1, bottom - top)
        fallback = ImageFont.truetype(symbol_path, max(2, round(size * scale)),
                                      index=face_index(symbol_path, symbol_face))
        # Sit it on the baseline like the letters, centred on the capitals.
        fb = fallback.getbbox(chunk, anchor="ls")
        dy = -(cap_h // 2) - (fb[1] + fb[3]) // 2
        draw.text((x, base + dy), chunk, font=fallback, fill=255, anchor="ls",
                  stroke_width=stroke, stroke_fill=255)
        x += int(fallback.getlength(chunk)) + 2 * stroke
    return canvas, canvas.getbbox(), cap_h


def fit_size(text, max_w, max_h, args):
    """Largest font size whose ink fits the allowed width and height."""
    lo, hi = 8, 400
    while lo < hi:
        mid = (lo + hi + 1) // 2
        _, box, _ = layout(text, mid, args.font, args.face, args.symbol_font, args.symbol_face, args.symbol_bold)
        if box and box[2] - box[0] <= max_w and box[3] - box[1] <= max_h:
            lo = mid
        else:
            hi = mid - 1
    return lo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--pct", type=int, default=25, help="banner height, percent of the screen")
    ap.add_argument("--margin", type=int, default=48, help="minimum clear pixels left and right")
    ap.add_argument("--height-fraction", type=float, default=0.62,
                    help="tallest the text may be, as a fraction of the banner height")
    ap.add_argument("--font", default=MAIN_FONT)
    ap.add_argument("--face", default=MAIN_FACE, help="face name inside the font file")
    ap.add_argument("--symbol-font", default=None, help="font for characters the main font lacks")
    ap.add_argument("--symbol-face", default="")
    ap.add_argument("--symbol-bold", type=float, default=SYMBOL_BOLD,
                    help="thicken fallback glyphs, as a fraction of the font size (0 for none)")
    ap.add_argument("--out", default="tetris_banner.png")
    args = ap.parse_args()

    if args.symbol_font is None:
        import glob
        args.symbol_font = next((hit for pat in SYMBOL_FONTS for hit in sorted(glob.glob(pat))), None)
        if args.symbol_font is None and set(args.text) - covered(args.font, args.face, args.text):
            sys.exit("the main font lacks some characters and no --symbol-font was found")

    banner_h = HEIGHT * args.pct // 100
    size = fit_size(args.text, WIDTH - 2 * args.margin, int(banner_h * args.height_fraction), args)
    # Render at SUPERSAMPLE x the size, centre the ink in the band, shrink, threshold.
    big, box, _ = layout(args.text, size * SUPERSAMPLE, args.font, args.face,
                         args.symbol_font, args.symbol_face, args.symbol_bold)
    ink = big.crop(box)
    band = Image.new("L", (WIDTH * SUPERSAMPLE, banner_h * SUPERSAMPLE), 0)
    band.paste(ink, ((band.width - ink.width) // 2, (band.height - ink.height) // 2))
    mask = band.resize((WIDTH, banner_h), Image.LANCZOS).point(lambda v: 255 if v >= 128 else 0)

    out = Image.new("P", (WIDTH, banner_h), 0)
    out.putpalette([255, 0, 0, 255, 255, 255] + [0] * 756)  # 0 = red, 1 = white
    out.paste(1, mask=mask)
    out.save(args.out, bits=1, optimize=True)

    ink_box = mask.getbbox()
    print(f"wrote {args.out}: {WIDTH}x{banner_h}, font size {size} px, ink {ink_box}, "
          f"margins left {ink_box[0]} right {WIDTH - ink_box[2]} top {ink_box[1]} "
          f"bottom {banner_h - ink_box[3]}")
    print("main font:", args.font, f"({args.face})", "| symbol font:", args.symbol_font)


if __name__ == "__main__":
    main()
