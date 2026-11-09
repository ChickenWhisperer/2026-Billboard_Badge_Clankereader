# Billboard B1 MicroPython library

Version 0.1.0-rc.1 for the 960 × 672 Billboard Badge B1 display. Copy this directory
to `/lib/billboard_epd`, or use `mpremote mip install path/to/package.json`.
Requires MicroPython `machine`, `asyncio`, Viper and enough heap/PSRAM; no LVGL,
PNG decoder or badge-app modules are needed. Importing does not touch hardware.

```python
from billboard_epd import create_panel
from billboard_epd.display import Display
panel = create_panel()
display = Display(panel)
await display.draw(frame)              # 80640 bytes, MSB-first, 1=white
await display.grayscale(black, gray)    # slow; 1=selected tone in each plane
await panel.close()
```

Use these awaits inside an async function. `Display` snapshots input, manages
the monochrome pulse planner and schedules a factory clean every 2400 passes.
Slow grayscale performs a white factory baseline, six white preparation pulls,
then three grayscale pulls. Its planes must not overlap; it cannot show red.
Use `await panel.full_refresh(black, red)` for factory black/white/red images.

Low-level `panel.drive(black_mask, white_mask, red_mask=None)` keeps controllers
powered across completed fast passes; call `await panel.end_burst()` when done.
Callers of this API own clean scheduling and framebuffer history. Use one task
per panel and keep input buffers stable until calls return. A fault requires
`await panel.recover()` (or `await display.recover()` with the managed planner).

Default pins: CS 13/3, BUSY 12/8, DC 10, RESET 11, SCK 21, MOSI 47. All can be
configured in `create_panel(...)`; an existing SPI object can also be supplied.
Default hardware SPI is mode 0 at 20 MHz. Driver-8 panels are not supported here.

Host software checks pass; physical validation of this release is
still required before a stable hardware release. Full documentation, examples,
and validation notes are included in the release archive.
