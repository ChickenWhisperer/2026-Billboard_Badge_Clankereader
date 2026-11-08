"""Standalone driver for the 960 x 672 Billboard Badge B1 display.

Importing this package does not configure pins or allocate framebuffers.
See the distribution README for framebuffer conventions and examples.
"""

from .decay import Decay

__version__ = "0.1.0-rc.1"
WIDTH = 960
HEIGHT = 672
FRAME_BYTES = WIDTH * HEIGHT // 8


def create_panel(**kwargs):
    """Construct a MicroPython Panel lazily, without an LVGL dependency."""
    from .panel import Panel
    return Panel(**kwargs)


def create_display(**kwargs):
    """Construct a Panel with the managed monochrome/slow-grayscale API."""
    from .display import Display
    return Display(create_panel(**kwargs))
