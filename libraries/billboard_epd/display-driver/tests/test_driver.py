"""Host checks against the actual C core and MicroPython Python fallback.

Set BILLBOARD_TEST_DLL to the library built by tools/test_native.py.
The simulator verifies command/mask contracts, not physical image quality.
"""
import asyncio
import ctypes as ct
import importlib
import os
from pathlib import Path
import random
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Python/badge/lib'))
from billboard_epd import Decay, FRAME_BYTES
from billboard_epd.panel import Panel
from billboard_epd.render import rgb565_to_grayscale
from billboard_epd.waveforms import BOLD, RED, GRAY

U8 = ct.c_uint8
PTR = ct.POINTER(U8)
WRITE = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_uint, U8, PTR, ct.c_size_t)
RESET = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_bool)
BUSY = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_uint)
DELAY = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_uint)
NOW = ct.CFUNCTYPE(ct.c_uint32, ct.c_void_p)


class IO(ct.Structure):
    _fields_ = [('context', ct.c_void_p), ('write', WRITE), ('reset', RESET),
                ('busy', BUSY), ('delay_ms', DELAY), ('now_ms', NOW)]


class CPanel(ct.Structure):
    _fields_ = [('io', IO), ('black', PTR), ('red', PTR), ('half', PTR),
                ('lut', PTR), ('ready', ct.c_bool), ('fault', ct.c_int), ('phase', ct.c_char_p)]


class CDecay(ct.Structure):
    _fields_ = [('size', ct.c_size_t), ('target', PTR), ('counts', PTR),
                ('pending', ct.c_uint), ('inflight', ct.c_bool)]


def cbuf(data):
    return (U8 * len(data)).from_buffer_copy(data)


def load_c():
    path = os.environ.get('BILLBOARD_TEST_DLL')
    if not path:
        return None
    lib = ct.CDLL(str(Path(path).resolve()))
    lib.billboard_panel_init.argtypes = [ct.POINTER(CPanel), ct.POINTER(IO), PTR, ct.c_size_t]
    lib.billboard_panel_drive.argtypes = [ct.POINTER(CPanel), PTR, PTR, PTR, ct.c_size_t]
    for name in ('full_refresh', 'grayscale'):
        getattr(lib, 'billboard_panel_' + name).argtypes = [ct.POINTER(CPanel), PTR, PTR, ct.c_size_t]
    for name in ('end_burst', 'recover'):
        getattr(lib, 'billboard_panel_' + name).argtypes = [ct.POINTER(CPanel)]
    lib.billboard_decay_init.argtypes = [ct.POINTER(CDecay), PTR, ct.c_size_t, PTR, ct.c_size_t]
    lib.billboard_decay_reset.argtypes = [ct.POINTER(CDecay), PTR, ct.c_size_t]
    lib.billboard_decay_prepare.argtypes = [ct.POINTER(CDecay), PTR, ct.c_size_t,
                                           ct.c_uint, ct.c_uint, PTR, PTR]
    lib.billboard_decay_complete.argtypes = [ct.POINTER(CDecay), PTR, PTR, ct.c_size_t]
    return lib


LIB = load_c()


