"""ESP-NOW room: one host badge and up to 3 guests (lib/espnow_link.py pairs two).

The first badge to start hosts; the others join while its room is open, and each
gets a seat (1-3) that it keeps if it drops out and comes back. A badge that
hears a closed room (a game running) waits for it to open instead of hosting.
"""

import asyncio as aio
import random
import time

import espnow
import network

from lib.espnow_link import (BCAST, BEACON_MS, CHANNEL, HDR, LOST_MS, STALL_MS, STRAND_MS,
                             TRUST_MS, _hex, drop_radio, take_radio)

MAX_GUESTS = 3
SEATS = MAX_GUESTS + 1
DROP_MS = 15_000             # an open room frees the seat of a guest silent this long
T_HOST, T_JOIN, T_ACCEPT, T_FULL, T_BYE = 0x48, 0x4A, 0x41, 0x46, 0x42   # 'H' 'J' 'A' 'F' 'B'


class Room:
    def __init__(self, tag: bytes, on_message) -> None:
        self.tag = tag
        self.on_message = on_message    # on_message(type, msg, seat); payload at msg[HDR:]
        self.host = None                # None while listening, then True or False
        self.seat = None                # ours: 0 when hosting, 1-3 once a host seats us
        self.connected = False          # guest: seated, and the host heard lately
        self.full = False               # guest: the room we found is closed or full
        self.open = True                # host: seat new badges (the app closes it in a game)
        self.players = 1                # badges in the room, the host included
        self.member = [False] * SEATS   # host: the seat has a badge (maybe away)
        self.live = [False] * SEATS     # host: and that badge was heard lately
        self.mac = None
        self.e = None
        self._guests = {}               # host: mac -> [seat, last heard ms]
        self._host_mac = None           # guest: our host, or the one we're asking
        self._host_ms = 0
        self._listen_until = 0
        self._sweep_ms = 0
        self._warned = False
        self._beacon = self.packet(T_HOST, 2)
        self._join = self.packet(T_JOIN, 0)
        self._accept = self.packet(T_ACCEPT, 1)
        self._full = self.packet(T_FULL, 0)
        self._bye = self.packet(T_BYE, 0)

    def packet(self, ptype: int, size: int) -> bytearray:
        """A packet buffer to reuse: header filled in, `size` payload bytes from HDR on."""
        return bytearray(self.tag + bytes((ptype,)) + bytes(size))

    def send_guests(self, pkt: bytearray) -> None:
        """Host: send to every guest heard lately."""
        live = self.live
        for mac, g in self._guests.items():
            if live[g[0]]:
                self._raw(mac, pkt)

    def send_host(self, pkt: bytearray) -> bool:
        """Guest: send to our host, once it has seated us."""
        if self.seat is None or self._host_mac is None:
            return False
        return self._raw(self._host_mac, pkt)

    async def run(self, alive) -> None:
        """Own the radio until alive() turns False."""
        await take_radio(self)
        sta = network.WLAN(network.STA_IF)
        e = None
        try:
            sta.active(True)
            try:
                sta.disconnect()
            except OSError:
                pass
            try:
                sta.config(channel=CHANNEL)    # every badge must be on the same channel
            except Exception as ex:
                print("room: couldn't set the channel:", ex)
            self.mac = bytes(sta.config("mac"))
            e = self.e = espnow.ESPNow()
            try:
                e.config(rxbuf=2048)           # room for a burst while the screen holds us up
            except Exception:
                pass
            e.active(True)
            self._add(BCAST)
            print(f"room: listening on channel {CHANNEL} as {_hex(self.mac)}")
            now = time.ticks_ms()
            self._listen_until = self._listen_end(now)
            last = next_tx = now
            trust = time.ticks_add(now, TRUST_MS)   # getting here may have waited on the screen
            while True:
                now = time.ticks_ms()
                gap = time.ticks_diff(now, last)
                last = now
                if gap > STALL_MS:             # we were frozen, and so was the app
                    self._shift(gap)
                    trust = time.ticks_add(now, TRUST_MS)   # let its run() catch up first
                if time.ticks_diff(now, trust) >= 0 and not alive():
                    break
                self._receive(now)
                self._check(now)
                if time.ticks_diff(now, next_tx) >= 0:
                    next_tx = time.ticks_add(now, BEACON_MS)
                    self._announce()
                await aio.sleep_ms(10)
        finally:
            mine = drop_radio(self)
            if e is not None:
                if self.host:
                    for mac in self._guests:
                        self._raw(mac, self._bye, True)   # so the guests know at once
                elif self._host_mac is not None:
                    self._raw(self._host_mac, self._bye, True)
                if mine:
                    try:
                        e.active(False)
                    except Exception:
                        pass
            self.connected = False
            if mine:
                sta.active(False)
            print("room: radio off")

    # ---- internals -------------------------------------------------------------

    def _listen_end(self, now):
        return time.ticks_add(now, 500 + random.getrandbits(10))   # 0.5-1.5 s

    def _shift(self, gap):
        """A stall isn't anyone's silence: move every 'last heard' forward by it."""
        self._listen_until = time.ticks_add(self._listen_until, gap)
        self._host_ms = time.ticks_add(self._host_ms, gap)
        for g in self._guests.values():
            g[1] = time.ticks_add(g[1], gap)

    def _check(self, now):
        if self.host is None:
            if time.ticks_diff(now, self._listen_until) >= 0:
                self.host, self.seat, self.connected = True, 0, True
                self.member[0] = self.live[0] = True
                print("room: no game nearby; hosting")
        elif self.host:
            if time.ticks_diff(now, self._sweep_ms) >= 0:
                self._sweep_ms = time.ticks_add(now, 100)
                self._update_seats(now)
        else:
            silent = time.ticks_diff(now, self._host_ms)
            if self.connected and silent > LOST_MS:
                self.connected = False
                print("room: lost the host")
            if silent > STRAND_MS:
                print("room: no host any more; looking again")
                self.host = self.seat = self._host_mac = None
                self.connected = self.full = False
                self._listen_until = self._listen_end(now)

    def _update_seats(self, now):
        member, live = self.member, self.live
        for s in range(1, SEATS):
            member[s] = live[s] = False
        gone = None
        players = 1
        for mac, g in self._guests.items():
            silent = time.ticks_diff(now, g[1])
            if self.open and silent > DROP_MS:
                gone = mac
                continue
            member[g[0]] = True
            live[g[0]] = silent <= LOST_MS
            players += 1
        if gone is not None:
            print(f"room: seat {self._guests.pop(gone)[0]} is free again")
        self.players = players

    def _announce(self):
        if self.host:
            b = self._beacon
            b[HDR] = 1 if self.open and len(self._guests) < MAX_GUESTS else 0
            b[HDR + 1] = self.players
            self._raw(BCAST, b)
        elif self.host is False and self._host_mac is not None and not self.connected:
            self._raw(self._host_mac, self._join)

    def _receive(self, now):
        e, tag = self.e, self.tag
        for _ in range(20):                    # a burst at most per pass
            try:
                mac, msg = e.irecv(0)
            except ValueError:                 # ESP-NOW's receive buffer got out of step (rare)
                self._restart()
                return
            if mac is None:
                return
            if (len(msg) < HDR or msg[0] != tag[0] or msg[1] != tag[1]
                    or msg[2] != tag[2] or msg[3] != tag[3]):
                continue                       # someone else's ESP-NOW traffic
            if self.host:
                self._host_rx(mac, msg, now)
            else:
                self._guest_rx(mac, msg, now)

    def _host_rx(self, mac, msg, now):
        t = msg[4]
        g = self._guests.get(mac)
        if g is not None:
            g[1] = now
        if t == T_JOIN:
            self._on_join(mac, g, now)
        elif t == T_BYE:
            if g is not None:
                g[1] = time.ticks_add(now, -LOST_MS - 1)   # away at once
        elif t == T_HOST:
            if not self._guests and bytes(mac) < self.mac:   # two empty rooms: lower MAC hosts
                print("room: another badge is hosting too; joining it")
                self.host, self.seat, self.connected = False, None, False
                self.member[0] = self.live[0] = False
                self._ask(mac, now)
        elif g is not None:
            self._deliver(t, msg, g[0])

    def _on_join(self, mac, g, now):
        if g is None:
            self._add(mac)
            if not self.open or len(self._guests) >= MAX_GUESTS:
                self._raw(mac, self._full)
                return
            g = [self._free_seat(), now]
            self._guests[bytes(mac)] = g
            print(f"room: {_hex(mac)} took seat {g[0]}")
            self._update_seats(now)
        self._accept[HDR] = g[0]
        self._raw(mac, self._accept)

    def _free_seat(self):
        used = 0
        for g in self._guests.values():
            used |= 1 << g[0]
        for s in range(1, SEATS):
            if not used & (1 << s):
                return s
        return MAX_GUESTS

    def _guest_rx(self, mac, msg, now):
        t = msg[4]
        mine = self._host_mac is not None and mac == self._host_mac
        if t == T_HOST:
            self._on_beacon(mac, msg, now, mine)
            return
        if not mine:
            return
        self._host_ms = now
        if t == T_ACCEPT and len(msg) > HDR:
            if not self.connected:
                print(f"room: in the game as seat {msg[HDR]}")
            self.seat, self.full, self.connected = msg[HDR], False, True
        elif t == T_FULL:
            self.full = True
        elif t == T_BYE:
            if self.connected:
                print("room: the host left")
            self.connected = False
        elif self.seat is not None:
            self.connected = True              # its game packets: it still has us
            self._deliver(t, msg, 0)

    def _on_beacon(self, mac, msg, now, mine):
        is_open = len(msg) > HDR and msg[HDR] == 1
        if mine:
            self._host_ms = now
            if self.seat is None:
                self.full = not is_open
            return
        if self.seat is not None:
            return                             # seated in another room
        if self._host_mac is not None and not (self.full and is_open):
            return                             # already joining an open room
        self.host = False
        self.full = not is_open
        self._ask(mac, now)

    def _ask(self, mac, now):
        """Guest: make `mac` our host and ask it for a seat."""
        self._host_mac = bytes(mac)
        self._add(self._host_mac)
        self._host_ms = now
        if not self.full:
            self._raw(self._host_mac, self._join)

    def _deliver(self, t, msg, seat):
        try:
            self.on_message(t, msg, seat)
        except Exception as ex:                # a bad packet mustn't end the room
            if not self._warned:
                self._warned = True
                print("room: couldn't use a packet (all badges on the same version?):", repr(ex))

    def _restart(self):
        """Empty ESP-NOW's receive buffer by restarting it; the room stays as it was."""
        print("room: ESP-NOW receive error; restarting it")
        e = self.e
        e.active(False)
        e.active(True)
        self._add(BCAST)
        if self._host_mac is not None:
            self._add(self._host_mac)
        for mac in self._guests:
            self._add(mac)

    def _add(self, mac):
        try:
            self.e.add_peer(mac)
        except OSError:
            pass                               # already known

    def _raw(self, mac, pkt, sync=False):
        try:
            return self.e.send(mac, pkt, sync)
        except OSError:
            return False                       # send queue full or radio busy: drop it
