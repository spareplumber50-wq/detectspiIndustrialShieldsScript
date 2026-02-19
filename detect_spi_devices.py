#!/usr/bin/env python3
"""
MAX6675 K-Type Thermocouple Detection Script
Industrial Shields TouchBerry Pi 4B -- SPI Bus 0, CS0 and CS1

The MAX6675 is a read-only SPI device. It does not respond to data
sent to it; you simply assert CS low, clock out 16 bits, and read MISO.

16-bit response format (MSB first):
  D15     -- dummy sign bit, always 0
  D14-D3  -- 12-bit temperature value (multiply by 0.25 for degrees C)
  D2      -- open thermocouple flag (1 = thermocouple not connected)
  D1      -- device ID, always 0 for MAX6675
  D0      -- state bit (ignore)

Detection logic:
  - If the raw word is 0xFFFF, MISO is floating -- no device present.
  - If D15 is not 0, the response is invalid.
  - If D1 is not 0, this is not a MAX6675.
  - If D2 is 1, the MAX6675 is present but the thermocouple is open/disconnected.
  - Otherwise, a valid temperature reading was obtained.

Requirements:
    pip install spidev

Ensure SPI is enabled on the TouchBerry Pi 4B:
    sudo raspi-config -> Interface Options -> SPI -> Enable
Or add 'dtparam=spi=on' to /boot/firmware/config.txt and reboot.

Run with:
    sudo python3 detect_max6675.py
"""

import spidev
import os
import time

# SPI configuration
SPI_BUS      = 0          # SPI bus 0 (/dev/spidev0.x)
SPI_SPEED_HZ = 1_000_000  # 1 MHz -- well within MAX6675's 4.3 MHz maximum
SPI_MODE     = 0b00        # Mode 0: CPOL=0, CPHA=0

# The MAX6675 needs up to 220 ms to complete a conversion after CS is raised.
# We pulse CS low then high to trigger a conversion, wait, then read.
CONVERSION_WAIT_S = 0.25  # 250 ms to be safe (datasheet max is 220 ms)


def node_exists(bus, device):
    return os.path.exists(f"/dev/spidev{bus}.{device}")


def trigger_conversion(spi):
    """
    Pulse CS low then high to start a new conversion, then wait.
    spidev handles CS automatically on each xfer2 call, so a short dummy
    transfer followed by a delay is enough to get a fresh reading.
    """
    spi.xfer2([0x00, 0x00])  # MAX6675 ignores MOSI; this just pulses CS
    time.sleep(CONVERSION_WAIT_S)


def read_max6675(spi):
    """
    Read 16 bits from the MAX6675 and return the raw integer value.
    MOSI content is irrelevant; the chip only drives MISO.
    """
    raw_bytes = spi.xfer2([0x00, 0x00])
    return (raw_bytes[0] << 8) | raw_bytes[1]


def parse_raw(raw):
    """
    Parse the 16-bit MAX6675 response.
    Returns a dict with all decoded fields.
    """
    d15           = (raw >> 15) & 0x1   # always 0
    temp_raw      = (raw >> 3) & 0xFFF  # 12-bit temperature count
    open_flag     = (raw >> 2) & 0x1    # 1 = thermocouple open
    device_id_bit = (raw >> 1) & 0x1    # always 0 for MAX6675
    temperature_c = temp_raw * 0.25

    return {
        "raw_hex"      : f"0x{raw:04X}",
        "d15_dummy"    : d15,
        "temp_raw"     : temp_raw,
        "open_flag"    : open_flag,
        "device_id_bit": device_id_bit,
        "temperature_c": temperature_c,
        "temperature_f": temperature_c * 9.0 / 5.0 + 32.0,
    }


