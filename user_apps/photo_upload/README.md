# Photo Upload

Take a picture with your phone and put it on the badge's e-paper display over WiFi.

The badge joins your phone's hotspot and serves a small web page. Open it on the
phone, snap a photo, tune it for black / white / red, and send it. All the image
work (crop, resize, dither) happens in the phone's browser, so the badge just
copies the finished frame straight into its framebuffers.

by Travis Foss

Vibe Coded with Claude

## Where each file goes

| File | Goes in | Notes |
| --- | --- | --- |
| `photo_upload.py` | `/apps/` | The app itself. It shows up in the badge menu as "Photo Upload". |
| `photo_upload.html` | `/data/` | The web page the badge serves to your phone. |
| `wifi.json` | `/data/` | Your hotspot name and password. Make it from `wifi.example.json` (step 1 below). |

Both data files go directly in `/data/`, next to the `graphics` and `gallery`
folders, not inside them. The app makes `/data/uploads/` by itself the first time
you send a photo, so you don't create that folder.

```
/
├── apps/
│   └── photo_upload.py
└── data/
    ├── photo_upload.html
    ├── wifi.json
    ├── graphics/        (leave as is)
    └── gallery/         (leave as is)
```

## Install with Thonny

1. **Make your WiFi file.** On your computer, make a copy of `wifi.example.json`,
   rename the copy to `wifi.json`, and open it in a text editor. Replace the
   placeholders with your hotspot's exact name and password, keeping the quotes:

   ```json
   {"ssid": "WifiName", "password": "yourpassword"}
   ```

   The name and password are case-sensitive. See the iPhone tips below if your
   hotspot name has an apostrophe in it.

2. **Connect Thonny to the badge.** Plug the badge into your computer over USB and
   open Thonny. Go to **Tools → Options → Interpreter**, choose **MicroPython
   (ESP32)**, pick the badge's port, and click **OK**. If the shell doesn't show a
   `>>>` prompt, click the red **Stop** button to interrupt the running firmware.

3. **Show the file browser.** Go to **View → Files**. The top pane is **This
   computer**, the bottom pane is **MicroPython device** (the badge).

4. **Upload the app.** In the bottom (badge) pane, double-click the `apps` folder to
   open it. In the top (computer) pane, find `photo_upload.py`, right-click it, and
   choose **Upload to /apps**.

5. **Upload the two data files.** In the badge pane, go back up a level and
   double-click the `data` folder. In the computer pane, right-click
   `photo_upload.html` and choose **Upload to /data**, then do the same for
   `wifi.json`. Don't go into `graphics` or `gallery` first.

6. **Check it.** The badge pane should now show `photo_upload.html` and `wifi.json`
   inside `data`, and `photo_upload.py` inside `apps`.

7. **Restart the badge.** Press **Ctrl+D** in Thonny's shell for a soft reboot, or
   unplug the badge and plug it back in. Photo Upload now appears in the menu.

To change your hotspot later, edit `wifi.json` on your computer and upload it to
`/data` again (Thonny asks before overwriting), or double-click `wifi.json` in the
badge pane to edit it in place and save with **Ctrl+S**.

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
