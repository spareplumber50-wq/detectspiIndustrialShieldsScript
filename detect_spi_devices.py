#!/usr/bin/env python3
"""
MAX6675 K-Type Thermocouple Reader
Industrial Shields TouchBerry Pi 4B -- SPI Bus 0, CS0 and CS1

The MAX6675 requires a very specific read sequence that spidev alone cannot
handle cleanly, because spidev ties CS low for the entire transfer:

  1. Assert CS HIGH  --> stops any in-progress conversion, starts a new one
  2. Wait 220 ms    --> allow the ADC conversion to complete (datasheet max)
  3. Assert CS LOW  --> chip begins outputting the first bit on MISO
  4. Clock 16 bits  --> read the temperature word
  5. Assert CS HIGH --> end the read

To do this correctly we disable spidev's automatic CS control (no_cs=True)
and drive the CS GPIO pins directly using RPi.GPIO, while still using the
hardware SPI bus for clocking via /dev/spidev0.0.

Pin mapping on the Raspberry Pi 4B (BCM numbering):
  SPI0 MISO : GPIO 9
  SPI0 SCLK : GPIO 11
  SPI0 CE0  : GPIO 8   -- used for CS0 (driven manually)
  SPI0 CE1  : GPIO 7   -- used for CS1 (driven manually)

Requirements:
    pip install spidev RPi.GPIO

Enable SPI on the TouchBerry Pi 4B:
    sudo raspi-config -> Interface Options -> SPI -> Enable
Or add 'dtparam=spi=on' to /boot/firmware/config.txt and reboot.

Run with:
    sudo python3 detect_max6675.py
"""

import spidev
import time
import os
import sys

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not found. Install it with: pip install RPi.GPIO")
    sys.exit(1)

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------

SPI_BUS      = 0          # /dev/spidev0.x
SPI_SPEED_HZ = 500_000    # 500 kHz -- conservative, datasheet max is 4.3 MHz
SPI_MODE     = 0b00        # CPOL=0, CPHA=0

# BCM GPIO numbers for the CS lines
CS_PINS = {
    0: 8,   # CE0 = BCM GPIO 8
    1: 7,   # CE1 = BCM GPIO 7
}

# MAX6675 timing
CONVERSION_TIME_S = 0.25   # 250 ms (datasheet conversion max is 220 ms)
CS_SETTLE_TIME_S  = 0.002  # 2 ms CS settling time before clocking


# -------------------------------------------------------------------------
# GPIO helpers
# -------------------------------------------------------------------------

def gpio_setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in CS_PINS.values():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # CS idle high


def gpio_cleanup():
    GPIO.cleanup()


def cs_high(cs_index):
    GPIO.output(CS_PINS[cs_index], GPIO.HIGH)


def cs_low(cs_index):
    GPIO.output(CS_PINS[cs_index], GPIO.LOW)


# -------------------------------------------------------------------------
# MAX6675 read
# -------------------------------------------------------------------------

def read_max6675_raw(spi, cs_index):
    """
    Perform the correct MAX6675 read sequence.

    The chip starts a new conversion when CS goes high, and outputs data
    when CS is pulled low again after the conversion is complete.

    Returns the raw 16-bit integer.
    """
    # Trigger a new conversion by asserting CS high
    cs_high(cs_index)
    time.sleep(CONVERSION_TIME_S)   # wait for ADC to complete

    # Assert CS low to begin clocking out the result
    cs_low(cs_index)
    time.sleep(CS_SETTLE_TIME_S)

    # Read 2 bytes -- the MAX6675 ignores MOSI entirely
    raw_bytes = spi.xfer2([0x00, 0x00])

    # Release CS
    cs_high(cs_index)

    return (raw_bytes[0] << 8) | raw_bytes[1]


