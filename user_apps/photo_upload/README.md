# Photo Upload

Take a picture with your phone and put it on the badge's e-paper display over WiFi.

The badge joins your phone's hotspot and serves a small web page. Open it on the
phone, snap a photo, tune it for black / white / red, and send it. All the image
work (crop, resize, dither) happens in the phone's browser, so the badge just
copies the finished frame straight into its framebuffers.

by Travis Foss

## Install

1. Copy `wifi.example.json` to `wifi.json` and put your hotspot's name and password in it.
2. Copy the files to the badge:

```
mpremote cp photo_upload.py :apps/
mpremote cp photo_upload.html :data/
mpremote cp wifi.json :data/
```

Both data files go directly in `/data/`, not in `graphics/` or `gallery/`. With
Thonny, drag the same files into those folders instead.

3. Restart the badge.

## Use

1. Turn on your phone's hotspot, then launch **Photo Upload** on the badge.
   The LEDs run a blue chase while it connects.
2. The badge shows an address (and `http://billboard.local`) plus a 4-digit PIN.
3. Open the address in your phone's browser and tap **Take Photo** or **From library**.
4. Adjust the image. The preview shows exactly what the badge will display:
   - **Fill / Fit / Rotate**: crop to fill the panel, or show the whole photo
   - **Brightness / Contrast**: photos usually look best with contrast up
   - **Red**: how red something must be to print in red ink (0 = no red)
   - **Smooth / Pattern / Posterize**: how shading turns into dots
5. Enter the PIN and tap **Send to Badge**. The panel redraws in about 20 s.

On the badge, **User A** steps through saved uploads (the last 12 are kept in
`/data/uploads/`) and **User B** shows the address and PIN again.

## Safety

- Uploads need the PIN shown on the badge, which changes on every boot.
- Uploads are only accepted while Photo Upload is on screen, so nobody can
  paint over another app.
- Your hotspot password stays in `/data/wifi.json` on the badge. Don't commit it.

## iPhone hotspot tips

The ESP32-S3 only does 2.4 GHz WiFi, and iPhone hotspots have a few quirks:

- Turn on **Settings → Personal Hotspot → Maximize Compatibility**, or the badge
  can't see the hotspot at all.
- Keep the Personal Hotspot screen open while the badge connects. iPhones stop
  advertising the hotspot when nothing is connected.
- Hotspot names like "Travis's iPhone" use a curly apostrophe (’). Rename the
  phone without one (Settings → General → About → Name) and use that in `wifi.json`.
- `billboard.local` may not resolve on Android. Use the number address instead.

If it won't connect, open Photo Upload again to retry, or run this in the REPL to
see which networks the badge can find:

```python
import network
w = network.WLAN(network.STA_IF); w.active(True)
for n in w.scan(): print(n[0], 'channel', n[2], 'signal', n[3])
```

## Known issue

The battery sense pin (GPIO14) is on the ESP32-S3's ADC2, which shares hardware
with WiFi. Once WiFi is on, battery readings may fail or jump until the badge
reboots.

## How it works

The phone sends the badge's native frame format: two 1-bit planes (black, then
red), 960 × 672, 120 bytes per row, most significant bit = leftmost pixel, set
bit = ink. That's 161,280 bytes, POSTed to `/upload` with the PIN in an `X-Pin`
header. The server is a minimal `asyncio` HTTP server, so buttons and LEDs keep
working while it runs.
