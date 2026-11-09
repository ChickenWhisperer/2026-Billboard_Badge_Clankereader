# Changelog

## 0.1.0-rc.1

* Initial standalone ESP-IDF and MicroPython B1 panel libraries.
* Expose a portable C callback interface and a matching per-pixel pulse planner.
* Support fast timing, BOLD monochrome LUT, HOLD masks, burst reuse and red masks.
* Add slow three-tone grayscale.
* Bound BUSY waits, require both controllers to assert and finish, propagate I/O
  failures, latch faults, and provide explicit recovery and resource cleanup.
* Generate both languages' waveform tables from one reviewed source.
* Include examples, package manifests, license notices, deterministic release
  archives, host parity tests and an ESP-IDF build check.

This is a release candidate pending physical verification of the
drivers. No hardware testing of this release is claimed.
