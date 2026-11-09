"""B1 dual-COG transport.

Sequences derive from Pervasive Displays and the Hackaday Billboard Badge.
One task owns a Panel. Buffers must remain unchanged until an await returns.
"""

import asyncio as aio
import time
from .waveforms import BOLD, RED, GRAY
try:
    from micropython import viper
except ImportError:
    from .decay import viper, ptr8

WIDTH = 960
HEIGHT = 672
FRAME_BYTES = 80640
HALF_BYTES = 40320
def _reverse(value):
    value = ((value & 0xF0) >> 4) | ((value & 0x0F) << 4)
    value = ((value & 0xCC) >> 2) | ((value & 0x33) << 2)
    return ((value & 0xAA) >> 1) | ((value & 0x55) << 1)


_REV = bytes(_reverse(i) for i in range(256))


@viper
def _split_half(source, destination, table, offset: int):
    src = ptr8(source)
    dst = ptr8(destination)
    rev = ptr8(table)
    for row in range(672):
        for x in range(60):
            dst[row * 60 + x] = rev[src[row * 120 + offset + x]]


@viper
def _pack_masks(black, white, red, out_black, out_red, has_red: int) -> int:
    b = ptr8(black)
    w = ptr8(white)
    r = ptr8(red)
    ob = ptr8(out_black)
    ore = ptr8(out_red)
    active = red_active = 0
    for i in range(80640):
        ink = int(b[i])
        pigment = 0
        if has_red:
            pigment = int(r[i]) & (255 ^ ink)
        drive = ink | int(w[i]) | pigment
        hold = 255 ^ drive
        ob[i] = ink | hold
        ore[i] = pigment | hold
        active |= drive
        red_active |= pigment
    return active | (red_active << 8)


