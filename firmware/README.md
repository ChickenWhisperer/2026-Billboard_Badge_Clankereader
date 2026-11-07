# Billboard Badge Firmware


# Micropython
Baseline micropython 1.27
Download from https://micropython.org/download/ESP32_GENERIC_S3/ `Firmware (Support for Octal-SPIRAM)` v1.27.0 .bin
`esptool.py --baud 460800 write_flash 0 ~/Downloads/ESP32_GENERIC_S3-SPIRAM_OCT-20251209-v1.27.0.bin`

# Build notes (lv_micropython)

You should not need to go through this unless you want to modify the LVGL
behavior or add addional compiled C modules. Most hacking is effective just in 
micropython (the .py files), which is much easier.

Install esp-idf: https://github.com/lvgl/lv_micropython/blob/master/ports/esp32/README.md#setting-up-the-toolchain-and-esp-idf
`./install.sh` and `. ./export.sh` in the shell that will be doing builds.

In `firmware/lv_micropython/ports/esp32` run `make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=BILLBOARD_BADGE`  to build and `python -m esptool --chip esp32s3 -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 build-ESP32_GENERIC_S3-BILLBOARD_BADGE/bootloader/bootloader.bin 0x8000 build-ESP32_GENERIC_S3-BILLBOARD_BADGE/partition_table/partition-table.bin 0x10000 build-ESP32_GENERIC_S3-BILLBOARD_BADGE/micropython.bin` to flash.
You will need to `make clean BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=BILLBOARD_BADGE` (yes including the arguments) to rebuild. The BOARD argument defines a build directory, so clean won't clean the correct directory without it.

Flashing the pre-built bin files: From `firmware/` run `python -m esptool --chip esp32s3 -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 8MB --flash_freq 80m 0x0 bin/bootloader.bin 0x8000 bin/partition-table.bin 0x10000 bin/micropython.bin`. To get into the mode to flash this, you need to hold down the BOOT button while either plugging in or pressing and releasing RESET.

## lv_micropython changes:
Copy the files from `firmware/lv_files/` to their matching paths, ex: `lv_files/foo/` -> `lv_micropython/foo/`.
