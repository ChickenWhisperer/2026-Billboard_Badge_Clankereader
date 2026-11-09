"""Two badges talking over ESP-NOW: find each other, then swap small packets.

The first badge to start hosts and the other joins; they pair again after a dropout.
Every packet: tag (4 bytes, one per game) + type (1 byte) + payload (from HDR on).

    link = Link(tag, on_message)     # on_message(type, msg) gets the game's packets
    pkt = link.packet(ptype, size)   # fill pkt[HDR:], then link.send(pkt)
    await link.run(alive)            # in a task: owns the radio until alive() is False
"""

import asyncio as aio
import random
import time

import espnow
import network

CHANNEL = 1
BCAST = b"\xff" * 6
HDR = 5                      # tag + type; payloads start here
BEACON_MS = 200              # host beacons and guest join requests, while unpaired
LOST_MS = 1500               # partner silent this long: unpaired
STRAND_MS = 3 * LOST_MS      # unpaired guest's host silent this long: look for a host again
STALL_MS = 500               # our loop stalled longer than this: the badge was busy
TRUST_MS = 1000              # after a stall, give the app this long to run before believing alive()
T_HOST, T_JOIN, T_ACCEPT, T_BYE = 0x48, 0x4A, 0x41, 0x42     # 'H' 'J' 'A' 'B'
OWNER_WAIT_MS = 5000         # longest to wait for another app's radio task to close

owner = None                 # the Link or Room (lib/espnow_room.py) that has the radio now


def _hex(mac):
    return ":".join(f"{b:02x}" for b in mac)


async def take_radio(who) -> None:
    """Wait for another app's radio task to close (up to OWNER_WAIT_MS), then own the radio."""
    global owner
    t0 = time.ticks_ms()
    while owner is not None and owner is not who:
        if time.ticks_diff(time.ticks_ms(), t0) >= OWNER_WAIT_MS:
            break
        await aio.sleep_ms(50)
    owner = who


def drop_radio(who) -> bool:
    """Give the radio up; True if `who` still owned it (then it should turn it off)."""
    global owner
    if owner is not who:
        return False
    owner = None
    return True


