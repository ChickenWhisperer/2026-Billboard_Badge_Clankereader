"""Snake for the 2026 Supercon Billboard Badge, drawn with the partial-refresh
driver from PR #33 (lib/epd_partial.py), the same one Pong uses.

Copy to /apps/snake.py. Needs /lib/epd_partial.py on the badge.

Controls (relative to the way the snake is heading):
  User A - turn left
  User B - turn right
  Menu +/- - back to the menu
On launch the badge plays a demo game until you press A or B.

The snake moves one cell per screen frame (about 0.65 s on the B1 panel).
Press during a frame and the turn is applied on the next move; up to two
turns can be queued, and pressing the opposite button cancels the last one.

Screen: black and white only, with a header showing the score and the high
score (saved in /data/snake.json). If the last app left red on the screen,
Snake starts with one slow full refresh, then switches to fast frames.

LEDs:
  Demo       - a rainbow snake crawls along the bar
  Play       - green dot = the snake's head column, gold dot = the food column;
               cyan blink at the left/right end = a queued left/right turn
  Danger     - the whole bar pulses red if the next move would crash
  Food eaten - a gold burst from the middle
  Game over  - red strobe, then a slow red glow (rainbow for a new high score)
"""

import asyncio as aio
import gc
import json
import random
import sys
import time

import framebuf

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd_partial import PartialEPD, SESSION_PULLS
from lib.led_bar import leds, NUM_LEDS

# ---- Layout ---------------------------------------------------------------
W = 960
H = 672
HEADER_H = 72
CELL = 40                              # byte-aligned: 5 bytes per cell
COLS = W // CELL                       # 24
ROWS = (H - HEADER_H) // CELL          # 15
FIELD_Y = H - ROWS * CELL              # 72
TEXT_SCALE = 4                         # 8x8 font scaled to 32 px

# ---- Tunables -------------------------------------------------------------
STEP_MS = 40                 # run() rate: input polling and LED animation
MIN_MOVE_MS = 0              # slow the game down below the panel's frame rate
START_LEN = 4
MAX_QUEUED_TURNS = 2
DEMO_IDLE_MS = 10 * 60 * 1000  # demo pauses (panel powered down) after this idle
OVER_TO_DEMO_MS = 30000        # game-over screen returns to the demo after this
HISCORE_FILE = "/data/snake.json"
LED_ON = 24                  # peak LED brightness, 0-255 (same as the nametag)

# Directions: 0 right, 1 down, 2 left, 3 up
DX = (1, 0, -1, 0)
DY = (0, 1, 0, -1)

DEMO, PLAY, OVER, IDLE = 0, 1, 2, 3


# ---- Text (same approach as Pong: cached, scaled labels) -------------------

_labels = {}


