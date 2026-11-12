#!/usr/bin/env python3
"""Convert .txt / .epub / .html to the ASCII, one-paragraph-per-line format the badge e-reader wants.

  python3 prep_book.py "Moby Dick.epub"                 # -> Moby_Dick.txt
  python3 prep_book.py book.txt -o out/ --push          # also copy to badge:/books via mpremote
  python3 prep_book.py *.epub --push --port /dev/ttyACM0

Pictures in EPUB/HTML books are converted to grayscale, resized, Floyd-Steinberg dithered to
black/white and saved as full-page 960x672 PNGs (white/black/red palette, like the official
ImageMagick recipe). They show up as their own pages in the reader.
  --no-images        skip pictures          --max-images N   cap per book (default 30)
  --min-size PX      ignore pictures smaller than this (icons, rules; default 150)
  --contrast C       1.0 = off; try 1.2-1.5 for flat/washed-out pictures   --gamma G  (1.0 = off)
  --red              keep strongly red areas red instead of dithering them to black

Needs Pillow for pictures (pip install pillow); mpremote only for --push.
"""
import argparse, os, re, subprocess, sys, unicodedata, zipfile, io, posixpath
from urllib.parse import unquote
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

TRANSLIT = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "--", "\u2015": "--", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u2022": "*", "\u00b7": "*",
    "\u00df": "ss", "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe", "\u0152": "OE",
    "\u00f8": "o", "\u00d8": "O", "\u0142": "l", "\u0141": "L",
}

def to_ascii(s):
    out = []
    for ch in s:
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch])
        elif ord(ch) < 128:
            out.append(ch)
        else:
            d = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in d if ord(c) < 128 and not unicodedata.combining(c)))
    return "".join(out)

BLOCK = {"p", "div", "br", "tr", "blockquote", "section", "article",
         "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr"}

class _Text(HTMLParser):
    def __init__(self, img_cb=None):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.img_cb = [], 0, img_cb
    def handle_starttag(self, tag, attrs):
        if tag in ("img", "image") and self.img_cb and not self.skip:
            a = dict(attrs)
            src = a.get("src") or a.get("xlink:href") or a.get("href")
            marker = self.img_cb(src) if src else None
            if marker:
                self.parts.append("\n\n" + marker + "\n\n")
        elif tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "sup":
            self.parts.append("[")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag == "li":
            v = dict(attrs).get("value")
            self.parts.append("\n\n" + (v + ". " if v else "- "))
        elif tag in BLOCK:
            self.parts.append("\n\n" if tag != "br" else "\n")
    def handle_endtag(self, tag):
        if tag == "sup":
            self.parts.append("]")
        elif tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.parts.append("\n\n")
    def handle_data(self, d):
        if not self.skip:
            self.parts.append(d)

def html_to_text(html, img_cb=None):
    p = _Text(img_cb)
    p.feed(html)
    return "".join(p.parts)

SCREEN_W, SCREEN_H, PAD = 960, 672, 20
PALETTE = [255, 255, 255, 0, 0, 0, 255, 0, 0]       # white, black, red

class Images:
    """Turns picture bytes into badge-ready PNGs and returns reader markers."""
    def __init__(self, outdir, base, opts):
        self.dir = os.path.join(outdir, base + "_img")
        self.base, self.o, self.files, self.cache = base, opts, [], {}
        self.skipped = 0

    def convert(self, data, key):
        if key in self.cache:
            return self.cache[key]
        marker = None
        if len(self.files) < self.o.max_images:
            try:
                marker = self._convert(data)
            except Exception as ex:                  # corrupt / unsupported (e.g. SVG)
                print("  skipped image %s: %s" % (key, ex), file=sys.stderr)
        else:
            self.skipped += 1
        self.cache[key] = marker
        return marker

    def _convert(self, data):
        from PIL import Image, ImageOps, ImageEnhance
        im = Image.open(io.BytesIO(data))
        if min(im.size) < self.o.min_size:
            return None
        if im.mode in ("RGBA", "LA", "P"):           # flatten transparency onto white
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        rgb = im.convert("RGB")
        gray = ImageOps.autocontrast(rgb.convert("L"), cutoff=1)      # grayscale
        if self.o.contrast != 1.0:
            gray = ImageEnhance.Contrast(gray).enhance(self.o.contrast)
        if self.o.gamma != 1.0:
            gray = gray.point(lambda v: int(255 * (v / 255) ** (1 / self.o.gamma)))
        box = (SCREEN_W - 2 * PAD, SCREEN_H - 2 * PAD)
        scale = min(box[0] / gray.width, box[1] / gray.height, 2.0)   # fit, upscale at most 2x
        size = (max(1, round(gray.width * scale)), max(1, round(gray.height * scale)))
        gray = gray.resize(size, Image.LANCZOS)
        red_mask = None
        if self.o.red:                               # strongly red pixels stay red
            from PIL import ImageChops
            r, g, b = [c.resize(size, Image.LANCZOS) for c in rgb.split()]
            red_mask = ImageChops.multiply(
                ImageChops.multiply(r.point(lambda v: 255 if v > 150 else 0),
                                    g.point(lambda v: 255 if v < 100 else 0)),
                b.point(lambda v: 255 if v < 100 else 0))
        bw = gray.convert("1")                       # Floyd-Steinberg dither
        canvas = Image.new("L", (SCREEN_W, SCREEN_H), 0)             # index 0 = white
        ox, oy = (SCREEN_W - size[0]) // 2, (SCREEN_H - size[1]) // 2
        canvas.paste(bw.convert("L").point(lambda v: 0 if v > 127 else 1), (ox, oy))
        if red_mask is not None:
            canvas.paste(Image.new("L", size, 2), (ox, oy), red_mask)
        out = Image.frombytes("P", canvas.size, canvas.tobytes())
        out.putpalette(PALETTE)
        os.makedirs(self.dir, exist_ok=True)
        name = "%03d.png" % (len(self.files) + 1)
        path = os.path.join(self.dir, name)
        out.save(path, optimize=False)
        self.files.append(path)
        return "[[img:%s_img/%s]]" % (self.base, name)

