"""Hardware abstraction – auto-selects mock or real backend."""

import os
import sys

from app.hardware.base import HardwareInterface


def create_hardware() -> HardwareInterface:
    """Return the appropriate hardware backend.

    Selection logic (in priority order):
      1. COILGUN_HW env-var  ("mock" | "real")
      2. Auto-detect: use real gpiozero on Linux/RPi, mock everywhere else
    """
    choice = os.environ.get("COILGUN_HW", "").lower()

    if choice == "real":
        from app.hardware.real import RealHardware
        return RealHardware()

    if choice == "mock":
        from app.hardware.mock import MockHardware
        return MockHardware()

    # Auto-detect
    if sys.platform.startswith("linux"):
        try:
            from app.hardware.real import RealHardware
            return RealHardware()
        except ImportError:
            pass

    from app.hardware.mock import MockHardware
    return MockHardware()


__all__ = ["HardwareInterface", "create_hardware"]
