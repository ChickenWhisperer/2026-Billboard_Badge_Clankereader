"""Copy billboard_epd into /lib, then run this on a B1 Billboard Badge."""
import asyncio
import framebuf
from billboard_epd import create_panel, FRAME_BYTES, WIDTH, HEIGHT
from billboard_epd.display import Display


async def main():
    panel = create_panel()
    display = Display(panel)
    frame = bytearray(b"\xff" * FRAME_BYTES)
    canvas = framebuf.FrameBuffer(frame, WIDTH, HEIGHT, framebuf.MONO_HLSB)
    canvas.text("Hello Billboard", 32, 32, 0)
    canvas.rect(24, 24, 160, 28, 0)
    try:
        await display.draw(frame)
        # Slow three-tone image: unlike monochrome frames, these are pigment masks.
        black, gray = bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)
        ink = framebuf.FrameBuffer(black, WIDTH, HEIGHT, framebuf.MONO_HLSB)
        mid = framebuf.FrameBuffer(gray, WIDTH, HEIGHT, framebuf.MONO_HLSB)
        ink.fill_rect(0, 0, 320, HEIGHT, 1)
        mid.fill_rect(320, 0, 320, HEIGHT, 1)
        await display.grayscale(black, gray)
    finally:
        await panel.close()


asyncio.run(main())
