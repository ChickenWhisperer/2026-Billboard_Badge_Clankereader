"""LED Bar for Billboard Badge"""

import asyncio as aio
from collections import deque
import sys

if sys.platform == "esp32":
    import machine
    import neopixel
else:
    machine = None
    neopixel = None

NUM_LEDS   = 8
WS2812_PIN = 38

## 64 Apps should be enough for anyone (TM)
MENU_BACKGROUND_COLORS  = [(2,1,0), (0,1,1), (2,0,1), (3,0,0), (0,2,0), (0,0,1)]
MENU_SELECTED_COLORS    = [(3,2,1), (3,2,1), (3,2,1), (3,2,1), (3,2,1), (3,2,1)]  ## white looks good
SUBMENU_SELECTED_COLORS = MENU_BACKGROUND_COLORS   ## same for consistency?


class LedBar:
    def __init__(self) -> None:
        print("Starting LED manager")
        if sys.platform == "esp32":
            print("Running on esp32, using Neopixel library")
            self.leds = neopixel.NeoPixel(pin=machine.Pin(WS2812_PIN), n=NUM_LEDS, bpp=3)
        else:  # For debugging in CPython
            print("Using simulated LED buffer in a list")
            self.leds = []
            for i in range(NUM_LEDS):
                self.leds.append([0, 0, 0])
        self.output = []

        # LED pattern sequencer
        self.sequence_func = None
        self.sequence_paused = False
        self._sequence_updated = aio.Event()

    def __setitem__(self, name: int, value) -> None:
        idx = int(name)
        self.leds[idx] = value

    def __getitem__(self, name: int) -> tuple[int, int, int]:
        idx = int(name)
        return self.leds[idx]

    def __str__(self) -> str:
        return ":".join(f"{l[0]}.{l[1]}.{l[2]}" for l in self.leds)

    def set(self, value: list[tuple[int, int, int] | list[int]]):
        for i, led in enumerate(value):
            self.leds[i] = led

    def set_sequence(self, fn):
        """Sets a sequence to set to the LED bar.

        Must be a Generator function that yields a tuple of:
          - The LED values (a list of tuples to set to the LEDs)
          - The delay time in ms before the next update

        def example_sequence():
            # Create an array of LED values to populate
            vals = []
            for _ in range(led_bar.NUM_LEDS):
                vals.append([0, 0, 0])
            # Run the sequence forever
            i = 0
            while True:
                # Assign each LED. Careful, they're bright!
                for n, led in enumerate(vals):
                    led[0] = ((i + n) % 2) * 6  # red
                    led[1] = ((i + n) % 3) * 4  # green
                    led[2] = ((i + n) % 4) * 3  # blue
                # yield (like return, but the function keeps going)
                # the values to set to the LEDs, and how many milliseconds
                # to delay before getting the next values
                yield vals, 1000.0
                # Increment the counter so the values change next cycle
                i += 1
        """
        self.sequence_func = fn
        self._sequence_updated.set()

    def clear_sequence(self):
        self.sequence_func = None
        self._sequence_updated.set()

    def fill(self, val):
        if neopixel:
            self.leds.fill(val)
        else:
            for i in range(NUM_LEDS):
                self.leds[i] = val
        self.leds.write()

    def clear(self):
        self.leds.fill((0,0,0))
        self.leds.write()


    def write(self):
        """Write the buffered values out to the actual LEDs"""
        if sys.platform == "esp32":
            self.leds.write()  # pyright: ignore[reportAttributeAccessIssue]
        return [(led[0], led[1], led[2]) for led in self.leds]

    async def update(self):
        sequence_generator = None
        while True:
            if self._sequence_updated.is_set():
                if self.sequence_func:
                    sequence_generator = self.sequence_func()
                elif self.sequence_func is None:
                    sequence_generator = None
                self._sequence_updated.clear()
            sleep_ms: int = 1
            if not self.sequence_paused and sequence_generator:
                for new_leds, sleep_ms in sequence_generator:
                    for idx, led in enumerate(new_leds):
                        self.leds[idx] = led
                    if sys.platform == "esp32":
                        self.leds.write()  # pyright: ignore[reportAttributeAccessIssue]
                        await aio.sleep_ms(int(sleep_ms))
                    else:  # Running in cpython
                        await aio.sleep(sleep_ms / 1000.0)
                    if self.sequence_paused or self._sequence_updated.is_set():
                        break
            else:
                if sys.platform == "esp32":
                    await aio.sleep_ms(sleep_ms)
                else:  # Running in cpython, sleep takes float(s)
                    await aio.sleep(sleep_ms / 1000.0)

    def pause(self):
        self.sequence_paused = True

    def unpause(self):
        self.sequence_paused = False

    def toggle_pause(self):
        self.sequence_paused = not self.sequence_paused

    # def override(self, new_leds):
    #     self.pause()
    #     for idx, led in enumerate(new_leds):
    #         self.leds[idx] = led
    #         self.write()
    #     yield
    #     self.unpause()

    def __or__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([min(max(self.leds[i][0], r[0]), 255),
                         min(max(self.leds[i][1], r[1]), 255),
                         min(max(self.leds[i][2], r[2]), 255)])
        return vals

    def __ror__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([min(max(self.leds[i][0], r[0]), 255),
                            min(max(self.leds[i][1], r[1]), 255),
                            min(max(self.leds[i][2], r[2]), 255)])
        return vals

    def __and__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([max(min(self.leds[i][0], r[0]), 0),
                            max(min(self.leds[i][1], r[1]), 0),
                            max(min(self.leds[i][2], r[2]), 0)])
        return vals

    def __rand__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([max(min(self.leds[i][0], r[0]), 0),
                            max(min(self.leds[i][1], r[1]), 0),
                            max(min(self.leds[i][2], r[2]), 0)])
        return vals

    def __add__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([min(self.leds[i][0] + r[0], 255),
                            min(self.leds[i][1] + r[1], 255),
                            min(self.leds[i][2] + r[2], 255)])
        return vals

    def __radd__(self, other: list[tuple[int, int, int] | list[int]]) -> list[list[int]]:
        vals: list[list[int]] = []
        for i, r in enumerate(other):
            vals.append([min(self.leds[i][0] + r[0], 255),
                            min(self.leds[i][1] + r[1], 255),
                            min(self.leds[i][2] + r[2], 255)])
        return vals

    def __iadd__(self, other: list[tuple[int, int, int] | list[int]]):
        for i, r in enumerate(other):
            self.leds[i] = (min(self.leds[i][0] + r[0], 255),
                            min(self.leds[i][1] + r[1], 255),
                            min(self.leds[i][2] + r[2], 255))
        return self

    def menu_display(self, app_index, total_number_apps):
        self.pause()
        self.clear()

        ## Each page of NUM_LEDS apps gets its own color. With more apps than
        ## colors the palette wraps around, so any number of apps is usable.
        current_page = app_index // NUM_LEDS
        current_LED  = app_index % NUM_LEDS
        palette_idx  = current_page % len(MENU_BACKGROUND_COLORS)

        ## Full fill if the page is filled
        if current_page < total_number_apps // NUM_LEDS:
            self.fill(MENU_BACKGROUND_COLORS[palette_idx])
        else:
            ## fill only up until the number of apps
            for i in range(total_number_apps % NUM_LEDS):
                self.leds[i] = MENU_BACKGROUND_COLORS[palette_idx]

        self.leds[current_LED] = MENU_SELECTED_COLORS[palette_idx % len(MENU_SELECTED_COLORS)]
        self.write()

    def submenu_display(self, item_index):
        ## Single LED in submenus
        self.pause()
        self.clear()
        ## Different single LED color for every menu page (wraps like the menu)
        palette_idx = (item_index // NUM_LEDS) % len(SUBMENU_SELECTED_COLORS)
        self.leds[item_index % NUM_LEDS] = SUBMENU_SELECTED_COLORS[palette_idx]
        self.write()


leds = LedBar()

def bitmask_to_leds(x: int, r=3, g=3, b=3):
    """Convert an 8 bit int into a binary bitmask for the LEDs.
       Sets them all to a single color, defaulting white.
       Set r/g/b for the color of the red/green/blue pixel.
    """
    vals = []
    for i in range(NUM_LEDS):
        v = (x >> (NUM_LEDS - 1 - i)) & 0x1
        vals.append([v * r, v * g, v * b])
    return vals
