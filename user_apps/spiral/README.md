# Spiral

Partial-refresh screensaver for the `E2969JS0B1` (960x672 B/W/R) panel.

Steps a 48x48-pixel rectangular spiral clockwise from the outer border to the center and back outward across a 20x14 grid (280 byte-aligned tiles), cycling Black -> Red -> White.

## Install

```bash
mpremote cp user_apps/spiral/spiral.py :apps/spiral.py
```

## Controls

- `User A`: Toggle between snake mode (default, trailing tiles fade) and solid-fill mode (LED bar fills up to the current wavefront position).
- `User B`: Cycle step speed (`4 -> 8 -> 2` tiles per step).
- `Boot`: Skip to the next color/direction phase.

## Theory of Operation (vs. Stock `lib/epd.py`)

1. **Persistent HV session + 20-frame LUT (`0xA7` -> `0xB0`):**
   Stock `global_update()` resets the dual COGs, runs the full OTP waveform, and powers down the charge pumps on every update. `spiral.py` reads the panel's 88-byte OTP block (`0x70`/`0xB9`), runs the `0xA7` OTP load sequence (`0x45 = 0x84`) once when opening a session, waits 125 ms before writing a 400-byte (20-frame) LUT to `0xB0`, and keeps the HV booster running across steps. Each subsequent step writes planes `0x10`/`0x11` and strobes `0x15`.

2. **Direct 4-rail LUT mapping (`(bit_0x11 << 1) | bit_0x10`):**
   The 20-frame LUT maps the two 1-bpp planes to the four source rails:
   - `00` (`Table 0`): `GND` (`0 V`)
   - `01` (`Table 1`): `VDH` (Black)
   - `10` (`Table 2`): `VDL` (White)
   - `11` (`Table 3`): `VDHR` (Red)
   Group 9 Phase B is set to `GND` across all four tables.

3. **Snake mode vs. Solid mode (`User A`):**
   - **Snake mode (`solid_mode = False`):** Drives only the active wavefront tiles per step (plus a `VDL` white clearing window ahead of the Red pass) and sends `00` (`GND`) for all other tiles. Across repeated full-panel gate scans, tiles left at `GND` gradually fade behind the moving head.
   - **Solid mode (`solid_mode = True`):** Holds untouched tiles ahead of the wavefront on the previous pass's rail and filled tiles behind the wavefront on the current pass's rail on every step so static Black and Red regions do not fade across repeated scans.

## Credits & References

- **[TheESTest/Supercon_2026_Badge_Firmware](https://github.com/TheESTest/Supercon_2026_Badge_Firmware)** (`badge_files/lib/epd_fast.py`): MicroPython fast-refresh session architecture, `make_lut()` / `_frames_per_group()` waveform builder, OTP reader, and Driver-B (`0x45 = 0x84`) initialization sequence.
- **[phthallo/supercon](https://github.com/phthallo/supercon)** (`firmware/lib/epd.py`, `firmware/lib/luts.py`): 20-frame register `0xB0` custom LUT structure (`off = 0`, 2-frame minimum group timing) and persistent-booster partial refresh technique on the badge.
- **[Hack-a-Day/2026-Billboard_Badge](https://github.com/Hack-a-Day/2026-Billboard_Badge)** (`firmware/esp32s3-itc97/src/epd97.cpp`, commit `6ba6dd8`) & **[PervasiveDisplays/EPD_Driver_GU_large](https://github.com/PervasiveDisplays/EPD_Driver_GU_large)**: Official C++ fast-mode driver and `lutb_base` (`data/rendered_luts.py`).
- **[@trandi (PR #4)](https://github.com/Hack-a-Day/2026-Billboard_Badge/pull/4)** & **[@FacioErgoSum (PR #3)](https://github.com/Hack-a-Day/2026-Billboard_Badge/pull/3)**: 20 MHz hardware `SPI(1)` upgrade and `@micropython.viper` single-pass dual-COG plane split + 256-byte LUT bit-reversal.
