"""Tetris Tag. Tetris-like pieces fall and stack up to spell a name along the
bottom edge of the screen, the name blinks, stays up for HOLD_S seconds, then it is
built again (or the next name, if NAMES lists more than one).

User A: turn the left falling piece 90 degrees clockwise
User B: turn the right falling piece 90 degrees clockwise
(between names, User A skips to the next name and User B restarts the current one)

A red banner with white Helvetica Bold lettering runs across the top of the screen.
The lettering is an image made on a computer by make_banner.py. Edit NAMES and the
settings below to make it your own.

The panel's own refresh takes about 30 s, far too slow to animate. On B1 panels
with current firmware (display.supports_fast) two pieces fall at a time, like a
two-player Tetris, in partial refreshes (see lib/epd_fast.py): the screen is cleared
with one full refresh, then only the pixels that change are redrawn. Both pieces appear
at 25% of the screen height turned a random quarter turn and fall without turning.
User A turns the left piece and User B the right one, 90 degrees clockwise per press.
Once a piece is the right way up it drops into place on the next refresh. A piece
nobody has turned starts turning itself when it reaches 50% of the screen height.
Elsewhere the app falls back to a slideshow, one full refresh per snapshot, showing
the pieces landed (black) and the next few still in the air (red).
"""

import gc
import random
import time

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display, display_width, display_height
from lib.led_bar import leds

NAMES = ("YOUR NAME",)
"""Shown in turn, forever. Replace "YOUR NAME" with your own, using only the characters
in FONT below (the letters A to Z and a to z, é, and space), or add more names to the
tuple to cycle through them. Digits and punctuation need a 5 x 7 glyph of their own."""

BANNER_IMAGE = "/data/graphics/tetris_banner.png"
"""White lettering on red, 960 px wide, made on a computer by make_banner.py and
copied to the badge (the badge has no Helvetica, and its built-in font has neither
Cyrillic nor the hammer and sickle). If the file is missing, the banner is a plain
red band."""

BANNER_PCT = 25
"""Banner height as a percent of the screen height. 0 for no banner. Must match the
image (make_banner.py --pct). The banner is red, so it is drawn by the full refresh
at the start of each name, and the fast partial refreshes hold it untouched."""

TARGET_FRAMES = 8
"""Roughly how many e-paper refreshes it takes to build one name.
More frames is smoother, but each one costs a full refresh."""

MAX_CELL = 16
"""Largest block size in pixels. Names are scaled down to fit the width."""

BOTTOM_MARGIN = 8
"""Pixels left clear under the letters."""

BLINK_MODE = "leds"
"""'leds':   the finished name stays on the paper and the LED bar blinks.
This is the only way to get a real half-second on/off, since the paper cannot
refresh that quickly.
'screen': blink the paper itself, name -> blank -> name. Each step waits for a
full refresh to finish, so it is slow and flashes the whole panel."""

BLINK_COUNT = 3
BLINK_MS = 500
BLINK_COLOR = (20, 20, 20)
"""LED brightness is 0-255 and they are bright. Keep it low."""

HOLD_S = 30
"""Seconds the finished name stays on the screen, after the blinking, before the next
round starts. User A skips the wait to the next name, User B restarts this one."""

FAST = True
"""Use partial refreshes where the panel and firmware support them. False forces
the slideshow."""

PIECES_AT_ONCE = 2
"""Pieces falling at the same time: 1 or 2. With 2, User A turns the left one and User
B the right one (with 1, either button turns it). Two pieces only fall together if
they land in separate columns, and each stays on its own side of the screen while it
is in the air."""

SPAWN_PCT = 25
"""Every piece appears at this percent of the screen height, measured from the top.
25 is the bottom edge of the banner. It is never put inside the banner."""

FALL_ROWS = 3
"""Rows a piece falls in each refresh while it is turning or waiting to be turned.
Once it is the right way up it falls the rest of the way into place in one refresh."""

AUTO_TURN_PCT = 50
"""A piece that is still not the right way up turns itself, 90 degrees a refresh by the
shorter way round, once it reaches this percent of the screen height. Buttons only turn
a piece above this line."""

HOP_PULLS = 1
"""Drive steps (about 1.1 s each) for frames while a piece is moving. 1 is
quickest but pale. Raise to 2 or 3 if falling pieces are too faint to see."""

