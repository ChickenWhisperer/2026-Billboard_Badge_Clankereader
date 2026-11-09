"""Pong for up to four players over ESP-NOW (lib/espnow_room.py).

Open "Pong 4P" on 1-4 badges: the first one opened hosts, the others join, and the
host presses A to start. The badge plays any empty side. Every badge shows the same
court with your paddle outlined: bottom and top paddles move with A (left) and
B (right), left and right ones with A (up) and B (down). Each player has 3 lives; at
0 that side becomes a wall, and the last player left wins. The LED bar shows where
the ball is along your side, live.

By DavidSober (github.com/DavidSober).
"""

import asyncio as aio
import gc
import random
import struct
import sys
import time

import framebuf

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd_partial import SESSION_PULLS, PartialEPD
from lib.led_bar import NUM_LEDS, leds

try:   # the radio; without it the app just says so
    from lib.espnow_room import HDR, Room
except Exception as ex:
    print("Pong 4P: no radio:", ex)
    Room = None
    HDR = 5

W = 960
C = 672                       # the court: a C x C square in the middle of the screen
CX = (W - C) // 2             # its left edge on screen
FIX = 16                      # positions and speeds are in 1/16 px
STEP_MS = 20                  # game step, 50 a second
CORNER = 64                   # corner blocks: the ball bounces off them
PAD_LEN, PAD_T, PAD_GAP = 120, 14, 18
FACE = PAD_GAP + PAD_T        # paddle faces sit this far in from their edge
BALL = 20
LIVES = 3
SERVE_MS = 2000               # pause before each serve
IDLE_MS = 3000                # screen unchanged this long: the panel sleeps
READY_WAIT_MS = 45_000        # longest the host waits for every screen to be ready
TRAIL_EVERY, TRAIL_DOTS = 5, 3
LED_ON = 6                    # LED brightness, 0-255


def _v(px_per_s):
    """A speed in px/s as 1/16 px per step."""
    return px_per_s * FIX * STEP_MS // 1000


BALL_V0 = _v(170)             # serve speed
BALL_V_MAX = _v(320)
SIDE_MAX = _v(200)            # the ball's speed along a side after a paddle hit
SIDE_MIN = _v(40)             # never straight across, so rallies don't stall
YOU_V = _v(330)               # a player's paddle while a button is held
CPU_V = _v(150)               # a computer paddle

LOBBY, SERVE, PLAY, OVER = 0, 1, 2, 3
CPU, HUMAN, AWAY = 0, 1, 2    # who plays a seat; AWAY: its badge dropped out, the CPU covers
SEATS = 4                     # 0 bottom (the host), 1 left, 2 top, 3 right
MID = (C - PAD_LEN) // 2 * FIX
LO, HI = CORNER * FIX, (C - CORNER - PAD_LEN) * FIX   # a paddle's travel

TAG = b"P4pg"                 # this game's packets
T_STATE, T_INPUT = 0x53, 0x49  # 'S' host -> guests, 'I' guest -> host
S_FMT = "<BBbBBBhh4hB6h"      # game id, state, winner, kinds, lives, players, ball, paddles, trail
I_FMT = "<BBB"                # up, down, screen ready
SEND_MS = 40                  # game and buttons, 25 a second
REMOTE_STALE_MS = 250         # no buttons from a guest this long: its paddle stops

LOOKING = "LOOKING FOR A GAME"
WAIT_HOST = "WAITING FOR THE HOST"
IN_PROGRESS = "GAME IN PROGRESS"
NO_RADIO = "NO RADIO, SEE THE CONSOLE"