class Link:
    def __init__(self, tag: bytes, on_message) -> None:
        self.tag = tag
        self.on_message = on_message   # on_message(type, msg); payload at msg[HDR:]
        self.mac = None                # ours
        self.peer = None               # the partner, or the host we're asking to join
        self.host = None               # None while listening, then True or False
        self.paired = False
        self.e = None
        self._rx_ms = 0                # when the peer was last heard
        self._listen_until = 0
        self._warned = False
        self._beacon = self.packet(T_HOST, 0)
        self._join = self.packet(T_JOIN, 0)
        self._accept = self.packet(T_ACCEPT, 0)
        self._bye = self.packet(T_BYE, 0)

    def packet(self, ptype: int, size: int) -> bytearray:
        """A packet buffer to reuse: header filled in, `size` payload bytes from HDR on."""
        return bytearray(self.tag + bytes((ptype,)) + bytes(size))

    def send(self, pkt: bytearray) -> bool:
        """Send to the partner. False if not paired or the radio is busy."""
        if not self.paired:
            return False
        return self._raw(self.peer, pkt)

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
                sta.config(channel=CHANNEL)    # both badges must be on the same channel
            except Exception as ex:
                print("link: couldn't set the channel:", ex)
            self.mac = bytes(sta.config("mac"))
            e = self.e = espnow.ESPNow()
            try:
                e.config(rxbuf=2048)           # room for a burst while the screen holds us up
            except Exception:
                pass
            e.active(True)
            self._add(BCAST)
            print(f"link: listening on channel {CHANNEL} as {_hex(self.mac)}")
            now = time.ticks_ms()
            self._listen_until = self._listen_end(now)
            last = next_tx = now
            trust = time.ticks_add(now, TRUST_MS)   # getting here may have waited on the screen
            while True:
                now = time.ticks_ms()
                gap = time.ticks_diff(now, last)
                last = now
                if gap > STALL_MS:             # we were frozen, and so was the app
                    self._rx_ms = time.ticks_add(self._rx_ms, gap)
                    self._listen_until = time.ticks_add(self._listen_until, gap)
                    trust = time.ticks_add(now, TRUST_MS)   # let its run() catch up first
                if time.ticks_diff(now, trust) >= 0 and not alive():
                    break
                self._receive(now)
                silent = time.ticks_diff(now, self._rx_ms)
                if self.paired and silent > LOST_MS:
                    self.paired = False
                    print("link: lost the other badge")
                elif not self.paired and self.host is False and silent > STRAND_MS:
                    self.host = self.peer = None
                    self._listen_until = self._listen_end(now)
                    print("link: our host is gone; looking again")
                if not self.paired:
                    if self.host is None and time.ticks_diff(now, self._listen_until) >= 0:
                        self.host = True
                        print("link: no other badge yet; hosting")
                    if time.ticks_diff(now, next_tx) >= 0:
                        next_tx = time.ticks_add(now, BEACON_MS)
                        if self.host:
                            self._raw(BCAST, self._beacon)
                        elif self.peer is not None:
                            self._raw(self.peer, self._join)
                await aio.sleep_ms(10)
        finally:
            mine = drop_radio(self)
            if e is not None:
                if self.peer is not None:
                    self._raw(self.peer, self._bye, True)   # so the partner pauses right away
                if mine:
                    try:
                        e.active(False)
                    except Exception:
                        pass
            self.paired = False
            if mine:
                sta.active(False)
            print("link: radio off")

    def _listen_end(self, now):
        return time.ticks_add(now, 500 + random.getrandbits(10))   # 0.5-1.5 s

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
            t = msg[4]
            mine = self.peer is not None and mac == self.peer
            if mine:
                self._rx_ms = now
            if t == T_HOST:
                self._on_beacon(mac)
            elif t == T_JOIN:
                self._on_join(mac)
            elif t == T_ACCEPT:
                if mine and self.host is False and not self.paired:
                    self._pair()
            elif t == T_BYE:
                if mine and self.paired:
                    self.paired = False
                    print("link: the other badge left")
            elif mine:                         # the game's own packets
                if not self.paired:
                    self._pair()               # it was only a pause
                try:
                    self.on_message(t, msg)
                except Exception as ex:        # a bad packet mustn't end the link
                    if not self._warned:
                        self._warned = True
                        print("link: couldn't use a packet (both badges on the same version?):",
                              repr(ex))

    def _restart(self):
        """Empty ESP-NOW's receive buffer by restarting it; pairing state stays."""
        print("link: ESP-NOW receive error; restarting it")
        e = self.e
        e.active(False)
        e.active(True)
        self._add(BCAST)
        if self.peer is not None:
            self._add(self.peer)

    def _on_beacon(self, mac):
        if self.paired:
            return
        if self.host:
            if bytes(mac) >= self.mac:
                return                         # they give way to us
            print("link: another badge is hosting too; joining it")
        self.host = False
        if self.peer is None or mac != self.peer:
            self.peer = bytes(mac)
            self._add(self.peer)
        self._rx_ms = time.ticks_ms()
        self._raw(self.peer, self._join)

    def _on_join(self, mac):
        if not self.host:
            return
        if self.paired and mac != self.peer:
            return                             # already playing someone else
        if self.peer is None or mac != self.peer:
            self.peer = bytes(mac)
            self._add(self.peer)
        self._raw(self.peer, self._accept)
        if not self.paired:
            self._pair()

    def _pair(self):
        self.paired = True
        self._rx_ms = time.ticks_ms()
        role = "hosting" if self.host else "joined"
        print(f"link: playing with {_hex(self.peer)} ({role})")

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
