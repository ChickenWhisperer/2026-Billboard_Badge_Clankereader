# Photo Upload

Take a picture with your phone and put it on the badge's e-paper display over WiFi.

The badge joins your phone's hotspot and shows a QR code. Scan it, snap a photo,
tune it for black / white / red, and send it. All the image work (crop, resize,
dither) happens in the phone's browser, so the badge just copies the finished
frame straight into its framebuffers.

No config files to edit: the first time you open the app, it walks you through
connecting to your hotspot from your phone.

by Travis Foss

Vibe Coded with Claude

## Where each file goes

| File | Goes in | Notes |
| --- | --- | --- |
| `photo_upload.py` | `/apps/` | The app itself. It shows up in the badge menu as "Photo Upload". |
| `photo_upload.html` | `/data/` | The web page the badge serves to your phone. |

`photo_upload.html` goes directly in `/data/`, next to the `graphics` and
`gallery` folders, not inside them. The app creates `/data/wifi.json` (your saved
hotspots) and `/data/uploads/` (your photos) by itself.

```
/
├── apps/
│   └── photo_upload.py
└── data/
    ├── photo_upload.html
    ├── graphics/        (leave as is)
    └── gallery/         (leave as is)
```

## Install with Thonny

1. **Connect Thonny to the badge.** Plug the badge into your computer over USB and
   open Thonny. Go to **Tools → Options → Interpreter**, choose **MicroPython
   (ESP32)**, pick the badge's port, and click **OK**. If the shell doesn't show a
   `>>>` prompt, click the red **Stop** button to interrupt the running firmware.

2. **Show the file browser.** Go to **View → Files**. The top pane is **This
   computer**, the bottom pane is **MicroPython device** (the badge).

3. **Upload the app.** In the bottom (badge) pane, double-click the `apps` folder to
   open it. In the top (computer) pane, find `photo_upload.py`, right-click it, and
   choose **Upload to /apps**.

4. **Upload the web page.** In the badge pane, go back up a level and double-click
   the `data` folder. In the computer pane, right-click `photo_upload.html` and
   choose **Upload to /data**. Don't go into `graphics` or `gallery` first.

5. **Restart the badge.** Press **Ctrl+D** in Thonny's shell for a soft reboot, or
   unplug the badge and plug it back in. Photo Upload now appears in the menu.

## First time: connect to your hotspot

1. Open **Photo Upload** on the badge. With no hotspot saved yet, it shows
   **WIFI SETUP**, a QR code, and a 4-digit PIN.
2. Scan the QR code with your phone's camera and join the network it offers
   (`Billboard-Setup-xxxx`, unique to your badge). The setup page should open by
   itself. If it doesn't, open the address shown on the badge (`http://192.168.4.1`).
3. Tap your hotspot in the list, type its password, enter the PIN, and tap
   **Save hotspot**.
4. Leave the setup network on your phone and make sure your hotspot is on. The
   badge joins it (LEDs run a blue chase) and shows the photo QR code.

You can save several hotspots, say yours and a friend's. The badge uses whichever
saved one is on, preferring the most recently added. To add or remove hotspots
later, open **WiFi settings** at the bottom of the photo page, or press **User B**
on the badge's WiFi problem screen to go back into setup.

## Sending photos

1. Turn on your phone's hotspot, then launch **Photo Upload** on the badge.
2. Scan the QR code on the badge (or open the address under it).
3. Tap **Take Photo** or **From library**, then adjust the image. The preview
   shows exactly what the badge will display:
   - **Fill / Fit / Rotate**: crop to fill the panel, or show the whole photo
   - **Brightness / Contrast**: photos usually look best with contrast up
   - **Red**: how red something must be to print in red ink (0 = no red)
   - **Smooth / Pattern / Posterize**: how shading turns into dots
4. Enter the PIN shown on the badge and tap **Send to Badge**. The panel redraws
   in about 20 s.

## Buttons

| Screen | User A | User B |
| --- | --- | --- |
| Photo QR code / a photo | Next saved photo (last 12 kept) | Back to the QR code screen |
| WiFi problem | Try again | WiFi setup |
| WiFi setup | Leave setup, try saved hotspots | |

## Safety

- Sending photos and changing hotspots both need the PIN shown on the badge,
  which changes on every boot.
- Photos are only accepted while Photo Upload is on screen, so nobody can paint
  over another app.
- Hotspot passwords are stored on the badge in `/data/wifi.json` and are never
  sent back to the phone page. Don't commit that file anywhere.
- The setup network is open (so your phone can join it without a password) and
  only runs while the setup screen is up.

## If it won't connect

The badge only does 2.4 GHz WiFi. When it can't join, the screen says why and
lists the networks it *can* see.

| The badge says | What to do |
| --- | --- |
| Can't see 'name' | The hotspot isn't visible to the badge. See the phone tips below. |
| Wrong password | Press **User B** for WiFi setup and save the hotspot again with the right password. |
| ...is WPA3-only | Set the hotspot's security to WPA2 or WPA2/WPA3. |
| Timed out | Usually the password, or the phone stopped the hotspot. Press **User A** to retry. |

Picking your hotspot from the list in setup avoids typos in the name. If you
type it by hand, the app ignores capital letters and curly vs straight
apostrophes. If WiFi drops later, the badge rejoins by itself.

**iPhone**

- Turn on **Settings → Personal Hotspot → Maximize Compatibility**. Without it,
  newer iPhones use 5 GHz and the badge can't see the hotspot at all.
- Keep the Personal Hotspot screen open while the badge connects. iPhones stop
  advertising the hotspot when nothing is connected.
- The hotspot name is the phone's name (Settings → General → About → Name). If
  two phones nearby share a name, iOS may add " 2" to one of them.
- During setup, your iPhone can't run its hotspot and join the badge's setup
  network at the same time. That's expected: save the hotspot, leave the setup
  network, and the hotspot comes back.

**Android**

- In the hotspot settings, set the band to **2.4 GHz** (sometimes called
  "Extend compatibility" or "Wi-Fi 4"), and security to **WPA2** or **WPA2/WPA3**.
- If the setup page doesn't pop up, tap the "Sign in to network" notification or
  open `http://192.168.4.1`.

## Setting up by hand (optional)

You can skip the phone setup by creating `/data/wifi.json` yourself. Copy
`wifi.example.json`, fill in your hotspot, rename it to `wifi.json`, and upload it
to `/data`:

```json
{"ssid": "WifiName", "password": "yourpassword"}
```

Several hotspots look like this:

```json
{"networks": [{"ssid": "WifiName", "password": "yourpassword"},
              {"ssid": "Friend's Phone", "password": "theirpassword"}]}
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

In setup mode the badge runs an open access point plus a tiny DNS server that
answers every lookup with its own address, which makes phones open the setup
page automatically. The QR codes are generated on the badge by a small built-in
encoder (byte mode, error correction L).
