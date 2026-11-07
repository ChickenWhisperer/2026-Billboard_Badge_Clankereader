"""Conway's Game of Life. Live cells are black; cells that die next generation are red.

User A: change cell size (restarts with a new random board)
User B: new random board
Boot:   pause / resume
"""

import random
import time

from apps.base_app import BaseApp
from lib.button import UserA, UserB, Boot
from lib.epd import display, display_width, display_height
from lib.led_bar import leds

CELL_SIZES = (4, 6, 8, 12, 16, 24)
DEFAULT_SIZE_IDX = 2
MIN_STEP_MS = 1000
"""Never step faster than this, even if the display finishes sooner."""
FILL_PERCENT = 25


class LifeApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Life"
        self.sleep_ms = 50
        self.launched = False
        self.size_idx = DEFAULT_SIZE_IDX
        self.paused = False
        self.last_draw_ms = 0
        self.prev = None
        self.prev2 = None
        self.nxt = None
        self._setup_grid()

    def _setup_grid(self):
        self.cell = CELL_SIZES[self.size_idx]
        self.cols = display_width // self.cell
        self.rows = display_height // self.cell
        # Center the grid on the screen
        self.x0 = (display_width - self.cols * self.cell) // 2
        self.y0 = (display_height - self.rows * self.cell) // 2
        # Leave a 1px gap between cells when they are big enough to afford it
        self.draw_size = self.cell - 1 if self.cell >= 6 else self.cell
        self.grid = bytearray(self.cols * self.rows)
        self.prev = None
        self.prev2 = None
        self.nxt = None

    def _randomize(self):
        g = self.grid
        for i in range(len(g)):
            g[i] = 1 if random.randrange(100) < FILL_PERCENT else 0
        self.prev = None
        self.prev2 = None
        self.nxt = self._compute_next(g)

    def _compute_next(self, g):
        cols, rows = self.cols, self.rows
        new = bytearray(cols * rows)
        for y in range(rows):
            up = ((y - 1) % rows) * cols
            mid = y * cols
            dn = ((y + 1) % rows) * cols
            for x in range(cols):
                xl = x - 1 if x else cols - 1
                xr = x + 1 if x < cols - 1 else 0
                n = (g[up + xl] + g[up + x] + g[up + xr]
                     + g[mid + xl] + g[mid + xr]
                     + g[dn + xl] + g[dn + x] + g[dn + xr])
                if n == 3 or (n == 2 and g[mid + x]):
                    new[mid + x] = 1
        return new

    def _step(self):
        self.prev2 = self.prev
        self.prev = self.grid
        self.grid = self.nxt
        self.nxt = self._compute_next(self.grid)

    def _stagnant(self):
        """Board is frozen or oscillating with period 2, so it is boring now."""
        return self.grid == self.prev or self.grid == self.prev2

    def _draw(self):
        display.clear()
        black = display.black
        red = display.red
        cols, cell, ds = self.cols, self.cell, self.draw_size
        g = self.grid
        nxt = self.nxt
        for y in range(self.rows):
            row = y * cols
            py = self.y0 + y * cell
            for x in range(cols):
                if g[row + x]:
                    target = black if nxt[row + x] else red
                    target.fill_rect(self.x0 + x * cell, py, ds, ds, 1)
        display.update()
        self.last_draw_ms = time.ticks_ms()

    def _display_busy(self):
        # _framebuffer_ready stays set from update() until the refresh finishes,
        # covering the gap before display.run() picks the frame up.
        return display._framebuffer_ready.is_set() or not display.is_done_updating()

    def _restart(self):
        self._setup_grid()
        self._randomize()
        self._draw()

    def run(self):
        if self.launched:
            leds.fill((0, 0, 0))
            self.paused = False
            self._restart()
            return

        if UserA.pressed():
            self.size_idx = (self.size_idx + 1) % len(CELL_SIZES)
            print(f"Life cell size: {CELL_SIZES[self.size_idx]}")
            if not self._display_busy():
                self._restart()
            else:
                self._setup_grid()
                self._randomize()
                self.last_draw_ms = 0  # draw as soon as the display frees up
            return
        if UserB.pressed():
            self._randomize()
            self.last_draw_ms = 0
            return
        if Boot.pressed():
            self.paused = not self.paused
            return

        if self._display_busy():
            return
        if time.ticks_diff(time.ticks_ms(), self.last_draw_ms) < MIN_STEP_MS:
            return

        if self.last_draw_ms == 0:
            # A new board is waiting to be shown
            self._draw()
            return
        if self.paused:
            return

        self._step()
        if self._stagnant():
            self._randomize()
        self._draw()
