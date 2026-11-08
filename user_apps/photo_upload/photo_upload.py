"""Photo Upload: send a photo from your phone to the badge over WiFi

Setup (once):
  1. Put your hotspot details in /data/wifi.json on the badge:
         {"ssid": "My Hotspot", "password": "hunter2"}
  2. Copy this file to /apps/ and photo_upload.html to /data/.

Use:
  - Turn on your phone's hotspot, then launch "Photo Upload" on the badge.
  - The badge joins the hotspot and shows an address and a 4-digit PIN.
  - Open the address in your phone's browser, take a photo, enter the PIN, Send.

Buttons while the app is open:
  User A - show the next saved upload
  User B - show the address and PIN again

The phone does all the image work (crop, resize, dither to black/white/red)
and sends the badge its native format: two 1-bit planes (black, then red),
960 x 672, 120 bytes per row, most significant bit = leftmost pixel. The
badge copies those bytes straight into the display framebuffers.

Uploads are only accepted while this app is on screen, so nobody can paint
over another app. Up to MAX_SAVED uploads are kept in /data/uploads/.

LEDs: blue chase = connecting, green blink = upload received, red = problem.
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
HTTP_PORT = 80
CONNECT_TIMEOUT_S = 25
HOSTNAME = "billboard"


# ---------------------------------------------------------------- drawing --

def big_text(fb, text, x, y, scale, color=1):
    """Draw text with framebuf's 8x8 font, blown up `scale` times."""
    w = 8 * len(text)
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


def centered(fb, text, y, scale, color=1):
    x = (WIDTH - 8 * scale * len(text)) // 2
    big_text(fb, text, max(0, x), y, scale, color)


def fit_scale(text, max_scale, margin=40):
    """Largest scale at which text fits the panel width."""
    s = max_scale
    while s > 1 and 8 * s * len(text) > WIDTH - margin:
        s -= 1
    return s


# ----------------------------------------------------------- the app ------

class PhotoUploadApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Photo Upload"
        self.sleep_ms = 50
        self.launched = False
        self.show_battery_icon = False  # keep the corner clean on photos

        self.pin = "%04d" % random.randint(0, 9999)
        self.ip = None
        self.error = None
        self.state = "idle"  # idle, connecting, serving, failed
        self._server_task = None
        self._frame = None  # receive buffer, allocated when the server starts
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

    def _draw_info(self):
        display.clear()
        black, red = display.black, display.red
        red.fill_rect(0, 0, WIDTH, 110, 1)
        centered(red, "PHOTO UPLOAD", 31, 6, 0)  # white text knocked out of the red band

        if self.state == "serving":
            url = "http://" + self.ip
            centered(black, "On your phone, open:", 170, 4)
            centered(black, url, 240, fit_scale(url, 7))
            centered(black, "or http://%s.local" % HOSTNAME, 330, 3)
            centered(black, "PIN", 420, 4)
            centered(red, self.pin, 470, 12)
            centered(black, "A: saved photos   B: this screen", 622, 3)
        else:
            centered(red, "WiFi problem", 180, 7)
            msg = self.error or "Unknown error"
            # Wrap the message over a few lines
            words, line, y = msg.split(" "), "", 300
            for word in words:
                trial = (line + " " + word).strip()
                if len(trial) > 28:
                    centered(black, line, y, 4)
                    line, y = word, y + 50
                else:
                    line = trial
            if line:
                centered(black, line, y, 4)
            centered(black, "Fix it, then reopen the app", 560, 3)
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

    def _save(self, frame):
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

    # ---- WiFi + server --------------------------------------------------

    async def _start(self):
        import network

        self.state = "connecting"
        try:
            with open(WIFI_CONFIG) as f:
                cfg = json.load(f)
            ssid, password = cfg["ssid"], cfg.get("password", "")
        except Exception:
            self.state, self.error = "failed", "Put your hotspot in /data/wifi.json"
            self._draw_info()
            return

        try:
            network.hostname(HOSTNAME)
        except Exception:
            pass
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            print("Photo Upload: connecting to", ssid)
            wlan.connect(ssid, password)
            t0 = time.time()
            while not wlan.isconnected():
                if time.time() - t0 > CONNECT_TIMEOUT_S:
                    wlan.active(False)
                    self.state = "failed"
                    self.error = "Could not join '%s'. Is the hotspot on?" % ssid
                    self._draw_info()
                    return
                await aio.sleep_ms(250)

        self.ip = wlan.ifconfig()[0]
        print("Photo Upload: serving on http://%s  PIN %s" % (self.ip, self.pin))
        gc.collect()
        self._frame = bytearray(FRAME_BYTES)
        await aio.start_server(self._handle, "0.0.0.0", HTTP_PORT)
        self.state = "serving"
        self._draw_info()

    async def _handle(self, reader, writer):
        try:
            request = await reader.readline()
            if not request:
                return
            parts = request.decode().split(" ")
            method, path = parts[0], parts[1] if len(parts) > 1 else "/"
            headers = {}
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                kv = line.decode().split(":", 1)
                if len(kv) == 2:
                    headers[kv[0].strip().lower()] = kv[1].strip()

            if method == "GET" and path in ("/", "/index.html"):
                await self._send_page(writer)
            elif method == "GET" and path == "/status":
                body = "ready" if self._on_screen() else "Open the Photo Upload app on the badge"
                await self._reply(writer, 200, body)
            elif method == "POST" and path == "/upload":
                await self._receive(reader, writer, headers)
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

    async def _reply(self, writer, code, body):
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 409: "Conflict"}.get(code, "")
        data = body.encode()
        head = ("HTTP/1.0 %d %s\r\nContent-Type: text/plain\r\n"
                "Content-Length: %d\r\nConnection: close\r\n\r\n" % (code, reason, len(data)))
        writer.write(head.encode())
        writer.write(data)
        await writer.drain()

    async def _send_page(self, writer):
        try:
            size = os.stat(PAGE_FILE)[6]
        except OSError:
            await self._reply(writer, 404, "Copy photo_upload.html to /data/ on the badge")
            return
        head = ("HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                "Content-Length: %d\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n" % size)
        writer.write(head.encode())
        buf = bytearray(2048)
        with open(PAGE_FILE, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                writer.write(buf[:n])
                await writer.drain()

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
        self._spin = (self._spin + 1) % (NUM_LEDS * 4)
        if self.state == "connecting":
            pos = self._spin // 4
            for i in range(NUM_LEDS):
                leds[i] = (0, 0, 8) if i == pos else (0, 0, 0)
            leds.write()

    def _flash(self, color):
        leds.fill(color)

    # ---- app loop -------------------------------------------------------

    def run(self):
        if self.launched:
            if self._server_task is None or self.state == "failed":
                # First launch, or retry after a failed connect
                self.state = "connecting"
                self._server_task = aio.create_task(self._start())
            elif self.state in ("serving", "failed"):
                self._draw_info()
            return

        self._leds()

        if self._pending:
            self._pending = False
            self._flash((0, 8, 0))
            self._show_frame(self._frame)
            self._save(self._frame)
            leds.clear()

        if UserA.pressed():
            self._show_next_saved()
        if UserB.pressed() and self.state in ("serving", "failed"):
            self._draw_info()
