"""Photo Upload: send a photo from your phone to the badge over WiFi

Setup (once):
  Copy this file to /apps/ and photo_upload.html to /data/. That's it: the first
  time you open the app it starts WiFi setup and walks you through it on screen.

WiFi setup mode:
  The badge starts its own open network, "Billboard-Setup-xxxx", and shows a QR
  code. Scan it with your phone camera to join; the setup page opens by itself
  (or open http://192.168.4.1). Tap your hotspot in the list, type its password
  and the PIN shown on the badge, and Save. The badge then joins your hotspot.
  You can save several hotspots; the badge uses whichever one is on.

Use:
  - Turn on your phone's hotspot, then launch "Photo Upload" on the badge.
  - The badge joins the hotspot and shows a QR code, an address and a PIN.
  - Scan the QR code (or open the address), take a photo, enter the PIN, Send.

Buttons:
  Connected:     User A - next saved upload     User B - show the QR/PIN screen
  WiFi problem:  User A - try again             User B - WiFi setup
  WiFi setup:    User A - leave setup and try the saved hotspots

The phone does all the image work (crop, resize, dither to black/white/red)
and sends the badge its native format: two 1-bit planes (black, then red),
960 x 672, 120 bytes per row, most significant bit = leftmost pixel. The
badge copies those bytes straight into the display framebuffers.

Uploads are only accepted while this app is on screen, so nobody can paint
over another app. Up to MAX_SAVED uploads are kept in /data/uploads/.
Hotspots are saved in /data/wifi.json. Changing them needs the PIN too.

LEDs: blue chase = connecting, green blink = upload received.
"""

import asyncio as aio
import gc
import json
import os
import random
import time

import framebuf

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds

WIDTH = 960
HEIGHT = 672
PLANE_BYTES = WIDTH * HEIGHT // 8  # 80,640
FRAME_BYTES = PLANE_BYTES * 2  # 161,280: black plane then red plane

WIFI_CONFIG = "/data/wifi.json"
PAGE_FILE = "/data/photo_upload.html"
UPLOAD_DIR = "/data/uploads"
MAX_SAVED = 12
MAX_NETWORKS = 8
HTTP_PORT = 80
CONNECT_TIMEOUT_S = 25
WAIT_FOR_HOTSPOT_S = 90  # after setup, how long to keep looking for the hotspot
HOSTNAME = "billboard"
SETUP_SSID = "Billboard-Setup"  # a per-badge suffix is added so badges don't collide


# ------------------------------------------------------------ QR codes -----
# Tiny QR encoder: byte mode, error correction level L, versions 1-6 (up to
# 134 bytes). Plenty for a URL or a WiFi join code.

_QR_VERSIONS = {1: (26, 7, 1), 2: (44, 10, 1), 3: (70, 15, 1),
                4: (100, 20, 1), 5: (134, 26, 1), 6: (172, 18, 2)}
_QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
_EXP = bytearray(512)
_LOG = bytearray(256)
_v = 1
for _i in range(255):
    _EXP[_i] = _v
    _LOG[_v] = _i
    _v <<= 1
    if _v & 0x100:
        _v ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_ec(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gmul(g, _EXP[i])
        gen = nxt
    rem = [0] * n
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for j in range(n):
                rem[j] ^= _gmul(gen[j + 1], factor)
    return rem


def _qr_codewords(data, ver):
    total, ec_n, blocks = _QR_VERSIONS[ver]
    data_n = total - ec_n * blocks
    bits = []

    def put(val, length):
        for i in range(length - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)
    put(len(data), 8)
    for b in data:
        put(b, 8)
    put(0, min(4, data_n * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    out = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        out.append(v)
    i = 0
    while len(out) < data_n:
        out.append(0xEC if i % 2 == 0 else 0x11)
        i += 1
    per = data_n // blocks
    dblocks = [out[i * per:(i + 1) * per] for i in range(blocks)]
    eblocks = [_rs_ec(b, ec_n) for b in dblocks]
    res = []
    for i in range(per):
        for b in dblocks:
            res.append(b[i])
    for i in range(ec_n):
        for b in eblocks:
            res.append(b[i])
    return res


def _qr_mask(m, r, c):
    if m == 0:
        return (r + c) % 2 == 0
    if m == 1:
        return r % 2 == 0
    if m == 2:
        return c % 3 == 0
    if m == 3:
        return (r + c) % 3 == 0
    if m == 4:
        return (r // 2 + c // 3) % 2 == 0
    if m == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if m == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _qr_format(mask):
    data = (0b01 << 3) | mask
    v = data << 10
    for i in range(14, 9, -1):
        if v & (1 << i):
            v ^= 0x537 << (i - 10)
    return ((data << 10) | v) ^ 0x5412


def qr_matrix(text):
    """Rows of 0/1 (1 = dark), without the quiet zone."""
    data = text.encode() if isinstance(text, str) else bytes(text)
    ver = None
    for v in range(1, 7):
        total, ec_n, blocks = _QR_VERSIONS[v]
        if len(data) <= total - ec_n * blocks - 2:
            ver = v
            break
    if ver is None:
        raise ValueError("QR text too long")
    size = 17 + 4 * ver
    m = [[0] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def setf(r, c, val):
        m[r][c] = val
        fixed[r][c] = True

    for (r0, c0) in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < size and 0 <= cc < size:
                    on = (0 <= r <= 6 and 0 <= c <= 6 and
                          (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)))
                    setf(rr, cc, 1 if on else 0)
    for i in range(8, size - 8):
        setf(6, i, 1 - i % 2)
        setf(i, 6, 1 - i % 2)
    al = _QR_ALIGN[ver]
    for r in al:
        for c in al:
            if fixed[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    setf(r + dr, c + dc, 1 if max(abs(dr), abs(dc)) != 1 else 0)
    for i in range(9):
        if not fixed[8][i]:
            setf(8, i, 0)
        if not fixed[i][8]:
            setf(i, 8, 0)
    for i in range(8):
        setf(8, size - 1 - i, 0)
        setf(size - 1 - i, 8, 0)
    setf(size - 8, 8, 1)

    bits = []
    for b in _qr_codewords(data, ver):
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    idx, up, c = 0, True, size - 1
    while c > 0:
        if c == 6:
            c -= 1
        for r in (range(size - 1, -1, -1) if up else range(size)):
            for cc in (c, c - 1):
                if not fixed[r][cc]:
                    m[r][cc] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        up = not up
        c -= 2

    best, best_score = None, None
    for mask in range(8):
        out = [row[:] for row in m]
        for r in range(size):
            for c in range(size):
                if not fixed[r][c] and _qr_mask(mask, r, c):
                    out[r][c] ^= 1
        f = _qr_format(mask)
        for i in range(15):
            bit = (f >> i) & 1
            if i < 6:
                out[i][8] = bit
            elif i < 8:
                out[i + 1][8] = bit
            else:
                out[8][14 - i if i > 8 else 7] = bit
            if i < 8:
                out[8][size - 1 - i] = bit
            else:
                out[size - 15 + i][8] = bit
        out[size - 8][8] = 1
        score = _qr_penalty(out, size)
        if best_score is None or score < best_score:
            best, best_score = out, score
    return best


def _qr_penalty(m, size):
    score = 0
    for lines in (m, [list(col) for col in zip(*m)]):
        for row in lines:
            run = 1
            for i in range(1, size):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(sum(row) for row in m)
    return score + 10 * (abs(dark * 20 - size * size * 10) // (size * size))


def draw_qr(fb, text, x, y, box):
    """Draw a QR code (with its quiet zone) as large as fits in a box x box square."""
    m = qr_matrix(text)
    n = len(m) + 8
    scale = box // n
    x += (box - n * scale) // 2
    y += (box - n * scale) // 2
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            if v:
                fb.fill_rect(x + (c + 4) * scale, y + (r + 4) * scale, scale, scale, 1)


# ---------------------------------------------------------------- drawing --

def big_text(fb, text, x, y, scale, color=1):
    """Draw text with framebuf's 8x8 font, blown up `scale` times."""
    w = 8 * len(text)
    if not w:
        return
    buf = bytearray(w)  # 8 rows x w columns, MONO_VLSB is 1 byte per column
    small = framebuf.FrameBuffer(buf, w, 8, framebuf.MONO_VLSB)
    small.text(text, 0, 0, 1)
    for col in range(w):
        bits = buf[col]
        if not bits:
            continue
        for row in range(8):
            if bits & (1 << row):
                fb.fill_rect(x + col * scale, y + row * scale, scale, scale, color)


def centered(fb, text, y, scale, color=1, left=0, width=WIDTH):
    x = left + (width - 8 * scale * len(text)) // 2
    big_text(fb, text, max(left, x), y, scale, color)


def wrap(text, width):
    lines, line = [], ""
    for word in text.split(" "):
        trial = (line + " " + word).strip()
        if len(trial) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def fit_scale(text, max_scale, width=WIDTH - 40):
    """Largest scale at which text fits the given width."""
    s = max_scale
    while s > 1 and 8 * s * len(text) > width:
        s -= 1
    return s


def ascii_name(name):
    """A network name made safe for the 8x8 font (which is ASCII only)."""
    if isinstance(name, bytes):
        name = name.decode("utf-8", "ignore")
    return "".join(c if 32 <= ord(c) < 127 else ("'" if c in "‘’" else "?") for c in name)


def norm_name(name):
    """Compare network names ignoring case, outer spaces and curly vs straight apostrophes."""
    if isinstance(name, bytes):
        name = name.decode("utf-8", "ignore")
    for ch in ("’", "‘", "`"):
        name = name.replace(ch, "'")
    return name.strip().lower()


# --------------------------------------------------------- saved networks --

def load_networks():
    """Saved hotspots from /data/wifi.json as [(ssid, password), ...], newest first.

    Accepts the older {"ssid": ..., "password": ...} form too, and forgives a
    UTF-8 byte-order mark (Windows Notepad) and curly quotes (macOS TextEdit).
    Returns (networks, problem) where problem is a message or None."""
    try:
        with open(WIFI_CONFIG, "rb") as f:
            raw = f.read()
    except OSError:
        return [], None
    text = raw.decode("utf-8", "ignore")
    text = text.replace("﻿", "")
    for bad, good in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(bad, good)
    if not text.strip():
        return [], None
    try:
        cfg = json.loads(text)
    except Exception:
        return [], "wifi.json couldn't be read, so it was ignored. Saving a hotspot in setup replaces it."
    entries = cfg.get("networks") if isinstance(cfg, dict) and "networks" in cfg else [cfg]
    nets = []
    for e in entries or []:
        try:
            ssid = str(e["ssid"])
        except Exception:
            continue
        if ssid and not ssid.startswith("YOUR HOTSPOT"):
            nets.append((ssid, str(e.get("password", ""))))
    return nets, None


def save_networks(nets):
    data = {"networks": [{"ssid": s, "password": p} for s, p in nets[:MAX_NETWORKS]]}
    tmp = WIFI_CONFIG + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(data))
    try:
        os.remove(WIFI_CONFIG)
    except OSError:
        pass
    os.rename(tmp, WIFI_CONFIG)


# ------------------------------------------------------------ captive DNS --

class CaptiveDNS:
    """Answers every DNS lookup with the badge's address while in setup mode.
    That makes phones pop up the setup page by themselves when they join."""

    def __init__(self, ip):
        import socket
        self.ip = bytes(int(p) for p in ip.split("."))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(socket.getaddrinfo("0.0.0.0", 53)[0][-1])
        self.running = True

    def reply(self, q):
        if len(q) < 12:
            return None
        # Find the end of the question: the name's labels, then type and class
        i = 12
        while i < len(q) and q[i]:
            i += 1 + q[i]
        end = i + 5
        if end > len(q):
            return None
        qtype = (q[i + 1] << 8) | q[i + 2]
        answer_a = qtype == 1
        head = q[:2] + b"\x81\x80" + b"\x00\x01" + (b"\x00\x01" if answer_a else b"\x00\x00") + b"\x00\x00\x00\x00"
        out = head + q[12:end]
        if answer_a:
            out += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + self.ip
        return out

    async def run(self):
        while self.running:
            try:
                q, addr = self.sock.recvfrom(512)
                r = self.reply(q)
                if r:
                    self.sock.sendto(r, addr)
            except OSError:
                await aio.sleep_ms(40)
            except Exception as ex:
                print("Photo Upload: DNS error:", ex)
                await aio.sleep_ms(200)
        self.sock.close()

    def stop(self):
        self.running = False


# ----------------------------------------------------------- the app ------

class PhotoUploadApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Photo Upload"
        self.sleep_ms = 50
        self.launched = False
        self.show_battery_icon = False  # keep the corner clean on photos

        self.pin = "%04d" % random.randint(0, 9999)
        self.ip = None
        self.ssid_in_use = None
        self.error = None
        self.notice = None  # extra line for the setup screen
        self.nearby = []  # [(name, rssi, secure)], strongest first
        self.state = "idle"  # idle, connecting, serving, failed, setup
        self._task = None
        self._server_started = False
        self._watching = False
        self._sta = None
        self._ap = None
        self._dns = None
        self._setup_ssid = None
        self._frame = None  # receive buffer
        self._pending = False  # an upload arrived and should go on screen
        self._saved_idx = -1
        self._spin = 0

    # ---- is this app the one on screen? --------------------------------

    def _on_screen(self):
        try:
            from lib.app_switcher import switcher  # late import, avoids a cycle
            return (not switcher.selecting_mode) and switcher.apps[switcher.active_idx] is self
        except Exception:
            return True

    # ---- screens --------------------------------------------------------

    def _header(self, title):
        display.clear()
        display.red.fill_rect(0, 0, WIDTH, 100, 1)
        centered(display.red, title, 26, 6, 0)  # white text knocked out of the red band

    def _draw(self):
        if self.state == "serving":
            self._draw_serving()
        elif self.state == "setup":
            self._draw_setup()
        elif self.state == "failed":
            self._draw_failed()

    def _draw_serving(self):
        self._header("PHOTO UPLOAD")
        black, red = display.black, display.red
        url = "http://" + self.ip
        draw_qr(black, url, 30, 130, 420)
        left, width = 470, WIDTH - 470 - 20
        centered(black, "Scan, or open:", 150, 3, 1, left, width)
        centered(black, url, 200, fit_scale(url, 4, width), 1, left, width)
        centered(black, "PIN", 290, 4, 1, left, width)
        centered(red, self.pin, 340, 10, 1, left, width)
        if self.ssid_in_use:
            on = "on " + ascii_name(self.ssid_in_use)
            centered(black, on, 460, fit_scale(on, 3, width), 1, left, width)
        centered(black, "A: saved photos   B: this screen", 610, 3)
        display.update()

    def _draw_setup(self):
        self._header("WIFI SETUP")
        black, red = display.black, display.red
        draw_qr(black, "WIFI:T:nopass;S:%s;;" % self._setup_ssid, 20, 120, 400)
        left, width = 440, WIDTH - 440 - 20
        y = 130
        lines = (
            (black, "1. Scan to join", 3),
            (red, self._setup_ssid, fit_scale(self._setup_ssid, 3, width)),
            (black, "2. The setup page opens.", 2),
            (black, "   If it doesn't, open", 2),
            (black, "   http://" + (self.ip or "192.168.4.1"), 2),
            (black, "3. Pick your hotspot, type", 2),
            (black, "   its password and this PIN:", 2),
        )
        for fb, text, scale in lines:
            big_text(fb, text, left, y, scale)
            y += 8 * scale + 14
        centered(red, self.pin, y + 6, 9, 1, left, width)
        if self.notice:
            for i, line in enumerate(wrap(self.notice, 56)[:2]):
                centered(black, line, 560 + i * 22, 2)
        centered(black, "A: leave setup, try saved hotspots", 625, 2)
        display.update()

    def _draw_failed(self):
        self._header("PHOTO UPLOAD")
        black, red = display.black, display.red
        centered(red, "WiFi problem", 130, 6)
        y = 210
        for line in wrap(self.error or "Unknown error", 34):
            centered(black, line, y, 3)
            y += 34
        if self.nearby:
            y += 14
            centered(black, "Networks the badge can see:", y, 3)
            y += 36
            for name, _, _ in self.nearby[:5]:
                centered(red, ascii_name(name)[:34], y, 3)
                y += 32
        centered(black, "A: try again     B: WiFi setup", 625, 3)
        display.update()

    def _show_frame(self, frame):
        mv = memoryview(frame)
        display._framebuffer_black[:] = mv[:PLANE_BYTES]
        display._framebuffer_red[:] = mv[PLANE_BYTES:]
        display.update()

    # ---- saved uploads --------------------------------------------------

    def _saved_files(self):
        try:
            return sorted(f for f in os.listdir(UPLOAD_DIR) if f.endswith(".bin"))
        except OSError:
            return []

    def _save_upload(self, frame):
        try:
            try:
                os.mkdir(UPLOAD_DIR)
            except OSError:
                pass
            files = self._saved_files()
            n = int(files[-1][3:6]) + 1 if files else 1
            if n > 999:
                n = 1
            with open("%s/up_%03d.bin" % (UPLOAD_DIR, n), "wb") as f:
                f.write(frame)
            files = self._saved_files()
            while len(files) > MAX_SAVED:
                os.remove(UPLOAD_DIR + "/" + files.pop(0))
            self._saved_idx = len(files) - 1
        except Exception as ex:
            print("Photo Upload: could not save upload:", ex)

    def _show_next_saved(self):
        files = self._saved_files()
        if not files:
            print("Photo Upload: nothing saved yet")
            return
        self._saved_idx = (self._saved_idx + 1) % len(files)
        if self._frame is None:
            self._frame = bytearray(FRAME_BYTES)
        try:
            with open(UPLOAD_DIR + "/" + files[self._saved_idx], "rb") as f:
                f.readinto(self._frame)
            print("Photo Upload: showing", files[self._saved_idx])
            self._show_frame(self._frame)
        except Exception as ex:
            print("Photo Upload: could not load", files[self._saved_idx], ex)

    # ---- WiFi -------------------------------------------------------------

    def _wlan(self):
        import network
        if self._sta is None:
            try:
                network.hostname(HOSTNAME)
            except Exception:
                pass
            self._sta = network.WLAN(network.STA_IF)
        self._sta.active(True)
        return self._sta

    async def _scan(self, tries=3):
        """Scan a few times (iPhones only advertise now and then when idle).
        Returns {name_bytes: (name, rssi, secure)} keeping the strongest sighting."""
        wlan = self._wlan()
        seen = {}
        for t in range(tries):
            try:
                for net in wlan.scan():
                    name, rssi = net[0], net[3]
                    secure = net[4] if len(net) > 4 else 3
                    if name and (name not in seen or rssi > seen[name][1]):
                        seen[name] = (name, rssi, secure)
            except Exception as ex:
                print("Photo Upload: scan failed:", ex)
            if t < tries - 1:
                await aio.sleep_ms(300)
        self.nearby = sorted(seen.values(), key=lambda n: -n[1])
        return seen

    def _fail(self, message):
        print("Photo Upload:", message)
        self.state, self.error = "failed", message
        self._draw()

    async def _connect(self, wait_s=0):
        """Join the first saved hotspot that's on. wait_s > 0 keeps looking that long."""
        import network

        self.state = "connecting"
        self.error = None
        nets, problem = load_networks()
        if not nets:
            await self._start_setup(problem or "No hotspot saved yet.")
            return

        wlan = self._wlan()
        if wlan.isconnected():
            await self._serve()
            return
        try:
            wlan.disconnect()  # clear any half-finished earlier attempt
        except Exception:
            pass

        deadline = time.time() + wait_s
        target = None
        while True:
            seen = await self._scan()
            for ssid, password in nets:  # saved order = preference
                for name in seen:
                    if name == ssid.encode() or norm_name(name) == norm_name(ssid):
                        target = (name, password, seen[name][2])
                        break
                if target:
                    break
            if target or time.time() >= deadline:
                break
            await aio.sleep_ms(1500)

        if target is None:
            names = ", ".join("'%s'" % ascii_name(s) for s, _ in nets[:3])
            self._fail("Can't see %s. iPhone: turn on Maximize Compatibility and keep the "
                       "hotspot screen open. Android: use 2.4 GHz." % names)
            return

        name, password, secure = target
        print("Photo Upload: connecting to", ascii_name(name))
        try:
            wlan.connect(name, password)
        except Exception as ex:
            self._fail("WiFi error: %s. Restart the badge and try again." % ex)
            return
        wrong_pw = getattr(network, "STAT_WRONG_PASSWORD", 202)
        no_ap = getattr(network, "STAT_NO_AP_FOUND", 201)
        t0 = time.time()
        while not wlan.isconnected():
            st = wlan.status()
            if st == wrong_pw:
                wlan.disconnect()
                self._fail("Wrong password for '%s'. Press B to fix it in WiFi setup." % ascii_name(name))
                return
            if time.time() - t0 > CONNECT_TIMEOUT_S:
                wlan.disconnect()
                if st == no_ap:
                    why = "The hotspot disappeared. Keep the hotspot screen open on the phone."
                elif secure == 6:
                    why = "Timed out. '%s' is WPA3-only; set the hotspot to WPA2/WPA3." % ascii_name(name)
                else:
                    why = "Timed out joining '%s'. Check its password in WiFi setup (B)." % ascii_name(name)
                self._fail(why)
                return
            await aio.sleep_ms(250)

        self.ssid_in_use = name
        await self._serve()

    async def _ensure_server(self):
        if not self._server_started:
            gc.collect()
            if self._frame is None:
                self._frame = bytearray(FRAME_BYTES)
            await aio.start_server(self._handle, "0.0.0.0", HTTP_PORT)
            self._server_started = True

    async def _serve(self):
        import network
        wlan = self._wlan()
        try:
            # WiFi power saving makes the badge slow or unreachable for incoming requests
            wlan.config(pm=network.WLAN.PM_NONE)
        except Exception:
            pass
        self.ip = wlan.ifconfig()[0]
        print("Photo Upload: serving on http://%s  PIN %s" % (self.ip, self.pin))
        await self._ensure_server()
        if not self._watching:
            self._watching = True
            aio.create_task(self._watch_wifi())
        self.state = "serving"
        self._draw()

    async def _watch_wifi(self):
        """Rejoin if the hotspot drops (phone locked, walked away, hotspot timed out)."""
        while True:
            await aio.sleep(10)
            wlan = self._sta
            if self.state != "serving" or wlan is None or wlan.isconnected():
                continue
            print("Photo Upload: WiFi dropped, rejoining")
            nets, _ = load_networks()
            try:
                wlan.disconnect()
            except Exception:
                pass
            seen = await self._scan(tries=1)
            target = None
            for ssid, password in nets:
                for name in seen:
                    if norm_name(name) == norm_name(ssid):
                        target = (name, password)
                        break
                if target:
                    break
            if not target:
                continue
            try:
                wlan.connect(*target)
            except Exception:
                continue
            for _ in range(80):
                if wlan.isconnected() or self.state != "serving":
                    break
                await aio.sleep_ms(250)
            if wlan.isconnected() and self.state == "serving":
                old, self.ip = self.ip, wlan.ifconfig()[0]
                self.ssid_in_use = target[0]
                print("Photo Upload: rejoined as", self.ip)
                if self.ip != old and self._on_screen():
                    self._draw()

    # ---- setup mode -------------------------------------------------------

    async def _start_setup(self, notice=None):
        import network

        self.state = "connecting"  # becomes "setup" once the network is up
        self.notice = notice
        # Scan first: the list on the setup page comes from here
        sta = self._wlan()
        try:
            sta.disconnect()
        except Exception:
            pass
        await self._scan()

        if self._ap is None:
            self._ap = network.WLAN(network.AP_IF)
        ap = self._ap
        ap.active(True)
        if self._setup_ssid is None:
            try:
                import binascii
                tail = binascii.hexlify(ap.config("mac")).decode()[-4:]
            except Exception:
                tail = "%04x" % random.getrandbits(16)
            self._setup_ssid = "%s-%s" % (SETUP_SSID, tail)
        try:
            ap.config(ssid=self._setup_ssid, security=0)
        except Exception:
            ap.config(essid=self._setup_ssid, authmode=0)
        for _ in range(40):
            if ap.active():
                break
            await aio.sleep_ms(50)
        self.ip = ap.ifconfig()[0]
        print("Photo Upload: setup network %s at http://%s  PIN %s" % (self._setup_ssid, self.ip, self.pin))
        await self._ensure_server()
        if self._dns is None:
            try:
                self._dns = CaptiveDNS(self.ip)
                aio.create_task(self._dns.run())
            except Exception as ex:
                print("Photo Upload: no captive DNS:", ex)
                self._dns = None
        self.state = "setup"
        self._draw()

    def _stop_setup(self):
        if self._dns:
            self._dns.stop()
            self._dns = None
        if self._ap:
            try:
                self._ap.active(False)
            except Exception:
                pass

    async def _finish_setup(self):
        await aio.sleep_ms(1500)  # let the reply reach the phone first
        self._stop_setup()
        self.notice = None
        await self._connect(wait_s=WAIT_FOR_HOTSPOT_S)

    # ---- HTTP ---------------------------------------------------------------

    async def _handle(self, reader, writer):
        try:
            request = await reader.readline()
            if not request:
                return
            parts = request.decode().split(" ")
            method = parts[0]
            path = (parts[1] if len(parts) > 1 else "/").split("?")[0]
            query = parts[1].split("?", 1)[1] if len(parts) > 1 and "?" in parts[1] else ""
            headers = {}
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                kv = line.decode().split(":", 1)
                if len(kv) == 2:
                    headers[kv[0].strip().lower()] = kv[1].strip()

            if method == "GET" and path == "/api/wifi":
                await self._wifi_info(writer, "scan=1" in query)
            elif method == "POST" and path == "/api/wifi":
                await self._wifi_change(reader, writer, headers)
            elif method == "GET" and path == "/status":
                body = "ready" if self._on_screen() else "Open the Photo Upload app on the badge"
                await self._reply(writer, 200, body)
            elif method == "POST" and path == "/upload":
                if self.state == "setup":
                    await self._reply(writer, 409, "The badge is in WiFi setup. Save a hotspot first.")
                else:
                    await self._receive(reader, writer, headers)
            elif method == "GET" and (path in ("/", "/index.html") or self.state == "setup"):
                # In setup mode every page is the setup page, so phones' captive
                # portal checks open it automatically.
                await self._send_page(writer)
            else:
                await self._reply(writer, 404, "Not found")
        except Exception as ex:
            print("Photo Upload: request failed:", ex)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            gc.collect()

    async def _reply(self, writer, code, body, ctype="text/plain"):
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 409: "Conflict"}.get(code, "")
        data = body.encode()
        head = ("HTTP/1.0 %d %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\n"
                "Content-Length: %d\r\nConnection: close\r\n\r\n" % (code, reason, ctype, len(data)))
        writer.write(head.encode())
        writer.write(data)
        await writer.drain()

    async def _reply_json(self, writer, code, obj):
        await self._reply(writer, code, json.dumps(obj), "application/json")

    async def _send_page(self, writer):
        try:
            size = os.stat(PAGE_FILE)[6]
        except OSError:
            await self._reply(writer, 404, "Copy photo_upload.html to /data/ on the badge")
            return
        head = ("HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                "Content-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" % size)
        writer.write(head.encode())
        buf = bytearray(2048)
        with open(PAGE_FILE, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                writer.write(buf[:n])
                await writer.drain()

    async def _wifi_info(self, writer, rescan):
        if rescan:
            await self._scan(tries=1 if self.state == "setup" else 2)
        nets, problem = load_networks()
        saved_norm = [norm_name(s) for s, _ in nets]
        connected = None
        if self.ssid_in_use and self.state == "serving":
            for s, _ in nets:
                if norm_name(s) == norm_name(self.ssid_in_use):
                    connected = s  # report it exactly as saved, so the page can match it
        await self._reply_json(writer, 200, {
            "mode": "setup" if self.state == "setup" else "photo",
            "saved": [s for s, _ in nets],
            "connected": connected,
            "nearby": [{"ssid": n[0].decode("utf-8", "ignore"), "rssi": n[1], "lock": n[2] != 0,
                        "saved": norm_name(n[0]) in saved_norm}
                       for n in self.nearby[:15]],
            "problem": problem,
        })

    async def _read_body(self, reader, headers, limit=1024):
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > limit:
            return None
        body = b""
        while len(body) < length:
            chunk = await reader.read(length - len(body))
            if not chunk:
                return None
            body += chunk
        return body

    async def _wifi_change(self, reader, writer, headers):
        body = await self._read_body(reader, headers)
        try:
            req = json.loads(body)
        except Exception:
            await self._reply_json(writer, 400, {"ok": False, "message": "Bad request"})
            return
        if str(req.get("pin", "")) != self.pin:
            await self._reply_json(writer, 403, {"ok": False, "message": "Wrong PIN. It's shown on the badge."})
            return
        action = req.get("action")
        ssid = str(req.get("ssid", "")).strip()
        if not ssid:
            await self._reply_json(writer, 400, {"ok": False, "message": "Pick or type a hotspot name"})
            return
        nets, _ = load_networks()
        nets = [(s, p) for s, p in nets if norm_name(s) != norm_name(ssid)]
        if action == "add":
            password = str(req.get("password", ""))
            if password and len(password) < 8:
                await self._reply_json(writer, 400, {"ok": False, "message": "WiFi passwords are at least 8 characters"})
                return
            nets.insert(0, (ssid, password))
            msg = "Saved '%s'." % ssid
        elif action == "remove":
            msg = "Removed '%s'." % ssid
        else:
            await self._reply_json(writer, 400, {"ok": False, "message": "Unknown action"})
            return
        try:
            save_networks(nets)
        except Exception as ex:
            await self._reply_json(writer, 400, {"ok": False, "message": "Couldn't save: %s" % ex})
            return
        print("Photo Upload:", msg)

        switching = action == "add" and self.state == "setup"
        if switching:
            msg += " The badge is joining it now. Make sure that hotspot is on, then scan the new QR code on the badge."
        await self._reply_json(writer, 200, {"ok": True, "message": msg, "switching": switching,
                                             "saved": [s for s, _ in nets]})
        if switching:
            aio.create_task(self._finish_setup())

    async def _receive(self, reader, writer, headers):
        if headers.get("x-pin", "") != self.pin:
            await self._reply(writer, 403, "Wrong PIN. It's shown on the badge.")
            return
        if not self._on_screen():
            await self._reply(writer, 409, "Open the Photo Upload app on the badge first")
            return
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length != FRAME_BYTES:
            await self._reply(writer, 400, "Expected %d bytes, got %d" % (FRAME_BYTES, length))
            return

        mv = memoryview(self._frame)
        got = 0
        while got < FRAME_BYTES:
            chunk = await reader.read(min(4096, FRAME_BYTES - got))
            if not chunk:
                await self._reply(writer, 400, "Upload cut off")
                return
            mv[got:got + len(chunk)] = chunk
            got += len(chunk)

        print("Photo Upload: got a photo")
        self._pending = True
        await self._reply(writer, 200, "Got it! The badge is redrawing (about 20 s).")

    # ---- LEDs -----------------------------------------------------------

    def _leds(self):
        if self.state == "connecting":
            self._spin = (self._spin + 1) % (NUM_LEDS * 4)
            pos = self._spin // 4
            for i in range(NUM_LEDS):
                leds[i] = (0, 0, 8) if i == pos else (0, 0, 0)
            leds.write()
        elif self._spin:
            self._spin = 0
            leds.clear()

    # ---- app loop -------------------------------------------------------

    def _go(self, coro):
        self._task = aio.create_task(coro)

    def run(self):
        if self.launched:
            if self.state in ("idle", "failed"):
                self._go(self._connect())
            elif self.state in ("serving", "setup"):
                self._draw()
            return

        self._leds()

        if self._pending:
            self._pending = False
            leds.fill((0, 8, 0))
            self._show_frame(self._frame)
            self._save_upload(self._frame)
            leds.clear()

        a, b = UserA.pressed(), UserB.pressed()
        if self.state == "serving":
            if a:
                self._show_next_saved()
            if b:
                self._draw()
        elif self.state == "failed":
            if a:
                self._go(self._connect())
            elif b:
                self._go(self._start_setup())
        elif self.state == "setup":
            if a:
                self._stop_setup()
                self._go(self._connect())