class Panel:
    def __init__(self, spi=None, cs1=13, cs2=3, busy1=12, busy2=8,
                 dc=10, reset=11, sck=21, mosi=47, power=None,
                 spi_id=1, baudrate=20_000_000, revision="B1"):
        if revision != "B1":
            raise ValueError("standalone driver supports the B1 panel only")
        if not 1 <= baudrate <= 20_000_000:
            raise ValueError("SPI frequency must be between 1 and 20000000 Hz")
        self._half = bytearray(HALF_BYTES)
        self._black = bytearray(FRAME_BYTES)
        self._red = bytearray(FRAME_BYTES)
        from machine import Pin, SPI
        def pin(value, mode, initial=None):
            if hasattr(value, "value"):
                return value
            return Pin(value, mode) if initial is None else Pin(value, mode, value=initial)
        self.cs1 = pin(cs1, Pin.OUT, 1)
        self.cs2 = pin(cs2, Pin.OUT, 1)
        self.busy1 = pin(busy1, Pin.IN)
        self.busy2 = pin(busy2, Pin.IN)
        self.dc = pin(dc, Pin.OUT, 1)
        self.reset_pin = pin(reset, Pin.OUT, 1)
        self.power = None if power is None else pin(power, Pin.OUT, 0)
        self._owns_spi = spi is None
        self.spi = spi if spi is not None else SPI(spi_id, baudrate=baudrate,
                                                   polarity=0, phase=0,
                                                   sck=Pin(sck), mosi=Pin(mosi))
        self._ready = False
        self._lut = None
        self.fault = None
        self.phase = "idle"
        self._closed = False

    def _check(self):
        if self._closed:
            raise RuntimeError("panel is closed")
        if self.fault is not None:
            raise RuntimeError("panel fault; call recover() before reuse")

    def _abort(self, error):
        self.fault = error
        self._ready = False
        self._lut = None
        self.cs1.value(1)
        self.cs2.value(1)
        self.reset_pin.value(0)
        self.phase = "fault"

    def _send(self, index, data, half=0):
        # half: 0 both, 1 left, 2 right. Always release both chip selects.
        self.cs1.value(1)
        self.cs2.value(1)
        try:
            self.dc.value(0)
            self.cs1.value(1 if half == 2 else 0)
            self.cs2.value(1 if half == 1 else 0)
            self.spi.write(bytes((index,)))
            self.cs1.value(1)
            self.cs2.value(1)
            self.dc.value(1)
            self.cs1.value(1 if half == 2 else 0)
            self.cs2.value(1 if half == 1 else 0)
            self.spi.write(bytes(data) if isinstance(data, (list, tuple)) else data)
        finally:
            self.cs1.value(1)
            self.cs2.value(1)

    async def _wait(self, timeout_ms=15000):
        started = time.ticks_ms()
        while not self.busy1.value() or not self.busy2.value():
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                raise OSError("EPD BUSY timeout during " + self.phase)
            await aio.sleep_ms(5)

    async def _reset(self):
        self.phase = "reset"
        await aio.sleep_ms(50)
        self.reset_pin.value(1)
        await aio.sleep_ms(20)
        self.reset_pin.value(0)
        await aio.sleep_ms(100)
        self.reset_pin.value(1)
        await aio.sleep_ms(100)
        self.cs1.value(1)
        self.cs2.value(1)
        await aio.sleep_ms(5)

    async def _init(self, lut=None):
        self.phase = "init"
        self._send(0x05, (0x7D,))
        await aio.sleep_ms(200)
        self._send(0x05, (0,))
        await aio.sleep_ms(10)
        self._send(0xD8, (0x80,))
        self._send(0xD6, (0,))
        await self._strobe()
        self._send(0x03, (0, 0x11))
        self._send(0x44, (0,))
        self._send(0x45, (0x80,))
        await self._strobe()
        self._send(0x44, (6,))
        self._send(0x45, (0x82,))
        await self._strobe()
        self._send(0x60, (0x25,))
        self._send(0x61, (1,), 1)
        self._send(0x02, (0,))
        if lut is not None:
            self._send(0xB0, lut)

    async def _strobe(self):
        self._send(0xA7, (0x10,))
        await aio.sleep_ms(20)
        self._send(0xA7, (0,))
        await aio.sleep_ms(20)

    async def _start_power(self):
        self.phase = "power on"
        self._send(0x51, (0x50, 1))
        for first, last, low, high, fixed, swap in (
                (1, 5, 0x1F, 0x9F, 0x50, False),
                (1, 11, 0x1F, 0x9F, 0x0A, False),
                (3, 11, 0x7F, 0xFF, 0x0A, False),
                (9, 1, 0x7F, 0xFF, 0x0A, True)):
            for value in range(first, last, -1 if swap else 1):
                self._send(0x09, (low,))
                self._send(0x51, (value, fixed) if swap else (fixed, value))
                self._send(0x09, (high,))
                await aio.sleep_ms(2)
        self._send(0x09, (0xFF,))
        await aio.sleep_ms(10)

    async def _pull(self, timeout_ms=15000):
        self.phase = "refresh"
        await self._wait(timeout_ms)
        self._send(0x15, (0x3C,))
        # Observe both BUSY assertions before yielding to other application code.
        started = time.ticks_ms()
        left = right = False
        while not (left and right):
            left = left or not self.busy1.value()
            right = right or not self.busy2.value()
            if time.ticks_diff(time.ticks_ms(), started) >= 100:
                raise OSError("EPD BUSY did not assert on both controllers")
            time.sleep_ms(1)
        await self._wait(timeout_ms)
        self.phase = "ready"

    def _images(self, black, red):
        self.phase = "send images"
        self._send(0x13, (0, 0x3B, 0, 0, 0x9F, 2))
        self._send(0x90, (0, 0x3B, 0, 0xA9))
        for half in (1, 2):
            offset = (half - 1) * 60
            for register, plane in ((0x10, black), (0x11, red)):
                _split_half(plane, self._half, _REV, offset)
                self._send(0x12, (0x3B, 0, 0x14), half)
                self._send(register, self._half, half)

    @staticmethod
    def _validate(*planes):
        for plane in planes:
            if plane is not None and len(plane) != FRAME_BYTES:
                raise ValueError("framebuffer must contain 80640 bytes")

    async def _begin(self, lut):
        if not self._ready:
            await self._reset()
            await self._init(lut)
            await self._start_power()
            self._ready = True
            self._lut = lut
        else:
            await self._wait()
            if self._lut is not lut:
                self._send(0xB0, lut)
                self._lut = lut

    async def drive(self, black, white, red=None):
        """One completed fast pass. Masks: 1 drives; priority black > red > white.

        All other pixels select zero-volt HOLD. The controller stays powered
        for the next pass; await end_burst() when done, including during pauses.
        """
        self._check()
        self._validate(black, white, red)
        if black is None or white is None:
            raise ValueError("black and white masks are required")
        flags = _pack_masks(black, white, red if red is not None else black,
                            self._black, self._red, int(red is not None))
        if not flags:
            return False
        try:
            await self._begin(RED if flags >> 8 else BOLD)
            self._images(self._black, self._red)
            await self._pull()
        except BaseException as error:
            self._abort(error)
            raise
        return True

    async def end_burst(self):
        self._check()
        if not self._ready:
            return
        try:
            self.phase = "shutdown"
            await self._wait()
            self._send(0x09, (0x7F,))
            self._send(0x05, (0x7D,))
            self._send(0x09, (0,))
            await aio.sleep_ms(100)
            await self._wait()
            self.dc.value(0)
            self.reset_pin.value(0)
            self._ready = False
            self._lut = None
            self.phase = "idle"
        except BaseException as error:
            self._abort(error)
            raise

    async def full_refresh(self, black=None, red=None):
        """Factory OTP waveform. Plane bits are 1 = pigment; None = all white."""
        self._check()
        self._validate(black, red)
        try:
            await self.end_burst()
            for i in range(FRAME_BYTES):
                b = black[i] if black is not None else 0
                r = red[i] if red is not None else 0
                self._black[i] = b & (255 ^ r)
                self._red[i] = r
            await self._reset()
            self._images(self._black, self._red)
            await self._init()
            await self._start_power()
            self._ready = True
            await self._pull(90000)
            await self.end_burst()
        except BaseException as error:
            self._abort(error)
            raise

    async def grayscale(self, black, gray):
        """Slow B1 three-tone refresh: 00 white, 01 black, 10 gray; reject 11.

        Always perform a factory white baseline, six white preparation pulls,
        then three grayscale pulls. This mode cannot display red.
        """
        self._check()
        self._validate(black, gray)
        if black is None or gray is None or any(b & g for b, g in zip(black, gray)):
            raise ValueError("grayscale needs separate, non-overlapping black/gray planes")
        try:
            await self.full_refresh()
            await self._begin(GRAY)
            self._images(self._black, self._red)  # both zero after white baseline
            for _ in range(6):
                await self._pull()
            self._images(black, gray)
            for _ in range(3):
                await self._pull()
            await self.end_burst()
        except BaseException as error:
            self._abort(error)
            raise

    async def recover(self):
        """Clear a fault by resetting and completing a factory white refresh."""
        if self._closed:
            raise RuntimeError("panel is closed")
        self.fault = None
        self._ready = False
        self._lut = None
        await self.full_refresh()

    async def close(self):
        if self._closed:
            return
        try:
            if self.fault is None:
                await self.end_burst()
        finally:
            self.reset_pin.value(0)
            self.cs1.value(1)
            self.cs2.value(1)
            if self._owns_spi:
                self.spi.deinit()
            self._closed = True