class FakeHardware:
    def __init__(self):
        self.clock = 0
        self.busy_until = {1: 0, 2: 0}
        self.writes = []
        self.resets = []
        self.never_assert = False
        self.stuck = None
        self.fail_register = None

    def busy(self, half):
        return int(half != self.stuck and self.clock >= self.busy_until[half])

    def write(self, half, register, data):
        if register == self.fail_register:
            return -1
        self.writes.append((half, register, bytes(data)))
        if register == 0x15 and not self.never_assert:
            self.busy_until = {1: self.clock + 8, 2: self.clock + 12}
        return 0

    def reset(self, value):
        self.resets.append(value)
        return 0

    def delay(self, ms):
        self.clock += ms

    async def sleep(self, ms):
        self.delay(ms)
        await asyncio.sleep(0)

    def python_panel(self):
        panel = Panel.__new__(Panel)
        panel._half = bytearray(40320)
        panel._black = bytearray(FRAME_BYTES)
        panel._red = bytearray(FRAME_BYTES)
        panel._ready = False
        panel._lut = None
        panel.fault = None
        panel.phase = 'idle'
        panel._closed = False
        panel._owns_spi = False
        panel.reset_pin = types.SimpleNamespace(value=self.reset)
        panel.cs1 = panel.cs2 = panel.dc = types.SimpleNamespace(value=lambda value: None)
        panel.busy1 = types.SimpleNamespace(value=lambda: self.busy(1))
        panel.busy2 = types.SimpleNamespace(value=lambda: self.busy(2))
        def send(register, data, half=0):
            if self.write(half, register, data):
                raise OSError('injected SPI failure')
        panel._send = send
        return panel

    def c_panel(self):
        panel, storage = CPanel(), (U8 * 201600)()
        io = IO(None,
                WRITE(lambda ctx, half, reg, data, size: self.write(half, reg, ct.string_at(data, size))),
                RESET(lambda ctx, high: self.reset(high)),
                BUSY(lambda ctx, half: self.busy(half)),
                DELAY(lambda ctx, ms: self.delay(ms)),
                NOW(lambda ctx: self.clock & 0xffffffff))
        assert LIB.billboard_panel_init(ct.byref(panel), ct.byref(io), storage, len(storage)) == 0
        self.keepalive = (storage, io)
        return panel

    def patches(self):
        module = importlib.import_module('billboard_epd.panel')
        clock = types.SimpleNamespace(ticks_ms=lambda: self.clock,
                                     ticks_diff=lambda a, b: a - b, sleep_ms=self.delay)
        return patch.multiple(module, time=clock, aio=types.SimpleNamespace(sleep_ms=self.sleep))


