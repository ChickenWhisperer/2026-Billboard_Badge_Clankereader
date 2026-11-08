"""Convenient blocking-until-complete async monochrome display API."""
import asyncio as aio
from .decay import Decay
from . import FRAME_BYTES


class Display:
    def __init__(self, panel, clean_interval=2400):
        if type(clean_interval) is not int or clean_interval < 1:
            raise ValueError("clean_interval must be a positive integer")
        self.panel = panel
        self.clean_interval = clean_interval
        self.passes_since_clean = 0
        self._known = False
        self._planner = Decay(b"\xff" * FRAME_BYTES)
        self._black = bytearray(FRAME_BYTES)
        self._white = bytearray(FRAME_BYTES)
        self._lock = aio.Lock()

    async def draw(self, frame, black_pulses=1, white_pulses=3):
        """Draw an MSB-first monochrome frame (1 white), including settling passes.

        Input is copied before awaiting. First use, and first use after grayscale,
        establishes a factory white baseline. Full cleans occur every 2400 passes.
        """
        if len(frame) != FRAME_BYTES:
            raise ValueError("framebuffer must contain 80640 bytes")
        if (type(black_pulses) is not int or type(white_pulses) is not int
                or not 1 <= black_pulses <= 15 or not 1 <= white_pulses <= 15):
            raise ValueError("pulse budgets must be integers from 1 to 15")
        snapshot = bytearray(frame)
        async with self._lock:
            try:
                if not self._known:
                    await self.panel.full_refresh()
                    self._planner.reset(b"\xff" * FRAME_BYTES)
                    self.passes_since_clean = 0
                    self._known = True
                while self._planner.prepare(snapshot, self._black, self._white,
                                             black_pulses, white_pulses):
                    await self.panel.drive(self._black, self._white)
                    self._planner.complete(self._black, self._white)
                    self.passes_since_clean += 1
                    if self.passes_since_clean >= self.clean_interval:
                        for i in range(FRAME_BYTES):
                            self._black[i] = snapshot[i] ^ 255
                        await self.panel.full_refresh(self._black)
                        self._planner.reset(snapshot)
                        self.passes_since_clean = 0
                        break
                await self.panel.end_burst()
            except BaseException:
                self._known = False
                raise

    async def grayscale(self, black, gray):
        """Slow three-tone refresh. Each plane uses 1 = selected pigment/tone."""
        self.panel._validate(black, gray)
        if black is None or gray is None:
            raise ValueError("black and gray planes are required")
        black_copy, gray_copy = bytearray(black), bytearray(gray)
        async with self._lock:
            self._known = False
            await self.panel.grayscale(black_copy, gray_copy)
            self.passes_since_clean = 0

    async def recover(self):
        async with self._lock:
            self._known = False
            await self.panel.recover()
            self._planner.reset(b"\xff" * FRAME_BYTES)
            self._known = True
            self.passes_since_clean = 0
