"""Show KITT with Larson Scanner"""

from apps.base_app import BaseApp
from lib.button import MenuMinus, MenuPlus, UserA, UserB, Boot
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds

import time
import random

class KittApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "KITT"
        self.sleep_ms = 100
        self.launched = False
        self.direction = +1
        self.lit_led = 0
    
    def show_kitt(self):
        print(f"Showing KITT")
        display.clear()
        display.draw_png(f'/data/graphics/kitt_b_w_r.png')
        display.update()

    def update_scanner(self):
        # scanner is red LEDS that scan left to right, then right to left

        leds.clear()
        if self.lit_led - 2 >= 0:
            leds[self.lit_led - 1] = (5, 0, 0)
        if self.lit_led - 1 >= 0:
            leds[self.lit_led - 1] = (20, 0, 0)
        leds[self.lit_led] = (50, 0, 0)
        if self.lit_led + 1 < NUM_LEDS:
            leds[self.lit_led + 1] = (10, 0, 0)
        if self.lit_led + 2 < NUM_LEDS:
            leds[self.lit_led + 2] = (5, 0, 0)
        leds.write()

        self.lit_led += self.direction
        if self.lit_led >= NUM_LEDS:
            self.lit_led = NUM_LEDS - 2
            self.direction = -1
        elif self.lit_led < 0:
            self.lit_led = 1
            self.direction = +1
    
    def run(self):
        if self.launched:
            self.show_kitt()
            self.lastupdate = time.time()
        else:
            self.update_scanner()
            pass