class DecayTests(unittest.TestCase):
    def test_randomized_against_independent_per_pixel_reference(self):
        rng = random.Random(2026)
        size = 13  # Deliberately not word-aligned.
        planner = Decay(bytes([255]) * size)
        target = [1] * (size * 8)
        counters = [0] * len(target)
        black, white = bytearray(size), bytearray(size)
        c = CDecay()
        storage = (U8 * (5 * size))()
        cb, cw = (U8 * size)(), (U8 * size)()
        if LIB:
            self.assertEqual(LIB.billboard_decay_init(ct.byref(c), cbuf(planner.target), size,
                                                      storage, len(storage)), 0)
        for cycle in range(180):
            frame = bytes(rng.randrange(256) for _ in range(size)) if cycle % 4 == 0 else bytes(planner.target)
            bp, wp = rng.randint(1, 15), rng.randint(1, 15)
            expected_b, expected_w = bytearray(size), bytearray(size)
            for bit in range(size * 8):
                value = (frame[bit // 8] >> (bit % 8)) & 1
                if value != target[bit]:
                    counters[bit] = wp if value else bp
                    target[bit] = value
                if counters[bit]:
                    (expected_w if value else expected_b)[bit // 8] |= 1 << (bit % 8)
            pending = sum(value != 0 for value in counters)
            self.assertEqual(planner.prepare(frame, black, white, bp, wp), pending)
            self.assertEqual((black, white), (expected_b, expected_w))
            if LIB:
                self.assertEqual(LIB.billboard_decay_prepare(ct.byref(c), cbuf(frame), size, bp, wp, cb, cw), 0)
                self.assertEqual((bytes(cb), bytes(cw), c.pending), (bytes(black), bytes(white), pending))
            if pending:
                for bit in range(len(counters)):
                    counters[bit] = max(counters[bit] - 1, 0)
                self.assertEqual(planner.complete(black, white), sum(value != 0 for value in counters))
                if LIB:
                    self.assertEqual(LIB.billboard_decay_complete(ct.byref(c), cb, cw, size), 0)
                    self.assertEqual(bytes(storage[size:]), bytes(planner.counters))

    def test_validation_and_reset(self):
        planner = Decay(b'\xff')
        b, w = bytearray(1), bytearray(1)
        for budget in (0, 16, True, 1.5):
            with self.assertRaises(ValueError):
                planner.prepare(b'\x00', b, w, budget, 3)
        with self.assertRaises(ValueError):
            planner.prepare(b'xx', b, w)
        planner.prepare(b'\x00', b, w)
        with self.assertRaises(RuntimeError):
            planner.prepare(b'\xff', b, w)
        planner.reset(b'\x00')
        self.assertEqual(planner.prepare(b'\x00', b, w), 0)
        with self.assertRaises(RuntimeError):
            planner.complete(b, w)


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_and_modes_match_c(self):
        pyhw, chw = FakeHardware(), FakeHardware()
        p = pyhw.python_panel()
        c = chw.c_panel() if LIB else None
        black, white, red = bytearray(FRAME_BYTES), bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)
        black[0] = 0x80
        white[60] = 0x40
        red[0] = 0xA0
        with pyhw.patches():
            await p.drive(black, white)
            if LIB:
                self.assertEqual(LIB.billboard_panel_drive(ct.byref(c), cbuf(black), cbuf(white), None, FRAME_BYTES), 0)
            await p.drive(black, white, red)
            if LIB:
                self.assertEqual(LIB.billboard_panel_drive(ct.byref(c), cbuf(black), cbuf(white), cbuf(red), FRAME_BYTES), 0)
            await p.end_burst()
            if LIB:
                self.assertEqual(LIB.billboard_panel_end_burst(ct.byref(c)), 0)
                self.assertEqual(pyhw.writes, chw.writes)
        self.assertEqual([data for _, reg, data in pyhw.writes if reg == 0xB0], [BOLD, RED])
        self.assertEqual(len(pyhw.resets), 4)  # one reset sequence and one shutdown
        left_black = [data for half, reg, data in pyhw.writes if half == 1 and reg == 0x10]
        self.assertEqual(left_black[0][0], 0xff)
        self.assertEqual(left_black[1][0], 0xfb)  # bit-reversed 0xdf, red pixel cleared
        self.assertEqual(left_black[0][-1], 0xff)
        self.assertGreaterEqual(pyhw.clock, pyhw.busy_until[2])

    async def test_zero_masks_do_not_power_on(self):
        hw = FakeHardware()
        p = hw.python_panel()
        zeros = bytearray(FRAME_BYTES)
        with hw.patches():
            self.assertFalse(await p.drive(zeros, zeros))
        self.assertEqual(hw.writes, [])
        self.assertEqual(hw.resets, [])

    async def test_slow_grayscale_full_baseline_and_nine_custom_passes(self):
        pyhw, chw = FakeHardware(), FakeHardware()
        p = pyhw.python_panel()
        c = chw.c_panel() if LIB else None
        black, gray = bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)
        black[0], gray[60] = 0x80, 0x40
        with pyhw.patches():
            await p.grayscale(black, gray)
        if LIB:
            self.assertEqual(LIB.billboard_panel_grayscale(ct.byref(c), cbuf(black), cbuf(gray), FRAME_BYTES), 0)
            self.assertEqual(pyhw.writes, chw.writes)
        self.assertEqual(sum(reg == 0x15 for _, reg, _ in pyhw.writes), 10)
        luts = [data for _, reg, data in pyhw.writes if reg == 0xB0]
        self.assertEqual(luts, [GRAY])
        first_lut = next(i for i, (_, reg, _) in enumerate(pyhw.writes) if reg == 0xB0)
        self.assertEqual(sum(reg == 0x15 for _, reg, _ in pyhw.writes[:first_lut]), 1)
        self.assertFalse(p._ready)

    async def test_grayscale_rejects_overlap_before_io(self):
        hw = FakeHardware()
        p = hw.python_panel()
        frame = bytearray(FRAME_BYTES)
        frame[0] = 1
        with hw.patches(), self.assertRaises(ValueError):
            await p.grayscale(frame, frame)
        self.assertEqual(hw.writes, [])
        if LIB:
            c = hw.c_panel()
            self.assertEqual(LIB.billboard_panel_grayscale(ct.byref(c), cbuf(frame), cbuf(frame), FRAME_BYTES), -1)
            self.assertEqual(hw.writes, [])

    async def test_busy_assertion_timeout_fault_latch_and_recovery(self):
        for failure in ('no_assert', 'stuck_right', 'spi'):
            hw = FakeHardware()
            p = hw.python_panel()
            black, white = bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)
            black[0] = 1
            hw.never_assert = failure == 'no_assert'
            hw.stuck = 2 if failure == 'stuck_right' else None
            hw.fail_register = 0x10 if failure == 'spi' else None
            with hw.patches():
                with self.assertRaises(OSError):
                    await p.drive(black, white)
                self.assertIsNotNone(p.fault)
                self.assertEqual(hw.resets[-1], 0)
                count = len(hw.writes)
                with self.assertRaises(RuntimeError):
                    await p.drive(black, white)
                self.assertEqual(len(hw.writes), count)
                hw.never_assert = False
                hw.stuck = hw.fail_register = None
                await p.recover()
                self.assertIsNone(p.fault)

    @unittest.skipUnless(LIB, 'native C library not provided')
    async def test_c_faults_latch_and_recover(self):
        for failure in ('no_assert', 'stuck_right', 'spi'):
            hw = FakeHardware()
            p = hw.c_panel()
            black, white = (U8 * FRAME_BYTES)(), (U8 * FRAME_BYTES)()
            black[0] = 1
            hw.never_assert = failure == 'no_assert'
            hw.stuck = 2 if failure == 'stuck_right' else None
            hw.fail_register = 0x10 if failure == 'spi' else None
            expected = -2 if failure == 'spi' else -3
            self.assertEqual(LIB.billboard_panel_drive(ct.byref(p), black, white, None, FRAME_BYTES), expected)
            self.assertEqual(p.fault, expected)
            self.assertEqual(LIB.billboard_panel_drive(ct.byref(p), black, white, None, FRAME_BYTES), -4)
            hw.never_assert = False
            hw.stuck = hw.fail_register = None
            self.assertEqual(LIB.billboard_panel_recover(ct.byref(p)), 0)


