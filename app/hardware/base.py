"""Abstract hardware interface for the coil-gun sequencer.

Concrete implementations:
  - MockHardware  (development on any platform)
  - RealHardware  (Raspberry Pi with gpiozero + ADC)
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional


class HardwareInterface(ABC):
    """Platform-independent contract for GPIO + ADC access."""

    # -- lifecycle --------------------------------------------------------

    @abstractmethod
    def setup(self) -> None:
        """Initialise pins / ADC.  Called once at startup."""

    @abstractmethod
    def cleanup(self) -> None:
        """Release all hardware resources."""

    # -- coil outputs -----------------------------------------------------

    @abstractmethod
    def set_coil(self, coil_num: int, state: bool) -> None:
        """Drive a coil output HIGH (True) or LOW (False).

        coil_num: 1, 2, or 3
        """

    # -- gate inputs ------------------------------------------------------

    @abstractmethod
    def register_gate_callback(
        self,
        gate_num: int,
        edge: str,
        callback: Callable[[], None],
    ) -> None:
        """Register *callback* for a gate edge event.

        gate_num : 1, 2, or 3
        edge     : "rising" (beam break / leading) or "falling" (beam restore / trailing)
        callback : zero-arg callable invoked from a background thread
        """

    @abstractmethod
    def unregister_gate_callbacks(self) -> None:
        """Remove all previously registered gate callbacks."""

    @abstractmethod
    def read_gate_state(self, gate_num: int) -> Optional[bool]:
        """Return the current logical line state of *gate_num*.

        True  = line HIGH at the GPIO pin
        False = line LOW
        None  = state unknown / hardware unavailable

        Used by the Manual page for pre-flight calibration; sample rate is
        whatever the UI polls at, not the timing-critical edge path.
        """

    # -- external trigger -------------------------------------------------

    @abstractmethod
    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        """Register *callback* for the external fire-trigger button press (rising edge)."""

    @abstractmethod
    def unregister_trigger_callback(self) -> None:
        """Remove the external-trigger callback."""

    # -- voltage monitoring (ADC) -----------------------------------------

    @abstractmethod
    def read_coil_voltage(self, coil_num: int) -> Optional[float]:
        """Return the coil-capacitor voltage in volts, or None if ADC unavailable.

        coil_num: 1, 2, or 3
        """
