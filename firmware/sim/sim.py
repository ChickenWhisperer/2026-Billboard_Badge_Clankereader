"""Host simulator for the Hackaday Supercon 2026 Billboard Badge.

Runs the firmware in ../badge/, unchanged, on lv_micropython's Unix port (the same
MicroPython+LVGL the badge is built with). Only the hardware edges are replaced:
  * machine (Pin/Signal/ADC/SoftSPI/I2C/...) -> fakes; button pins read the sim's buttons,
                                               e-paper BUSY pins follow the simulated panel
  * neopixel                                 -> the row of 8 LED blocks in the window
  * the e-paper panel                        -> an image in the window. The firmware's own
                                               LVGL display and framebuffers run unchanged.
  * the SAO I2C bus                          -> a TCP server; SAO emulators connect to it
  * the badge filesystem                     -> ../badge mounted at /, ./data at /data
                                               (os.ilistdir gives 4-tuples, as on the badge)

See SIMULATOR.md for building the interpreter and how each piece works.

Usage:
    micropython sim/sim.py [--scale F] [--real-refresh] [--bw-time S] [--bwr-time S]
                           [--led-full N] [--sao-port P] [--no-repl]

    --scale F        Initial window scale, default 1.0 (the panel at 1:1). The window
                     can also be resized; the badge keeps its shape.
    --real-refresh   Make the e-paper take as long as the real one to refresh. Without
                     this, the image appears as soon as the firmware calls display.update().
    --bw-time S      Refresh time in seconds for an image with no red, default 2.
    --bwr-time S     Refresh time in seconds for an image with red, default 20.
    --led-full N     LED channel value drawn at full brightness, default 4. The firmware
                     uses small values (0-3) because the real LEDs are very bright.
    --sao-port P     TCP port for SAO emulators, default 47100. 0 turns it off.
    --no-repl        Don't run a REPL in the terminal. (There is none anyway when stdin
                     isn't a terminal.)
"""

import sys
import os
import time
import asyncio
import micropython

FACE_W = 1008  # Logical size of the simulated badge face; scaled to fit the window
FACE_H = 880
EPD_W = 960
EPD_H = 672


def _parse_args():
    options = {
        "scale": 1.0,
        "real-refresh": False,
        "bw-time": 2.0,
        "bwr-time": 20.0,
        "led-full": 4.0,
        "sao-port": 47100,
        "repl": True,
    }
    args = sys.argv[1:]
    while args:
        arg = args.pop(0)
        name = arg[2:]
        if arg == "--real-refresh":
            options[name] = True
        elif arg == "--no-repl":
            options["repl"] = False
        elif arg.startswith("--") and name in options and args:
            options[name] = float(args.pop(0))
        else:
            print(__doc__)
            sys.exit(1)
    options["sao-port"] = int(options["sao-port"])
    return options


OPTIONS = _parse_args()

_script = sys.argv[0]
if not _script.startswith("/"):
    _script = os.getcwd() + "/" + _script
SIM_DIR = _script.rsplit("/", 1)[0]
FIRMWARE_DIR = SIM_DIR + "/../badge"
DATA_DIR = SIM_DIR + "/data"


class _Module:
    """Stand-in for a module, placed in sys.modules before the firmware imports it."""

    def __init__(self, name, **attrs):
        self.__name__ = name
        for key, value in attrs.items():
            setattr(self, key, value)


# ---------------------------------------------------------------------------
# machine: the firmware creates its Pins, SPI and I2C when its modules are imported
# ---------------------------------------------------------------------------

import machine as _real_machine


class Pin:
    IN = 1
    OUT = 3
    OPEN_DRAIN = 7
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, id=None, mode=-1, pull=-1, value=None, **_):
        self.id = id
        self._value = 1 if value else 0
        self.source = None  # For inputs: a function the sim sets that returns the level

    def value(self, value=None):
        if value is None:
            return self.source() if self.source else self._value
        self._value = 1 if value else 0

    __call__ = value

    def on(self):
        self._value = 1

    def off(self):
        self._value = 0

    def init(self, *_, **__):
        pass

    def irq(self, *_, **__):
        return None