SETTLE_PULLS = 3
"""Drive steps for the frame where a piece lands. 3 is solid black."""

BLINK_PULLS = 3
"""Drive steps for each flash when BLINK_MODE is 'screen' and fast refresh is on."""

FLIGHT_MIN_ROWS = 2
"""Slideshow only: pieces in the air are drawn at least this many rows above
their landing spot."""

SPLIT_ATTEMPTS = 10
"""Random tries at cutting each letter into pieces. Fewer pieces is better."""

GLYPH_ROWS = 7

FONT = {
    " ": ("...",) * GLYPH_ROWS,
    "A": (".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "B": ("XXXX.", "X...X", "X...X", "XXXX.", "X...X", "X...X", "XXXX."),
    "C": (".XXX.", "X...X", "X....", "X....", "X....", "X...X", ".XXX."),
    "D": ("XXXX.", "X...X", "X...X", "X...X", "X...X", "X...X", "XXXX."),
    "E": ("XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"),
    "F": ("XXXXX", "X....", "XXXX.", "X....", "X....", "X....", "X...."),
    "G": (".XXXX", "X....", "X....", "X.XXX", "X...X", "X...X", ".XXXX"),
    "H": ("X...X", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "I": ("XXX", ".X.", ".X.", ".X.", ".X.", ".X.", "XXX"),
    "J": ("..XXX", "...X.", "...X.", "...X.", "...X.", "X..X.", ".XX.."),
    "K": ("X...X", "X..X.", "X.X..", "XX...", "X.X..", "X..X.", "X...X"),
    "L": ("X....", "X....", "X....", "X....", "X....", "X....", "XXXXX"),
    "M": ("X...X", "XX.XX", "X.X.X", "X.X.X", "X...X", "X...X", "X...X"),
    "N": ("X...X", "XX..X", "XX..X", "X.X.X", "X..XX", "X..XX", "X...X"),
    "O": (".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "P": ("XXXX.", "X...X", "X...X", "XXXX.", "X....", "X....", "X...."),
    "Q": (".XXX.", "X...X", "X...X", "X...X", "X.X.X", "X..X.", ".XX.X"),
    "R": ("XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"),
    "S": (".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."),
    "T": ("XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."),
    "U": ("X...X", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "V": ("X...X", "X...X", "X...X", "X...X", "X...X", ".X.X.", "..X.."),
    "W": ("X...X", "X...X", "X...X", "X.X.X", "X.X.X", "XX.XX", "X...X"),
    "X": ("X...X", "X...X", ".X.X.", "..X..", ".X.X.", "X...X", "X...X"),
    "Y": ("X...X", "X...X", ".X.X.", "..X..", "..X..", "..X..", "..X.."),
    "Z": ("XXXXX", "....X", "...X.", "..X..", ".X...", "X....", "XXXXX"),
    # Lowercase letters have no descender, so every letter sits on the same floor.
    "a": (".....", ".....", ".XXX.", "....X", ".XXXX", "X...X", ".XXXX"),
    "b": ("X....", "X....", "XXXX.", "X...X", "X...X", "X...X", "XXXX."),
    "c": (".....", ".....", ".XXXX", "X....", "X....", "X....", ".XXXX"),
    "d": ("....X", "....X", ".XXXX", "X...X", "X...X", "X...X", ".XXXX"),
    "e": (".....", ".....", ".XXX.", "X...X", "XXXXX", "X....", ".XXXX"),
    # Acute accent in the two rows above the x-height.
    "é": ("...X.", "..X..", ".XXX.", "X...X", "XXXXX", "X....", ".XXXX"),
    "f": ("..XX.", ".X...", "XXXX.", ".X...", ".X...", ".X...", ".X..."),
    "g": (".....", ".....", ".XXXX", "X...X", ".XXXX", "....X", ".XXX."),
    "h": ("X....", "X....", "X.XX.", "XX..X", "X...X", "X...X", "X...X"),
    "i": (".X.", "...", "XX.", ".X.", ".X.", ".X.", "XXX"),
    "j": ("...X.", ".....", "...X.", "...X.", "...X.", "X..X.", ".XX.."),
    "k": ("X....", "X....", "X..X.", "X.X..", "XX...", "X.X..", "X..X."),
    "l": ("XX.", ".X.", ".X.", ".X.", ".X.", ".X.", "XXX"),
    "m": (".....", ".....", "XX.X.", "X.X.X", "X.X.X", "X.X.X", "X.X.X"),
    "n": (".....", ".....", "X.XX.", "XX..X", "X...X", "X...X", "X...X"),
    "o": (".....", ".....", ".XXX.", "X...X", "X...X", "X...X", ".XXX."),
    "p": (".....", ".....", "XXXX.", "X...X", "XXXX.", "X....", "X...."),
    "q": (".....", ".....", ".XXXX", "X...X", ".XXXX", "....X", "....X"),
    "r": (".....", ".....", "X.XX.", "XX..X", "X....", "X....", "X...."),
    "s": (".....", ".....", ".XXXX", "X....", ".XXX.", "....X", "XXXX."),
    "t": (".X...", ".X...", "XXXX.", ".X...", ".X...", ".X..X", "..XX."),
    "u": (".....", ".....", "X...X", "X...X", "X...X", "X...X", ".XXXX"),
    "v": (".....", ".....", "X...X", "X...X", "X...X", ".X.X.", "..X.."),
    "w": (".....", ".....", "X...X", "X...X", "X.X.X", "X.X.X", ".X.X."),
    "x": (".....", ".....", "X...X", ".X.X.", "..X..", ".X.X.", "X...X"),
    "y": (".....", ".....", "X...X", "X...X", ".XXXX", "....X", ".XXX."),
    "z": (".....", ".....", "XXXXX", "...X.", "..X..", ".X...", "XXXXX"),
}

_BUILD = 0
_BLINK = 1
_CLEAR = 2
_DROP = 3
_HOLD = 4


def _neighbors(c):
    x, y = c
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def _around(c):
    """The eight cells around c, the four that share an edge first."""
    x, y = c
    return _neighbors(c) + ((x + 1, y + 1), (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1))


def _text_cells(text):
    """The cells (x, y) of each letter on a grid GLYPH_ROWS tall, and the text width."""
    glyphs = []
    x = 0
    for ch in text:
        rows = FONT[ch]
        cells = []
        for y in range(GLYPH_ROWS):
            for gx, mark in enumerate(rows[y]):
                if mark == "X":
                    cells.append((x + gx, y))
        if cells:
            glyphs.append(cells)
        x += len(rows[0]) + 1
    return glyphs, x - 1


def _greedy_split(cells):
    """Cut cells into pieces of up to four, hardest-to-fill cells first. A piece prefers
    cells that share an edge, but may reach across a corner, so a diagonal stroke can be
    cut into pieces of two."""
    free = set(cells)
    pieces = []
    while free:
        best = None
        best_n = 9
        for c in free:
            n = sum(1 for nb in _around(c) if nb in free)
            if n < best_n or (n == best_n and random.randrange(2)):
                best, best_n = c, n
        piece = [best]
        free.discard(best)
        while len(piece) < 4:
            options = []
            for pc in piece:
                for nb in _neighbors(pc):
                    if nb in free and nb not in options:
                        options.append(nb)
            if not options:
                for pc in piece:
                    for nb in _around(pc)[4:]:
                        if nb in free and nb not in options:
                            options.append(nb)
            if not options:
                break
            nxt = random.choice(options)
            piece.append(nxt)
            free.discard(nxt)
        pieces.append(piece)
    return _absorb_singles(pieces)


def _connected(cells, corners=True):
    """True if the cells touch as one group, by edges only unless corners is True."""
    rest = set(cells)
    stack = [rest.pop()]
    while stack:
        c = stack.pop()
        for nb in (_around(c) if corners else _neighbors(c)):
            if nb in rest:
                rest.discard(nb)
                stack.append(nb)
    return not rest


def _absorb_singles(pieces):
    """Fold lone cells into a piece next to them (edge or corner): join one that has room,
    or take a cell off a bigger one that stays whole without it. A cell with nothing
    around it stays alone."""
    changed = True
    while changed:
        changed = False
        for single in [p for p in pieces if len(p) == 1]:
            cell = single[0]
            for nb in _around(cell):
                other = None
                for q in pieces:
                    if nb in q and q is not single:
                        other = q
                        break
                if other is None:
                    continue
                if len(other) < 4:
                    other.append(cell)
                    pieces.remove(single)
                elif len(other) > 2 and _connected([c for c in other if c != nb]):
                    other.remove(nb)
                    single.append(nb)
                else:
                    continue
                changed = True
                break
            if changed:
                break
    return pieces


def _land_order(pieces):
    """What has to land before what, so every piece can fall straight down.
    Returns a list of sets, [i] being the pieces that must land before piece i,
    or None if the pieces block each other."""
    columns = {}
    for i, piece in enumerate(pieces):
        for x, y in piece:
            columns.setdefault(x, []).append((y, i))
    before = [set() for _ in pieces]
    for col in columns.values():
        for y_a, a in col:
            for y_b, b in col:
                if a != b and y_a > y_b:
                    # b is above a in this column, so a lands first.
                    before[b].add(a)
    # Cycle check: peel off pieces whose requirements are all met.
    done = set()
    while len(done) < len(pieces):
        ready = [i for i in range(len(pieces)) if i not in done and before[i] <= done]
        if not ready:
            return None
        done.update(ready)
    return before


def _split_glyph(cells):
    best = None
    best_score = None
    for _ in range(SPLIT_ATTEMPTS):
        pieces = _greedy_split(cells)
        # Fewest single blocks first, then fewest pieces held together only by a corner
        # (not like a real Tetris piece), then fewest pieces.
        score = (sum(1 for p in pieces if len(p) == 1),
                 sum(1 for p in pieces if not _connected(p, False)),
                 len(pieces))
        if best is not None and score >= best_score:
            continue
        before = _land_order(pieces)
        if before is not None:
            best = (pieces, before)
            best_score = score
    if best is None:
        # Single blocks can always be stacked bottom-up.
        pieces = [[c] for c in cells]
        best = (pieces, _land_order(pieces))
    return best


def _turn(shape):
    """A list of (x, y) cells turned a quarter turn clockwise, moved back to the origin."""
    turned = [(-y, x) for x, y in shape]
    left = min(x for x, _ in turned)
    top = min(y for _, y in turned)
    return sorted((x - left, y - top) for x, y in turned)


class _Flier:
    """A piece on its way down: turned some way, in its own columns, at some row."""

    def __init__(self, plan, i, lane, side):
        cells = plan.pieces[i]
        self.plan = plan
        self.i = i
        self.lane = lane
        self.side = side
        self.left = min(x for x, _ in cells)
        self.top = min(y for _, y in cells)
        final = sorted((x - self.left, y - self.top) for x, y in cells)
        self.width = max(x for x, _ in final) + 1
        self.shapes = [final]  # shapes[n]: the landed shape turned n quarter turns clockwise
        for _ in range(3):
            self.shapes.append(_turn(self.shapes[-1]))
        self.rot = random.randint(1, 3)
        self.way = random.choice((1, -1))  # which way to turn when both ways are as short
        self.row = plan.spawn_row
        self.landed = False
        self.cells = self._place()

    def upright(self):
        return self.shapes[self.rot] == self.shapes[0]

    def _place(self):
        """Cells at the current turn and row, centred over the landing spot but kept
        inside this piece's lane."""
        shape = self.shapes[self.rot]
        w = max(x for x, _ in shape) + 1
        x = max(self.lane[0], min(self.left + (self.width - w) // 2, self.lane[1] - w))
        return [(x + sx, self.row + sy) for sx, sy in shape]

    def _auto_turn(self):
        """+1 or -1: the shorter way round to the right way up."""
        cw = 1
        while self.shapes[(self.rot + cw) % 4] != self.shapes[0]:
            cw += 1
        ccw = 1
        while self.shapes[(self.rot - ccw) % 4] != self.shapes[0]:
            ccw += 1
        if cw == ccw:
            return self.way
        return 1 if cw < ccw else -1

    def step(self, turns, auto_row):
        """Move on one refresh. A piece that is the right way up drops into place;
        otherwise it falls FALL_ROWS, turned by the player's presses if it is still above
        auto_row, and by itself once it has fallen to auto_row."""
        if self.upright():
            self.landed = True
            self.cells = list(self.plan.pieces[self.i])
            return
        if turns and self.row < auto_row:
            self.rot = (self.rot + turns) % 4
        self.row = min(self.row + FALL_ROWS, self.top)
        if not self.upright() and self.row >= auto_row:
            self.rot = (self.rot + self._auto_turn()) % 4
        self.cells = self._place()


class _Banner:
    """A red band across the top, with the lettering image drawn over it."""

    def __init__(self):
        self.height = display_height * BANNER_PCT // 100

    def draw(self):
        """Red band in the red plane, then the white lettering on top of it. If the
        image cannot be drawn the band stays plain red."""
        if not self.height:
            return
        display.red.fill_rect(0, 0, display_width, self.height, 1)
        try:
            display.draw_png(BANNER_IMAGE, 0, 0)
        except Exception as ex:  # missing file, not a palette PNG, ...
            print("Banner image not drawn:", ex)


class _Plan:
    """A name cut into pieces, with the pixel layout to draw it."""

    def __init__(self, text):
        glyphs, cols = _text_cells(text)
        self.cell = max(2, min(MAX_CELL, display_width // (cols + 2)))
        self.gap = max(1, self.cell // 8)
        self.x0 = (display_width - cols * self.cell) // 2
        self.field_rows = (display_height - BOTTOM_MARGIN) // self.cell
        self.y_grid = self.field_rows - GLYPH_ROWS
        # First grid row below the banner; pieces in the air stay under it.
        self.top_row = (display_height * BANNER_PCT // 100 + self.cell - 1) // self.cell
        # Row where every piece appears, and how far sideways a turned piece may reach.
        self.spawn_row = max(self.top_row,
                             (display_height * SPAWN_PCT // 100 + self.cell - 1) // self.cell)
        self.min_x = -(self.x0 // self.cell)
        self.max_x = (display_width - self.x0) // self.cell
        self.pieces = []
        self.before = []
        for cells in glyphs:
            pieces, before = _split_glyph(cells)
            base = len(self.pieces)
            for piece in pieces:
                self.pieces.append([(x, y + self.y_grid) for x, y in piece])
            for req in before:
                self.before.append(set(base + r for r in req))

    def land_sequence(self):
        """A random order the pieces could land in."""
        done = set()
        order = []
        while len(order) < len(self.pieces):
            ready = [i for i in range(len(self.pieces))
                     if i not in done and self.before[i] <= done]
            pick = random.choice(ready)
            done.add(pick)
            order.append(pick)
        return order

    def span(self, i):
        """Columns piece i covers when landed: (first, one past the last)."""
        xs = [x for x, _ in self.pieces[i]]
        return min(xs), max(xs) + 1

    def reach(self, i):
        """The most columns piece i covers in any orientation."""
        xs = [x for x, _ in self.pieces[i]]
        ys = [y for _, y in self.pieces[i]]
        return max(max(xs) - min(xs), max(ys) - min(ys)) + 1

    def lanes(self, wave):
        """The columns (first, one past the last) each piece of a wave of one or two may
        use while it is in the air, left piece first. None if they cannot be kept apart."""
        if len(wave) == 1:
            return [(self.min_x, self.max_x)]
        (a0, a1), (b0, b1) = self.span(wave[0]), self.span(wave[1])
        low = max(a1, self.min_x + self.reach(wave[0]))
        high = min(b0, self.max_x - self.reach(wave[1]))
        if low > high:
            return None
        edge = (low + high) // 2
        return [(self.min_x, edge), (edge, self.max_x)]

    def land_waves(self, at_most):
        """The pieces grouped into waves of one or two that fall together, each a list
        of piece numbers, left piece first. A wave only takes pieces whose supports have
        already landed, so none waits on another, and two share a wave only if they can
        be kept in separate columns on the way down."""
        done = set()
        waves = []
        while len(done) < len(self.pieces):
            ready = [i for i in range(len(self.pieces))
                     if i not in done and self.before[i] <= done]
            first = random.choice(ready)
            ready.remove(first)
            wave = [first]
            while at_most >= 2 and ready:
                other = random.choice(ready)
                ready.remove(other)
                pair = sorted((first, other), key=lambda i: self.span(i)[0])
                if self.span(pair[0])[1] <= self.span(pair[1])[0] and self.lanes(pair):
                    wave = pair
                    break
            done.update(wave)
            waves.append(wave)
        return waves

    def draw_cells(self, plane, cells):
        size = self.cell - self.gap
        for x, y in cells:
            plane.fill_rect(self.x0 + x * self.cell, y * self.cell, size, size, 1)

    def draw_piece(self, plane, cells, rows_up=0):
        size = self.cell - self.gap
        for x, y in cells:
            plane.fill_rect(self.x0 + x * self.cell,
                            (y - rows_up) * self.cell, size, size, 1)

    def flight_heights(self, batch):
        """How far above its landing spot each piece in the air is drawn."""
        taken = set()
        heights = {}
        for i in batch:
            top = min(y for _, y in self.pieces[i]) - self.top_row
            start = random.randint(min(FLIGHT_MIN_ROWS, top), top)
            # Nudge up to clear other pieces in the air, or down if out of room.
            up = start
            for cand in list(range(start, top + 1)) + list(range(start - 1, 0, -1)):
                if not any((x, y - cand) in taken for x, y in self.pieces[i]):
                    up = cand
                    break
            for x, y in self.pieces[i]:
                taken.add((x, y - up))
            heights[i] = up
        return heights


class TetrisTagApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Tetris Tag"
        self.sleep_ms = 25
        self.launched = False
        self.plans = {}
        self.word = 0
        self.state = _BUILD
        self.fast = False
        self.plan = None
        self.banner = None
        self.screen_ready = False
        self.order = []
        self.batch_size = 1
        self.frame = 0
        self.frames = 0
        self.waves = []
        self.batch = 0
        self.landed = []
        self.blink_step = 0
        self.blink_ms = 0
        self.hold_ms = 0
        self.frame_ms = 0
        self.fliers = []  # the pieces of the current wave in the air
        self.taps = [0, 0]  # presses of User A and User B since the last refresh

    def _start_word(self, idx):
        self.word = idx % len(NAMES)
        plan = self.plans.get(self.word)
        if plan is None:
            gc.collect()  # planning allocates a lot; start from a clean heap
            plan = self.plans[self.word] = _Plan(NAMES[self.word])
        self.plan = plan
        if self.banner is None:
            self.banner = _Banner()
        self.screen_ready = False  # the next frame starts from a full clear and banner
        self.fast = FAST and getattr(display, "supports_fast", False)
        self.frame_ms = 0
        if self.fast:
            self.waves = plan.land_waves(max(1, PIECES_AT_ONCE))
            self.order = [i for wave in self.waves for i in wave]
            self.batch = 0
            self.frame = 0
            self.landed = []
            self.state = _CLEAR
            print(f"{NAMES[self.word]}: {len(self.order)} pieces in {len(self.waves)} waves, "
                  f"cell {plan.cell}px")
            return
        self.order = plan.land_sequence()
        count = len(self.order)
        self.batch_size = max(1, (count + TARGET_FRAMES - 1) // TARGET_FRAMES)
        # One snapshot per batch in the air, then one with everything landed.
        self.frames = (count + self.batch_size - 1) // self.batch_size + 1
        self.frame = 0
        self.state = _BUILD
        print(f"{NAMES[self.word]}: {count} pieces, {self.frames} frames, cell {plan.cell}px")

    def _new_frame(self):
        """Blank canvas for the next frame. The banner is drawn once per name and then
        left where it is: redrawing it would decode the image for every frame."""
        if not self.screen_ready:
            display.clear()
            self.banner.draw()
            self.screen_ready = True
            return
        top = self.banner.height
        display.black.fill_rect(0, top, display_width, display_height - top, 0)
        display.red.fill_rect(0, top, display_width, display_height - top, 0)

    def _show(self, landed, in_air=()):
        """Slideshow: queue a snapshot, landed pieces in black, in the air in red."""
        plan = self.plan
        self._new_frame()
        for i in landed:
            plan.draw_piece(display.black, plan.pieces[i])
        heights = plan.flight_heights(in_air)
        for i in in_air:
            plan.draw_piece(display.red, plan.pieces[i], heights[i])
        display.update()
        self.frame_ms = time.ticks_ms()

    def _show_frame(self):
        landed = self.order[:self.frame * self.batch_size]
        in_air = self.order[len(landed):len(landed) + self.batch_size]
        self._show(landed, in_air)
        self.frame += 1

    def _show_fast(self, moving, pulls):
        """Partial refresh: landed pieces and the ones in the air, all in
        black. moving is a list of cell lists, one per piece in the air."""
        plan = self.plan
        self._new_frame()
        for i in self.landed:
            plan.draw_piece(display.black, plan.pieces[i])
        for cells in moving:
            plan.draw_cells(display.black, cells)
        display.update(fast=True, pulls=pulls)
        self.frame_ms = time.ticks_ms()

    def _drop_frame(self):
        """One partial refresh of the current wave. The first one shows the pieces at
        the spawn height, turned a random way. After that each piece that is not yet the
        right way up falls a few rows, turned by its player if they pressed a button,
        or by itself once it is low enough. A piece that is the right way up drops into
        place."""
        wave = self.waves[self.batch]
        landing = False  # a piece drops into place in this refresh
        if self.frame == 0:
            lanes = self.plan.lanes(wave)
            self.fliers = [_Flier(self.plan, i, lanes[n], n) for n, i in enumerate(wave)]
            self.taps = [0, 0]
        else:
            auto_row = display_height * AUTO_TURN_PCT // 100 // self.plan.cell
            for f in self.fliers:
                if len(self.fliers) == 1:
                    turns = self.taps[0] + self.taps[1]
                else:
                    turns = self.taps[f.side]
                was = f.landed
                f.step(turns % 4, auto_row)
                landing = landing or (f.landed and not was)
            self.taps = [0, 0]
        pulls = SETTLE_PULLS if landing else HOP_PULLS
        self._show_fast([f.cells for f in self.fliers], pulls)
        if all(f.landed for f in self.fliers):
            self.landed.extend(wave)
            self.batch += 1
            self.frame = 0
        else:
            self.frame += 1

    def _start_blink(self):
        self.state = _BLINK
        self.blink_step = 0
        self.blink_ms = time.ticks_ms()
        if BLINK_MODE == "leds":
            leds.fill(BLINK_COLOR)

    def _start_hold(self):
        """The name stays up, untouched, for HOLD_S seconds."""
        self.state = _HOLD
        self.hold_ms = time.ticks_ms()
        print(f"Holding {NAMES[self.word]} for {HOLD_S} s")

    def _blink_leds(self):
        if time.ticks_diff(time.ticks_ms(), self.blink_ms) < BLINK_MS:
            return
        self.blink_ms = time.ticks_add(self.blink_ms, BLINK_MS)
        self.blink_step += 1
        if self.blink_step >= 2 * BLINK_COUNT:
            leds.fill((0, 0, 0))
            self._start_hold()
        else:
            leds.fill(BLINK_COLOR if self.blink_step % 2 == 0 else (0, 0, 0))

    def _blink_screen(self):
        # The name is up. Alternate blank and name, once per finished refresh.
        if self.blink_step >= 2 * BLINK_COUNT:
            self._start_hold()
            return
        shown = self.order if self.blink_step % 2 else []
        if self.fast and not display.supports_fast:
            self.fast = False
        if self.fast:
            self.landed = shown
            self._show_fast([], BLINK_PULLS)
        else:
            self._show(shown)
        self.blink_step += 1

    def run(self):
        if self.launched:
            leds.fill((0, 0, 0))
            self._start_word(0)
            return

        if self.state == _DROP and self.fast:
            # Presses wait for the next refresh, which turns the piece.
            if UserA.pressed():
                self.taps[0] += 1
                print("User A: turn left piece")
            if UserB.pressed():
                self.taps[1] += 1
                print("User B: turn right piece")
        elif UserA.pressed():
            leds.fill((0, 0, 0))
            self._start_word(self.word + 1)
        elif UserB.pressed():
            leds.fill((0, 0, 0))
            self._start_word(self.word)

        # A refresh in progress owns the framebuffers.
        if not display.is_done_updating():
            return

        if self.frame_ms:
            print("refresh ms:", time.ticks_diff(time.ticks_ms(), self.frame_ms))
            self.frame_ms = 0

        if self.state == _CLEAR:
            # The one full refresh per name: wipes the last name and any ghosts,
            # and draws the red banner, which the fast frames then hold.
            self.screen_ready = False
            self._new_frame()
            display.update()
            self.frame_ms = time.ticks_ms()
            self.state = _DROP
        elif self.state == _DROP:
            if not display.supports_fast:
                # The driver gave up on fast refresh after repeated failures.
                print("Fast refresh stopped working, using the slideshow")
                self._start_word(self.word)
            elif self.batch < len(self.waves):
                self._drop_frame()
            else:
                self._start_blink()
        elif self.state == _BUILD:
            if self.frame < self.frames:
                self._show_frame()
            else:
                self._start_blink()
        elif self.state == _HOLD:
            if time.ticks_diff(time.ticks_ms(), self.hold_ms) >= HOLD_S * 1000:
                self._start_word(self.word + 1)
        elif BLINK_MODE == "leds":
            self._blink_leds()
        else:
            self._blink_screen()
