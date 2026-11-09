"""Pong for two badges, over ESP-NOW.

Open "Pong 1v1" on both badges: the first one hosts, the other joins a second or two later.
Each player sees their own paddle on the left: hold A to move it up, B to move it down.
First to 5 wins, then A or B on either badge starts a new game.
The game waits while either screen is clearing or the other badge is away.

By DavidSober (github.com/DavidSober).
"""

import asyncio as aio
import struct
import sys
import time

import apps.pong as pong
from apps.pong import (BALL, BOTTOM, FIX, H, LED_ON, OVER, PAD_H, PAD_W, PAD_X,
                       PLAY, TOP, W, WALL, YOU_V, Game, _clamp, _text)
from lib.button import UserA, UserB
from lib.led_bar import NUM_LEDS, leds

try:   # the radio link; without it the app just says so
    from lib.espnow_link import HDR, Link
except Exception as ex:
    print("Pong 1v1: no radio link:", ex)
    Link = None
    HDR = 5

TAG = b"P1v1"                        # this game's packets
T_STATE, T_INPUT = 0x53, 0x49        # 'S' host -> guest, 'I' guest -> host
S_FMT = "<BBBhhhhBBB8h"              # game id, state, hint, ball x y, paddles, scores, trail
I_FMT = "<BBBB"                      # up, down, presses so far, screen ready
SEND_MS = 40                         # game and buttons, 25 a second
REMOTE_STALE_MS = 250                # no buttons from the guest this long: its paddle stops
WAIT = "WAITING FOR THE OTHER BADGE"
NO_RADIO = "NO RADIO, SEE THE CONSOLE"