def parse_max6675(raw):
    """
    Decode the 16-bit MAX6675 response word.

    Bit layout (MSB first):
      D15     : dummy sign bit, always 0
      D14-D3  : 12-bit ADC result, temperature = counts * 0.25 degrees C
      D2      : open thermocouple flag (1 = thermocouple not connected)
      D1      : device ID bit, always 0 for MAX6675
      D0      : tri-state output (ignore)
    """
    d15           = (raw >> 15) & 0x1
    temp_counts   = (raw >> 3)  & 0xFFF
    open_flag     = (raw >> 2)  & 0x1
    device_id_bit = (raw >> 1)  & 0x1
    temp_c        = temp_counts * 0.25
    temp_f        = temp_c * 9.0 / 5.0 + 32.0

    return {
        "d15"          : d15,
        "temp_counts"  : temp_counts,
        "open_flag"    : open_flag,
        "device_id_bit": device_id_bit,
        "temp_c"       : temp_c,
        "temp_f"       : temp_f,
        "raw_hex"      : f"0x{raw:04X}",
        "raw_bin"      : f"{raw:016b}",
    }


# -------------------------------------------------------------------------
# Detection
# -------------------------------------------------------------------------

def detect_max6675(cs_index):
    """
    Detect and read a MAX6675 on the given CS index.
    Always opens /dev/spidev0.0 with no_cs=True and drives CS via GPIO.
    Returns a result dict.
    """
    dev_node = f"/dev/spidev{SPI_BUS}.0"
    result = {
        "cs_index"       : cs_index,
        "cs_label"       : f"CS{cs_index}",
        "cs_gpio_pin"    : CS_PINS[cs_index],
        "dev_node"       : dev_node,
        "node_exists"    : False,
        "opened"         : False,
        "raw_hex"        : None,
        "raw_bin"        : None,
        "temp_c"         : None,
        "temp_f"         : None,
        "open_flag"      : None,
        "device_found"   : False,
        "thermocouple_ok": False,
        "status"         : "UNKNOWN",
        "notes"          : "",
    }

    if not os.path.exists(dev_node):
        result["status"] = "NO SPI NODE"
        result["notes"]  = (
            f"{dev_node} not found. Enable SPI in raspi-config or "
            "add 'dtparam=spi=on' to /boot/firmware/config.txt and reboot."
        )
        return result

    result["node_exists"] = True

    spi = spidev.SpiDev()
    try:
        spi.open(SPI_BUS, 0)
        result["opened"]  = True
        spi.max_speed_hz  = SPI_SPEED_HZ
        spi.mode          = SPI_MODE
        spi.no_cs         = True   # we drive CS manually via GPIO

        raw    = read_max6675_raw(spi, cs_index)
        parsed = parse_max6675(raw)

        result["raw_hex"]   = parsed["raw_hex"]
        result["raw_bin"]   = parsed["raw_bin"]
        result["open_flag"] = parsed["open_flag"]
        result["temp_c"]    = parsed["temp_c"]
        result["temp_f"]    = parsed["temp_f"]

        if raw == 0xFFFF:
            result["status"] = "NOT DETECTED"
            result["notes"]  = (
                "Response is 0xFFFF -- MISO is floating (all high). "
                "No MAX6675 on this CS line, or MISO needs a pull-up resistor."
            )

        elif raw == 0x0000:
            result["status"] = "NOT DETECTED"
            result["notes"]  = (
                "Response is 0x0000 -- MISO is stuck low. "
                "Check wiring; MISO may be shorted to GND."
            )

        elif parsed["d15"] != 0:
            result["status"] = "INVALID RESPONSE"
            result["notes"]  = (
                f"D15 must always be 0 for MAX6675 but was 1 (raw={parsed['raw_hex']}). "
                "Check wiring or SPI mode settings."
            )

        elif parsed["device_id_bit"] != 0:
            result["status"] = "WRONG DEVICE"
            result["notes"]  = (
                f"D1 (device ID) must be 0 for MAX6675 but was 1 "
                f"(raw={parsed['raw_hex']}). A different device may be connected."
            )

        elif parsed["open_flag"] == 1:
            result["device_found"]    = True
            result["thermocouple_ok"] = False
            result["status"]          = "DETECTED -- THERMOCOUPLE OPEN"
            result["notes"]           = (
                "MAX6675 is responding correctly (D15=0, D1=0), but D2=1 "
                "means the thermocouple is open or not connected. "
                "Check that both K-type thermocouple wires are firmly connected."
            )

        else:
            result["device_found"]    = True
            result["thermocouple_ok"] = True
            result["status"]          = "DETECTED -- OK"
            result["notes"]           = "Valid temperature reading received."

    except PermissionError:
        result["status"] = "PERMISSION DENIED"
        result["notes"]  = (
            f"Permission denied on {dev_node}. "
            "Run with sudo, or: sudo usermod -aG spi $USER  then log out and back in."
        )
    except AttributeError:
        result["status"] = "SPIDEV VERSION ERROR"
        result["notes"]  = (
            "Your spidev version may not support no_cs mode. "
            "Try: pip install --upgrade spidev"
        )
    except OSError as e:
        result["status"] = "OS ERROR"
        result["notes"]  = f"OS error opening {dev_node}: {e}"
    except Exception as e:
        result["status"] = "ERROR"
        result["notes"]  = f"Unexpected error: {e}"
    finally:
        try:
            spi.close()
        except Exception:
            pass

    return result


