"""
E-Reader for the 2026 Hackaday Supercon "Billboard" badge (ESP32-S3, MicroPython,
960x672 red/black/white e-paper).

Install:   copy to the badge as  apps/ereader.py   (auto-discovered at boot)
Books:     plain ASCII .txt files in /books  (make them with tools/prep_book.py)

Controls
  Library:  B = next book        A = open        (hold B on the calibration entry = nothing)
  Reading:  A = next page         B = previous page
            hold A = +10 pages    hold B = back to library
  (the MENU +/- buttons are owned by the launcher and exit the app; the bookmark is saved)

Design notes
  * Heap is tiny (~60 KB free at idle) so the book is NEVER loaded: each page is laid out
    by seeking to a byte offset and reading one paragraph at a time.
  * The book is pre-converted to ASCII, so byte offset == character index.
  * Line wrapping is done here (fixed chars-per-line) so page boundaries are exact and
    independent of LVGL. Tune W / H below if text clips or leaves too much space.
"""
import os
import gc
import time

try:
    from apps.base_app import BaseApp
except ImportError:                      # running on a host for tests
    class BaseApp:
        sleep_ms = 20
        launched = False
        def __init__(self): pass

# ----------------------------------------------------------------- tunables
BOOK_DIR   = "/books"
W          = 62        # max characters per line  (Montserrat 24 on 900 px)
H          = 17        # lines per page
LONG_MS    = 700       # hold time for "long press"
FAST_REFRESH = True   # text pages via lib/epd_partial.py (~0.6 s); False = stock ~10 s refresh
WIPE_EVERY = 100       # black/white ghost-clearing flash (~3.5 s) every N page turns; 0 = never
DRIVE_FRAMES = 16      # of the 20 frames in a pull, drive changed pixels this many (stock: 10, max 19)
HOLD_FRAMES = 0        # >0: also push UNCHANGED pixels toward their own colour this many frames
                       # (re-seats lazy beads; try 2-4; unbalanced DC, so keep it small)
IDLE_STOP_MS = 60000   # release the display after this long without a page turn
SAVE_EVERY = 8         # write bookmark every N page turns (flash wear)
PIN_A, PIN_B = 39, 16  # User A / User B (active low) per community firmware dump
MARGIN_X, MARGIN_Y = 30, 22
FONT_NAME  = "font_montserrat_24"
LINE_SPACE = 6


# ------------------------------------------------------------ text engine
# A paragraph of the form  [[img:Book_img/001.png]]  becomes a full-page image page.
# layout_page() returns such a page as lines == [IMG_TAG + "Book_img/001.png"].
IMG_OPEN = "[[img:"
IMG_TAG = "\x01IMG:"


def _to_text(raw):
    """bytes -> str with exactly one char per byte (keeps offsets == indexes)."""
    if raw and max(raw) < 128:
        s = raw.decode()
    else:
        s = "".join(chr(b) if b < 128 else "?" for b in raw)
    return s.replace("\t", " ").replace("\r", "")


def layout_page(f, pos, w=W, h=H):
    """Lay out one page starting at byte `pos`.
    Returns (lines, next_pos, hit_eof)."""
    f.seek(pos)
    lines = []
    while len(lines) < h:
        raw = f.readline()
        if not raw:
            return lines, pos, True
        text = _to_text(raw).rstrip("\n")
        n = len(text)
        if not text.strip():
            pos += len(raw)
            if lines and lines[-1] != "":        # collapse blanks, none at page top
                lines.append("")
            continue
        st = text.strip()
        if st.startswith(IMG_OPEN) and st.endswith("]]"):
            if lines:                                # finish the text page first;
                while lines and lines[-1] == "":     # the image starts the next page
                    lines.pop()
                return lines, pos, False
            return [IMG_TAG + st[len(IMG_OPEN):-2]], pos + len(raw), False
        start = 0
        while start < n and text[start] == " ":
            start += 1
        while start < n:
            if len(lines) >= h:
                return lines, pos + start, False
            if n - start <= w:
                end = nxt = n
            else:
                sp = text.rfind(" ", start, start + w + 1)
                if sp <= start:                  # no space: hard break
                    end = nxt = start + w
                else:
                    end = sp
                    nxt = sp
                    while nxt < n and text[nxt] == " ":
                        nxt += 1
            lines.append(text[start:end].rstrip())
            start = nxt
        pos += len(raw)
    return lines, pos, False


