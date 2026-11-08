# Snake

A black-and-white Snake game for the Billboard Badge, drawn with the fast
partial-refresh driver so the snake moves about once every 0.65 s.

> **Requires PR #33** (`firmware/badge/lib/epd_partial.py`, "add fast refresh
> w/ low ghosting"). Until #33 is merged, copy `lib/epd_partial.py` from that
> PR onto your badge first. Pong (#34) uses the same driver.

## Install

1. Make sure `/lib/epd_partial.py` (from PR #33) is on the badge.
2. Copy `snake.py` to `/apps/snake.py` on the badge.
3. Reset the badge. **Snake** appears in the app menu.

## Controls

The turns are relative to the way the snake is heading.

| Button | Action |
|---|---|
| User A | Turn left |
| User B | Turn right |
| Menu + / - | Back to the menu |

You can queue up to two turns during a frame, and pressing the opposite
button cancels the last one.

## Screens

- **Demo:** On launch, a computer player runs the game until you press A or
  B. After 10 minutes with no button press, it pauses and powers the panel
  down.
- **Play:** The header shows the current score and the high score. The high
  score is saved to `/data/snake.json`.
- **Game over:** Shows your score. Press A or B to play again, or it returns
  to the demo after 30 seconds.

If the previous app left red on the screen, the first frame is one normal
full refresh. Each new game starts with a short wipe to clear ghosting.

## LEDs

| Mode | LEDs |
|---|---|
| Demo | A rainbow snake crawls along the bar |
| Play | Green dot = head column, gold dot = food column |
| Turn queued | Cyan blink at the left or right end |
| About to crash | The whole bar pulses red |
| Food eaten | A gold burst from the middle |
| Game over | Red strobe, then a slow red glow (rainbow for a new high score) |

## Tuning

These settings are at the top of `snake.py`:

- `MIN_MOVE_MS`: slows the game below the panel's frame rate, for example `900`.
- `LED_ON`: LED brightness, 0-255.
- `DEMO_IDLE_MS`, `OVER_TO_DEMO_MS`: idle and game-over timeouts.
