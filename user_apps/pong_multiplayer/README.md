# Multiplayer Pong

Two Pong games played badge to badge over ESP-NOW (no Wi-Fi network needed), drawn with the partial-refresh driver: a new frame about every 0.65 s with no flashing.

## Install

From the repo root, on every badge that will play:

```bash
mpremote cp user_apps/pong_multiplayer/espnow_link.py :lib/espnow_link.py
mpremote cp user_apps/pong_multiplayer/espnow_room.py :lib/espnow_room.py
mpremote cp user_apps/pong_multiplayer/pong_1v1.py :apps/pong_1v1.py
mpremote cp user_apps/pong_multiplayer/pong_4p.py :apps/pong_4p.py
mpremote reset
```

Both need `lib/epd_partial.py` on the badge. Pong 1v1 also needs Pong (`user_apps/pong/pong.py`, installed as `apps/pong.py`).

## Pong 1v1

- Two badges. Open it on both; the first one opened hosts and the other joins.
- Each player sees their own paddle on the left: `User A` up, `User B` down.
- First to 5 wins; then `User A` or `User B` on either badge starts a new game.

## Pong 4P

- 1 to 4 badges; the badge plays any empty side.
- The first badge opened hosts, the others join, and the host presses `User A` to start.
- Every badge shows the same court, with your paddle outlined. Bottom and top paddles: `User A` left, `User B` right. Left and right paddles: `User A` up, `User B` down.
- 3 lives each; at 0 your side becomes a wall, and the last player left wins.
- The LED bar shows where the ball is along your side, live.
