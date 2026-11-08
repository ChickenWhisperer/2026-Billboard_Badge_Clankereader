# Pong

You against the badge, drawn with the partial-refresh driver (`lib/epd_partial.py`): a new frame about every 0.65 s, with no flashing.

## Install

```bash
mpremote cp user_apps/pong/pong.py :apps/pong.py
mpremote reset
```

Needs `lib/epd_partial.py` on the badge.

## Controls

- `User A`: paddle up
- `User B`: paddle down
- First to 5 wins; then `User A` or `User B` starts a new game.
- The LED bar shows where the ball is right now; the screen catches up about 1.5 times a second.
