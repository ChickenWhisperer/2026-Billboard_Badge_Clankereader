# Billboard display libraries

Reusable C and MicroPython drivers for the Billboard Badge's Pervasive Displays
E2969JS0B1, a 960 × 672 dual-controller black/white/red panel.

Version **0.1.0-rc.1** supports fast refresh and slow three-tone grayscale.
The standalone C example builds with ESP-IDF 5.3.1. Native C/Python protocol
tests and MicroPython compilation checks cover the software; this release
still needs the badge checks in
[VALIDATION.md](VALIDATION.md) before a stable hardware release.

## Choose a library

| Library | Location | Runtime |
| --- | --- | --- |
| C component | `C/components/billboard_epd` | ESP-IDF 5.3.1, ESP32-S3 with PSRAM |
| Portable C core | Same component, excluding `billboard_espidf.c` | Caller-supplied SPI/GPIO/time callbacks |
| MicroPython package | `Python/badge/lib/billboard_epd` | ESP32 MicroPython with `machine`, `asyncio`, Viper and enough heap/PSRAM |

Both libraries operate independently of a graphics framework. Importing the
Python package does not configure hardware. Create one panel instance and give
it one owner; the low-level API is not reentrant.

## Modes

| Mode | API | Behavior |
| --- | --- | --- |
| Fast monochrome | `Display.draw()` / C decay planner plus `billboard_panel_drive()` | Per-pixel 1–15 pulse budgets; defaults one black and three white pulses |
| Fast masks including red | `Panel.drive()` / `billboard_panel_drive()` | Only selected pixels drive; others use zero-volt HOLD; red strength depends on repeated passes |
| Slow factory color | `Panel.full_refresh()` / `billboard_panel_full_refresh()` | Full factory OTP waveform for black, white and red |
| Slow grayscale | `Display.grayscale()` or `Panel.grayscale()` / `billboard_panel_grayscale()` | Black, gray and white; factory white baseline, six white preparation pulls, three grayscale pulls |

Grayscale uses a custom B1 waveform with three tones: black, gray and white.
Its second plane selects gray and cannot simultaneously display red. Driver-8
panels are not supported.

Fast mode writes only volatile `0xB0` waveform registers. All transmissions use
the full native controller window. These custom waveform presets target 25°C;
long-term panel life and operation over other temperatures have not been validated.

## Framebuffer contract

All full-frame buffers contain exactly **80,640 bytes**, 120 bytes per row.
Pixels are horizontal and MSB-first: bit `0x80` is the leftmost pixel in a byte.
The driver performs the B1 wire bit reversal and left/right split.

* **Monochrome target** for `Display.draw()` and the decay planner: `1 = white`,
  `0 = black`.
* **Factory color planes**: `1 = pigment`, `0 = absent`; red wins overlaps.
* **Grayscale planes**: separate black and gray masks, `1 = selected tone`.
  Overlap is rejected before hardware I/O. Two zero bits mean white.
* **Fast drive masks**: `1 = drive this pass`; priority is black, then red,
  then white. A pixel in none of the masks receives HOLD.

Panel methods return only after both BUSY signals have completed. Pulse counters
must be decremented after a successful completed pass, never after merely issuing
the refresh command. Keep input buffers stable until a low-level call returns;
`Display.draw()` and `Display.grayscale()` take their own snapshots.

## MicroPython quick start

Copy the `billboard_epd` directory to `/lib`, including its license notices.
Alternatively, from the library source directory with a connected badge:

```sh
mpremote mip install Python/badge/lib/billboard_epd/package.json
```

This is a MicroPython package, not a CPython hardware driver or a PyPI package.
The Python fallback exists for host tests. Source files are distributed so they
can be compiled by the badge's own MicroPython version.

```python
import asyncio
import framebuf
from billboard_epd import create_panel, FRAME_BYTES
from billboard_epd.display import Display

async def main():
    panel = create_panel()
    display = Display(panel)
    frame = bytearray(b"\xff" * FRAME_BYTES)
    canvas = framebuf.FrameBuffer(frame, 960, 672, framebuf.MONO_HLSB)
    canvas.text("Hello!", 32, 32, 0)
    try:
        await display.draw(frame)
    finally:
        await panel.close()

asyncio.run(main())
```

