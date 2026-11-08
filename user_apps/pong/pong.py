"""Pong, you against the badge, drawn with the partial-refresh driver (lib/epd_partial.py).

Hold A to move your paddle (the left one) up, B to move it down.
The screen redraws about every 0.65 s: the LED bar shows where the ball is right now,
and the dots behind the ball show where it's heading.
First to 5 wins, then A or B starts a new game. Menu +/- goes back to the menu.
If the last app left red on the screen, Pong starts with a slow full refresh.

By DavidSober (github.com/DavidSober).
"""

import asyncio as aio
import gc
import random
import sys
import time

import framebuf

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd_partial import PartialEPD, SESSION_PULLS
from lib.led_bar import leds, NUM_LEDS

W = 960
H = 672
STEP_MS = 20             # game step, 50 a second
FIX = 16                 # positions and speeds are in 1/16 px
WIN = 5                  # points to win (the LED bar shows up to 4 a side)
SERVE_MS = 2000          # pause before each serve
IDLE_MS = 3000           # screen unchanged this long: power the panel down

TOP = 80                 # court: TOP..BOTTOM, scores above it
BOTTOM = H - 8
WALL = 8
BALL = 24
PAD_W = 16
PAD_H = 128
PAD_X = 32               # paddle's gap from its edge

def _v(px_per_s):
    """A speed in px/s as 1/16 px per step."""
    return px_per_s * FIX * STEP_MS // 1000


BALL_VX0 = _v(160)       # serve speed across
BALL_VX_MAX = _v(330)
BALL_VY_MAX = _v(200)
BALL_VY_MIN = _v(30)     # never flat, or a rally could go on forever
YOU_V = _v(330)          # your paddle while a button is held
BADGE_V = _v(150)        # the badge's paddle
TRAIL_EVERY = 5          # steps between trail dots (100 ms)
TRAIL_DOTS = 4

LED_ON = 6               # LED brightness, 0-255

SERVE, PLAY, OVER = 0, 1, 2


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


_labels = {}


def _label(text, scale):
    """`text` in the 8x8 font, `scale` times bigger, as (framebuffer, width, height); cached."""
    key = (text, scale)
    lab = _labels.get(key)
    if lab is None:
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


def _text(fb, text, scale, x, y, clear=0):
    """Draw text at x, y (top-left, x None: centered); clear > 0 whites out a margin that wide."""
    big, w, h = _label(text, scale)
    if x is None:
        x = (W - w) // 2
    if clear:
        fb.fill_rect(x - clear, y - clear, w + 2 * clear, h + 2 * clear, 0)
    fb.blit(big, x, y, 0)
    return w


