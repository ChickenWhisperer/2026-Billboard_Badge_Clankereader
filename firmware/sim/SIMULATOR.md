# Badge Simulator

Runs the Supercon 2026 Billboard Badge firmware (`firmware/badge/`) on a laptop. The window shows the e-paper panel, the 8 RGB LEDs and the status LED, and the buttons. The buttons respond to the keyboard or the mouse. The terminal gives a REPL into the running firmware. SAOs can be emulated by programs that connect over TCP.

- `sim.py` is the whole simulator: one MicroPython file, run by lv_micropython's Unix port.
- `badge-sim` is a launcher that runs `sim.py` with the right interpreter and heap size.
- `sao_example.py` is an example SAO (a small I2C memory) to copy.

The firmware runs unchanged; only the hardware is replaced. Nothing in `firmware/badge/` is modified or written to.

## Quick start

Build the interpreter once (see [Build](#build)), then from the repo root:

```bash
firmware/sim/badge-sim                    # or run it from anywhere
firmware/sim/badge-sim --scale 0.7        # smaller window
firmware/sim/badge-sim --real-refresh     # e-paper takes as long as the real one
python3 firmware/sim/sao_example.py       # in another terminal: an SAO at I2C address 0x50
```

| Option | Meaning |
|---|---|
| `--scale F` | Initial window scale (default 1.0, so the panel is 1:1, about 1008×880). The window can be resized freely; the badge keeps its shape, with black around it. |
| `--real-refresh` | The image appears when the panel would finish refreshing, not as soon as the firmware calls `display.update()`. |
| `--bw-time S` / `--bwr-time S` | Refresh time for an image without / with red (default 2 and 20 seconds). |
| `--led-full N` | LED channel value drawn at full brightness (default 4). See [LEDs](#leds). |
| `--sao-port P` | TCP port for SAO emulators (default 47100). `0` turns the SAO bus off. |
| `--no-repl` | No REPL in the terminal. |

Set `LV_MICROPYTHON` to point `badge-sim` at an interpreter somewhere other than `firmware/sim/lv_micropython/ports/unix/build-lvgl/micropython`.

The firmware's writable state lives in `firmware/sim/data/` (git-ignored), which is copied from `firmware/badge/data/` on the first run (missing files are copied on every run). Delete `firmware/sim/data/` to "factory reset" the simulated badge.

Close the window or press Ctrl-C in the terminal to quit.

### Buttons

| Key | Button |
|---|---|
| A | User A |
| Left | Menu - |
| Right | Menu + |
| B | User B |
| Space | Boot |

The window needs keyboard focus. The on-screen buttons can also be clicked and held, and several keys can be held at once. The screen buttons are labeled with the firmware's names and laid out in the badge's switch order (SW4–SW7, then Boot). `lib/button.py` notes that the silkscreen swaps + and -. Boot (GPIO0) is the strapping pin only at reset; at runtime it's an ordinary button that `lib/button.py` scans and apps can read.

### REPL

When the sim is started from a terminal, it gives a `>>>` prompt there about a second after boot. Its names are `main.py`'s (`leds`, `display`, `switcher`, `UserA`, `json_configs`, ...), plus `lvgl`, all five buttons, and `press()`:

```python
>>> press(UserA)                  # press and release a button (ms=150 by default)
>>> leds.fill((4, 0, 0))          # all LEDs red
>>> switcher.apps[switcher.active_idx].name
'Workshops'
>>> display.clear(); display.black.text("hi", 400, 300, 1); display.update()
```

- The firmware keeps running between lines. A line that blocks (`time.sleep(5)`) stops the badge until it returns, as it would on the badge.
- It's line-based, so the terminal does the editing; there's no history or tab completion. A line ending in `:` starts a block, which an empty line ends.
- While the menu is open, the app switcher redraws the LEDs every 10 ms, so LED changes only last once an app is running.
- Ctrl-D closes the REPL and leaves the badge running. Ctrl-C quits the sim.
- The firmware's own messages are printed in the same terminal, so they can land in the middle of what you're typing. The line you're typing is still intact.

### LEDs

The firmware uses small channel values like `(0,1,1)` and `(3,2,1)` because the real LEDs are very bright. Drawn as-is they would look black, so each channel is drawn at `sqrt(value / led-full)` of full brightness. With the default of 4, 1 shows at half brightness and 4 or more at full. An LED with all channels at 0 is drawn as a dark gray block.

As on the badge, the window only changes when the firmware calls `write()` on the NeoPixel. Setting `leds[i]` without writing shows nothing on the badge, and nothing in the sim. Values that don't fit in a byte raise the same `ValueError` the real `neopixel` module does.

The round "status" indicator is the status LED on GPIO1 (`lib/board.py`). It's drawn green; the real LED's color may differ.

## Build

The sim runs on [lv_micropython](https://github.com/lvgl/lv_micropython)'s Unix port, the same MicroPython+LVGL the badge is built with (see `firmware/README.md`), using LVGL's own SDL window and mouse drivers. It was verified on Ubuntu with lv_micropython `44aa2b1` (September 2026: MicroPython 1.24.1, LVGL 9.3).

```bash
sudo apt-get install -y build-essential libreadline-dev libffi-dev git pkg-config libsdl2-dev

cd firmware/sim
git clone https://github.com/lvgl/lv_micropython   # git-ignored
cd lv_micropython
git submodule update --init --recursive user_modules/lv_binding_micropython
B=user_modules/lv_binding_micropython

# 1. All Montserrat font sizes. The binding's lv_conf.h only has 14, 16 and 24, but
#    nametag.py uses 48, and turning them all on means new apps don't need a rebuild.
sed -i -E 's/^#define LV_FONT_MONTSERRAT_([0-9]+) +0/#define LV_FONT_MONTSERRAT_\1 1/' $B/lv_conf.h

# 2. Put LVGL's SDL functions in the lvgl module. The binding is generated from
#    lvgl_private.h, which doesn't include LVGL's drivers, so lvgl.sdl_window_create
#    and friends are missing even with SDL found. This adds lvgl.h to the generator's input.
sed -i 's|\t\t\$(LVGL_DIR)/lvgl_private.h \| \$(LV_PP_FILTER)|\t\t-include $(LVGL_DIR)/lvgl.h $(LVGL_DIR)/lvgl_private.h \| $(LV_PP_FILTER)|' $B/micropython.mk

make -C mpy-cross -j
make -C ports/unix VARIANT=lvgl submodules
make -C ports/unix VARIANT=lvgl -j     # a few minutes
ports/unix/build-lvgl/micropython -c "import lvgl; print(lvgl.sdl_window_create)"   # check
```

SDL support is only built in if `pkg-config` finds `sdl2` when `make` runs. If `libsdl2-dev` was installed after a build (or the check above fails), run `make -C ports/unix VARIANT=lvgl clean` and build again.

## How sim.py works

Every piece of hardware is created when its module is imported, so `sim.py` installs its fakes in `sys.modules` before importing any firmware.

| Hardware | Firmware | In the sim |
|---|---|---|
| Buttons (GPIO, polled every 1 ms) | `lib/button.py` | Fake `machine.Pin`. Each button's pin reads the sim's state for that button: its key (read from SDL's keyboard state through `ffi`) or the mouse. The sim finds the pins through the firmware's own `Button` objects, so it doesn't repeat pin numbers. |
| WS2812 LEDs | `lib/led_bar.py` via `neopixel` | Fake `neopixel` module. `write()` sends the colors to the window. |
| Status LED, battery sense | `lib/board.py` | The status LED's pin is shown as an indicator. The ADC reads ~2.35 V, which is USB power through the 1/2 divider. |
| E-paper panel (SPI + pins) | `lib/epd.py` | The firmware's LVGL display, flush callback and framebuffers all run unchanged. The sim wraps `display.update()` to copy the black and red framebuffers into the window. With `--real-refresh`, a fake SPI bus watches for the panel's refresh command (`0x15`). The BUSY pins (active low) then read busy for the refresh time, and the firmware's own code waits on them as it does on the badge. |
| SAO I2C bus | `lib/sao.py` | Fake `machine.I2C` that forwards transactions to SAO emulators over TCP. `machine.I2CTarget` is accepted but does nothing. |
| Filesystem | everything | `firmware/badge/` is mounted at `/` and `firmware/sim/data/` at `/data`, using MicroPython's `VfsPosix`. Paths and `sys.path` match the badge. |

### Where the Unix port differs from the badge

The firmware runs unchanged, so `sim.py` covers these differences itself:

- **`os.ilistdir()`** returns 3-tuples from the Unix port's `VfsPosix`, but 4-tuples on the badge, and `lib/app_switcher.py` unpacks 4. The firmware gets a stand-in `os` module whose `ilistdir()` adds the size.
- **`sys.platform`** is `"linux"`, and `lib/led_bar.py` only uses the NeoPixel when it's `"esp32"`. (Its fallback, meant for CPython, crashes on `write()`.) The sim imports `lib/led_bar.py` with a stand-in `sys` whose `platform` is `"esp32"`, so it takes the badge's code path with the fake `neopixel`. No other module sees it.
- **`main.py` resets the badge if an LVGL display exists when it starts** (one left over from a soft reset). So the sim imports `main` before it creates its window, and only then calls `main.main()`.

### Two LVGL displays

On the badge, LVGL has one display: the firmware's 960×672 e-paper display. **The firmware never runs LVGL's timers.** It redraws only when it calls `lvgl.refr_now(display.lv_display)`, and then calls `display.update()`.

The sim adds a second LVGL display for its window (`lvgl.sdl_window_create`, with `lvgl.sdl_mouse_create` for the mouse), and it runs LVGL's timers (`lvgl.timer_handler()`) every 33 ms. Three details keep the two displays apart:

- **The firmware's display stays the default display**, because apps call `lvgl.obj()`, `lvgl.screen_active()` and `lvgl.layer_top()` without naming a display. The sim sets it back as the default after creating its window, and attaches the mouse to the window explicitly.
- **The timers must not redraw the firmware's display on their own.** LVGL resumes a display's refresh timer whenever anything on it changes, so pausing the timer doesn't stick. Instead the sim sets its period to about 25 days. `refr_now()` calls the refresh function directly, so it still works.
- **The window's layout is the sim's own.** On resize, LVGL's resolution follows the window. The sim scales a fixed 1008×880 "badge face" to fit, centers it, and leaves black around it. The e-paper image is an RGB565 image stretched to its scaled box.

### Gotchas

- **Heap.** LVGL allocates the window's frame buffer from the MicroPython heap (2 bytes per window pixel, so about 16 MB when maximized on a 4K screen). That's why `badge-sim` passes `-X heapsize=96M`. As a result, `micropython.mem_info()` in the firmware's heartbeat shows the sim's heap, not the badge's. The firmware itself uses about 4–10 MB (mostly LVGL's 1.3 MB draw buffer and the framebuffers).
- **Closing the window ends the process** from inside LVGL's SDL driver (`LV_SDL_DIRECT_EXIT`), so nothing after it runs.
- **Keys come from SDL directly.** LVGL's SDL keyboard driver reports only the last key pressed, so the sim reads `SDL_GetKeyboardState()` through `ffi` (from the `libSDL2-2.0.so.0` LVGL already loaded).
- **Private attributes are used:** the firmware's `display.black`/`.red` framebuffers and `epd.EPD_*_PIN` module globals.

## SAO bus over TCP

All three SAO headers share one I2C bus, so the sim has one bus too. Each emulated SAO is a program that connects to `127.0.0.1:47100` and says which addresses it answers. The sim then forwards every transaction to that address over the connection. The messages are lines of ASCII, with bytes in hex:

```
SAO -> sim:  SAO <addr> [<addr> ...]       once, on connect, e.g. "SAO 50"
sim -> SAO:  I2C <addr> <write> <nread>    write the bytes (or "-"), then read nread
                                           bytes with a repeated start (if nread > 0)
SAO -> sim:  OK <read>                     the bytes read (or "-")
             NACK                          the device didn't acknowledge
```

All of `machine.I2C`'s methods map onto that one transaction: `scan`, `writeto`, `writevto`, `readfrom`, `readfrom_into`, `writeto_mem`, `readfrom_mem` and `readfrom_mem_into`.

- An address with no connected SAO raises `OSError(19)` (ENODEV), as on an empty bus.
- A NACK also raises ENODEV.
- If the SAO doesn't answer within 1 s, the sim raises ETIMEDOUT and disconnects it.
- SAOs can connect and disconnect while the sim runs. A new connection for an address replaces the old one.
- The SAO GPIO pins aren't simulated.

## Known gaps

- **Not the badge's exact build.** The badge's own `lv_conf.h` and lv_micropython commit aren't in the repo, so the sim uses lv_micropython's defaults plus the two changes in [Build](#build). The badge's build may differ in LVGL options (fonts, image decoders, ...).
- **E-paper realism:** a refresh doesn't flash, there's no ghosting, and partial refresh isn't modeled. In `--real-refresh` mode, whether an image counts as "BWR" is decided by whether any red pixel is set. The firmware always sends both planes, so on the real panel every refresh may take the long time; adjust `--bw-time` if so. The refresh time is on top of the ~1.5 s of delays in the firmware's own update sequence.
- **Fidelity:** ESP32-S3 speed, RAM limits and timing aren't reproduced. PNG decoding and the firmware's viper code run much faster here than on the badge.
- **Mouse wheel and keyboard focus** in the window only drive the sim's buttons. The firmware has no LVGL input devices.