def _label(text, scale):
    key = (text, scale)
    lab = _labels.get(key)
    if lab is None:
        if len(_labels) > 24:       # scores change; don't let the cache grow forever
            _labels.clear()
        w = 8 * len(text)
        small = framebuf.FrameBuffer(bytearray(w), w, 8, framebuf.MONO_HLSB)
        small.text(text, 0, 0, 1)
        bw, bh = w * scale, 8 * scale
        big = framebuf.FrameBuffer(bytearray((bw + 7) // 8 * bh), bw, bh, framebuf.MONO_HLSB)
        for y in range(8):
            for x in range(w):
                if small.pixel(x, y):
                    big.fill_rect(x * scale, y * scale, scale, scale, 1)
        lab = _labels[key] = (big, bw, bh)
    return lab


def _text(fb, text, scale, x, y):
    """Black text at x, y (top-left); x None centres it. Returns the width."""
    big, w, h = _label(text, scale)
    if x is None:
        x = (W - w) // 2
    fb.blit(big, x, y, 0)
    return w


def _text_w(text, scale):
    return 8 * scale * len(text)


def _hsv(h, v):
    """Hue 0-360, full saturation, value 0-255 -> (r, g, b)."""
    h = (h % 360) / 60
    i = int(h)
    f = h - i
    q = int(v * (1 - f))
    t = int(v * f)
    v = int(v)
    return ((v, t, 0), (q, v, 0), (0, v, t), (0, q, v), (t, 0, v), (v, 0, q))[i % 6]


def _dim(rgb, k):
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


# ---- Game rules -------------------------------------------------------------

class Game:
    def __init__(self) -> None:
        self.hiscore = self._load_hi()
        self.mode = DEMO
        self.new_hi = False
        self.reset()

    # persistence
    def _load_hi(self):
        try:
            with open(HISCORE_FILE) as f:
                return int(json.load(f).get("hiscore", 0))
        except Exception:
            return 0

    def _save_hi(self):
        try:
            with open(HISCORE_FILE, "w") as f:
                json.dump({"hiscore": self.hiscore}, f)
        except Exception as ex:
            print("Snake: could not save high score:", ex)

    def reset(self):
        cy = ROWS // 2
        cx = COLS // 2 - START_LEN
        self.body = [(cx + i, cy) for i in range(START_LEN)]   # tail ... head
        self.occ = set(x + y * COLS for x, y in self.body)
        self.dir = 0
        self.score = 0
        self.turns = []          # queued relative turns: "L" / "R"
        self.spawn_food()

    def spawn_food(self):
        if len(self.body) >= COLS * ROWS:
            self.food = None
            return
        for _ in range(60):
            x, y = random.randint(0, COLS - 1), random.randint(0, ROWS - 1)
            if x + y * COLS not in self.occ:
                self.food = (x, y)
                return
        for i in range(COLS * ROWS):       # crowded board: first free cell
            if i not in self.occ:
                self.food = (i % COLS, i // COLS)
                return

    def free(self, x, y):
        """Can the head move into (x, y) on the next step?"""
        if not (0 <= x < COLS and 0 <= y < ROWS):
            return False
        idx = x + y * COLS
        if idx not in self.occ:
            return True
        tx, ty = self.body[0]               # the tail moves away unless we eat
        return idx == tx + ty * COLS and self.food != (x, y)

    @staticmethod
    def turned(d, t):
        return (d + 3) % 4 if t == "L" else (d + 1) % 4

    def next_dir(self):
        return self.turned(self.dir, self.turns[0]) if self.turns else self.dir

    def danger(self):
        hx, hy = self.body[-1]
        d = self.next_dir()
        return not self.free(hx + DX[d], hy + DY[d])

    def queue_turn(self, left):
        t = "L" if left else "R"
        if self.turns and self.turns[-1] != t:
            self.turns.pop()                # opposite press cancels the last turn
        elif len(self.turns) < MAX_QUEUED_TURNS:
            self.turns.append(t)

    def step(self):
        """Move one cell. Returns 'ok', 'eat' or 'dead'."""
        if self.turns:
            self.dir = self.turned(self.dir, self.turns.pop(0))
        hx, hy = self.body[-1]
        nx, ny = hx + DX[self.dir], hy + DY[self.dir]
        if not self.free(nx, ny):
            return "dead"
        eat = self.food == (nx, ny)
        if not eat:
            tx, ty = self.body.pop(0)
            self.occ.discard(tx + ty * COLS)
        self.body.append((nx, ny))
        self.occ.add(nx + ny * COLS)
        if eat:
            self.score += 1
            self.spawn_food()
            return "eat"
        return "ok"

    def game_over(self):
        self.mode = OVER
        self.new_hi = self.score > self.hiscore
        if self.new_hi:
            self.hiscore = self.score
            self._save_hi()

    # demo player
    def _space(self, x, y, limit):
        """Free cells reachable from (x, y), counted up to `limit`."""
        start = x + y * COLS
        seen = {start}
        todo = [start]
        while todo and len(seen) < limit:
            i = todo.pop()
            cx, cy = i % COLS, i // COLS
            for d in range(4):
                nx, ny = cx + DX[d], cy + DY[d]
                if 0 <= nx < COLS and 0 <= ny < ROWS:
                    j = nx + ny * COLS
                    if j not in seen and j not in self.occ:
                        seen.add(j)
                        todo.append(j)
        return len(seen)

    def ai(self):
        """Chase the food, but never into a pocket too small to live in."""
        hx, hy = self.body[-1]
        fx, fy = self.food if self.food else (hx, hy)
        need = len(self.body) + 4
        best, best_score = self.dir, None
        for d in (self.dir, (self.dir + 3) % 4, (self.dir + 1) % 4):
            nx, ny = hx + DX[d], hy + DY[d]
            if not self.free(nx, ny):
                continue
            room = self._space(nx, ny, need)
            score = min(room, need) * 100 - (abs(fx - nx) + abs(fy - ny)) * 10
            score += random.randint(0, 6) + (3 if d == self.dir else 0)
            if best_score is None or score > best_score:
                best, best_score = d, score
        self.dir = best

    # ---- drawing ----------------------------------------------------------

    def draw(self, fb):
        """Draw everything into fb, which starts out white (1 = black)."""
        # Header: black text, a solid rule underneath
        ty = (HEADER_H - 8 * TEXT_SCALE) // 2 - 2
        _text(fb, "SNAKE", TEXT_SCALE, 16, ty)
        hi = "HI %d" % self.hiscore
        _text(fb, hi, TEXT_SCALE, W - 16 - _text_w(hi, TEXT_SCALE), ty)
        if self.mode in (DEMO, IDLE):
            _text(fb, "DEMO  A/B:PLAY", TEXT_SCALE, None, ty)
        else:
            _text(fb, "SCORE %d" % self.score, TEXT_SCALE, None, ty)

        # Walls: a frame around the field
        for k in range(4):
            fb.rect(k, FIELD_Y + k, W - 2 * k, ROWS * CELL - 2 * k, 1)

        # Food: a ring
        if self.food:
            px, py = self.food[0] * CELL, FIELD_Y + self.food[1] * CELL
            fb.fill_rect(px + 8, py + 8, CELL - 16, CELL - 16, 1)
            fb.fill_rect(px + 14, py + 14, CELL - 28, CELL - 28, 0)

        # Snake: a block per cell, bridged to the next segment
        g = 4
        prev = None
        for x, y in self.body:
            px, py = x * CELL, FIELD_Y + y * CELL
            fb.fill_rect(px + g, py + g, CELL - 2 * g, CELL - 2 * g, 1)
            if prev is not None:
                ox, oy = prev
                fb.fill_rect(min(ox, x) * CELL + g, FIELD_Y + min(oy, y) * CELL + g,
                             (abs(ox - x) + 1) * CELL - 2 * g,
                             (abs(oy - y) + 1) * CELL - 2 * g, 1)
            prev = (x, y)

        # Eyes on the head, facing the direction of travel
        hx, hy = self.body[-1]
        cx, cy = hx * CELL + CELL // 2, FIELD_Y + hy * CELL + CELL // 2
        fx, fy = DX[self.dir] * 8, DY[self.dir] * 8
        sx, sy = -DY[self.dir] * 8, DX[self.dir] * 8
        for s in (1, -1):
            fb.fill_rect(cx + fx + s * sx - 3, cy + fy + s * sy - 3, 6, 6, 0)

        # Banners
        if self.mode == OVER:
            lines = [(7, "GAME OVER"), (4, "SCORE %d" % self.score)]
            if self.new_hi:
                lines.append((4, "NEW HIGH SCORE!"))
            lines.append((3, "A/B: PLAY AGAIN"))
            self._banner(fb, lines)
        elif self.mode == IDLE:
            self._banner(fb, [(5, "PRESS A OR B"), (5, "TO PLAY")])

    def draw_start_hint(self, fb):
        """Controls reminder, above the snake's starting row."""
        self._banner(fb, [(4, "A: TURN LEFT"), (4, "B: TURN RIGHT")], top=40)

    def _banner(self, fb, lines, top=None):
        h = sum(8 * s + 16 for s, _ in lines) + 32
        w = max(_text_w(t, s) for s, t in lines) + 80
        x = (W - w) // 2
        y = FIELD_Y + (ROWS * CELL - h) // 2 if top is None else FIELD_Y + top
        fb.fill_rect(x, y, w, h, 0)
        for k in range(6):
            fb.rect(x + k, y + k, w - 2 * k, h - 2 * k, 1)
        y += 24
        for s, t in lines:
            _text(fb, t, s, None, y)
            y += 8 * s + 16


# ---- The app ---------------------------------------------------------------

class SnakeApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        super().__init__("Snake")
        self.name = "Snake"
        self.sleep_ms = STEP_MS
        self.show_battery_icon = False
        self.game = Game()
        self.panel = None
        self.task = None
        self.beat = 0
        self.failed = False
        self._mode_t0 = time.ticks_ms()
        self._last_input = self._mode_t0
        self._start_req = False     # a button asked for a new game
        self._hint = False          # show the controls on the first frame of a game
        self._wipe = False          # clear ghosts before the next frame
        self._flash = None          # (kind, until_ms)
        self._t = 0

    # ---- run(): input and LEDs, every STEP_MS ------------------------------

    def run(self) -> None:
        now = time.ticks_ms()
        self.beat = now
        g = self.game

        if self.launched:
            UserA.pressed()          # drop the press that opened the app
            UserB.pressed()
            self.failed = False
            if self.task is None or self.task.done():
                g.mode = DEMO
                g.reset()
                self._mode_t0 = self._last_input = now
        if not self.failed and (self.task is None or self.task.done()):
            self.task = aio.create_task(self._screen())

        a = UserA.pressed()
        b = UserB.pressed()
        if a or b:
            self._last_input = now
            if g.mode == PLAY:
                if a:
                    g.queue_turn(True)
                if b:
                    g.queue_turn(False)
            else:
                self._start_req = True

        self._leds(now)

    def _alive(self):
        # run() stops being called once Menu is pressed (same test as Pong).
        return time.ticks_diff(time.ticks_ms(), self.beat) < 1500

    # ---- the screen task: game steps and frames ----------------------------

    def _advance(self, now):
        """One game step, done just before each frame is drawn."""
        g = self.game
        if self._start_req:
            self._start_req = False
            g.reset()
            g.mode = PLAY
            g.new_hi = False
            self._mode_t0 = now
            self._hint = True
            self._wipe = True       # fresh game: shake off the demo's ghosts
            return
        if g.mode == DEMO:
            if time.ticks_diff(now, self._last_input) > DEMO_IDLE_MS:
                g.mode = IDLE
                return
            g.ai()
            if g.step() == "dead":
                g.reset()
        elif g.mode == PLAY:
            r = g.step()
            if r == "dead":
                g.game_over()
                self._mode_t0 = now
                self._flash = ("newhi" if g.new_hi else "dead",
                               time.ticks_add(now, 4000 if g.new_hi else 1500))
            elif r == "eat":
                self._flash = ("eat", time.ticks_add(now, 900))
        elif g.mode == OVER:
            if time.ticks_diff(now, self._mode_t0) > OVER_TO_DEMO_MS:
                g.mode = DEMO
                g.reset()
                self._last_input = now
                self._wipe = True
        elif g.mode == IDLE:
            pass                    # waits for a press (handled by _start_req)

    async def _screen(self):
        """Owns the panel while the app runs: cleans it, then draws frames."""
        if self.panel is None:
            self.panel = PartialEPD()
        panel, g = self.panel, self.game
        frames = total = 0
        try:
            await panel.start()            # slow full refresh if red is on screen
            gc.collect()
            last = time.ticks_ms()
            while self._alive():
                now = time.ticks_ms()
                if MIN_MOVE_MS and time.ticks_diff(now, last) < MIN_MOVE_MS:
                    await aio.sleep_ms(10)
                    continue

                self._advance(now)
                if self._wipe:
                    self._wipe = False
                    await panel.restart(wipe=True)   # ~3.5 s, at game start only
                    gc.collect()

                panel.clear()
                g.draw(panel.fb)
                if self._hint:
                    self._hint = False
                    g.draw_start_hint(panel.fb)

                shown = await panel.show()
                if not shown:                      # nothing changed (idle, game over)
                    if g.mode in (IDLE, OVER):
                        await panel.sleep()        # power down while it sits still
                    await aio.sleep_ms(100)
                    last = time.ticks_ms()
                    continue

                now = time.ticks_ms()
                total += time.ticks_diff(now, last)
                last = now
                frames += 1
                if frames == 20:
                    print("Snake: %d ms a frame (refresh %d ms, sending %d ms)"
                          % (total // frames, panel.last_pull_ms, panel.last_send_ms))
                    frames = total = 0
                # Reopen the session in a calm moment rather than mid-move
                if g.mode != PLAY and panel.pulls > SESSION_PULLS // 2:
                    await panel.restart()
                    last = time.ticks_ms()
        except Exception as ex:
            print("Snake: screen stopped:", ex)
            sys.print_exception(ex)
            self.failed = True
        finally:
            await panel.stop()

    # ---- LEDs --------------------------------------------------------------

    def _leds(self, now):
        self._t += 1
        t = self._t
        n = NUM_LEDS
        g = self.game
        B = LED_ON
        px = [(0, 0, 0)] * n

        if self.failed:
            px = [(B, 0, 0)] * n
        else:
            flash = None
            if self._flash is not None:
                if time.ticks_diff(self._flash[1], now) > 0:
                    flash = self._flash[0]
                else:
                    self._flash = None

            if flash == "eat":
                r = (t % 12) / 2
                for i in range(n):
                    d = abs(i - (n - 1) / 2)
                    if d <= r:
                        px[i] = _dim((B, int(B * 0.7), 0), max(0.2, 1 - (r - d) / 3))
            elif flash == "newhi":
                for i in range(n):
                    px[i] = _hsv(t * 18 + i * 50, B)
            elif flash == "dead":
                px = [(B, 0, 0) if (t // 3) % 2 else (0, 0, 0)] * n
            elif g.mode in (DEMO, IDLE):
                speed = 3 if g.mode == DEMO else 6
                head = (t // speed) % (n + 4)
                for k in range(4):
                    i = head - k
                    if 0 <= i < n:
                        px[i] = _hsv(t * 2 + k * 25, B * (1.0, 0.45, 0.2, 0.08)[k])
            elif g.mode == PLAY:
                if g.danger():
                    k = 0.35 + 0.65 * ((t % 10) / 9)
                    px = [_dim((B, 0, 0), k)] * n
                else:
                    if g.food:
                        fi = min(n - 1, g.food[0] * n // COLS)
                        px[fi] = (B, int(B * 0.6), 0)
                    hi = min(n - 1, g.body[-1][0] * n // COLS)
                    px[hi] = (0, B, 0)
                on = (t // 4) % 2 == 0
                for q, turn in enumerate(g.turns):
                    i = q if turn == "L" else n - 1 - q
                    px[i] = (0, B, B) if on else (0, B // 4, B // 4)
            elif g.mode == OVER:
                k = 0.2 + 0.8 * abs(((t % 50) / 25) - 1)
                px = [_dim((B, 0, 0), k)] * n

        for i in range(n):
            leds[i] = px[i]
        leds.write()
