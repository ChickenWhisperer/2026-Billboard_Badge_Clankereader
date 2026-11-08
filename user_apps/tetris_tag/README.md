# Tetris Tag

Tetris Tag takes inspiration from Tetris to assemble your name on the badge. Use User A and B buttons to play!

Tetris-like pieces fall and stack up to spell a name along the bottom edge of
the screen, two at a time, and you steer them. The name blinks three times, stays up for
30 seconds, then it is built again, and it loops forever. It ships with the placeholder
"YOUR NAME"; put your own in `NAMES` (see below).

Author: Nikita Pashenkov, <https://github.com/pa-nik>

Concept by the Soviet people, execution by [pa-nik](https://github.com/pa-nik) and Claude.

Controls:
```
User A: turn the left falling piece 90 degrees clockwise (bottom left button)
User B: turn the right falling piece 90 degrees clockwise (bottom right button)
Menu +/-: back to the app menu (as for every app)
```
The two top buttons are Menu -/+, which the badge firmware reserves for leaving an app, so
the game uses the two bottom ones (positions from the PCB layout in `hardware/billboard_pcb`).
Between names (while the screen is clearing, the name blinks or the finished name is held)
User A skips to the next name and User B restarts the current one.

To install, run this from the root of the repo (the app also needs the newer display driver
and its banner image; see Installing below):
```
mpremote cp firmware/badge/lib/epd.py :lib/epd.py
mpremote cp firmware/badge/lib/epd_fast.py :lib/epd_fast.py
mpremote cp firmware/badge/lib/large_png.py :lib/large_png.py
mpremote cp firmware/badge/data/rendered_luts.py :data/rendered_luts.py
mpremote cp user_apps/tetris_tag/tetris_banner.png :data/graphics/tetris_banner.png
mpremote cp user_apps/tetris_tag/tetris_tag.py :apps/tetris_tag.py
mpremote reset
```
Then select "Tetris Tag" from the menu.

## How to play

Two pieces appear together at 25% of the screen height, each turned a random quarter
turn. They fall a few rows per refresh (about 1.4 s) **without turning** until you turn
them. Press User A for the piece on the left and User B for the one on the right; each
press turns it 90 degrees clockwise at the next refresh. When a piece is the right way up
it drops into its place on the next refresh. A piece you leave alone turns itself,
90 degrees a refresh by the shorter way, once it reaches 50% of the screen height
(`AUTO_TURN_PCT`), so the game plays itself if nobody presses. Buttons only count above
that line. Each piece stays in its own half of the screen while it is falling, so the two
never collide. When one piece is alone (the last of a letter), either button turns it.

## Make it your own

Edit the top of `tetris_tag.py`:

- `NAMES`: what to show, in order. It starts as `("YOUR NAME",)`: replace that with your
  own name, or list several to cycle through them. Only characters in `FONT` can be
  shown: the letters A to Z and a to z, é, and space. There are no digits or punctuation yet:
  add a glyph to `FONT` for any other character (5 wide, 7 tall, `X` is a block). Long names
  get small blocks and take longer, since the pieces fall a fixed number of rows per refresh.
- `PIECES_AT_ONCE`, `SPAWN_PCT`, `FALL_ROWS`, `AUTO_TURN_PCT`: how many pieces fall at a time
  (1 or 2), the screen height they start from, how far they fall each refresh, and the
  screen height where an unturned piece starts turning itself.
- `HOP_PULLS`, `SETTLE_PULLS`: how hard each refresh drives the panel (below).
- `BLINK_MODE`, `BLINK_COUNT`, `BLINK_MS`: see below. `HOLD_S`: how long the finished name
  stays up after the blinking (30 s).
- `BANNER_PCT`, `BANNER_IMAGE`: the banner height and its image (see Banner).

## Banner

A red banner runs across the top 25% of the screen, with "ТЕТРИС ☭ TAG" in white
Helvetica Bold centred on it. Pieces fall in the space below it.

The lettering is an image, `tetris_banner.png`, not text drawn by the badge: the badge
has no Helvetica, and its built-in font has neither Cyrillic nor the hammer and sickle.
`make_banner.py` makes it on your computer:

```
pip install pillow fonttools
python3 make_banner.py                                   # rewrites tetris_banner.png
python3 make_banner.py --text "HELLO" --pct 20           # other text or height
mpremote cp tetris_banner.png :data/graphics/tetris_banner.png
```

It sets the text in the Helvetica Bold that ships with macOS, which has the Cyrillic
and Latin letters. Helvetica has no hammer and sickle, so that one glyph comes from a
fallback font (Apple Symbols by default, thickened a little to match the bold letters;
`--symbol-font` picks another). Only the rendered shapes end up in the PNG, no font file.
`BANNER_PCT` in `tetris_tag.py` and `--pct` must agree (25% is 168 px). If the image
is not on the badge, the banner is a plain red band.

The panel can only make red with a full refresh, so the banner is drawn once, by the full
refresh at the start of each name, and the partial refreshes then hold it untouched.

## Fast partial refresh

The panel's own refresh takes about 30 s, which is no use for animation. This app
uses the fast partial refresh in `firmware/badge/lib/epd_fast.py`
(`display.update(fast=True)`), which redraws only the pixels that changed, in about
1.1 s per drive step ("pull"):

1. Each name starts with one full refresh to a blank field. That is the "clear
   screen" step, and it also wipes any ghosting.
2. The pieces then fall two at a time (see How to play). A fall is one refresh to show the
   pieces, a few more while they fall and turn, and one to drop each into place. A square,
   which looks the same turned, is never turned and drops straight away.
   Pieces are two to four blocks, with no single-block pieces. A piece normally joins blocks
   edge to edge, like a real Tetris piece, but where a letter needs it (the diagonal strokes
   of K, N, R, V, Y) a piece may join two blocks that touch only at a corner, a shape the real
   game does not have. The splitter prefers real shapes and fewer pieces. A block with nothing
   around it at all, such as the dot of a lowercase i, would still be alone; none of the
   built-in letters in the default name do that.
3. Moving frames use `HOP_PULLS` (1, so the piece is pale while it moves) and the frame where
   a piece lands `SETTLE_PULLS` (3, solid). Anything left pale is topped up by the next frame.
   There is no LED fill any more; the LEDs only blink at the end of a name.

Red pieces were tried (falling red, then one extra refresh to turn black) and did not work
on the fast refresh: the fast waveform is black and white by design and cannot make a good
red on this panel. The red banner is unaffected: it is held, not driven.

Speed. A pass of "YOUR NAME" is 37 pieces in about 19 waves. In simulation the falling time per pass
(plus the full-screen refresh at the start, about 37 s, which cannot be made faster) is
about 235 s if nobody presses a button and less if you turn the pieces (about 195 s with some
random presses): a piece you turn right way up early drops sooner. These times are the
simulator's; the app prints `refresh ms:` over serial after every frame, so you can see the
real ones.

If
the badge is not a B1 panel, or the firmware does not have `epd_fast.py`, the app
falls back to a slideshow of full refreshes, one per snapshot, with the pieces in the
air in red. The slideshow does not turn the pieces.

## Blinking and holding

The paper cannot refresh in half a second, so `BLINK_MODE = "leds"` (the default)
keeps the finished name on the paper and blinks the LED bar, half a second on and
half a second off, three times. `BLINK_MODE = "screen"` blinks the paper itself
(name, blank, name...), but each step is at least one drive step, so it is much slower.

When the blinking is over the finished name stays on the screen, untouched, for `HOLD_S`
seconds (30) before the next round starts with a full-screen clear. User A skips the wait to
the next name and User B restarts the current one right away.

## Installing

The commands at the top copy the app, its banner image and the display files it needs.
The app needs the newer display driver on the badge as well as the app itself. Badges that
shipped with older firmware are missing `rendered_luts.py`, which the fast refresh uses, and
an updated `large_png.py`, which the new `epd.py` relies on. `mpremote ls :lib :data` shows
what is on the badge. After the first install only `tetris_tag.py` changes, so
`mpremote cp user_apps/tetris_tag/tetris_tag.py :apps/tetris_tag.py` and a reset is enough.

Alternatively, `python scripts/update.py push` from `firmware/` brings the whole badge up
to date, but it deletes anything on the badge that is not in `firmware/badge/`,
including this app, so copy the app again afterwards.