def page_starts_before(f, target, w=W, h=H):
    """All page-start offsets < target, scanning from the top of the file."""
    starts = []
    pos = 0
    while pos < target:
        lines, nxt, eof = layout_page(f, pos, w, h)
        if not lines:
            break
        starts.append(pos)
        if eof or nxt <= pos:
            break
        pos = nxt
        if len(starts) % 50 == 0:
            gc.collect()
    return starts


class Book:
    def __init__(self, name):
        self.name = name
        self.path = BOOK_DIR + "/" + name
        self.size = os.stat(self.path)[6]
        self.f = open(self.path, "rb")
        self.pos = self._load_mark()
        self.hist = []                       # starts of pages before self.pos
        self.lines, self.nxt, _ = layout_page(self.f, self.pos)
        self.turns = 0

    # ---- bookmark
    def _mark_path(self):
        return self.path[:-4] + ".mark" if self.path.endswith(".txt") else self.path + ".mark"

    def _load_mark(self):
        try:
            with open(self._mark_path()) as m:
                p = int(m.read().strip() or 0)
            return p if 0 <= p < self.size else 0
        except (OSError, ValueError):
            return 0

    def save(self):
        try:
            with open(self._mark_path(), "w") as m:
                m.write(str(self.pos))
        except OSError:
            pass

    @property
    def image(self):
        """Path of the image if the current page is an image page, else None."""
        if self.lines and self.lines[0].startswith(IMG_TAG):
            return BOOK_DIR + "/" + self.lines[0][len(IMG_TAG):]
        return None

    # ---- navigation
    @property
    def at_end(self):
        if self.nxt >= self.size:
            return True
        probe, _, _ = layout_page(self.f, self.nxt)
        return not probe

    def percent(self):
        return 100 if self.at_end else self.nxt * 100 // self.size

    def _set(self, pos):
        self.pos = pos
        self.lines, self.nxt, _ = layout_page(self.f, pos)

    def next(self, count=1):
        moved = False
        for _ in range(count):
            if self.at_end:
                break
            self.hist.append(self.pos)
            self._set(self.nxt)
            moved = True
        return self._turned(moved)

    def prev(self, count=1):
        moved = False
        for _ in range(count):
            if not self.hist:
                if self.pos == 0:
                    break
                self.hist = page_starts_before(self.f, self.pos)   # slow path, rare
                if not self.hist:
                    break
            self._set(self.hist.pop())
            moved = True
        return self._turned(moved)

    def _turned(self, moved):
        if moved:
            self.turns += 1
            if self.turns % SAVE_EVERY == 0:
                self.save()
            gc.collect()
        return moved

    def close(self):
        self.save()
        try:
            self.f.close()
        except OSError:
            pass


def list_books():
    try:
        names = [n for n in os.listdir(BOOK_DIR) if n.endswith(".txt")]
    except OSError:
        return []
    names.sort()
    return names


def pretty(name):
    return name[:-4].replace("_", " ")


def calibration_lines():
    out = ["W" * W]
    digits = "0123456789" * 10
    for i in range(1, H):
        out.append("%2d " % (i + 1) + digits[:W - 3])
    return out


# --------------------------------------------------------- input (GPIO polling)
class _Btn:
    """Short / long press detection straight from the GPIO (active low)."""
    def __init__(self, gpio):
        from machine import Pin
        self.p = Pin(gpio, Pin.IN, Pin.PULL_UP)
        self.down_at = None
        self.fired = False

    def arm(self):
        """Call on app launch: swallow the press that launched us."""
        if not self.p.value():
            self.down_at = time.ticks_ms()
            self.fired = True
        else:
            self.down_at = None

    def poll(self):
        now = time.ticks_ms()
        if not self.p.value():
            if self.down_at is None:
                self.down_at, self.fired = now, False
            elif not self.fired and time.ticks_diff(now, self.down_at) >= LONG_MS:
                self.fired = True
                return "long"
            return None
        if self.down_at is None:
            return None
        held = time.ticks_diff(now, self.down_at)
        was_fired = self.fired
        self.down_at = None
        if was_fired or held < 30:
            return None
        return "short"


# ------------------------------------------------- display glue (LVGL)
# !! This is the only badge-specific part that is guesswork. If it fails, check
# !! apps/template.py on the badge for how an app gets `display` and draws a label.
def _find_display(app):
    for obj in (app, getattr(app, "badge", None)):
        d = getattr(obj, "display", None) if obj is not None else None
        if d is not None:
            return d
    for mod, attr in (("lib.display", "display"), ("lib.epd", "display"),
                      ("display", "display"), ("main", "display")):
        try:
            m = __import__(mod, None, None, (attr,))
            return getattr(m, attr)
        except (ImportError, AttributeError):
            pass
    raise RuntimeError("ereader: cannot locate the badge display object")


