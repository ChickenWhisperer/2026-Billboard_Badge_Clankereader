"""Opt-in BLE presence broadcast, so badge apps can count and list nearby badges.

OFF by default. Turn it on with "broadcast": true in /data/owner.json, or from code
with presence.set_broadcast(True) (saved to owner.json). While off, Bluetooth is not
even switched on at boot.

What is broadcast (non-connectable advert, every 400 ms, 31 bytes max):
    02 01 06                         flags
    LL FF  FF FF  'S''C''2''6'  01   manufacturer data: company 0xFFFF, magic, version
           ID ID ID                  3 random bytes from /data/presence.json (not the MAC)
           text...                   owner.json "maker_handle", printable ASCII, <= 16 bytes

Keep the advert alive: on this firmware (MicroPython 1.24 / NimBLE, ESP32-S3) calling
ble.active(True) while Bluetooth is ALREADY active restarts the stack and silently drops
the advert (no error, active() still True). Apps that share the radio must check
ble.active() before ble.active(True). As a safety net, main.py runs keep_alive(), which
re-issues the advert every KEEPALIVE_S seconds while broadcasting (never when off).

Scanning is up to each app. presence.parse(adv_data) turns a received advert into
(id_hex, text), or None if it isn't a badge presence advert.
"""

import binascii
import json
import os

OWNER = "/data/owner.json"
ID_FILE = "/data/presence.json"
LEGACY_ID_FILE = "/data/maker.json"   # Maker Detector app; reuse its id if present

COMPANY = b"\xff\xff"   # 0xFFFF: "no company / testing" per the Bluetooth SIG
MAGIC = b"SC26"
VERSION = 1
TEXT_MAX = 16
INTERVAL_US = 400_000
KEEPALIVE_S = 30     # re-assert the advert this often (see "Keep the advert alive")

_ble = None        # bluetooth.BLE(), only created once broadcasting is wanted
_started = False
_on = False        # currently advertising
_id = None         # cached 6-hex-char badge id


def _read_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_json(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    try:
        os.rename(tmp, path)
    except OSError:
        # some filesystems won't rename over an existing file
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(tmp, path)


def _clean(s):
    s = "".join(c for c in str(s or "") if 32 <= ord(c) < 127)
    return s[:TEXT_MAX]


def _valid_id(v):
    if not isinstance(v, str) or len(v) != 6:
        return False
    try:
        binascii.unhexlify(v)
        return True
    except Exception:
        return False


def enabled():
    """True if /data/owner.json has "broadcast": true."""
    return (_read_json(OWNER) or {}).get("broadcast") is True


def text():
    """The broadcast text: maker_handle as printable ASCII, at most 16 bytes."""
    return _clean((_read_json(OWNER) or {}).get("maker_handle"))


def badge_id():
    """This badge's random 6-hex-char id, created in /data/presence.json on first use."""
    global _id
    if _id:
        return _id
    v = (_read_json(ID_FILE) or {}).get("id")
    if not _valid_id(v):
        v = (_read_json(LEGACY_ID_FILE) or {}).get("id")
        if not _valid_id(v):
            v = "".join("%02x" % b for b in os.urandom(3))
        try:
            _write_json(ID_FILE, {"id": v.lower()})
        except Exception as ex:
            print("presence: could not save id:", ex)
    _id = v.lower()
    return _id


def _payload(bid=None, txt=None):
    """The advert bytes for an id/text (defaults: this badge)."""
    bid = badge_id() if bid is None else bid
    txt = text() if txt is None else _clean(txt)
    mfr = COMPANY + MAGIC + bytes([VERSION]) + binascii.unhexlify(bid) + txt.encode()
    return b"\x02\x01\x06" + bytes([len(mfr) + 1, 0xFF]) + mfr


def _apply(on, quiet=False):
    global _ble, _on
    try:
        if on:
            if _ble is None:
                import bluetooth
                _ble = bluetooth.BLE()
            if not _ble.active():
                _ble.active(True)
            _ble.gap_advertise(INTERVAL_US, _payload(), connectable=False)
            _on = True
            if not quiet:
                print("presence: broadcasting as", badge_id(), repr(text()))
        elif _ble is not None:
            # Only touch the radio if we turned it on; never wake BLE just to say "off".
            if _ble.active():
                _ble.gap_advertise(None)
            _on = False
            print("presence: broadcasting off")
    except Exception as ex:
        print("presence: BLE error:", ex)


def refresh():
    """Re-read owner.json and start, update or stop advertising to match."""
    _apply(enabled())


def start():
    """Called once at boot. Does nothing (not even BLE init) unless broadcast is on."""
    global _started
    if _started:
        return
    _started = True
    if enabled():
        _apply(True)


async def keep_alive(period_s=KEEPALIVE_S):
    """asyncio task (main.py starts it): while broadcasting, re-issue the advert every
    period_s seconds, so another ble.active(True) can't silence it until reboot.
    Does nothing, and never touches Bluetooth, while broadcasting is off."""
    import asyncio
    await asyncio.sleep(2)   # first check right after boot: apps were created just before
    while True:
        if _on:
            _apply(True, quiet=True)
        await asyncio.sleep(period_s)


def set_broadcast(on):
    """Turn broadcasting on/off, save it to owner.json (other keys kept), and apply it.
    Returns False if the setting could not be saved (it still applies until reboot)."""
    on = bool(on)
    saved = True
    try:
        try:
            os.stat(OWNER)
            d = _read_json(OWNER)
            if d is None:
                raise ValueError("unreadable, not overwriting")
        except OSError:
            d = {}
        d["broadcast"] = on
        _write_json(OWNER, d)
    except Exception as ex:
        print("presence: could not save setting:", ex)
        saved = False
    _apply(on)
    return saved


def parse(adv):
    """(id_hex, text) from received advert data (e.g. data[4] of a scan result),
    or None if it isn't a badge presence advert."""
    try:
        n = len(adv)
        i = 0
        while i + 1 < n:
            ln = adv[i]
            if ln == 0 or i + 1 + ln > n:
                return None
            if adv[i + 1] == 0xFF and ln >= 1 + 2 + 4 + 1 + 3:
                d = bytes(adv[i + 2:i + 1 + ln])
                if d[0:2] == COMPANY and d[2:6] == MAGIC and d[6] == VERSION:
                    bid = "".join("%02x" % b for b in d[7:10])
                    return bid, _clean("".join(chr(c) for c in d[10:10 + TEXT_MAX]))
            i += 1 + ln
    except Exception:
        pass
    return None
