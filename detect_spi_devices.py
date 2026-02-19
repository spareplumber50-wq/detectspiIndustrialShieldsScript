#!/usr/bin/env python3
"""
SPI Device Detection Script for Industrial Shields TouchBerry Pi 4B
Detects devices on SPI bus 0, CS0 (/dev/spidev0.0) and CS1 (/dev/spidev0.1)

Requirements:
    pip install spidev

Make sure SPI is enabled:
    sudo raspi-config -> Interface Options -> SPI -> Enable
Or add 'dtparam=spi=on' to /boot/firmware/config.txt and reboot.
"""

import spidev
import os

# SPI configuration
SPI_BUS = 0          # SPI bus 0 (the standard SPI bus on the TouchBerry Pi 4B)
SPI_SPEED_HZ = 1_000_000  # 1 MHz - a safe default speed for detection
SPI_MODE = 0         # SPI mode 0 (CPOL=0, CPHA=0) - most common default

# Test payload: a simple non-zero byte pattern to check for a valid response
TEST_DATA = [0xAA, 0x55, 0xFF]


def check_spidev_node(bus, device):
    """Check if the /dev/spidevX.Y device node exists."""
    path = f"/dev/spidev{bus}.{device}"
    return os.path.exists(path), path


def detect_spi_device(bus, device):
    """
    Attempt to open and communicate over SPI to detect a device.

    A device is considered 'detected' if:
      - The /dev/spidevX.Y node exists
      - The SPI bus opens without error
      - The response bytes differ from the transmitted bytes OR
        are not all 0x00 or 0xFF (floating/no device typical responses)

    Returns a dict with detection results.
    """
    result = {
        "bus": bus,
        "device": device,
        "cs_label": f"CS{device}",
        "dev_node": f"/dev/spidev{bus}.{device}",
        "node_exists": False,
        "opened": False,
        "response": None,
        "device_detected": False,
        "notes": ""
    }

    # Step 1: Check if the device node exists
    node_exists, path = check_spidev_node(bus, device)
    result["node_exists"] = node_exists

    if not node_exists:
        result["notes"] = (
            f"Device node {path} not found. "
            "Ensure SPI is enabled and the CS line is configured."
        )
        return result

    # Step 2: Try to open and communicate
    spi = spidev.SpiDev()
    try:
        spi.open(bus, device)
        result["opened"] = True

        spi.max_speed_hz = SPI_SPEED_HZ
        spi.mode = SPI_MODE

        # Transfer test bytes and capture response
        response = spi.xfer2(TEST_DATA)
        result["response"] = response

        # Heuristic: if response is not all 0x00 or all 0xFF, a device likely responded
        all_zero = all(b == 0x00 for b in response)
        all_high = all(b == 0xFF for b in response)

        if not all_zero and not all_high:
            result["device_detected"] = True
            result["notes"] = "Non-trivial response received — device likely present."
        else:
            result["notes"] = (
                f"Response was {'all 0x00' if all_zero else 'all 0xFF'} — "
                "bus is accessible but no active device detected (MISO may be floating)."
            )

    except PermissionError:
        result["notes"] = (
            f"Permission denied on {path}. Try running with sudo, "
            "or add your user to the 'spi' group: sudo usermod -aG spi $USER"
        )
    except OSError as e:
        result["notes"] = f"OS error opening {path}: {e}"
    except Exception as e:
        result["notes"] = f"Unexpected error: {e}"
    finally:
        try:
            spi.close()
        except Exception:
            pass

    return result


def print_result(result):
    status = "DETECTED" if result["device_detected"] else "NOT DETECTED"
    print(f"\n--- SPI Bus {result['bus']}, {result['cs_label']} ({result['dev_node']}) ---")
    print(f"  Device node exists : {result['node_exists']}")
    print(f"  SPI opened         : {result['opened']}")
    if result["response"] is not None:
        hex_response = [f"0x{b:02X}" for b in result["response"]]
        print(f"  Response bytes     : {hex_response}")
    print(f"  Status             : {status}")
    print(f"  Notes              : {result['notes']}")


def main():
    print("=" * 55)
    print("  Industrial Shields TouchBerry Pi 4B -- SPI Detector")
    print("=" * 55)
    print(f"  SPI Bus    : {SPI_BUS}")
    print(f"  Speed      : {SPI_SPEED_HZ // 1000} kHz")
    print(f"  Mode       : {SPI_MODE}")
    print(f"  Test data  : {[f'0x{b:02X}' for b in TEST_DATA]}")

    cs0_result = detect_spi_device(SPI_BUS, 0)
    cs1_result = detect_spi_device(SPI_BUS, 1)

    print_result(cs0_result)
    print_result(cs1_result)

    print("\n" + "=" * 55)
    detected = [r for r in [cs0_result, cs1_result] if r["device_detected"]]
    if detected:
        labels = ", ".join(r["cs_label"] for r in detected)
        print(f"  Summary: Device(s) detected on {labels}")
    else:
        print("  Summary: No active SPI devices detected.")
    print("=" * 55)


if __name__ == "__main__":
    main()