# where each seat's name and lives go: next to its side of the court
_LABEL_XY = ((8, C - 140), (8, C // 2 - 50), (CX + C + 8, 16), (CX + C + 8, C // 2 - 50))


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
    """The court. The host runs it; guests only hold what the host sent last."""

    def __init__(self) -> None:
        self.gid = 0                   # bumps with each new game: screens wipe then
        self.state = LOBBY
        self.kind = [HUMAN, CPU, CPU, CPU]
        self.lives = [LIVES] * SEATS
        self.p = [MID] * SEATS         # paddle positions along their sides
        self.dir = [0] * SEATS         # human seats' buttons this step: -1, 0 or 1
        self.err = [0] * SEATS         # computer paddles' aim error for this approach
        self.bx = self.by = (C - BALL) // 2 * FIX
        self.vx = self.vy = 0
        self.trail = []
        self.steps = 0
        self.winner = -1
        self.players = 1
        self.serve_at = None
        self.t = 0
        self.over_ms = 0
        self.wants_wipe = True
        self.drawn = False             # a frame drawn since the screen (re)started or wiped

    def new_game(self, now: int, kind: list) -> None:
        self.gid = (self.gid + 1) & 0xFF
        for s in range(SEATS):
            self.kind[s] = kind[s]
            self.lives[s] = LIVES
            self.p[s] = MID
        self.winner = -1
        self.wants_wipe = True
        self.t = now
        self._serve(now)
        self.serve_at = None           # serve once the screens are ready

    def hold_serve(self, now: int) -> None:
        """(Re)start the serve countdown."""
        if self.state == SERVE:
            self.serve_at = time.ticks_add(now, SERVE_MS)

    def update(self, now: int) -> None:
        """Run the game up to `now` in fixed steps."""
        n = 0
        while time.ticks_diff(now, self.t) >= STEP_MS:
            self.t = time.ticks_add(self.t, STEP_MS)
            self._step()
            n += 1
            if n == 10:                # far behind (a long pause): don't fast-forward
                self.t = now
                break

    def _serve(self, now):
        self.state = SERVE
        self.bx = self.by = (C - BALL) // 2 * FIX
        self.vx = self.vy = 0
        self.trail = []
        self.steps = 0
        self.serve_at = time.ticks_add(now, SERVE_MS)

    def _step(self):
        for s in range(SEATS):
            if not self.lives[s]:
                continue
            if self.kind[s] == HUMAN:
                self.p[s] = _clamp(self.p[s] + self.dir[s] * YOU_V, LO, HI)
            else:
                self.p[s] = self._chase(s)
        if self.state == SERVE:
            if self.serve_at is not None and time.ticks_diff(self.t, self.serve_at) >= 0:
                self._launch()
        elif self.state == PLAY:
            self._move()

    def _launch(self):
        """Serve from the middle toward a random player still in."""
        n = 0
        for s in range(SEATS):
            if self.lives[s]:
                n += 1
        pick = random.getrandbits(8) % n
        for s in range(SEATS):
            if self.lives[s]:
                if not pick:
                    break
                pick -= 1
        side = SIDE_MIN + random.getrandbits(8) * (SIDE_MAX // 2 - SIDE_MIN) // 255
        if random.getrandbits(1):
            side = -side
        if s == 0:
            self.vx, self.vy = side, BALL_V0
        elif s == 1:
            self.vx, self.vy = -BALL_V0, side
        elif s == 2:
            self.vx, self.vy = side, -BALL_V0
        else:
            self.vx, self.vy = BALL_V0, side
        self.state = PLAY
        self._aim()

    def _move(self):
        x0, y0 = self.bx, self.by
        self.bx += self.vx
        self.by += self.vy
        self._corners(x0)
        for s in range(SEATS):
            self._side(s, x0, y0)
        b, e = BALL * FIX, C * FIX
        if self.by >= e:
            self._miss(0)
        elif self.bx + b <= 0:
            self._miss(1)
        elif self.by + b <= 0:
            self._miss(2)
        elif self.bx >= e:
            self._miss(3)
        else:
            self.steps += 1
            if self.steps % TRAIL_EVERY == 0:
                self.trail.append((self.bx // FIX, self.by // FIX))
                if len(self.trail) > TRAIL_DOTS:
                    self.trail.pop(0)

    def _corners(self, x0):
        """Bounce off a corner block the ball ran into this step."""
        b, k, e = BALL * FIX, CORNER * FIX, C * FIX
        left, right = self.bx < k, self.bx + b > e - k
        top, bottom = self.by < k, self.by + b > e - k
        if not ((left or right) and (top or bottom)):
            return
        kx0, kx1 = (0, k) if left else (e - k, e)
        if not (x0 + b > kx0 and x0 < kx1):     # came in through a side face
            self.vx = -self.vx
            self.bx = kx1 if left else kx0 - b
        else:                                    # through the top or bottom face
            self.vy = -self.vy
            self.by = k if top else e - k - b

    def _side(self, s, x0, y0):
        """Bounce off side s's paddle or wall if the ball crossed its face this step."""
        b = BALL * FIX
        if s == 0:
            face = (C - FACE) * FIX
            if not (self.vy > 0 and y0 + b <= face < self.by + b):
                return
            along = self.bx
        elif s == 2:
            face = FACE * FIX
            if not (self.vy < 0 and self.by < face <= y0):
                return
            along = self.bx
        elif s == 1:
            face = FACE * FIX
            if not (self.vx < 0 and self.bx < face <= x0):
                return
            along = self.by
        else:
            face = (C - FACE) * FIX
            if not (self.vx > 0 and x0 + b <= face < self.bx + b):
                return
            along = self.by
        wall = not self.lives[s]
        p = self.p[s]
        if not wall and not (along + b > p and along < p + PAD_LEN * FIX):
            return                               # missed: the ball goes on out
        if s == 0:
            self.by = 2 * (face - b) - self.by
        elif s == 2:
            self.by = 2 * face - self.by
        elif s == 1:
            self.bx = 2 * face - self.bx
        else:
            self.bx = 2 * (face - b) - self.bx
        if wall:
            if s & 1:
                self.vx = -self.vx
            else:
                self.vy = -self.vy
            return
        # a paddle: a little faster, and off-centre hits go off at an angle
        perp = min(abs(self.vx if s & 1 else self.vy) * 106 // 100 + 1, BALL_V_MAX)
        reach = (PAD_LEN + BALL) * FIX // 2
        off = along + b // 2 - (p + PAD_LEN * FIX // 2)
        side = _clamp(off * SIDE_MAX // reach + random.getrandbits(4) - 8, -SIDE_MAX, SIDE_MAX)
        if abs(side) < SIDE_MIN:
            side = SIDE_MIN if side >= 0 else -SIDE_MIN
        if s == 0:
            self.vx, self.vy = side, -perp
        elif s == 2:
            self.vx, self.vy = side, perp
        elif s == 1:
            self.vx, self.vy = perp, side
        else:
            self.vx, self.vy = -perp, side
        self._aim()

    def _miss(self, s):
        if self.lives[s]:
            self.lives[s] -= 1
            if not self.lives[s]:
                print(f"Pong 4P: P{s + 1} is out")
        alive = humans = 0
        last = -1
        for i in range(SEATS):
            if self.lives[i]:
                alive += 1
                last = i
                if self.kind[i] != CPU:
                    humans += 1
        if alive <= 1 or not humans:
            self.state = OVER
            self.winner = last if alive == 1 else -1
            self.vx = self.vy = 0
            self.trail = []
        else:
            self._serve(self.t)

    def _toward(self, s):
        if s == 0:
            return self.vy > 0
        if s == 1:
            return self.vx < 0
        if s == 2:
            return self.vy < 0
        return self.vx > 0

    def _chase(self, s):
        """Move a computer paddle towards where the ball will reach its side."""
        if self.state == PLAY and self._toward(s):
            target = self._landing(s) + self.err[s] + (BALL - PAD_LEN) * FIX // 2
        else:
            target = MID
        p = self.p[s] + _clamp(target - self.p[s], -CPU_V, CPU_V)
        return _clamp(p, LO, HI)

    def _landing(self, s):
        """Where along side s the ball will reach its face, other sides folded in."""
        f, b = FACE * FIX, BALL * FIX
        if s & 1:
            face = f if s == 1 else (C - FACE) * FIX - b
            steps = (face - self.bx) // self.vx if self.vx else 0
            pos, v = self.by, self.vy
        else:
            face = (C - FACE) * FIX - b if s == 0 else f
            steps = (face - self.by) // self.vy if self.vy else 0
            pos, v = self.bx, self.vx
        span = (C - 2 * FACE) * FIX - b
        y = (pos - f + v * steps) % (2 * span)
        return f + (2 * span - y if y > span else y)

    def _aim(self):
        """New aim errors for the computer paddles: up to 0.9 of a paddle's reach for a slow
        ball, up to 1.4 at full speed."""
        reach = (PAD_LEN + BALL) // 2 * FIX
        speed = max(abs(self.vx), abs(self.vy))
        span = reach * (9 + 5 * speed // BALL_V_MAX) // 10
        for s in range(SEATS):
            if self.kind[s] != HUMAN:
                self.err[s] = (random.getrandbits(10) * 2 * span >> 10) - span

    # ---- guests ------------------------------------------------------------------

    def apply_state(self, msg: bytearray) -> None:
        """Guest: take the game the host sent."""
        (gid, state, winner, kinds, lives, players, bx, by, p0, p1, p2, p3, n,
         x0, y0, x1, y1, x2, y2) = struct.unpack_from(S_FMT, msg, HDR)
        if gid != self.gid:
            self.gid = gid
            self.wants_wipe = True
            self.drawn = False             # not ready again until the wipe is done
        self.state, self.winner, self.players = state, winner, players
        for s in range(SEATS):
            self.kind[s] = (kinds >> (2 * s)) & 3
            self.lives[s] = (lives >> (2 * s)) & 3
        self.p[0], self.p[1], self.p[2], self.p[3] = p0 * FIX, p1 * FIX, p2 * FIX, p3 * FIX
        self.bx, self.by = bx * FIX, by * FIX
        pts = ((x0, y0), (x1, y1), (x2, y2))
        self.trail = list(pts[:n])

    # ---- drawing -----------------------------------------------------------------

    def draw(self, fb, me, banner) -> None:
        """Draw the court into fb, which starts out white. `me`: our seat, or None."""
        self.drawn = True
        k = CORNER
        for x, y in ((0, 0), (C - k, 0), (0, C - k), (C - k, C - k)):
            fb.fill_rect(CX + x, y, k, k, 1)
        for s in range(SEATS):
            self._draw_side(fb, s, s == me)
            self._label(fb, s, s == me)
        if self.state == SERVE or self.state == PLAY:
            for x, y in self.trail:
                fb.fill_rect(CX + x + BALL // 2 - 3, y + BALL // 2 - 3, 6, 6, 1)
            fb.fill_rect(CX + self.bx // FIX, self.by // FIX, BALL, BALL, 1)
        self._center(fb, me, banner)

    def _draw_side(self, fb, s, mine):
        if not self.lives[s]:                    # out: a wall along the whole side
            if s & 1:
                x = CX + (PAD_GAP if s == 1 else C - FACE)
                fb.fill_rect(x, CORNER, PAD_T, C - 2 * CORNER, 1)
            else:
                y = C - FACE if s == 0 else PAD_GAP
                fb.fill_rect(CX + CORNER, y, C - 2 * CORNER, PAD_T, 1)
            return
        q = self.p[s] // FIX
        if s & 1:
            x, y, w, h = CX + (PAD_GAP if s == 1 else C - FACE), q, PAD_T, PAD_LEN
        else:
            x, y, w, h = CX + q, (C - FACE if s == 0 else PAD_GAP), PAD_LEN, PAD_T
        fb.fill_rect(x, y, w, h, 1)
        if mine:                                 # outline yours
            for i in range(3):
                fb.rect(x - 6 - i, y - 6 - i, w + 12 + 2 * i, h + 12 + 2 * i, 1)

    def _label(self, fb, s, mine):
        x, y = _LABEL_XY[s]
        _text(fb, f"P{s + 1}", 3, x, y)
        kind = self.kind[s]
        tag = "YOU" if mine else "CPU" if kind == CPU else "AWAY" if kind == AWAY else ""
        if tag:
            _text(fb, tag, 2, x, y + 32)
        if not self.lives[s]:
            _text(fb, "OUT", 2, x, y + 56)
            return
        for i in range(LIVES):
            if i < self.lives[s]:
                fb.fill_rect(x + 24 * i, y + 56, 16, 16, 1)
            else:
                fb.rect(x + 24 * i, y + 56, 16, 16, 1)

    def _center(self, fb, me, banner):
        if banner:
            lines = ((banner, 3),)
        elif self.state == LOBBY:
            lines = (("PONG 4P", 6), (f"{self.players} OF 4 PLAYERS", 3),
                     ("A: START" if me == 0 else WAIT_HOST, 3))
        elif self.state == OVER:
            if me is not None and self.winner == me:
                top = "YOU WIN!"
            elif self.winner >= 0:
                top = f"P{self.winner + 1} WINS"
            else:
                top = "CPU WINS"
            lines = ((top, 6), ("A: PLAY AGAIN" if me == 0 else WAIT_HOST, 3))
        else:
            return
        y = C // 2
        for _, scale in lines:
            y -= (8 * scale + 24) // 2
        for text, scale in lines:
            _text(fb, text, scale, None, y, 12)
            y += 8 * scale + 24

    def light_key(self, me, banner) -> int:
        """A small number that changes whenever lights() would (no allocation)."""
        if banner:
            return 1
        if self.state == LOBBY:
            return 10 + self.players
        if self.state == OVER:
            return 21 if me is not None and self.winner == me else 20
        if me is None:
            return 2
        if not self.lives[me]:
            return 3
        if self.state == SERVE:
            return 30 + self.lives[me]
        return 100 + self._led_col(me)

    def lights(self, me, banner) -> tuple:
        """What the LED bar should show now, as a tuple of (r, g, b)."""
        out = [(0, 0, 0)] * NUM_LEDS
        if banner:
            out[0] = (0, 0, LED_ON)
        elif self.state == LOBBY:            # players in the room
            for i in range(min(self.players, NUM_LEDS)):
                out[i] = (0, 0, LED_ON)
        elif self.state == OVER:
            won = me is not None and self.winner == me
            out = [(0, LED_ON, 0) if won else (LED_ON, 0, 0)] * NUM_LEDS
        elif me is not None:
            if not self.lives[me]:
                out[0] = (LED_ON, 0, 0)
            elif self.state == SERVE:        # your lives
                for i in range(self.lives[me]):
                    out[i] = (0, LED_ON, 0)
            else:                            # the ball along your side, live
                out[self._led_col(me)] = (LED_ON, LED_ON, LED_ON)
        return tuple(out)

    def _led_col(self, me):
        pos = (self.by if me & 1 else self.bx) // FIX + BALL // 2
        return _clamp(pos * NUM_LEDS // C, 0, NUM_LEDS - 1)


class Pong4PApp(BaseApp):
    def __init__(self) -> None:
        super().__init__("Pong 4P")
        self.sleep_ms = STEP_MS
        self.game = Game()
        self.room = None
        self.net = None                      # the room's task
        self.task = None                     # the screen's task
        self.panel = None
        self.beat = 0                        # last run(): both tasks stop when these stop
        self.failed = False
        self.radio_failed = False
        self.role = None                     # room.host last step, to notice a change
        self.me = None                       # our seat
        self.banner = LOOKING
        self._lit = None
        self.next_tx = 0
        self.ready_wait = 0                  # host: when it started waiting for screens
        self.r_dir = [0] * SEATS             # host: each guest's buttons, -1/0/1
        self.r_ms = [0] * SEATS              # host: when they came
        self.r_ready = [False] * SEATS       # host: each guest's screen shows the game
        self.sent = -1                       # guest: buttons and readiness last sent
        self._kind = [HUMAN, CPU, CPU, CPU]  # host: who sits where right now
        self._spkt = self._ipkt = None
        self._trail = [0] * (2 * TRAIL_DOTS)

    def run(self) -> None:
        now = time.ticks_ms()
        self.beat = now
        if self.launched:
            UserA.pressed()                  # drop the press that opened the app
            UserB.pressed()
            self.failed = False
            self.radio_failed = False
            self._lit = None                 # the menu blanked the LED bar
        if not self.radio_failed and (self.net is None or self.net.done()):
            self._start_room()               # also restarts it if it stopped while we ran
        if not self.failed and (self.task is None or self.task.done()):
            self.task = aio.create_task(self._screen())
        a, b = UserA.value, UserB.value
        pressed = UserA.pressed()
        UserB.pressed()                      # read every step: a held button can't count later
        room, g = self.room, self.game
        banner = me = None
        if room is None or room.host is None:
            banner = NO_RADIO if self.radio_failed else LOOKING
        elif room.host:
            me = 0
            self._host_step(now, a, b, pressed)
        else:
            me = room.seat
            if room.seat is None:
                banner = IN_PROGRESS if room.full else LOOKING
            elif not room.connected:
                banner = WAIT_HOST
            self._guest_step(now, a, b)
        self.me, self.banner = me, banner
        key = 0 if self.failed else g.light_key(me, banner)
        if key != self._lit:                 # the LED bar only changes when this does
            self._lit = key
            leds.set(((LED_ON, 0, 0),) * NUM_LEDS if self.failed else g.lights(me, banner))

    def _alive(self):
        # run() stops being called once Menu is pressed. Allow for a garbage
        # collection (~0.4 s on this badge) on top of a frame's work.
        return time.ticks_diff(time.ticks_ms(), self.beat) < 1500

    def _start_room(self):
        if Room is None:
            self.radio_failed = True
            return
        self.room = Room(TAG, self._on_message)
        self.role = None
        self._spkt = self.room.packet(T_STATE, struct.calcsize(S_FMT))
        self._ipkt = self.room.packet(T_INPUT, struct.calcsize(I_FMT))
        self.net = aio.create_task(self._net())

    async def _net(self):
        try:
            await self.room.run(self._alive)
        except Exception as ex:
            print("Pong 4P: radio stopped:", repr(ex))
            sys.print_exception(ex)
            self.radio_failed = True

    # ---- host --------------------------------------------------------------------

    def _host_step(self, now, a, b, pressed):
        g, room = self.game, self.room
        if self.role is not True:            # just started hosting
            self.role = True
            g.state = LOBBY
        idle = g.state == LOBBY or g.state == OVER
        room.open = idle
        g.players = room.players
        kind = self._kind
        for s in range(1, SEATS):
            kind[s] = HUMAN if room.live[s] else AWAY if room.member[s] else CPU
            if idle:
                g.kind[s] = kind[s]          # show who's in the room
            elif g.kind[s] != CPU:           # in a game, a seat keeps its player
                g.kind[s] = HUMAN if room.live[s] else AWAY
        if idle:
            if pressed and (g.state == LOBBY or time.ticks_diff(now, g.over_ms) > 1000):
                g.new_game(now, kind)
                for s in range(SEATS):
                    self.r_ready[s] = False
                self.ready_wait = now
        else:
            g.dir[0] = (1 if b else 0) - (1 if a else 0)
            for s in range(1, SEATS):
                fresh = time.ticks_diff(now, self.r_ms[s]) <= REMOTE_STALE_MS
                g.dir[s] = self.r_dir[s] if fresh else 0
            if g.state == SERVE and not self._screens_ready(now):
                g.hold_serve(now)
            g.update(now)
            if g.state == OVER:
                g.over_ms = now
        if time.ticks_diff(now, self.next_tx) >= 0:
            self.next_tx = time.ticks_add(now, SEND_MS)
            self._send_state()

    def _screens_ready(self, now):
        """Every screen in the game shows it (or we've waited long enough)."""
        if time.ticks_diff(now, self.ready_wait) >= READY_WAIT_MS:
            return True
        g = self.game
        if not g.drawn:
            return False
        for s in range(1, SEATS):
            if g.kind[s] == HUMAN and not self.r_ready[s]:
                return False
        return True

    def _send_state(self):
        g, tr = self.game, self._trail
        n = len(g.trail)
        for i in range(TRAIL_DOTS):
            x, y = g.trail[i] if i < n else (0, 0)
            tr[2 * i], tr[2 * i + 1] = x, y
        kinds = lives = 0
        for s in range(SEATS):
            kinds |= g.kind[s] << (2 * s)
            lives |= g.lives[s] << (2 * s)
        struct.pack_into(S_FMT, self._spkt, HDR, g.gid, g.state, g.winner, kinds, lives,
                         g.players, g.bx // FIX, g.by // FIX, g.p[0] // FIX, g.p[1] // FIX,
                         g.p[2] // FIX, g.p[3] // FIX, n, *tr)
        self.room.send_guests(self._spkt)

    # ---- guest -------------------------------------------------------------------

    def _guest_step(self, now, a, b):
        self.role = False
        if self.room.seat is None:
            return
        up, down = (1 if a else 0), (1 if b else 0)
        ready = 1 if self.game.drawn else 0
        sent = (up << 2) | (down << 1) | ready
        if sent != self.sent or time.ticks_diff(now, self.next_tx) >= 0:
            self.sent = sent
            self.next_tx = time.ticks_add(now, SEND_MS)
            struct.pack_into(I_FMT, self._ipkt, HDR, up, down, ready)
            self.room.send_host(self._ipkt)

    # ---- both --------------------------------------------------------------------

    def _on_message(self, t, msg, seat):
        room = self.room
        if room is None:
            return
        if t == T_INPUT and room.host and 0 < seat < SEATS:
            up, down, ready = struct.unpack_from(I_FMT, msg, HDR)
            self.r_dir[seat] = down - up
            self.r_ms[seat] = time.ticks_ms()
            self.r_ready[seat] = bool(ready)
        elif t == T_STATE and room.host is False:
            self.game.apply_state(msg)

    async def _screen(self):
        """Owns the panel while the app runs: cleans it, then draws frames."""
        if self.panel is None:
            self.panel = PartialEPD()
        panel, g = self.panel, self.game
        g.drawn = False
        try:
            await panel.start()
            g.wants_wipe = False                 # start() already cleaned the screen
            g.hold_serve(time.ticks_ms())
            gc.collect()                         # ~0.4 s here; best done in the pauses
            still = time.ticks_ms()
            lives = -1
            while self._alive():
                if g.wants_wipe:
                    g.wants_wipe = False
                    g.drawn = False
                    await panel.restart(wipe=True)
                    gc.collect()
                    g.hold_serve(time.ticks_ms())
                total = g.lives[0] + g.lives[1] + g.lives[2] + g.lives[3]
                if total != lives:               # a life lost: the serve pause has time
                    lives = total
                    gc.collect()
                panel.clear()
                g.draw(panel.fb, self.me, self.banner)
                if not await panel.show():       # nothing changed
                    if time.ticks_diff(time.ticks_ms(), still) > IDLE_MS:
                        await panel.sleep()      # no point keeping the boost up
                    await aio.sleep_ms(50)
                    continue
                still = time.ticks_ms()
                if g.state != PLAY and panel.pulls > SESSION_PULLS // 2:
                    await panel.restart()        # reopen in a pause, not mid-rally
        except Exception as ex:
            print("Pong 4P: screen stopped:", ex)
            sys.print_exception(ex)
            self.failed = True
        finally:
            await panel.stop()
            self.panel = None                    # give its ~200 KB of buffers back once closed
