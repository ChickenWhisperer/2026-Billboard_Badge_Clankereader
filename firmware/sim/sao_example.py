"""Example SAO emulator for the badge simulator. Run with ordinary Python 3:

    python3 sim/sao_example.py [--addr 50] [--port 47100]

It connects to the simulator's SAO bus and acts like a small I2C memory (24C02-style
EEPROM): 256 bytes, an 8-bit address pointer that auto-increments. On the badge side:

    from lib.sao import i2c
    i2c.scan()                         # [80]  (0x50)
    i2c.writeto_mem(0x50, 0x10, b"hi")
    i2c.readfrom_mem(0x50, 0x10, 2)    # b'hi'

Copy it to make other SAOs. The protocol (lines of ASCII, bytes in hex):

    SAO -> sim:  SAO <addr> [<addr> ...]       once, on connect
    sim -> SAO:  I2C <addr> <write> <nread>    write the bytes (or "-"), then read
                                               nread bytes with a repeated start (if > 0)
    SAO -> sim:  OK <read>                     the bytes read (or "-")
                 NACK                          don't acknowledge
"""

import argparse
import socket
import time


class Memory:
    def __init__(self):
        self.data = bytearray(256)
        self.pointer = 0

    def transaction(self, write: bytes, nread: int) -> bytes:
        if write:
            self.pointer = write[0]
            for byte in write[1:]:
                self.data[self.pointer] = byte
                self.pointer = (self.pointer + 1) % 256
        out = bytearray()
        for _ in range(nread):
            out.append(self.data[self.pointer])
            self.pointer = (self.pointer + 1) % 256
        return bytes(out)


def serve(addr: int, port: int):
    device = Memory()
    while True:
        try:
            conn = socket.create_connection(("127.0.0.1", port))
        except OSError:
            print(f"Waiting for the simulator on port {port}...")
            time.sleep(2)
            continue
        print(f"Connected as I2C device 0x{addr:02x}")
        conn.sendall(f"SAO {addr:02x}\n".encode())
        for line in conn.makefile("r"):
            _, _, write_hex, nread = line.split()
            write = b"" if write_hex == "-" else bytes.fromhex(write_hex)
            read = device.transaction(write, int(nread))
            print(f"  wrote {write.hex() or '-'}, read {read.hex() or '-'}")
            conn.sendall(f"OK {read.hex() or '-'}\n".encode())
        print("Simulator disconnected")
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--addr", default="50", help="I2C address in hex (default 50)")
    parser.add_argument("--port", type=int, default=47100, help="simulator SAO port")
    args = parser.parse_args()
    serve(int(args.addr, 16), args.port)