def _ghost_table(drive, hold):
    """400-byte transition table for register 0xB0, same layout as lib/epd_partial.partial_table().
    5 tables x 10 groups x 8 bytes; table = old<<1|new; byte 0 of a group = levels, 2 bits per
    phase (1 = VDH push to black, 2 = VDL push to white); frames alternate phase c, d."""
    drive = max(1, min(19, drive))
    hold = max(0, min(drive, hold))
    t = bytearray(400)
    for k in range(50):
        t[8 * k:8 * k + 8] = b"\x00\x01\x01\x00\x01\x01\x01\x01"
    for f in range(drive):
        sh = 2 if f % 2 == 0 else 0
        t[(10 + f // 2) * 8] |= 1 << sh      # white -> black
        t[(20 + f // 2) * 8] |= 2 << sh      # black -> white
    for f in range(hold):
        sh = 2 if f % 2 == 0 else 0
        t[(0 + f // 2) * 8] |= 2 << sh       # stays white: nudge to white
        t[(30 + f // 2) * 8] |= 1 << sh      # stays black: nudge to black
    return t


class _Screen:
    """Text pages: LVGL renders into the stock framebuffer, then lib/epd_partial.PartialEPD
    shows only what changed (~0.6 s). Picture pages (red ink) use the stock full refresh.
    The panel work runs in an asyncio task because the partial driver is async."""

    def __init__(self, app):
        import lvgl as lv
        self.lv = lv
        self.display = _find_display(app)
        self.fast_ok = FAST_REFRESH
        self.pending = None          # "text" | "image" | "stop"
        self.task = None
        self.panel = None
        self.active = False          # partial session open (holds the display lock)
        self.pages = 0
        self.last_ms = time.ticks_ms()

    # ---- worker
    def _kick(self, kind):
        self.pending = kind
        if self.task is None:
            try:
                import asyncio as aio
                self.task = aio.create_task(self._pump())
            except Exception as ex:                  # no running loop: stock refresh
                print("ereader: no async loop:", ex)
                self.fast_ok = False
                self._stock()

    def _stock(self):
        self.pending = None
        self.display.update()

    async def _pump(self):
        import asyncio as aio
        d = self.display
        while True:
            kind, self.pending = self.pending, None
            try:
                if kind == "text" and self.fast_ok:
                    await self._show_text(d)
                elif kind == "text":
                    d.update()
                elif kind == "image":
                    await self._close()
                    d.update()
                elif kind == "stop":
                    await self._close()
                elif (self.active and
                      time.ticks_diff(time.ticks_ms(), self.last_ms) > IDLE_STOP_MS):
                    await self._close()      # never sit on the display lock while idle
            except BaseException as ex:
                print("ereader: fast refresh failed, using stock refresh:", repr(ex))
                self.fast_ok = False
                try:
                    await self._close()
                except BaseException:
                    pass
                if kind == "text":
                    d.update()
                if not isinstance(ex, Exception):
                    raise
            if kind:
                self.last_ms = time.ticks_ms()
            await aio.sleep_ms(30 if self.pending is None else 0)

    async def _show_text(self, d):
        if self.panel is None:
            gc.collect()
            from lib.epd_partial import PartialEPD
            self.panel = PartialEPD()
            self.panel._lut = _ghost_table(DRIVE_FRAMES, HOLD_FRAMES)
        p = self.panel
        if not self.active:
            gc.collect()
            await p.start()                  # waits for the stock driver, wipes to white (~3 s)
            self.active = True
            self.pages = 0
        elif WIPE_EVERY and self.pages >= WIPE_EVERY:
            await p.restart(wipe=True)       # black/white flash to take ghosting off (~3.5 s)
            self.pages = 0
        p.buf[:] = d._framebuffer_black      # LVGL's black plane: same 960x672 MONO_HLSB layout
        await p.show()
        self.pages += 1

    async def _close(self):
        if self.active and self.panel is not None:
            self.active = False
            await self.panel.stop()          # releases the display lock

    def release(self):
        """App is leaving: hand the panel back to the stock driver."""
        if self.task is not None:
            self.pending = "stop"

    # ---- drawing
    def draw(self, body, footer_l="", footer_r="", header=None):
        lv, d = self.lv, self.display
        scr = lv.screen_active() if hasattr(lv, "screen_active") else lv.scr_act()
        scr.clean()
        black = lv.color_black()
        scr.set_style_bg_color(lv.color_white(), 0)
        font = getattr(lv, FONT_NAME)

        def label(text, x, y, w=None):
            lb = lv.label(scr)
            lb.set_text(text)
            lb.set_pos(x, y)
            if w:
                lb.set_width(w)
            lb.set_style_text_font(font, 0)
            lb.set_style_text_color(black, 0)
            lb.set_style_text_line_space(LINE_SPACE, 0)
            return lb

        y = MARGIN_Y
        if header:
            label(header, MARGIN_X, y, 900)
            y += 50
        label(body, MARGIN_X, y, 900)
        label(footer_l, MARGIN_X, 672 - 44, 600)
        r = label(footer_r, 0, 672 - 44, 200)
        r.set_pos(960 - MARGIN_X - 200, 672 - 44)
        r.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0) if hasattr(lv, "TEXT_ALIGN") else None
        if hasattr(d, "refresh_lvgl"):
            d.refresh_lvgl()                 # render now into the framebuffers
        else:
            for fn in ("timer_handler", "task_handler"):
                if hasattr(lv, fn):
                    getattr(lv, fn)()
                    break
        self._kick("text")

    def draw_image(self, path):
        """Full-page dithered PNG (960x672, white/black/red palette) via the stock draw_png()."""
        lv, d = self.lv, self.display
        try:
            scr = lv.screen_active() if hasattr(lv, "screen_active") else lv.scr_act()
            scr.clean()
        except Exception:
            pass
        d.clear()
        gc.collect()
        d.draw_png(path)
        self._kick("image")


# --------------------------------------------------------------- the app
class EReader(BaseApp):
    def __init__(self):
        super().__init__()
        self.name = "E-Reader"
        self.sleep_ms = 20
        self.mode = "library"
        self.books = []
        self.sel = 0
        self.book = None
        self.screen = None
        self.a = self.b = None
        self.dirty = True

    # ---- helpers
    def _enter_library(self):
        if self.book:
            self.book.close()
            self.book = None
        self.books = list_books()
        self.sel = min(self.sel, len(self.books))     # last slot = calibration page
        self.mode = "library"
        self.dirty = True

    def _render(self):
        s = self.screen
        if self.mode == "library":
            rows = []
            for i, n in enumerate(self.books):
                rows.append(("> " if i == self.sel else "   ") + pretty(n)[:52])
            rows.append(("> " if self.sel == len(self.books) else "   ") + "[ font calibration page ]")
            vis = H - 3                                   # header takes ~2 lines
            top = max(0, min(self.sel - vis + 1, len(rows) - vis))
            s.draw("\n".join(rows[top:top + vis]), "A: open", "B: next",
                   header="Library  (%d books)" % len(self.books))
        elif self.mode == "calib":
            s.draw("\n".join(calibration_lines()), "A/B: back", "W=%d H=%d" % (W, H))
        else:
            bk = self.book
            if bk.image:
                try:
                    s.draw_image(bk.image)
                except Exception as ex:                # missing/too-big PNG: say so, keep going
                    s.draw("[picture could not be shown]\n" + bk.image + "\n" + repr(ex)[:80],
                           "A: next", "B: back")
                self.dirty = False
                return
            title = pretty(bk.name)[:40]
            s.draw("\n".join(bk.lines), "%s" % title, "%d%%" % bk.percent())
        self.dirty = False

    def on_exit(self):
        if self.screen:
            self.screen.release()
        if self.book:
            self.book.save()

    # ---- main loop (called repeatedly by the launcher)
    def run(self):
        if self.screen is None:
            self.screen = _Screen(self)
            self.a, self.b = _Btn(PIN_A), _Btn(PIN_B)
        if self.launched:
            self.a.arm()
            self.b.arm()
            self._enter_library()
        a, b = self.a.poll(), self.b.poll()

        if self.mode == "library":
            if b == "short":
                self.sel = (self.sel + 1) % (len(self.books) + 1)
                self.dirty = True
            elif a == "short":
                if self.sel == len(self.books):
                    self.mode = "calib"
                else:
                    gc.collect()
                    self.book = Book(self.books[self.sel])
                    self.mode = "read"
                self.dirty = True
        elif self.mode == "calib":
            if a or b:
                self._enter_library()
        else:
            if a == "short":
                self.dirty |= self.book.next()
            elif a == "long":
                self.dirty |= self.book.next(10)
            elif b == "short":
                self.dirty |= self.book.prev()
            elif b == "long":
                self._enter_library()

        if self.dirty:
            self._render()