def detect_max6675(bus, cs):
    """
    Attempt to detect and read a MAX6675 on the given bus and CS line.
    Returns a result dict describing what was found.
    """
    dev_node = f"/dev/spidev{bus}.{cs}"
    result = {
        "bus"            : bus,
        "cs"             : cs,
        "cs_label"       : f"CS{cs}",
        "dev_node"       : dev_node,
        "node_exists"    : False,
        "opened"         : False,
        "raw_hex"        : None,
        "temperature_c"  : None,
        "temperature_f"  : None,
        "open_flag"      : None,
        "device_found"   : False,
        "thermocouple_ok": False,
        "status"         : "UNKNOWN",
        "notes"          : "",
    }

    # Step 1: Check the /dev/spidevX.Y node
    if not node_exists(bus, cs):
        result["status"] = "NO NODE"
        result["notes"]  = (
            f"{dev_node} does not exist. "
            "Enable SPI in raspi-config or /boot/firmware/config.txt and reboot."
        )
        return result

    result["node_exists"] = True

    # Step 2: Open SPI bus
    spi = spidev.SpiDev()
    try:
        spi.open(bus, cs)
        result["opened"]     = True
        spi.max_speed_hz     = SPI_SPEED_HZ
        spi.mode             = SPI_MODE

        # Step 3: Trigger a conversion and wait for it to complete
        trigger_conversion(spi)

        # Step 4: Read 16 bits
        raw    = read_max6675(spi)
        parsed = parse_raw(raw)

        result["raw_hex"]       = parsed["raw_hex"]
        result["open_flag"]     = parsed["open_flag"]
        result["temperature_c"] = parsed["temperature_c"]
        result["temperature_f"] = parsed["temperature_f"]

        # Step 5: Validate the response against the MAX6675 bit definitions
        if raw == 0xFFFF:
            result["status"] = "NOT DETECTED"
            result["notes"]  = (
                "Response was 0xFFFF -- MISO is floating. "
                "No MAX6675 present on this CS line."
            )

        elif parsed["d15_dummy"] != 0:
            result["status"] = "INVALID RESPONSE"
            result["notes"]  = (
                f"D15 must always be 0 but was 1 (raw={parsed['raw_hex']}). "
                "This is not a valid MAX6675 response."
            )

        elif parsed["device_id_bit"] != 0:
            result["status"] = "WRONG DEVICE"
            result["notes"]  = (
                f"D1 device ID bit must be 0 for MAX6675 but was 1 "
                f"(raw={parsed['raw_hex']}). A different SPI device may be connected."
            )

        elif parsed["open_flag"] == 1:
            result["device_found"]    = True
            result["thermocouple_ok"] = False
            result["status"]          = "DETECTED -- THERMOCOUPLE OPEN"
            result["notes"]           = (
                "MAX6675 responded correctly, but D2=1 indicates the thermocouple "
                "is open or not connected. Check thermocouple wiring."
            )

        else:
            result["device_found"]    = True
            result["thermocouple_ok"] = True
            result["status"]          = "DETECTED -- OK"
            result["notes"]           = "MAX6675 responded with a valid temperature reading."

    except PermissionError:
        result["status"] = "PERMISSION DENIED"
        result["notes"]  = (
            f"Permission denied on {dev_node}. "
            "Run with sudo, or add your user to the spi group: "
            "sudo usermod -aG spi $USER"
        )
    except OSError as e:
        result["status"] = "OS ERROR"
        result["notes"]  = f"Failed to open {dev_node}: {e}"
    except Exception as e:
        result["status"] = "ERROR"
        result["notes"]  = f"Unexpected error: {e}"
    finally:
        try:
            spi.close()
        except Exception:
            pass

    return result


def print_result(r):
    print(f"\n  --- SPI Bus {r['bus']}, {r['cs_label']} ({r['dev_node']}) ---")
    print(f"  Node exists      : {r['node_exists']}")
    print(f"  SPI opened       : {r['opened']}")
    if r["raw_hex"]:
        print(f"  Raw response     : {r['raw_hex']}")
    if r["open_flag"] is not None:
        tc_str = "open/disconnected" if r["open_flag"] else "connected"
        print(f"  Open TC flag     : {r['open_flag']}  ({tc_str})")
    if r["thermocouple_ok"]:
        print(f"  Temperature      : {r['temperature_c']:.2f} C  /  {r['temperature_f']:.2f} F")
    print(f"  Status           : {r['status']}")
    print(f"  Notes            : {r['notes']}")


def main():
    print("=" * 60)
    print("  Industrial Shields TouchBerry Pi 4B -- MAX6675 Detector")
    print("=" * 60)
    print(f"  SPI Bus      : {SPI_BUS}")
    print(f"  Speed        : {SPI_SPEED_HZ // 1000} kHz")
    print(f"  Mode         : {SPI_MODE}")
    print(f"  Conv wait    : {CONVERSION_WAIT_S * 1000:.0f} ms")
    print(f"  Checking CS0 : /dev/spidev0.0")
    print(f"  Checking CS1 : /dev/spidev0.1")

    results = [
        detect_max6675(SPI_BUS, 0),
        detect_max6675(SPI_BUS, 1),
    ]

    for r in results:
        print_result(r)

    print("\n" + "=" * 60)
    print("  Summary")
    print("  -------")
    detected = [r for r in results if r["device_found"]]
    if detected:
        for r in detected:
            tc_status = "thermocouple OK" if r["thermocouple_ok"] else "thermocouple OPEN"
            print(f"  {r['cs_label']} : MAX6675 found -- {tc_status}")
            if r["thermocouple_ok"]:
                print(f"         Temperature : {r['temperature_c']:.2f} C / {r['temperature_f']:.2f} F")
    else:
        print("  No MAX6675 devices detected on CS0 or CS1.")
    print("=" * 60)


if __name__ == "__main__":
    main()
