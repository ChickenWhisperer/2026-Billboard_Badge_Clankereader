# Billboard B1 ESP-IDF component

960 × 672 dual-controller e-paper driver, version 0.1.0-rc.1. Tested by building
with ESP-IDF 5.3.1 for ESP32-S3; physical verification of this release remains
pending. Supports fast B/W/red drive masks, factory color and slow three-tone
grayscale. Grayscale is black/gray/white; it cannot display red in the same image.

Copy this directory into your project's `components/billboard_epd`, enable PSRAM,
and use `REQUIRES billboard_epd`. Include `billboard_espidf.h` for the ESP-IDF
adapter, `billboard_epd.h` for the portable transport API, and `billboard_decay.h`
for optional pulse planning. Header comments specify buffer sizes and ownership.

Default pins: CS 13/3, BUSY 12/8, DC 10, RESET 11, SCK 21, MOSI 47. SPI mode 0,
20 MHz. BUSY is low while active. The library has no dependency on game assets
or graphics software. `billboard_espidf_default_config()` provides editable pins
and a bus-ownership option.

Every plane is 80640 bytes, MSB-first horizontal bits. Monochrome planner targets
use 1=white, 0=black. Factory/gray planes and fast masks use 1=selected pigment or
drive. Gray masks must not overlap black. Both BUSY lines must complete before
the planner is advanced. Serialize calls and keep buffers alive and unchanged;
input buffers must not alias the driver's workspace.

The low-level API stays powered between passes. Call `billboard_panel_end_burst`
when pausing, schedule periodic factory cleans, and reset the planner to the
actual factory image after cleaning. Timeouts/I/O errors latch a fault; explicit
`billboard_panel_recover` establishes a new white baseline. Reset the planner too.

The standalone ZIP includes `examples/hello`; run `idf.py set-target esp32s3` and
`idf.py build` there. Full documentation and validation notes are included under
`docs/`. This component is not yet uploaded to the Espressif registry.
