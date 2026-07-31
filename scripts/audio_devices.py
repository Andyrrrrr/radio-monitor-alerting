#!/usr/bin/env python
"""List audio input devices, for filling in [audio].device in config.toml.

Run this first during hardware bring-up (docs/hardware.md §5): the device
name printed here is what the config's substring match runs against.

    python scripts/audio_devices.py
"""

import sounddevice as sd


def main() -> None:
    default_input = sd.default.device[0]
    found = False
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        found = True
        marker = "  <- system default" if index == default_input else ""
        print(
            f"[{index}] {dev['name']}  "
            f"(inputs: {dev['max_input_channels']}, "
            f"default rate: {dev['default_samplerate']:.0f} Hz)"
            f"{marker}"
        )
    if not found:
        print("no input devices found — is the interface plugged in and powered?")


if __name__ == "__main__":
    main()
