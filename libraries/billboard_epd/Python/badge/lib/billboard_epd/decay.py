"""Per-pixel pulse budgets stored as two independent 4-bit counters per byte.

Frames use MSB-first horizontal bits: 1 = white, 0 = black. Drive masks
use 1 = drive. Call complete() only after the physical refresh completes.
The planner is single-owner; never prepare another frame before complete().
"""

try:
    from micropython import viper
except ImportError:
    def viper(function):
        return function
    def ptr8(buffer):
        return buffer


@viper
def _prepare(target, counters, frame, black, white, size: int,
             black_pulses: int, white_pulses: int) -> int:
    previous = ptr8(target)
    remaining = ptr8(counters)
    requested = ptr8(frame)
    black_out = ptr8(black)
    white_out = ptr8(white)
    pending = 0
    for byte in range(size):
        desired = int(requested[byte])
        difference = desired ^ int(previous[byte])
        ink = paper = 0
        for bit in range(8):
            mask = 1 << bit
            location = byte * 4 + (bit >> 1)
            shift = (bit & 1) * 4
            packed = int(remaining[location])
            count = (packed >> shift) & 15
            if difference & mask:
                count = white_pulses if desired & mask else black_pulses
                remaining[location] = (packed & (255 ^ (15 << shift))) | (count << shift)
            if count:
                pending += 1
                if desired & mask:
                    paper |= mask
                else:
                    ink |= mask
        previous[byte] = desired
        black_out[byte] = ink
        white_out[byte] = paper
    return pending


@viper
def _complete(target, counters, black, white, size: int) -> int:
    requested = ptr8(target)
    remaining = ptr8(counters)
    black_sent = ptr8(black)
    white_sent = ptr8(white)
    pending = 0
    for byte in range(size):
        desired = int(requested[byte])
        delivered = (int(black_sent[byte]) & (desired ^ 255)) | (int(white_sent[byte]) & desired)
        for bit in range(8):
            location = byte * 4 + (bit >> 1)
            shift = (bit & 1) * 4
            packed = int(remaining[location])
            count = (packed >> shift) & 15
            if count and (delivered & (1 << bit)):
                count -= 1
                remaining[location] = (packed & (255 ^ (15 << shift))) | (count << shift)
            if count:
                pending += 1
    return pending


class Decay:
    def __init__(self, clean_frame):
        if not len(clean_frame):
            raise ValueError("empty framebuffer")
        self.target = bytearray(clean_frame)
        self.counters = bytearray(4 * len(clean_frame))
        self.pending = 0
        self.inflight = False

    def reset(self, clean_frame):
        if len(clean_frame) != len(self.target):
            raise ValueError("invalid framebuffer size")
        self.target[:] = clean_frame
        # Avoid a second large temporary allocation on MicroPython.
        for i in range(len(self.counters)):
            self.counters[i] = 0
        self.pending = 0
        self.inflight = False

    def prepare(self, frame, black, white, black_pulses=1, white_pulses=3):
        if self.inflight:
            raise RuntimeError("complete the previous pass first")
        if any(len(buf) != len(self.target) for buf in (frame, black, white)):
            raise ValueError("invalid framebuffer size")
        if (type(black_pulses) is not int or type(white_pulses) is not int
                or not 1 <= black_pulses <= 15 or not 1 <= white_pulses <= 15):
            raise ValueError("pulse budgets must be integers from 1 to 15")
        self.pending = _prepare(self.target, self.counters, frame, black, white,
                                len(self.target), black_pulses, white_pulses)
        self.inflight = bool(self.pending)
        return self.pending

    def complete(self, black, white):
        if not self.inflight:
            raise RuntimeError("no prepared pass")
        if any(len(buf) != len(self.target) for buf in (black, white)):
            raise ValueError("invalid drive-mask size")
        self.pending = _complete(self.target, self.counters, black, white, len(self.target))
        self.inflight = False
        return self.pending