class Signal:
    def __init__(self, pin, invert=False):
        self.pin = pin
        self.invert = invert

    def value(self, value=None):
        if value is None:
            return self.pin.value() ^ self.invert
        self.pin.value(bool(value) ^ self.invert)

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)


class ADC:
    """Only the battery sense uses this: half the regulator's input, here USB power."""

    ATTN_0DB, ATTN_2_5DB, ATTN_6DB, ATTN_11DB = 0, 1, 2, 3
    WIDTH_9BIT, WIDTH_10BIT, WIDTH_11BIT, WIDTH_12BIT = 0, 1, 2, 3
    _MICROVOLTS = 2_350_000  # ~4.7 V on USB, through the 1/2 divider

    def __init__(self, pin, **_):
        pass

    def init(self, *_, **__):
        pass

    def atten(self, *_):
        pass

    def width(self, *_):
        pass

    def read_uv(self):
        return self._MICROVOLTS

    def read_u16(self):
        return self._MICROVOLTS * 65535 // 3_300_000

    def read(self):
        return self._MICROVOLTS * 4095 // 3_300_000


_spi_listener = None  # The simulated e-paper panel watches the bus for commands


class SoftSPI:
    MSB = 0
    LSB = 1

    def __init__(self, *_, **__):
        pass

    def init(self, *_, **__):
        pass

    def deinit(self):
        pass

    def write(self, buf):
        if _spi_listener:
            _spi_listener(buf)

    def read(self, nbytes, write=0):
        return bytes(nbytes)

    def readinto(self, buf, write=0):
        pass

    def write_readinto(self, write_buf, read_buf):
        self.write(write_buf)


_sao_bus = None  # SaoBus, created at startup


