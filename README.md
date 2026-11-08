![Billboard_Badge](assets/frame_graphic_demo.png "2026 Hackaday Supercon Billboard Badge")

# Badge Hardware
The badge uses a 9.7 inch Pervasive Displays E2969JS0B1 electronic paper panel that was recycled from price tags which were destined for a now bankrupt German department store chain, coupled with an ESP32-S3 microcontroller.

A printable snap-on back cover is in [hardware/back_cover](hardware/back_cover).

## Pins

The board only has two truly free GPIOs

* GPIO9
* GPIO41

The SAO ports, however, have many possibilities

* SAO1: GPIO2 and GPIO42
* SAO2: GPIO4 and GPIO5
* SAO3: GPIO7 and GPIO6
* I2C bus: SDA on GPIO17 and SCL on GPIO18

The row of WS2812-compatible LEDs is on GPIO38

## Buttons

* Menu +/-: GPIO40 / GPIO15
* User A/B: GPIO39 / GPIO16

# Applications
In its out of the box configuration the badge can show the 2026 Supercon schedule, act as a digital name tag, and cycle through a built-in collection of art from Hackaday's incredible Art Director Joe Kim. If you come up with additional apps that you'd like to share, feel free to make a pull request. Please add new apps into the `user_apps/` directory, not in `firmware/badge/apps/` so everyone can choose which new apps they want to take on their badge.

## Badge presence (Bluetooth LE, off by default)

`lib/presence.py` lets a badge announce itself so apps can count and list badges nearby. It is **off by default**: until you turn it on, Bluetooth is not even started. Turn it on by adding `"broadcast": true` to `/data/owner.json`, or from code with `presence.set_broadcast(True)` (saved to `owner.json`, other keys kept). `set_broadcast(False)` stops it.

Only two things are broadcast: your `maker_handle` from `owner.json` (printable ASCII, at most 16 characters, may be empty) and a random 3-byte id kept in `/data/presence.json`. The id is not derived from the MAC address. The advert is non-connectable and repeats every 400 ms:

```
02 01 06                        flags
LL FF  FF FF  'S' 'C' '2' '6'  01  ID ID ID  handle...
       company 0xFFFF  magic   ver  random id  0-16 ASCII
```

Apps do their own scanning with `bluetooth.BLE().gap_scan(...)`; `presence.parse(adv_data)` returns `(id_hex, handle)` for a badge advert and `None` for anything else. `presence.badge_id()` gives your own id, so you can skip your own advert. If your app shares the radio, only call `ble.active(True)` when `ble.active()` is `False`: on this firmware, re-activating a radio that's already on silently stops the presence advert. `presence` re-sends the advert every 30 s as a safety net.

# Displaying Images

The badge's display only supports three colors: white, black, red. Shading between those needs to be implemented by dithering pixels of those colors together. Unlike LCD, CRT, or other displays you may be used to, you cannot set multiple colors within a single pixel, each pixel is only one of the three colors.

The badge firmware picks a color for each pixel based on this logic:
- If the red value is >50% and blue and green are both <50%, then the pixel will be red.
- If the red, green, and blue values of the pixel are all <50%, then the pixel will be black.
- Otherwise the pixel will be white.

Additionally, since the display is so large, loading large images can easily run out of memory. Therefore, it is best to compress each image down to a limited color space before placing on the badge. This is easily done with [ImageMagick](https://imagemagick.org/), namely with the command `magick oldfile.png -remap scripts/palette.gif newfile.png`. This reduces the image down to a colorspace that only supports 3 colors, which are white, black, and red.

Based on the above pixel logic, where each pixel is already one of the three supported colors, it's easy to see how the image will be displayed. Luckily, but converting the image on your computer, you can see what it will look like before rendering it on the badge.


# Contributing

We are looking at PRs over the weekend, but we're also busy with the conference so it may take a while to look at them. Please put user apps and infrastructure changes into separate PRs so they are easier to review. We want to take apps more quickly, and infrastructure updates a little more carefully.