`Display` establishes a white factory baseline on first use, resets its planner
after grayscale, and performs a factory clean after 2400 fast passes. The lower
level `Panel` and C APIs leave clean scheduling to the application. Use the
factory waveform to establish a known starting image, rebase the planner after
every factory refresh, and clean periodically. End each burst when the application
pauses; the low-level driver stays powered between fast calls.

For slow grayscale, fill two non-overlapping pigment masks and call
`await display.grayscale(black, gray)`. The example draws monochrome text and
then black/gray/white bars. It is at `display-driver/examples/micropython/hello.py`
in the source archive, or `examples/hello.py` in the MicroPython archive.

## C quick start

Copy `C/components/billboard_epd` into your project's `components/` directory
and add `REQUIRES billboard_epd` to the calling component. Enable octal PSRAM on
the Billboard Badge. No registry upload is required.

```c
#include "billboard_espidf.h"

billboard_panel_t *panel;
billboard_espidf_config_t config = billboard_espidf_default_config();
ESP_ERROR_CHECK(billboard_espidf_create(&config, &panel));
int result = billboard_panel_full_refresh(panel, NULL, NULL, BILLBOARD_FRAME_BYTES);
/* Handle result before submitting further work. */
billboard_espidf_destroy(panel);
```

The ESP-IDF example demonstrates the decay planner, correct completion ordering,
cleanup and slow grayscale. Build it from the library source directory:

```sh
cd display-driver/examples/esp-idf
idf.py set-target esp32s3
idf.py build
```

In the component archive, the same example is under `billboard_epd/examples/hello`.

The component includes `idf_component.yml`, but is not yet published to the
Espressif registry. Other ESP-IDF versions have not been validated. The portable
core has no allocation or RTOS dependency: supply `billboard_io_t` and a
`BILLBOARD_STORAGE_BYTES` buffer. The ESP-IDF adapter allocates storage from PSRAM,
uses a 4000-byte DMA staging buffer, and can share an already initialized SPI bus
with `initialize_bus = false`.

## Wiring and memory

| Signal | Default GPIO |
| --- | --- |
| Left / right CS | 13 / 3 |
| Left / right BUSY, low while busy | 12 / 8 |
| DC / RESET | 10 / 11 |
| SCK / MOSI | 21 / 47 |

SPI mode is 0, up to 20 MHz. MISO is not used. GPIO48 is not changed by default;
the application owns any board-specific power-enable wiring. Pass explicit pins
or SPI objects to the Python constructor, or change the C configuration before
creating the panel. These defaults are for the Billboard Badge, not a generic
PDI connector.

The low-level panel uses 201,600 bytes of frame/staging storage (plus 4000 DMA
bytes on ESP-IDF). The decay planner needs another five bytes of storage per
framebuffer byte, or 403,200 bytes. The managed Python display adds two drive
masks and a snapshot during drawing; budget at least 1 MiB of free heap, plus the
application's own framebuffers and runtime overhead.

## Errors and recovery

BUSY waits are bounded: 15 seconds for a fast pull and 90 seconds for a factory
pull, with a separate 100 ms deadline for both BUSY assertions. SPI errors,
timeouts and Python cancellation latch a fault and reset the controller.
Further operations fail until `recover()` / `billboard_panel_recover()` completes
a white factory refresh. A C application must then reset its own decay planner to
white; `Display.recover()` does that automatically. The original failure is not
reported as a successful display update.

## Development and packaging

From the library source directory:

```sh
python display-driver/tools/generate_waveforms.py --check
python display-driver/tools/test_native.py --cc gcc
python display-driver/tools/make_release.py
```

On Windows, `--cc path/to/zig.exe` builds the host DLL without a Visual Studio
installation. Native tests use real C code through `ctypes`; they compare pulse
budgets with a per-pixel reference and compare C and Python command traces,
including HOLD, bit order, red priority, grayscale, BUSY failures and recovery.

Release ZIPs and SHA-256 manifests are written to `display-driver/dist/`.
Separate archives contain the ESP-IDF component, MicroPython package and complete
library sources. See [CHANGELOG.md](CHANGELOG.md) for release notes and `LICENSE`
for licensing.