class RenderTests(unittest.TestCase):
    def test_rgb565_three_tones_and_bounds(self):
        b, g = bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)
        rgb = b'\x00\x00\x10\x84\xff\xff'
        rgb565_to_grayscale(rgb, b, g, 0, 0, 3, 1, 6)
        self.assertEqual((b[0], g[0]), (0x80, 0x40))
        with self.assertRaises(ValueError):
            rgb565_to_grayscale(rgb, b, g, 959, 0, 3, 1, 6)

    def test_waveforms_match_pinned_baseline(self):
        import hashlib
        # Frozen digests guard against accidental waveform changes.
        for table, digest in (
            (BOLD, 'c21fa4a66412bef79d6043be71ccba834c4758b7aed8651d440087b72e9cae21'),
            (RED, '54406876c77d0410ff37bb89e98737f3a8d85950d9039cebb14e69fa5620b7b3'),
            (GRAY, '659e537e96b19ca3f67caaf7f275dd6506e3fd03e4b17c3c526efee75c467ad3'),
        ):
            self.assertEqual(hashlib.sha256(table).hexdigest(), digest)
        for table in (BOLD, RED, GRAY):
            self.assertEqual(len(table), 400)
            self.assertTrue(all(table[offset] == 0 for offset in range(240, 320, 8)))


if __name__ == '__main__':
    unittest.main()
