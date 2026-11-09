"""Exercise managed display state across cleans, failures and queued draws."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Python/badge/lib'))
from billboard_epd.display import Display


class RecordingPanel:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def full_refresh(self, black=None):
        self.calls.append(('full', None if black is None else bytes(black)))

    async def drive(self, black, white):
        self.calls.append(('drive', bytes(black), bytes(white)))
        if self.fail:
            raise OSError('injected transport failure')

    async def end_burst(self):
        self.calls.append(('end',))

    def _validate(self, *planes):
        if any(plane is not None and len(plane) != 1 for plane in planes):
            raise ValueError('wrong size')

    async def grayscale(self, black, gray):
        self.calls.append(('gray', bytes(black), bytes(gray)))

    async def recover(self):
        self.calls.append(('recover',))


class DisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.size_patch = patch('billboard_epd.display.FRAME_BYTES', 1)
        self.size_patch.start()
        self.addCleanup(self.size_patch.stop)
        self.panel = RecordingPanel()

    async def test_periodic_factory_clean_rebases_pending_pulses(self):
        display = Display(self.panel, clean_interval=2)
        await display.draw(b'\x00', black_pulses=5)
        self.assertEqual([call[0] for call in self.panel.calls],
                         ['full', 'drive', 'drive', 'full', 'end'])
        self.assertEqual(self.panel.calls[-2], ('full', b'\xff'))
        self.assertEqual(display._planner.pending, 0)
        self.assertEqual(display.passes_since_clean, 0)
        self.panel.calls.clear()
        await display.draw(b'\x00')
        self.assertEqual(self.panel.calls, [('end',)])

    async def test_failed_pass_keeps_counter_and_requires_new_baseline(self):
        display = Display(self.panel)
        self.panel.fail = True
        with self.assertRaises(OSError):
            await display.draw(b'\xfe', black_pulses=3)
        self.assertFalse(display._known)
        self.assertTrue(display._planner.inflight)
        self.assertEqual(display._planner.counters[0] & 15, 3)
        self.assertEqual(display.passes_since_clean, 0)
        self.panel.fail = False
        await display.recover()
        await display.draw(b'\xfe', black_pulses=3)
        self.assertEqual(sum(call[0] == 'drive' for call in self.panel.calls), 4)
        self.assertEqual(display._planner.pending, 0)

    async def test_input_snapshot_survives_wait_for_another_draw(self):
        display = Display(self.panel)
        await display._lock.acquire()
        frame = bytearray(b'\xfe')
        task = asyncio.create_task(display.draw(frame))
        await asyncio.sleep(0)
        frame[0] = 255
        display._lock.release()
        await task
        self.assertIn(('drive', b'\x01', b'\x00'), self.panel.calls)
        self.assertEqual(display._planner.target, b'\xfe')

    async def test_grayscale_invalidates_monochrome_history(self):
        display = Display(self.panel)
        await display.draw(b'\xfe')
        await display.grayscale(b'\x01', b'\x02')
        self.assertFalse(display._known)
        self.panel.calls.clear()
        await display.draw(b'\xfe')
        self.assertEqual(self.panel.calls[0], ('full', None))
        self.assertEqual(self.panel.calls[1], ('drive', b'\x01', b'\x00'))


if __name__ == '__main__':
    unittest.main()
