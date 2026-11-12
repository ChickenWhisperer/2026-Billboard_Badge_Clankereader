"""
E-Reader for the 2026 Hackaday Supercon "Billboard" badge (ESP32-S3, MicroPython,
960x672 red/black/white e-paper).

Install:   copy to the badge as  apps/ereader.py   (auto-discovered at boot)
Books:     plain ASCII .txt files in /books  (make them with tools/prep_book.py)

Controls
  Library:  A = next book        B = open
  Reading:  B = next page         A = previous page
            hold B = +10 pages    hold A = back to library
  (the MENU +/- buttons are owned by the launcher and exit the app; the bookmark is saved)

Design notes
  * Heap is tiny (~60 KB free at idle) so the book is NEVER loaded: each page is laid out
    by seeking to a byte offset and reading one paragraph at a time.
  * The book is pre-converted to ASCII, so byte offset == character index.
  * Line wrapping is done here (fixed chars-per-line) so page boundaries are exact and
    independent of LVGL. Tune W / H below if text clips or leaves too much space.
  * Refresh: text uses lib/epd_partial.py (only changed pixels, ~0.6 s). Pictures, and the
    first text page after a picture with red in it, use the stock refresh (~10 s), because
    only the stock waveform removes red ink. Every refresh is logged to the serial console.
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
WIPE_EVERY = 100       # black/white ghost-clearing flash (~3.5 s) every N fast refreshes (pulls); 0 = never
DRIVE_FRAMES = 16      # of the 20 frames in a pull, drive changed pixels this many (stock: 10, max 19)
HOLD_FRAMES = 0        # >0: also push UNCHANGED pixels toward their own colour this many frames
                       # (re-seats lazy beads; try 2-4; unbalanced DC, so keep it small)
IDLE_SLEEP_MS = 5000   # power the panel's booster down after this long without a refresh
SAVE_EVERY = 8         # write bookmark every N page turns (flash wear)
PIN_A, PIN_B = 16, 39  # internal "A" (select/next page) = User B button (GPIO16); "B" = User A button (GPIO39)
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


# ------------------------------------------------------------ display
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


# What is physically on the e-paper right now:
FILM_KNOWN = 0   # black/white and exactly panel._shown: a fast session opens on it, no wipe
FILM_DIRTY = 1   # black/white, content unknown: wipe (~3 s) before going fast
FILM_RED = 2     # red ink: only the stock waveform clears it
_PLANE = 80640   # bytes in one 960 x 672 one-bit plane


class _Screen:
    """All rendering and refreshing runs in one asyncio task, strictly in order, so a refresh
    never races the next page's rendering. The app only posts what it wants shown; quick
    page turns coalesce into the latest one."""

    def __init__(self):
        import lvgl
        from lib.epd import display
        self.lv, self.d = lvgl, display
        self.want = None          # ("text", args) | ("image", path) | ("stop", None)
        self.task = None
        self.panel = None         # lib.epd_partial.PartialEPD, made on first use
        self.fast = FAST_REFRESH
        self.failures = 0
        self.session = False      # fast session open (we hold display.lock)
        self.film = FILM_DIRTY
        self.since_wipe = 0       # fast refreshes since the panel was last cleaned
        self.idle_since = time.ticks_ms()

    # ---- called by the app: never blocks
    def text(self, body, footer_l="", footer_r="", header=None):
        self._post("text", (body, footer_l, footer_r, header))

    def image(self, path):
        self._post("image", path)

    def release(self):
        """App is leaving: power the panel down and give it back to the stock driver."""
        self._post("stop", None)

    def launched(self):
        """App (re)opened: other apps may have drawn on the panel since our last refresh."""
        if not self.session:
            self.film = FILM_RED if self._has_red() else FILM_DIRTY

    def _post(self, kind, arg):
        self.want = (kind, arg)
        if self.task is None:
            import asyncio
            self.task = asyncio.create_task(self._worker())

    # ---- the worker
    async def _worker(self):
        import asyncio
        while True:
            job, self.want = self.want, None
            if job is None:
                await self._idle()
                await asyncio.sleep_ms(30)
                continue
            kind, arg = job
            try:
                if kind == "text":
                    self._render_text(*arg)
                    await self._refresh_text()
                elif kind == "image":
                    await self._show_image(arg)
                else:
                    await self._close()
                self.failures = 0
            except Exception as ex:
                await self._recover(kind, ex)
            self.idle_since = time.ticks_ms()

    async def _recover(self, kind, ex):
        self.failures += 1
        print("ereader: refresh failed:", repr(ex))
        if self.failures >= 3 or isinstance(ex, (ImportError, MemoryError)):
            self.fast = False
            print("ereader: fast refresh off until reboot")
        try:
            await self._close()
        except Exception:
            pass
        self.film = FILM_DIRTY
        if kind != "stop":
            try:
                await self._full_refresh("recovery")
            except Exception as ex2:
                print("ereader: stock refresh failed too:", repr(ex2))

    async def _idle(self):
        if not self.session:
            return
        if not self.d.is_done_updating():
            # Someone else queued a stock refresh while we hold the panel: the launcher took
            # over without calling on_exit. Let go so it can draw.
            print("ereader: another app wants the display, releasing it")
            await self._close()
            self.film = FILM_DIRTY
        elif (self.panel._awake and
              time.ticks_diff(time.ticks_ms(), self.idle_since) > IDLE_SLEEP_MS):
            await self.panel.sleep()    # booster off; we keep the lock, so the film stays known

    # ---- rendering into the stock framebuffers
    def _render_text(self, body, footer_l, footer_r, header):
        lv = self.lv
        scr = lv.screen_active()
        scr.clean()
        scr.set_style_bg_color(lv.color_white(), 0)
        font = getattr(lv, FONT_NAME)

        def label(text, x, y, w, align=None):
            lb = lv.label(scr)
            lb.set_text(text)
            lb.set_pos(x, y)
            lb.set_width(w)
            lb.set_style_text_font(font, 0)
            lb.set_style_text_color(lv.color_black(), 0)
            lb.set_style_text_line_space(LINE_SPACE, 0)
            if align is not None:
                lb.set_style_text_align(align, 0)

        y = MARGIN_Y
        if header:
            label(header, MARGIN_X, y, 900)
            y += 50
        label(body, MARGIN_X, y, 900)
        label(footer_l, MARGIN_X, 672 - 44, 600)
        label(footer_r, 960 - MARGIN_X - 200, 672 - 44, 200, lv.TEXT_ALIGN.RIGHT)
        scr.invalidate()          # repaint all of it: a picture page bypasses LVGL and would
        self.d.refresh_lvgl()     # otherwise show through wherever the new page has no label

    def _render_picture(self, path):
        scr = self.lv.screen_active()
        scr.clean()
        self.d.refresh_lvgl()     # flush LVGL now so a later LVGL pass can't paint over the PNG
        self.d.clear()
        gc.collect()
        self.d.draw_png(path)

    # ---- refreshing
    def _panel(self):
        if self.panel is None:
            if not self.d._reverse_fb_bits:
                raise ImportError("fast refresh is only known to work on the B1 panel")
            gc.collect()
            from lib.epd_partial import PartialEPD
            p = PartialEPD()
            p._lut = _ghost_table(DRIVE_FRAMES, HOLD_FRAMES)
            p.buf = bytearray(_PLANE)        # the frame to show (show() never uses p.fb)
            p._shown = bytearray(_PLANE)     # what the film shows
            p._stage = bytearray(_PLANE // 2)
            self.panel = p
        return self.panel

    def _has_red(self):
        try:
            from lib.epd_partial import _any
            return bool(_any(self.d._framebuffer_red, _PLANE))
        except ImportError:
            return any(self.d._framebuffer_red)

    async def _refresh_text(self):
        if not self.fast:
            await self._full_refresh("fast refresh off")
            return
        p = self._panel()
        if not self.session:
            if self.film == FILM_RED:
                # The stock waveform both clears the red and draws this page; go fast after.
                await self._full_refresh("clearing red ink")
                await self._open()
                return
            await self._open()
        elif WIPE_EVERY and self.since_wipe >= WIPE_EVERY:
            print("ereader: wipe (ghost clearing, every %d)" % WIPE_EVERY)
            await p.restart(wipe=True)
            self.since_wipe = 0
        p.buf[:] = self.d._framebuffer_black
        ms = await p.show()                  # 0 = nothing changed, no refresh
        if ms:
            self.since_wipe += 1
            print("ereader: fast refresh %d ms (%d since wipe)" % (ms, self.since_wipe))

    async def _show_image(self, path):
        try:
            self._render_picture(path)
        except Exception as ex:              # missing / too-big PNG: say so, keep going
            print("ereader: picture failed:", repr(ex))
            self._render_text("[picture could not be shown]\n%s\n%s" % (path, repr(ex)[:80]),
                              "A: back", "B: next", None)
            await self._refresh_text()
            return
        if self.fast and not self._has_red():
            await self._refresh_text()           # a black/white picture can go fast too
        else:
            await self._full_refresh("picture")

    async def _open(self):
        """Take the panel from the stock driver and open a fast session on the current film."""
        import asyncio
        d, p = self.d, self._panel()
        while d._framebuffer_ready.is_set() or not d.is_done_updating():
            await asyncio.sleep_ms(50)       # a stock refresh is queued or running
        await d.lock.acquire()
        p._locked = True                     # p.stop() releases it
        try:
            if p._rst():                     # chips awake: never reset them mid-refresh
                await p._ready("idle", 60_000)
            p._spi = d.spi
            if self.film != FILM_KNOWN:
                print("ereader: wipe (panel content unknown)")
                await p.wipe()               # ~3 s black/white flash; leaves _shown white
                self.film = FILM_KNOWN
                self.since_wipe = 0
            await p._open()
            self.session = True
        except BaseException:
            await p.stop()
            raise

    async def _close(self):
        if self.session:
            self.session = False
            await self.panel.stop()          # powers down, releases display.lock; the film
                                             # still equals panel._shown

    async def _full_refresh(self, why):
        """Stock waveform on what is in the stock framebuffers (~10 s)."""
        import asyncio
        await self._close()
        d = self.d
        red = self._has_red()
        if self.panel is not None and not red:
            self.panel._shown[:] = d._framebuffer_black    # the film will be exactly this
        print("ereader: full refresh (%s)" % why)
        d.update()
        while d._framebuffer_ready.is_set() or not d.is_done_updating():
            await asyncio.sleep_ms(50)
        self.film = FILM_RED if red else (FILM_KNOWN if self.panel else FILM_DIRTY)
        self.since_wipe = 0


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
        self.dirty = False
        if self.mode == "library":
            rows = [("> " if i == self.sel else "   ") + pretty(n)[:52]
                    for i, n in enumerate(self.books)]
            rows.append(("> " if self.sel == len(self.books) else "   ") + "[ font calibration page ]")
            vis = H - 3                                   # header takes ~2 lines
            top = max(0, min(self.sel - vis + 1, len(rows) - vis))
            s.text("\n".join(rows[top:top + vis]), "A: next", "B: open",
                   header="Library  (%d books)" % len(self.books))
        elif self.mode == "calib":
            s.text("\n".join(calibration_lines()), "A/B: back", "W=%d H=%d" % (W, H))
        elif self.book.image:
            s.image(self.book.image)
        else:
            bk = self.book
            s.text("\n".join(bk.lines), pretty(bk.name)[:40], "%d%%" % bk.percent())

    def on_exit(self):
        if self.screen:
            self.screen.release()
        if self.book:
            self.book.save()

    # ---- main loop (called repeatedly by the launcher)
    def run(self):
        if self.screen is None:
            self.screen = _Screen()
            self.a, self.b = _Btn(PIN_A), _Btn(PIN_B)
        if self.launched:
            self.a.arm()
            self.b.arm()
            self.screen.launched()
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
        elif a == "short":
            self.dirty |= self.book.next()
        elif a == "long":
            self.dirty |= self.book.next(10)
        elif b == "short":
            self.dirty |= self.book.prev()
        elif b == "long":
            self._enter_library()

        if self.dirty:
            self._render()