class I2C:
    """The SAO I2C bus. Transactions go to SAO emulators connected over TCP (SaoBus)."""

    def __init__(self, id=-1, scl=None, sda=None, freq=400000, **_):
        pass

    def init(self, *_, **__):
        pass

    def deinit(self):
        pass

    def scan(self):
        return _sao_bus.addresses() if _sao_bus else []

    def _xfer(self, addr, write, nread):
        if _sao_bus is None:
            raise OSError(19)  # ENODEV, as for an empty bus on the badge
        return _sao_bus.transfer(addr, bytes(write), nread)

    def writeto(self, addr, buf, stop=True):
        self._xfer(addr, buf, 0)
        return len(buf)

    def writevto(self, addr, vector, stop=True):
        self._xfer(addr, b"".join(bytes(v) for v in vector), 0)
        return sum(len(v) for v in vector)

    def readfrom(self, addr, nbytes, stop=True):
        return self._xfer(addr, b"", nbytes)

    def readfrom_into(self, addr, buf, stop=True):
        buf[:] = self._xfer(addr, b"", len(buf))

    @staticmethod
    def _mem(memaddr, addrsize):
        return memaddr.to_bytes(addrsize // 8, "big")

    def writeto_mem(self, addr, memaddr, buf, *, addrsize=8):
        self._xfer(addr, self._mem(memaddr, addrsize) + bytes(buf), 0)

    def readfrom_mem(self, addr, memaddr, nbytes, *, addrsize=8):
        return self._xfer(addr, self._mem(memaddr, addrsize), nbytes)

    def readfrom_mem_into(self, addr, memaddr, buf, *, addrsize=8):
        buf[:] = self._xfer(addr, self._mem(memaddr, addrsize), len(buf))


class I2CTarget:
    """The badge as an I2C target (lib/sao.py). Accepted, but nothing ever addresses it."""

    IRQ_ADDR_MATCH_READ = IRQ_ADDR_MATCH_WRITE = IRQ_READ_REQ = IRQ_WRITE_REQ = IRQ_END_READ = IRQ_END_WRITE = 0

    def __init__(self, *_, **__):
        pass

    def irq(self, *_, **__):
        pass

    def deinit(self):
        pass

    def readinto(self, buf):
        return 0

    def write(self, buf):
        return 0


def _reset():
    raise SystemExit("machine.reset() called")


_fake_machine = _Module("machine")
for _name in dir(_real_machine):  # Keep whatever the Unix port's machine has
    if not _name.startswith("__"):
        setattr(_fake_machine, _name, getattr(_real_machine, _name))
for _name, _value in (
    ("Pin", Pin),
    ("Signal", Signal),
    ("ADC", ADC),
    ("SoftSPI", SoftSPI),
    ("SPI", SoftSPI),
    ("I2C", I2C),
    ("SoftI2C", I2C),
    ("I2CTarget", I2CTarget),
    ("unique_id", lambda: b"\x5c\x00\x20\x26\x00\x00"),
    ("reset", _reset),
    ("soft_reset", _reset),
    ("freq", lambda *_: 240_000_000),
):
    setattr(_fake_machine, _name, _value)
sys.modules["machine"] = _fake_machine


# ---------------------------------------------------------------------------
# neopixel: same storage and errors as the real one (values must fit in a byte).
# Only write() changes what the window shows, as only write() lights the real LEDs.
# ---------------------------------------------------------------------------

_led_frame = None  # The colors last written, for the window to pick up


class NeoPixel:
    ORDER = (1, 0, 2, 3)  # GRB, as on the badge

    def __init__(self, pin, n, bpp=3, timing=1):
        self.pin = pin
        self.n = n
        self.bpp = bpp
        self.buf = bytearray(n * bpp)

    def __len__(self):
        return self.n

    def __setitem__(self, index, val):
        offset = index * self.bpp
        for i in range(self.bpp):
            self.buf[offset + self.ORDER[i]] = val[i]

    def __getitem__(self, index):
        offset = index * self.bpp
        return tuple(self.buf[offset + self.ORDER[i]] for i in range(self.bpp))

    def fill(self, val):
        for i in range(self.n):
            self[i] = val

    def write(self):
        global _led_frame
        _led_frame = [self[i][:3] for i in range(self.n)]


sys.modules["neopixel"] = _Module("neopixel", NeoPixel=NeoPixel)


# ---------------------------------------------------------------------------
# os and sys, where the Unix port differs from the badge's ESP32 port
# ---------------------------------------------------------------------------


def _copy_module(module, name, **overrides):
    fake = _Module(name)
    for attr in dir(module):
        if not attr.startswith("__"):
            setattr(fake, attr, getattr(module, attr))
    for attr, value in overrides.items():
        setattr(fake, attr, value)
    return fake


def _ilistdir(path="."):
    """os.ilistdir, giving (name, type, inode, size) as the badge's filesystem does.
    The Unix port's VfsPosix gives 3-tuples, and lib/app_switcher.py unpacks 4."""
    for entry in os.ilistdir(path):
        if len(entry) >= 4:
            yield entry
            continue
        name, kind, inode = entry[0], entry[1], entry[2]
        size = 0
        if kind == 0x8000:
            try:
                size = os.stat(path.rstrip("/") + "/" + name)[6]
            except OSError:
                pass
        yield (name, kind, inode, size)


sys.modules["os"] = _copy_module(os, "os", ilistdir=_ilistdir)

# lib/led_bar.py picks the real NeoPixel code by sys.platform == "esp32". The sim
# imports it with this stand-in for sys, so it takes the badge's own code path with
# the fake neopixel above. Nothing else sees it.
_esp32_sys = _copy_module(sys, "sys", platform="esp32")


def _import_as_esp32(name):
    real_sys = sys.modules.get("sys")
    sys.modules["sys"] = _esp32_sys
    try:
        __import__(name)
    finally:
        if real_sys is None:
            del sys.modules["sys"]
        else:
            sys.modules["sys"] = real_sys


# ---------------------------------------------------------------------------
# Filesystem: the badge runs from /. Its data directory is copied to sim/data on
# the first run, so the firmware's own writes don't touch ../badge/data.
# ---------------------------------------------------------------------------


def _copy_missing(src, dst):
    try:
        os.mkdir(dst)
    except OSError:
        pass
    existing = os.listdir(dst)
    for entry in os.ilistdir(src):
        name, kind = entry[0], entry[1]
        if kind == 0x4000:
            _copy_missing(src + "/" + name, dst + "/" + name)
        elif name not in existing:
            with open(src + "/" + name, "rb") as fin, open(dst + "/" + name, "wb") as fout:
                while True:
                    chunk = fin.read(16384)
                    if not chunk:
                        break
                    fout.write(chunk)


def _setup_filesystem():
    _copy_missing(FIRMWARE_DIR + "/data", DATA_DIR)
    try:
        import vfs
    except ImportError:
        vfs = os
    vfs.umount("/")
    vfs.mount(vfs.VfsPosix(FIRMWARE_DIR), "/")
    vfs.mount(vfs.VfsPosix(DATA_DIR), "/data")
    os.chdir("/")
    # Same search path as the badge
    while sys.path:
        sys.path.pop()
    sys.path.extend(["", ".frozen", "/lib"])


# ---------------------------------------------------------------------------
# SAO bus over TCP. Each SAO emulator connects to 127.0.0.1:<port>, and says which
# I2C addresses it answers, then serves transactions. Lines of ASCII, hex bytes:
#
#     SAO -> sim:  SAO <addr> [<addr> ...]       on connect, e.g. "SAO 50"
#     sim -> SAO:  I2C <addr> <write> <nread>    write bytes (or "-"), then read nread
#                                                bytes with a repeated start (if > 0)
#     SAO -> sim:  OK <read>                     the bytes read (or "-")
#                  NACK                          the device didn't acknowledge
#
# See sao_example.py.
# ---------------------------------------------------------------------------


class SaoBus:
    TIMEOUT_S = 1.0

    def __init__(self, port):
        import socket

        self._socket = socket
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(socket.getaddrinfo("127.0.0.1", port)[0][-1])
        self._server.listen(4)
        self._server.setblocking(False)
        self._devices = {}  # I2C address -> socket
        print(f"SAO bus listening on TCP port {port}")

    def addresses(self):
        return sorted(self._devices)

    def _drop(self, conn):
        for addr in [a for a, c in self._devices.items() if c is conn]:
            del self._devices[addr]
            print(f"[sao] device at 0x{addr:02x} disconnected")
        try:
            conn.close()
        except OSError:
            pass

    async def run(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:  # Nobody waiting
                await asyncio.sleep_ms(100)
                continue
            conn.settimeout(self.TIMEOUT_S)
            try:
                fields = conn.readline().split()
                assert fields[0] == b"SAO" and len(fields) > 1
                addrs = [int(f, 16) for f in fields[1:]]
            except Exception:
                print("[sao] connection didn't start with 'SAO <addr> ...'; closed")
                conn.close()
                continue
            for addr in addrs:
                if addr in self._devices:
                    self._drop(self._devices[addr])
                self._devices[addr] = conn
                print(f"[sao] device at 0x{addr:02x} connected")

    def transfer(self, addr, write, nread):
        conn = self._devices.get(addr)
        if conn is None:
            raise OSError(19)  # ENODEV: nothing answered at that address
        out = "I2C {:02x} {} {}\n".format(addr, write.hex() or "-", nread)
        try:
            conn.write(out.encode())
            reply = conn.readline().split()
        except OSError:
            reply = []
        if not reply:
            self._drop(conn)
            raise OSError(110)  # ETIMEDOUT
        if reply[0] != b"OK":
            raise OSError(19)
        data = b"" if len(reply) < 2 or reply[1] == b"-" else bytes.fromhex(reply[1].decode())
        if len(data) != nread:
            raise OSError(5)  # EIO
        return data


# ---------------------------------------------------------------------------
# E-paper: the window shows the firmware's black and red framebuffers.
# By default the image appears when the firmware calls display.update(). With
# --real-refresh, it appears when the panel finishes refreshing: the BUSY pins go
# low when the firmware sends the refresh command (0x15) and stay low for the
# refresh time, and the firmware's own code waits on them as on the badge.
# ---------------------------------------------------------------------------

_EPD_REFRESH_CMD = 0x15


@micropython.viper
def _render_epd(black, red, out, n: int):
    """Two 1-bit MONO_HLSB planes -> RGB565. Red wins where both are set."""
    b = ptr8(black)
    r = ptr8(red)
    o = ptr16(out)
    for i in range(n):
        byte = i >> 3
        mask = 0x80 >> (i & 7)
        if r[byte] & mask:
            o[i] = 0xF800
        elif b[byte] & mask:
            o[i] = 0x0000
        else:
            o[i] = 0xFFFF


class EpdPanel:
    def __init__(self, epd, face):
        self._display = epd.display
        self._face = face
        self._dc = epd.EPD_DC_PIN
        self._cs = (epd.EPD_CS1_PIN, epd.EPD_CS2_PIN)
        self._busy_until = None
        self._pending = None  # Image the firmware last asked for (real-refresh only)
        self._refreshing = None  # Image the panel is refreshing to
        self._no_red = bytes(len(memoryview(self._display.red)))
        epd.EPD_BUSY1_PIN.source = epd.EPD_BUSY2_PIN.source = self._busy_level

        firmware_update = self._display.update

        def update():
            self._on_update()
            firmware_update()

        self._display.update = update

        global _spi_listener
        _spi_listener = self._on_spi

    def _snapshot(self):
        return bytes(memoryview(self._display.black)), bytes(memoryview(self._display.red))

    def _on_update(self):
        if OPTIONS["real-refresh"]:
            self._pending = self._snapshot()
        else:
            self._face.show_epd(self._display.black, self._display.red)

    def _busy(self):
        return self._busy_until is not None and time.ticks_diff(self._busy_until, time.ticks_ms()) > 0

    def _busy_level(self):
        return 0 if self._busy() else 1  # BUSY is active low

    def _on_spi(self, buf):
        if not OPTIONS["real-refresh"] or len(buf) != 1 or buf[0] != _EPD_REFRESH_CMD:
            return
        if self._dc.value() or (self._cs[0].value() and self._cs[1].value()):
            return  # Data, or not addressed to the panel
        self._refreshing = self._pending or self._snapshot()
        has_red = self._refreshing[1] != self._no_red
        seconds = OPTIONS["bwr-time"] if has_red else OPTIONS["bw-time"]
        self._busy_until = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
        print(f"[sim] e-paper refreshing ({'BWR' if has_red else 'BW'}, {seconds} s)")

    def poll(self):
        if self._refreshing is None:
            return
        if self._busy():
            left = time.ticks_diff(self._busy_until, time.ticks_ms()) // 1000 + 1
            self._face.set_note(f"e-paper refreshing... {left} s")
        else:
            self._face.show_epd(*self._refreshing)
            self._refreshing = None
            self._face.set_note(None)


# ---------------------------------------------------------------------------
# The window: e-paper, LED row, and the buttons, laid out on a fixed-size badge
# face that is scaled to fit the window, with black around it.
# ---------------------------------------------------------------------------

# Physical left-to-right order on the badge (SW4..SW7), then Boot. SDL scancodes of
# the keys, and the label shown for them. Names come from the firmware's Button objects.
_BUTTONS = (
    ("UserA", 4, "A"),
    ("MenuMinus", 80, "Left"),
    ("MenuPlus", 79, "Right"),
    ("UserB", 5, "B"),
    ("Boot", 44, "Space"),
)

_FACE_COLOR = 0x1E1E1E
_LED_OFF_COLOR = 0x2E2E2E
_STATUS_ON_COLOR = 0x40FF40


def _sdl_keyboard_state():
    """SDL's array of which keys are down, indexed by scancode. LVGL's SDL keyboard
    driver only reports the last key, and the badge's buttons can be held together.
    The array is updated whenever LVGL's SDL driver reads events."""
    import ffi
    import uctypes

    sdl = ffi.open("libSDL2-2.0.so.0")  # Already loaded by LVGL's SDL driver
    get_state = sdl.func("p", "SDL_GetKeyboardState", "p")
    return uctypes.bytearray_at(get_state(None), 512)  # SDL_NUM_SCANCODES


def _font_for(pixels):
    import lvgl as lv

    best = lv.font_montserrat_12
    for size in range(10, 50, 2):
        font = getattr(lv, f"font_montserrat_{size}", None)
        if font and size <= pixels:
            best = font
    return best


class Face:
    def __init__(self, scale, buttons, status_led):
        import lvgl as lv

        self.lv = lv
        self._status_led = status_led
        self._status_shown = None
        firmware_disp = lv.display_get_default()

        # LVGL's SDL window (resizable; closing it ends the process). The firmware draws
        # on the default display, so that must stay the firmware's.
        self._disp = lv.sdl_window_create(int(FACE_W * scale), int(FACE_H * scale))
        lv.sdl_window_set_title(self._disp, "Billboard Badge simulator")
        firmware_disp.set_default()
        mouse = lv.sdl_mouse_create()
        mouse.set_display(self._disp)
        self._keys = _sdl_keyboard_state()

        self._disp.add_event_cb(lambda _: self._layout(), lv.EVENT.RESOLUTION_CHANGED, None)

        screen = self._disp.get_screen_active()
        screen.set_style_bg_color(lv.color_hex(0x000000), 0)
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self._placed = []  # (obj, x, y, w, h, font size or 0), in face coordinates

        face = self._box(screen, 0, 0, FACE_W, FACE_H, _FACE_COLOR)

        # E-paper: white until the firmware first updates it
        self._epd_buf = bytearray(b"\xff" * (EPD_W * EPD_H * 2))
        self._epd_dsc = lv.image_dsc_t(
            {
                "header": {
                    "magic": lv.IMAGE_HEADER_MAGIC,
                    "cf": lv.COLOR_FORMAT.RGB565,
                    "w": EPD_W,
                    "h": EPD_H,
                    "stride": EPD_W * 2,
                },
                "data_size": len(self._epd_buf),
                "data": self._epd_buf,
            }
        )
        self._epd = lv.image(screen)
        self._epd.set_src(self._epd_dsc)
        self._epd.set_inner_align(lv.image.ALIGN.STRETCH)
        self._place(self._epd, 24, 24, EPD_W, EPD_H)

        self._note = lv.label(screen)
        self._note.set_style_bg_color(lv.color_hex(0x000000), 0)
        self._note.set_style_bg_opa(160, 0)
        self._note.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        self._note.set_style_pad_all(6, 0)
        self._note.add_flag(lv.obj.FLAG.HIDDEN)
        self._place(self._note, 32, 32, 0, 0, 18)

        # LEDs: 8 blocks across the panel's width
        self._leds = []
        for i in range(8):
            led = self._box(screen, 24 + i * 120 + 32, 720, 56, 56, _LED_OFF_COLOR, radius=6)
            self._leds.append(led)
        self._led_shown = None

        # Buttons, then the status LED, in 6 slots across the panel's width
        self._buttons = {}  # firmware Button -> (lv button, scancode)
        self._key_shown = {}  # firmware Button -> whether its key was down at the last poll
        self._gui_down = set()
        for slot, (name, scancode, key_label) in enumerate(_BUTTONS):
            fw_button = buttons[name]
            btn = lv.button(screen)
            self._place(btn, 24 + slot * 160 + 12, 800, 136, 56)
            label = lv.label(btn)
            label.set_text(f"{fw_button.name}\n({key_label})")
            label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label.center()
            self._place(label, None, None, 0, 0, 16)
            btn.add_event_cb(lambda _, b=fw_button: self._gui(b, True), lv.EVENT.PRESSED, None)
            btn.add_event_cb(lambda _, b=fw_button: self._gui(b, False), lv.EVENT.RELEASED, None)
            btn.add_event_cb(lambda _, b=fw_button: self._gui(b, False), lv.EVENT.PRESS_LOST, None)
            self._buttons[fw_button] = (btn, scancode)
            self._key_shown[fw_button] = False
            fw_button.pin.pin.source = lambda b=fw_button: 0 if self._is_down(b) else 1

        self._status = self._box(screen, 24 + 5 * 160 + 68, 808, 24, 24, _LED_OFF_COLOR, radius=12)
        status_label = lv.label(screen)
        status_label.set_text("status")
        status_label.set_style_text_color(lv.color_hex(0xA0A0A0), 0)
        self._place(status_label, 24 + 5 * 160 + 54, 840, 0, 0, 14)

        self._layout()

    # --- layout ---------------------------------------------------------

    def _place(self, obj, x, y, w, h, font=0):
        self._placed.append((obj, x, y, w, h, font))

    def _box(self, parent, x, y, w, h, color, radius=0):
        lv = self.lv
        box = lv.obj(parent)
        box.remove_style_all()
        box.set_style_bg_opa(lv.OPA.COVER, 0)
        box.set_style_bg_color(lv.color_hex(color), 0)
        box.set_style_radius(radius, 0)
        box.remove_flag(lv.obj.FLAG.CLICKABLE)
        box.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self._place(box, x, y, w, h)
        return box

    def _layout(self):
        width = self._disp.get_horizontal_resolution()
        height = self._disp.get_vertical_resolution()
        s = min(width / FACE_W, height / FACE_H)
        ox = (width - FACE_W * s) / 2
        oy = (height - FACE_H * s) / 2
        for obj, x, y, w, h, font in self._placed:
            if font:
                obj.set_style_text_font(_font_for(font * s), 0)
            if w:
                obj.set_size(round(w * s), round(h * s))
            if x is not None:
                obj.set_pos(round(ox + x * s), round(oy + y * s))
            elif font:
                obj.center()
        # The radius scales too
        for led in self._leds:
            led.set_style_radius(round(6 * s), 0)
        self._status.set_style_radius(round(12 * s), 0)

    # --- e-paper ----------------------------------------------------------

    def show_epd(self, black, red):
        _render_epd(black, red, self._epd_buf, EPD_W * EPD_H)
        self._epd.invalidate()

    def set_note(self, text):
        if text:
            self._note.set_text(text)
            self._note.remove_flag(self.lv.obj.FLAG.HIDDEN)
        else:
            self._note.add_flag(self.lv.obj.FLAG.HIDDEN)

    # --- LEDs -----------------------------------------------------------

    @staticmethod
    def _led_color(rgb):
        full = OPTIONS["led-full"]
        out = 0
        for c in rgb:
            level = 0 if c <= 0 else int(255 * min(1.0, c / full) ** 0.5)
            out = (out << 8) | level
        return out or _LED_OFF_COLOR

    def poll(self):
        frame = _led_frame
        if frame is not None and frame != self._led_shown:
            self._led_shown = frame
            for led, rgb in zip(self._leds, frame):
                led.set_style_bg_color(self.lv.color_hex(self._led_color(rgb)), 0)
        status = bool(self._status_led.value())
        if status != self._status_shown:
            self._status_shown = status
            color = _STATUS_ON_COLOR if status else _LED_OFF_COLOR
            self._status.set_style_bg_color(self.lv.color_hex(color), 0)
        # Show a held key as a pressed on-screen button
        for fw_button, (btn, scancode) in self._buttons.items():
            down = bool(self._keys[scancode])
            if down != self._key_shown[fw_button]:
                self._key_shown[fw_button] = down
                if down:
                    btn.add_state(self.lv.STATE.PRESSED)
                elif fw_button not in self._gui_down:
                    btn.remove_state(self.lv.STATE.PRESSED)

    # --- buttons --------------------------------------------------------

    def _is_down(self, fw_button):
        return bool(self._keys[self._buttons[fw_button][1]]) or fw_button in self._gui_down

    def _gui(self, fw_button, down):
        if down:
            self._gui_down.add(fw_button)
        else:
            self._gui_down.discard(fw_button)


# ---------------------------------------------------------------------------
# Wire it up and run the firmware's own main()
# ---------------------------------------------------------------------------


async def _lvgl_loop(lv):
    # Runs LVGL's timers: the window's redraws and SDL's events (keys, mouse, resize).
    while True:
        lv.timer_handler()
        await asyncio.sleep_ms(33)


async def _poll_loop(face, panel):
    while True:
        face.poll()
        panel.poll()
        await asyncio.sleep_ms(20)


# ---------------------------------------------------------------------------
# REPL in the terminal, running alongside the firmware. Line-based: the terminal
# does the line editing, and the firmware keeps running between lines.
# ---------------------------------------------------------------------------

_REPL_BANNER = """
[sim] REPL ready. The firmware keeps running while you type. Try:
    press(UserA)               press and release a button (also MenuPlus, MenuMinus, UserB, Boot)
    leds.fill((4, 0, 0))       light every LED red. While the menu is open it redraws
                               the LEDs constantly, so launch an app first (press(UserA)).
    switcher.apps              the installed apps
    display.clear(); display.black.text("hi", 400, 300, 1); display.update()
End a block (if/for/def...) with an empty line. Ctrl-D closes the REPL; Ctrl-C quits.
"""


def _stdin_is_terminal():
    try:
        import termios

        termios.tcgetattr(0)
        return True
    except Exception:
        return False


async def _repl(namespace):
    import select

    poller = select.poll()
    poller.register(0, select.POLLIN)
    await asyncio.sleep_ms(1000)  # Let the firmware's start-up messages go first
    print(_REPL_BANNER)
    lines = []
    while True:
        sys.stdout.write("... " if lines else ">>> ")
        while not poller.poll(0):
            await asyncio.sleep_ms(50)
        line = sys.stdin.readline()
        if not line:  # Ctrl-D
            print("\n[sim] REPL closed. The badge keeps running; Ctrl-C quits.")
            return
        line = line.rstrip("\n")
        stripped = line.rstrip()
        if lines or stripped.endswith(":") or stripped.endswith("\\"):
            if line.strip():
                lines.append(line)
                continue
            source, mode, lines = "\n".join(lines) + "\n", "exec", []
        else:
            source, mode = line, "single"  # "single" prints the value of an expression
        if not source.strip():
            continue
        try:
            exec(compile(source, "<stdin>", mode), namespace)
        except Exception as exc:
            sys.print_exception(exc)


def _repl_namespace(main, face, lv):
    import lib.button

    namespace = dict(main.__dict__)  # leds, display, switcher, UserA, ... as main.py sees them
    namespace.update(lvgl=lv, lv=lv)
    for name, _, _ in _BUTTONS:
        namespace[name] = getattr(lib.button, name)

    def press(button, ms=150):
        """Press a firmware Button (e.g. MenuPlus) for ms milliseconds."""

        async def hold():
            face._gui(button, True)
            await asyncio.sleep_ms(ms)
            face._gui(button, False)

        asyncio.create_task(hold())

    namespace["press"] = press
    return namespace


def run():
    global _sao_bus
    _setup_filesystem()
    if OPTIONS["sao-port"]:
        try:
            _sao_bus = SaoBus(OPTIONS["sao-port"])
        except OSError as exc:  # e.g. another simulator already has the port
            print(f"No SAO bus: can't listen on port {OPTIONS['sao-port']} ({exc})")

    import boot  # noqa: F401  As on the badge, boot.py runs before main.py

    _import_as_esp32("lib.led_bar")  # Before anything else imports it

    # main.py resets the badge if an LVGL display already exists when it starts (one
    # left over from a soft reset), so the sim creates its window after importing it.
    # main.main() doesn't run until the sim calls it below.
    import main
    import lvgl as lv
    import lib.epd
    import lib.button
    import lib.board

    # The badge never runs LVGL's timers: the firmware redraws its display with
    # lvgl.refr_now(). The sim runs them for its window, so stop them redrawing the
    # firmware's display on their own (its refresh timer is resumed on every change).
    lib.epd.display.lv_display.get_refr_timer().set_period(0x7FFFFFFF)

    buttons = {name: getattr(lib.button, name) for name, _, _ in _BUTTONS}
    face = Face(OPTIONS["scale"], buttons, lib.board.status_led)
    panel = EpdPanel(lib.epd, face)

    async def sim_main():
        asyncio.create_task(_lvgl_loop(lv))
        asyncio.create_task(_poll_loop(face, panel))
        if _sao_bus:
            asyncio.create_task(_sao_bus.run())
        if OPTIONS["repl"] and _stdin_is_terminal():
            asyncio.create_task(_repl(_repl_namespace(main, face, lv)))
        await main.main()

    print("Supercon 2026 badge simulator")
    asyncio.run(sim_main())


run()