class Game:
    def __init__(self) -> None:
        self.new_game(time.ticks_ms())

    def new_game(self, now: int) -> None:
        self.score = [0, 0]                      # you, badge
        mid = ((TOP + BOTTOM) // 2 - PAD_H // 2) * FIX
        self.py = [mid, mid]                     # paddle tops
        self.serve_dir = 1 if random.getrandbits(1) else -1
        self.hint = True                         # show the buttons until the first point
        self.t = now
        self.badge_err = 0
        self.wants_wipe = True                   # shake the last game's ghosts off
        self._serve(now)
        self.serve_at = None                     # serve once the screen is ready

    def _serve(self, now):
        self.state = SERVE
        self.bx = (W - BALL) // 2 * FIX
        self.by = ((TOP + BOTTOM) // 2 - BALL // 2) * FIX
        self.vx = self.vy = 0
        self.trail = []
        self.steps = 0
        self.serve_at = time.ticks_add(now, SERVE_MS)

    def hold_serve(self, now: int) -> None:
        """(Re)start the serve countdown once the screen is ready again."""
        if self.state == SERVE:
            self.serve_at = time.ticks_add(now, SERVE_MS)

    def update(self, now: int, up: int, down: int) -> None:
        """Run the game up to `now` in fixed steps."""
        n = 0
        while time.ticks_diff(now, self.t) >= STEP_MS:
            self.t = time.ticks_add(self.t, STEP_MS)
            self._step(up, down)
            n += 1
            if n == 10:          # far behind (a long pause): don't fast-forward
                self.t = now
                break

    def _step(self, up, down):
        self.py[0] = _clamp(self.py[0] + (down - up) * YOU_V, TOP * FIX, (BOTTOM - PAD_H) * FIX)
        self.py[1] = self._chase(self.py[1], BADGE_V)
        if self.state == SERVE:
            if self.serve_at is not None and time.ticks_diff(self.t, self.serve_at) >= 0:
                self.state = PLAY
                self.vx = self.serve_dir * BALL_VX0
                vy = BALL_VY_MIN + random.getrandbits(8) * (BALL_VY_MAX // 2 - BALL_VY_MIN) // 255
                self.vy = vy if random.getrandbits(1) else -vy
                self.badge_err = self._error()
        elif self.state == PLAY:
            self._move_ball()

    def _error(self):
        """How far off the badge aims: up to 0.9 of a paddle's reach slow, 1.4 at full speed."""
        reach = (PAD_H + BALL) // 2 * FIX
        span = reach * (9 + 5 * abs(self.vx) // BALL_VX_MAX) // 10
        return (random.getrandbits(10) * 2 * span >> 10) - span

    def _landing(self):
        """Ball top where it will reach the badge's paddle face, walls folded in."""
        face = (W - PAD_X - PAD_W - BALL) * FIX
        steps = (face - self.bx) // self.vx if self.vx else 0
        lo, span = TOP * FIX, (BOTTOM - TOP - BALL) * FIX
        y = (self.by - lo + self.vy * steps) % (2 * span)
        return lo + (2 * span - y if y > span else y)

    def _chase(self, py, speed):
        """Move the badge's paddle towards where the ball will arrive."""
        coming = self.state == PLAY and self.vx > 0
        half = W // 2 * FIX
        if coming and self.bx > half - BALL * FIX:
            target = self._landing() + self.badge_err + (BALL - PAD_H) * FIX // 2
        else:
            target = ((TOP + BOTTOM) // 2 - PAD_H // 2) * FIX
        py += _clamp(target - py, -speed, speed)
        return _clamp(py, TOP * FIX, (BOTTOM - PAD_H) * FIX)

    def _move_ball(self):
        self.bx += self.vx
        self.by += self.vy
        lo, hi = TOP * FIX, (BOTTOM - BALL) * FIX
        if self.by < lo:
            self.by, self.vy = 2 * lo - self.by, -self.vy
        elif self.by > hi:
            self.by, self.vy = 2 * hi - self.by, -self.vy
        if self.vx < 0:
            face = (PAD_X + PAD_W) * FIX
            if self.bx < face <= self.bx - self.vx and self._meets(0):
                self._bounce(0, face)
        else:
            face = (W - PAD_X - PAD_W - BALL) * FIX
            if self.bx - self.vx <= face < self.bx and self._meets(1):
                self._bounce(1, face)
        if self.bx < -BALL * FIX:
            self._point(1)
        elif self.bx > W * FIX:
            self._point(0)
        else:
            self.steps += 1
            if self.steps % TRAIL_EVERY == 0:
                self.trail.append((self.bx // FIX, self.by // FIX))
                if len(self.trail) > TRAIL_DOTS:
                    self.trail.pop(0)

    def _meets(self, p):
        return self.by < self.py[p] + PAD_H * FIX and self.by + BALL * FIX > self.py[p]

    def _bounce(self, p, face):
        self.bx = 2 * face - self.bx
        speed = min(abs(self.vx) * 106 // 100 + 1, BALL_VX_MAX)
        self.vx = speed if p == 0 else -speed
        # off-centre hits go off at an angle, plus a little randomness
        reach = (PAD_H + BALL) * FIX // 2
        off = self.by + BALL * FIX // 2 - (self.py[p] + PAD_H * FIX // 2)
        vy = off * BALL_VY_MAX // reach + random.getrandbits(4) - 8
        vy = _clamp(vy, -BALL_VY_MAX, BALL_VY_MAX)
        if abs(vy) < BALL_VY_MIN:
            vy = BALL_VY_MIN if vy >= 0 else -BALL_VY_MIN
        self.vy = vy
        if p == 0:
            self.badge_err = self._error()

    def _point(self, winner):
        self.score[winner] += 1
        self.hint = False
        self.serve_dir = -1 if winner == 0 else 1   # serve towards whoever lost the point
        if self.score[winner] >= WIN:
            self.state = OVER
            self.vx = self.vy = 0
            self.trail = []
        else:
            self._serve(self.t)

    # ---- drawing -------------------------------------------------------------

    def draw(self, fb) -> None:
        """Draw the game into fb, which starts out white."""
        fb.fill_rect(0, TOP - WALL, W, WALL, 1)
        fb.fill_rect(0, BOTTOM, W, H - BOTTOM, 1)
        for y in range(TOP + 12, BOTTOM - 24, 48):
            fb.fill_rect(W // 2 - 4, y, 8, 24, 1)
        you, badge = str(self.score[0]), str(self.score[1])
        _text(fb, you, 7, W // 2 - 48 - 56 * len(you), 8)
        _text(fb, badge, 7, W // 2 + 48, 8)
        fb.fill_rect(PAD_X, self.py[0] // FIX, PAD_W, PAD_H, 1)
        fb.fill_rect(W - PAD_X - PAD_W, self.py[1] // FIX, PAD_W, PAD_H, 1)
        mid = (TOP + BOTTOM) // 2
        if self.state == OVER:
            won = self.score[0] > self.score[1]
            _text(fb, "YOU WIN!" if won else "BADGE WINS", 6, None, mid - 72, 16)
            _text(fb, "A or B: play again", 3, None, mid + 24, 12)
            return
        for x, y in self.trail:
            fb.fill_rect(x + BALL // 2 - 4, y + BALL // 2 - 4, 8, 8, 1)
        fb.fill_rect(self.bx // FIX, self.by // FIX, BALL, BALL, 1)
        if self.hint and self.state == SERVE:
            _text(fb, "A: up    B: down", 3, None, mid + 64, 12)

    def light_key(self) -> int:
        """A small number that changes whenever lights() would (no allocation)."""
        if self.state == OVER:
            return 1 if self.score[0] > self.score[1] else 2
        if self.state == SERVE:
            return 100 + 10 * self.score[0] + self.score[1]
        return 200 + _clamp((self.bx // FIX + BALL // 2) * NUM_LEDS // W, 0, NUM_LEDS - 1)

    def lights(self) -> tuple:
        """What the LED bar should show now, as a tuple of (r, g, b)."""
        off = (0, 0, 0)
        if self.state == OVER:
            c = (0, LED_ON, 0) if self.score[0] > self.score[1] else (LED_ON, 0, 0)
            return (c,) * NUM_LEDS
        out = [off] * NUM_LEDS
        if self.state == SERVE:   # score: yours green from the left, badge's red from the right
            for i in range(min(self.score[0], NUM_LEDS)):
                out[i] = (0, LED_ON, 0)
            for i in range(min(self.score[1], NUM_LEDS)):
                out[NUM_LEDS - 1 - i] = (LED_ON, 0, 0)
        else:                     # the ball, live
            col = _clamp((self.bx // FIX + BALL // 2) * NUM_LEDS // W, 0, NUM_LEDS - 1)
            out[col] = (LED_ON, LED_ON, LED_ON)
        return tuple(out)


class PongApp(BaseApp):
    def __init__(self) -> None:
        super().__init__("Pong")
        self.sleep_ms = STEP_MS
        self.game = Game()
        self.panel = None
        self.task = None         # draws the game whenever the panel is ready
        self.beat = 0            # last run(): the screen task stops when these stop
        self.failed = False
        self._lit = None

    def run(self) -> None:
        now = time.ticks_ms()
        self.beat = now
        if self.launched:
            UserA.pressed()      # drop the press that opened the app
            UserB.pressed()
            self.failed = False
            if self.task is None or self.task.done():
                self.game.new_game(now)
        if not self.failed and (self.task is None or self.task.done()):
            self.task = aio.create_task(self._screen())
        g = self.game
        if g.state == OVER:
            if UserA.pressed() or UserB.pressed():
                g.new_game(now)
        else:
            g.update(now, 1 if UserA.value else 0, 1 if UserB.value else 0)
        key = 0 if self.failed else g.light_key()
        if key != self._lit:   # the LED bar only changes when this does
            self._lit = key
            leds.set(((LED_ON, 0, 0),) * NUM_LEDS if self.failed else g.lights())

    def _alive(self):
        # run() stops being called once Menu is pressed. Allow for a garbage
        # collection (~0.4 s on this badge) on top of a frame's work.
        return time.ticks_diff(time.ticks_ms(), self.beat) < 1500

    async def _screen(self):
        """Owns the panel while the app runs: cleans it, then draws frames."""
        if self.panel is None:
            self.panel = PartialEPD()
        panel, g = self.panel, self.game
        frames = total = pulls = sends = 0
        try:
            await panel.start()
            g.wants_wipe = False                 # start() already cleaned the screen
            g.hold_serve(time.ticks_ms())
            gc.collect()                         # ~0.4 s here; best done in the pauses
            last = still = time.ticks_ms()
            points = 0
            while self._alive():
                if g.wants_wipe:
                    g.wants_wipe = False
                    await panel.restart(wipe=True)
                    gc.collect()
                    g.hold_serve(time.ticks_ms())
                    last = time.ticks_ms()
                if g.score[0] + g.score[1] != points:   # a point: the serve pause has time
                    points = g.score[0] + g.score[1]
                    gc.collect()
                    last = time.ticks_ms()
                panel.clear()
                g.draw(panel.fb)
                if not await panel.show():       # nothing changed
                    if time.ticks_diff(time.ticks_ms(), still) > IDLE_MS:
                        await panel.sleep()      # no point keeping the boost up
                    await aio.sleep_ms(50)
                    last = time.ticks_ms()       # the stats count back-to-back frames
                    continue
                still = now = time.ticks_ms()
                total += time.ticks_diff(now, last)
                last = now
                pulls += panel.last_pull_ms
                sends += panel.last_send_ms
                frames += 1
                if frames == 20:
                    frame, pull, send = total // frames, pulls // frames, sends // frames
                    print(f"Pong: {frame} ms a frame (refresh {pull} ms, sending {send} ms)")
                    frames = total = pulls = sends = 0
                if g.state == SERVE and panel.pulls > SESSION_PULLS // 2:
                    await panel.restart()        # reopen in a pause, not mid-rally
                    last = time.ticks_ms()
        except Exception as ex:
            print("Pong: screen stopped:", ex)
            sys.print_exception(ex)
            self.failed = True
        finally:
            await panel.stop()