def read_epub(path, images=None):
    z = zipfile.ZipFile(path)
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
          "o": "http://www.idpf.org/2007/opf"}
    opf_path = ET.fromstring(z.read("META-INF/container.xml")).find(".//c:rootfile", ns).get("full-path")
    opf = ET.fromstring(z.read(opf_path))
    base = os.path.dirname(opf_path)
    manifest = {i.get("id"): i.get("href") for i in opf.findall(".//o:manifest/o:item", ns)}
    chunks = []
    for ref in opf.findall(".//o:spine/o:itemref", ns):
        href = manifest.get(ref.get("idref"))
        if not href:
            continue
        name = os.path.normpath(os.path.join(base, href.split("#")[0])).replace("\\", "/")
        cb = None
        if images:
            def cb(src, _dir=posixpath.dirname(name)):
                if src.startswith("data:") or "://" in src:
                    return None
                key = posixpath.normpath(posixpath.join(_dir, unquote(src.split("#")[0])))
                try:
                    return images.convert(z.read(key), key)
                except KeyError:
                    return None
        try:
            chunks.append(html_to_text(z.read(name).decode("utf-8", "replace"), cb))
        except KeyError:
            pass
    return "\n\n".join(chunks)

def strip_gutenberg(t):
    m = re.search(r"\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", t, re.I | re.S)
    if m: t = t[m.end():]
    m = re.search(r"\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG", t, re.I)
    if m: t = t[:m.start()]
    return t

def normalise(text):
    """Unwrap hard-wrapped lines; one paragraph per line; blank line between paragraphs."""
    text = to_ascii(text.replace("\r\n", "\n").replace("\r", "\n"))
    paras = []
    for block in re.split(r"\n\s*\n", text):
        flat = re.sub(r"\s+", " ", block).strip()
        if flat:
            paras.append(flat)
    return "\n\n".join(paras) + "\n"

def convert(path, images=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        raw = read_epub(path, images)
    elif ext in (".html", ".htm", ".xhtml"):
        cb = None
        if images:
            def cb(src, _d=os.path.dirname(os.path.abspath(path))):
                if src.startswith("data:") or "://" in src:
                    return None
                p = os.path.join(_d, unquote(src.split("#")[0]))
                try:
                    return images.convert(open(p, "rb").read(), p)
                except OSError:
                    return None
        raw = html_to_text(open(path, encoding="utf-8", errors="replace").read(), cb)
    else:
        raw = strip_gutenberg(open(path, encoding="utf-8", errors="replace").read())
    return normalise(raw)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--outdir", default=".")
    ap.add_argument("--push", action="store_true", help="copy to the badge's /books with mpremote")
    ap.add_argument("--port", help="serial port for mpremote (default: auto)")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--max-images", type=int, default=30)
    ap.add_argument("--min-size", type=int, default=150)
    ap.add_argument("--contrast", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--red", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    have_pil = True
    if not a.no_images:
        try:
            import PIL  # noqa: F401
        except ImportError:
            have_pil = False
            print("Pillow not installed: pictures skipped (pip install pillow)", file=sys.stderr)
    outs, extra = [], []
    for f in a.files:
        base = re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(os.path.basename(f))[0]).strip("_")[:48] or "book"
        out = os.path.join(a.outdir, base + ".txt")
        imgs = Images(a.outdir, base, a) if (have_pil and not a.no_images) else None
        txt = convert(f, imgs)
        with open(out, "w", encoding="ascii") as fh:
            fh.write(txt)
        n_img = len(imgs.files) if imgs else 0
        kb = sum(os.path.getsize(p) for p in imgs.files) / 1024 if imgs else 0
        print("%-40s %7.1f KB text  ~%d pages  %d pictures (%.0f KB)%s" % (
            out, len(txt) / 1024, len(txt) // 1000, n_img, kb,
            "  [%d over the cap dropped]" % imgs.skipped if imgs and imgs.skipped else ""))
        outs.append(out)
        if imgs:
            extra += [(p, ":books/%s_img/%s" % (base, os.path.basename(p)), ":books/%s_img" % base) for p in imgs.files]
    if a.push:
        mp = ["mpremote"] + (["connect", a.port] if a.port else [])
        subprocess.run(mp + ["fs", "mkdir", ":books"], stderr=subprocess.DEVNULL)  # ok if it exists
        for d in sorted({e[2] for e in extra}):
            subprocess.run(mp + ["fs", "mkdir", d], stderr=subprocess.DEVNULL)
        for o in outs:
            print("pushing", o)
            subprocess.run(mp + ["fs", "cp", o, ":books/" + os.path.basename(o)], check=True)
        for local, remote, _ in extra:
            print("pushing", local)
            subprocess.run(mp + ["fs", "cp", local, remote], check=True)
        subprocess.run(mp + ["reset"])

if __name__ == "__main__":
    sys.exit(main())