class NetGame(Game):
    """Pong's game with the right paddle played from the other badge."""

    def __init__(self) -> None:
        self.gid = 0                         # bumps with each new game: the guest wipes then
        self.remote_up = self.remote_down = 0
        self.banner = WAIT
        self.drawn = False                   # a frame drawn since the screen (re)started or wiped
        super().__init__()

    def new_game(self, now: int) -> None:
        self.gid = (self.gid + 1) & 0xFF
        super().new_game(now)

    def _chase(self, py, speed):
        """The other player's paddle, moved by their buttons."""
        return _clamp(py + (self.remote_down - self.remote_up) * YOU_V,
                      TOP * FIX, (BOTTOM - PAD_H) * FIX)

    def apply_state(self, msg: bytearray) -> None:
        """Guest: take the host's game, swapping sides."""
        (gid, state, hint, bx, by, p0, p1, s0, s1, n,
         x0, y0, x1, y1, x2, y2, x3, y3) = struct.unpack_from(S_FMT, msg, HDR)
        if gid != self.gid:
            self.gid = gid
            self.wants_wipe = True
            self.drawn = False               # not ready again until the wipe is done
        self.state, self.hint = state, bool(hint)
        self.bx = (W - BALL - bx) * FIX
        self.by = by * FIX
        self.py[0], self.py[1] = p1 * FIX, p0 * FIX
        self.score[0], self.score[1] = s1, s0
        pts = ((x0, y0), (x1, y1), (x2, y2), (x3, y3))
        self.trail = [(W - BALL - x, y) for x, y in pts[:n]]

    def draw(self, fb) -> None:
        self.drawn = True
        if self.state != OVER:
            super().draw(fb)
        else:                                # Pong's, with two players' wording
            fb.fill_rect(0, TOP - WALL, W, WALL, 1)
            fb.fill_rect(0, BOTTOM, W, H - BOTTOM, 1)
            for y in range(TOP + 12, BOTTOM - 24, 48):
                fb.fill_rect(W // 2 - 4, y, 8, 24, 1)
            you, them = str(self.score[0]), str(self.score[1])
            _text(fb, you, 7, W // 2 - 48 - 56 * len(you), 8)
            _text(fb, them, 7, W // 2 + 48, 8)
            fb.fill_rect(PAD_X, self.py[0] // FIX, PAD_W, PAD_H, 1)
            fb.fill_rect(W - PAD_X - PAD_W, self.py[1] // FIX, PAD_W, PAD_H, 1)
            mid = (TOP + BOTTOM) // 2
            won = self.score[0] > self.score[1]
            _text(fb, "YOU WIN!" if won else "YOU LOSE", 6, None, mid - 72, 16)
            _text(fb, "A or B: play again", 3, None, mid + 24, 12)
        if self.banner:
            _text(fb, self.banner, 3, None, TOP + 56, 12)

    def light_key(self) -> int:
        return 3 if self.banner else super().light_key()

    def lights(self) -> tuple:
        if self.banner:                      # waiting: one blue LED
            out = [(0, 0, 0)] * NUM_LEDS
            out[0] = (0, 0, LED_ON)
            return tuple(out)
        return super().lights()


class Pong1v1App(pong.PongApp):
    def __init__(self) -> None:
        super().__init__()
        self.name = "Pong 1v1"
        self.game = NetGame()
        self.link = None
        self.net = None                      # the link's task
        self.radio_failed = False
        self.was_paired = False
        self.partner = None                  # host: who the current game is with
        self.next_tx = 0
        self.over_ms = 0                     # host: last step before the game ended
        self.remote_ms = 0                   # host: when the guest's buttons last came
        self.remote_presses = None           # host: the guest's press count, last seen
        self.remote_pressed = False
        self.remote_ready = False            # host: the guest's screen is showing the game
        self.presses = 0                     # guest: A/B presses so far
        self.sent = -1                       # guest: buttons and readiness last sent
        self._spkt = self._ipkt = None
        self._trail = [0] * 8

    def run(self) -> None:
        now = time.ticks_ms()
        self.beat = now
        g = self.game
        if self.launched:
            UserA.pressed()                  # drop the press that opened the app
            UserB.pressed()
            self.failed = False
            self.radio_failed = False
            self._lit = None                 # the menu blanked the LED bar
        if not self.radio_failed and (self.net is None or self.net.done()):
            self._start_link()               # also restarts it if it stopped while we ran
        if not self.failed and (self.task is None or self.task.done()):
            self.task = aio.create_task(self._screen())
        a, b = UserA.value, UserB.value
        # read every step: a held button can't restart later
        pa, pb = UserA.pressed(), UserB.pressed()
        link = self.link
        if link is None or not link.paired:
            self.was_paired = False
            g.banner = NO_RADIO if self.radio_failed else WAIT
            g.t = now                        # paused: no fast-forward when it resumes
        elif link.host:
            if not self.was_paired:
                self.was_paired = True
                self._host_paired(now)
            self._host_step(now, a, b, pa or pb)
        else:
            self.was_paired = True
            g.banner = None
            self._guest_step(now, a, b, pa or pb)
        key = 0 if self.failed else g.light_key()
        if key != self._lit:                 # the LED bar only changes when this does
            self._lit = key
            leds.set(((LED_ON, 0, 0),) * NUM_LEDS if self.failed else g.lights())

    def _start_link(self):
        if Link is None:
            self.radio_failed = True
            return
        self.link = Link(TAG, self._on_message)
        self._spkt = self.link.packet(T_STATE, struct.calcsize(S_FMT))
        self._ipkt = self.link.packet(T_INPUT, struct.calcsize(I_FMT))
        self.net = aio.create_task(self._net())

    async def _net(self):
        try:
            await self.link.run(self._alive)
        except Exception as ex:
            print("Pong 1v1: radio stopped:", repr(ex))
            sys.print_exception(ex)
            self.radio_failed = True

    async def _screen(self):
        self.game.drawn = False
        try:
            await pong.PongApp._screen(self)
        finally:
            self.panel = None                # give its ~200 KB of buffers back once closed

    # ---- host ------------------------------------------------------------------

    def _host_paired(self, now):
        """The guest is here (again): a new game for a new partner, else carry on."""
        g = self.game
        self.remote_presses = None
        self.remote_ready = False
        if self.link.peer != self.partner:
            self.partner = self.link.peer
            g.new_game(now)
        elif g.state == PLAY:
            g._serve(now)                    # serve the interrupted point again
        g.t = now

    def _host_step(self, now, a, b, pressed):
        g = self.game
        if time.ticks_diff(now, self.remote_ms) > REMOTE_STALE_MS:
            g.remote_up = g.remote_down = 0
        if not self.remote_ready:            # the other screen isn't showing the game yet
            g.banner = WAIT
            g.t = now
            g.hold_serve(now)
            self.over_ms = now
        else:
            g.banner = None
            if g.state == OVER:
                if (pressed or self.remote_pressed) and time.ticks_diff(now, self.over_ms) > 1000:
                    g.new_game(now)
            else:
                g.update(now, 1 if a else 0, 1 if b else 0)
                self.over_ms = now
        self.remote_pressed = False
        if time.ticks_diff(now, self.next_tx) >= 0:
            self.next_tx = time.ticks_add(now, SEND_MS)
            self._send_state()

    def _send_state(self):
        g, tr = self.game, self._trail
        n = len(g.trail)
        for i in range(4):
            x, y = g.trail[i] if i < n else (0, 0)
            tr[2 * i], tr[2 * i + 1] = x, y
        struct.pack_into(S_FMT, self._spkt, HDR, g.gid, g.state, 1 if g.hint else 0,
                         g.bx // FIX, g.by // FIX, g.py[0] // FIX, g.py[1] // FIX,
                         g.score[0], g.score[1], n, *tr)
        self.link.send(self._spkt)

    # ---- guest -----------------------------------------------------------------

    def _guest_step(self, now, a, b, pressed):
        if pressed:
            self.presses = (self.presses + 1) & 0xFF
        up, down = (1 if a else 0), (1 if b else 0)
        ready = 1 if self.game.drawn else 0
        sent = (up << 2) | (down << 1) | ready
        if sent != self.sent or pressed or time.ticks_diff(now, self.next_tx) >= 0:
            self.sent = sent
            self.next_tx = time.ticks_add(now, SEND_MS)
            struct.pack_into(I_FMT, self._ipkt, HDR, up, down, self.presses, ready)
            self.link.send(self._ipkt)

    # ---- both ------------------------------------------------------------------

    def _on_message(self, t, msg):
        link = self.link
        if link is None:
            return
        if t == T_STATE and link.host is False:
            self.partner = None              # our game is now a copy of the host's: never resume it
            self.game.apply_state(msg)
        elif t == T_INPUT and link.host:
            up, down, presses, ready = struct.unpack_from(I_FMT, msg, HDR)
            g = self.game
            g.remote_up, g.remote_down = up, down
            self.remote_ready = bool(ready)
            self.remote_ms = time.ticks_ms()
            if self.remote_presses is None:
                self.remote_presses = presses
            elif presses != self.remote_presses:
                self.remote_presses = presses
                self.remote_pressed = True
