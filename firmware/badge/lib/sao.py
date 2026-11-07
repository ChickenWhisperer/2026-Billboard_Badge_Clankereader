"""SAO Helper Interface for Billboard Badge"""

# https://docs.micropython.org/en/v1.27.0/library/machine.I2CTarget.html

import asyncio as aio

import machine


SAO_I2C_BUS = 0
SAO_I2C_SCL = 18
SAO_I2C_SDA = 17

SAO1_GPIO1 = 2
SAO1_GPIO2 = 42
SAO2_GPIO1 = 4
SAO2_GPIO2 = 5
SAO3_GPIO1 = 7
SAO3_GPIO2 = 6

i2c = machine.I2C(SAO_I2C_BUS, scl=SAO_I2C_SCL, sda=SAO_I2C_SDA, freq=400_000)


class Sao:
    def __init__(self, gpio1: int, gpio2: int) -> None:
        self.gpio1 = machine.Pin(gpio1)
        self.gpio2 = machine.Pin(gpio2)

        self.address: int|None = None
        self.target = None

    def set_address(self, addr: int):
        self.address = addr
        self.target = machine.I2C(
            SAO_I2C_BUS, 
            scl=machine.Pin(SAO_I2C_SCL), 
            sda=machine.Pin(SAO_I2C_SDA)
        )

    def readfrom(self, nbytes, stop=True):
        if self.address is None:
            raise ValueError("No address set, unable to read from target")
        return i2c.readfrom(self.address, nbytes, stop)

    def write(self, data: bytes) -> int:
        if self.address is None:
            raise ValueError("No address set, unable to write to target")
        return i2c.writeto(self.address, data)


SAO1 = Sao(SAO1_GPIO1, SAO1_GPIO2)
SAO2 = Sao(SAO2_GPIO1, SAO2_GPIO2)
SAO3 = Sao(SAO3_GPIO1, SAO3_GPIO2)

