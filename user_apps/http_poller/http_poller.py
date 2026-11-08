"""HTTP Poller App to periodically load an image from the internet"""

from apps.base_app import BaseApp
from lib.board import reg_in_v_sns
from lib.button import MenuMinus, MenuPlus, UserA, UserB, Boot
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds
import network
import time
from lib.json_configs import load
import requests
import asyncio as aio
import sys

WLAN_CREDENTIALS_FILE = "data/wlan_credentials.json"
IMAGE_URL = "https://thzinc.com/assets/2026-temporary-supercon-assets/test.png"
UPDATE_INTERVAL_SECONDS = 300
RETRY_INTERVAL_SECONDS = 5


def await_pressed_buttons():
    if MenuMinus.pressed():
        print("Menu -")
    if MenuPlus.pressed():
        print("Menu +")
    if UserA.pressed():
        print("User A")
    if UserB.pressed():
        print("User B")
    if Boot.pressed():
        print("Boot")


class HTTPPollerApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "HTTP Poller App"
        self.sleep_ms = 100
        self.launched = False

        # Time between updates
        self.update_interval_seconds = UPDATE_INTERVAL_SECONDS

        # Time to wait after a failed update
        self.retry_interval_seconds = RETRY_INTERVAL_SECONDS

        # Time at (or after) which to update
        self.next_update_time = 0

        # Variable to hold network interface handle
        self.nic = network.WLAN(network.WLAN.IF_STA)

        self.image_ready: bool = False

        # Variable to hold the last downloaded image
        self.image_data: bytes = None

    async def fetch_image(self):
        for attempt in range(20):
            print("attempt %d: connecting to network" % (attempt))
            self.nic.active(True)
            for credential in load(WLAN_CREDENTIALS_FILE):
                ssid = credential["ssid"]
                key = credential["key"]
                print("attempting to connect to ssid %s with %s" % (ssid, key))
                try:
                    self.nic.connect(ssid, key)
                    for _ in range(20):
                        if self.nic.isconnected():
                            break
                        await aio.sleep(0.250)
                except Exception as ex:
                    print("failed connecting to ssid %s" % (ssid))
                    sys.print_exception(ex)

            if not self.nic.isconnected():
                self.nic.active(False)
                print("failed to connect to any configured SSID")
                return False

            print("requesting %s" % (IMAGE_URL))
            response = requests.get(IMAGE_URL)

            if not (response.status_code >= 200 and response.status_code <= 299):
                self.nic.active(False)
                print(
                    "failed to get successful response; HTTP status code %d; headers %s"
                    % (response.status_code, response.headers)
                )
                return False

            print("reading response content")
            self.image_data = response.content
            self.image_ready = True

            print("disconnecting from network")
            self.nic.active(False)
            return True

    def display_image(self):
        if self.image_ready:
            print("drawing image")
            display.clear()
            display.draw_png(data=self.image_data)
            display.update()
            self.image_ready = False
            self.image_data = None

    async def attempt_fetch_image(self):
        if await self.fetch_image():
            self.next_update_time = time.time() + self.update_interval_seconds
        else:
            print(
                "update failed; waiting %d seconds before retry"
                % (self.retry_interval_seconds)
            )
            self.next_update_time = time.time() + self.retry_interval_seconds

    def run(self):
        """Add your app behavior here. Will be run in a while True loop."""
        if self.launched:  ## Setup
            pass
        else:  ## Mainloop
            if self.image_ready:
                self.display_image()
            elif time.time() > self.next_update_time:
                aio.create_task(self.attempt_fetch_image())
            else:
                await_pressed_buttons()