# -------------------------------------------------------------------------
# Output
# -------------------------------------------------------------------------

def print_result(r):
    print(f"\n  --- {r['cs_label']}  (BCM GPIO {r['cs_gpio_pin']}) ---")
    print(f"  SPI node         : {r['dev_node']}  (exists={r['node_exists']})")
    print(f"  SPI opened       : {r['opened']}")
    if r["raw_hex"]:
        print(f"  Raw hex          : {r['raw_hex']}")
        print(f"  Raw binary       : {r['raw_bin']}")
    if r["open_flag"] is not None:
        tc_str = "open / not connected" if r["open_flag"] else "connected"
        print(f"  Thermocouple     : {tc_str}  (D2={r['open_flag']})")
    if r["thermocouple_ok"]:
        print(f"  Temperature      : {r['temp_c']:.2f} C  /  {r['temp_f']:.2f} F")
    print(f"  Status           : {r['status']}")
    print(f"  Notes            : {r['notes']}")


def main():
    print("=" * 62)
    print("  Industrial Shields TouchBerry Pi 4B -- MAX6675 Detector")
    print("=" * 62)
    print(f"  SPI bus       : {SPI_BUS}  (/dev/spidev0.0)")
    print(f"  SPI speed     : {SPI_SPEED_HZ // 1000} kHz")
    print(f"  SPI mode      : {SPI_MODE}  (CPOL=0 CPHA=0)")
    print(f"  Conv wait     : {CONVERSION_TIME_S * 1000:.0f} ms")
    print(f"  CS control    : manual GPIO (no_cs mode)")
    print(f"  CS0 -> BCM GPIO {CS_PINS[0]}")
    print(f"  CS1 -> BCM GPIO {CS_PINS[1]}")

    gpio_setup()

    try:
        results = [
            detect_max6675(0),
            detect_max6675(1),
        ]
    finally:
        gpio_cleanup()

    for r in results:
        print_result(r)

    print("\n" + "=" * 62)
    print("  Summary")
    print("  -------")
    found = [r for r in results if r["device_found"]]
    if found:
        for r in found:
            tc = "thermocouple OK" if r["thermocouple_ok"] else "thermocouple OPEN"
            print(f"  {r['cs_label']} : MAX6675 found -- {tc}")
            if r["thermocouple_ok"]:
                print(f"       Temperature : {r['temp_c']:.2f} C / {r['temp_f']:.2f} F")
    else:
        print("  No MAX6675 devices detected on CS0 or CS1.")
    print("=" * 62)


if __name__ == "__main__":
    main()
