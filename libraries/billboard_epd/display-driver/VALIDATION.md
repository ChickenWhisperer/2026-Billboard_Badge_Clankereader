# Validation

## Software validation

* Native C core compiled on Windows with Zig 0.13.0, warnings treated as errors.
* Fourteen library tests pass, including C/Python command trace comparisons,
  randomized pulse-budget reference tests, queued snapshots and periodic cleans.
* Bad arguments, grayscale overlap, stalled BUSY, missing BUSY assertion,
  transport failures, fault latching and recovery exercised with fake hardware.
* Python modules cross-compiled for Xtensa-windowed MicroPython.
* ESP-IDF 5.3.1 example builds for ESP32-S3 with octal PSRAM, including from the
  standalone component archive.

These checks do not measure pigment behavior or validate a real SPI connection.

## Badge checks before a stable release

Record panel part/revision, board, firmware version, temperature and power source.

1. Run the packaged C example and the MicroPython example on a B1 badge.
2. Confirm factory white, black and red, including the seam between controllers.
3. Draw and erase text with one black and three white pulses. Confirm unchanged
   border pixels hold and successive passes do not reset the controller.
4. Switch between monochrome and red masks inside one burst, including red-only
   passes. Check overlap priority and verify both BUSY signals complete.
5. Verify the slow black/gray/white bars are distinct, neutral and consistent
   after starting from both a white and a red image.
6. Return from grayscale to fast monochrome; verify the white baseline and pulse
   planner are synchronized. Exercise a periodic clean and confirm no stale image.
7. Exercise power-off, an unavailable BUSY input and recovery. Confirm failures
   stop further refreshes and a recovery clears to white.
8. Record visual results and timing for repeated use before changing the version
   from a release candidate to a stable tag.

Physical validation of this release is pending.
