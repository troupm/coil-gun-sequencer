"""Mock hardware backend for development / testing on non-RPi platforms.

When a coil fires, the mock simulates a projectile traversing the next gate
after a short delay so the full firing sequence can be exercised without
physical hardware.
"""

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from app.hardware.base import HardwareInterface

log = logging.getLogger(__name__)

# Simulated flight characteristics
_SIM_COIL_TO_GATE_DELAY_S = 0.003   # 3 ms from coil fire to next gate trigger
_SIM_GATE_TRANSIT_S = 0.0005         # 500 µs beam-break duration


class MockHardware(HardwareInterface):
    """In-memory mock that simulates gate events when coils fire."""

    def __init__(self) -> None:
        self._coil_states: Dict[int, bool] = {1: False, 2: False, 3: False}
        self._gate_callbacks: Dict[Tuple[int, str], List[Callable]] = {}
        self._trigger_callback: Optional[Callable] = None
        self._sim_timers: List[threading.Timer] = []
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------

    def setup(self) -> None:
        log.info("[MockHW] setup complete (no real hardware)")

    def cleanup(self) -> None:
        with self._lock:
            for t in self._sim_timers:
                t.cancel()
            self._sim_timers.clear()
        log.info("[MockHW] cleanup complete")

    # -- coil outputs -----------------------------------------------------

    def set_coil(self, coil_num: int, state: bool) -> None:
        self._coil_states[coil_num] = state
        action = "ON" if state else "OFF"
        log.info(f"[MockHW] Coil {coil_num} -> {action}")

        # When a coil energises, schedule the simulated gate event for the
        # gate that sits AFTER that coil in the physical layout:
        #   coil_1 -> gate_1,  coil_2 -> gate_2,  coil_3 -> gate_3
        if state:
            gate_num = coil_num  # 1:1 mapping
            self._schedule_simulated_gate(gate_num)

    def _schedule_simulated_gate(self, gate_num: int) -> None:
        """Simulate a projectile breaking the beam at *gate_num*."""

        def _trigger_leading():
            log.info(f"[MockHW] Simulated gate {gate_num} LEADING edge (beam break)")
            cbs = self._gate_callbacks.get((gate_num, "falling"), [])
            for cb in cbs:
                cb()
            # Schedule trailing edge after transit time
            t2 = threading.Timer(_SIM_GATE_TRANSIT_S, _trigger_trailing)
            t2.daemon = True
            with self._lock:
                self._sim_timers.append(t2)
            t2.start()

        def _trigger_trailing():
            log.info(f"[MockHW] Simulated gate {gate_num} TRAILING edge (beam restore)")
            cbs = self._gate_callbacks.get((gate_num, "rising"), [])
            for cb in cbs:
                cb()

        t1 = threading.Timer(_SIM_COIL_TO_GATE_DELAY_S, _trigger_leading)
        t1.daemon = True
        with self._lock:
            self._sim_timers.append(t1)
        t1.start()

    # -- gate inputs ------------------------------------------------------

    def register_gate_callback(
        self,
        gate_num: int,
        edge: str,
        callback: Callable[[], None],
    ) -> None:
        key = (gate_num, edge)
        self._gate_callbacks.setdefault(key, []).append(callback)
        log.debug(f"[MockHW] Registered gate {gate_num} {edge} callback")

    def unregister_gate_callbacks(self) -> None:
        self._gate_callbacks.clear()
        log.debug("[MockHW] All gate callbacks removed")

    # -- external trigger -------------------------------------------------

    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        self._trigger_callback = callback
        log.debug("[MockHW] External trigger callback registered")

    def unregister_trigger_callback(self) -> None:
        self._trigger_callback = None
        log.debug("[MockHW] External trigger callback removed")

    # -- voltage monitoring -----------------------------------------------

    def read_coil_voltage(self, coil_num: int) -> Optional[float]:
        # Simulate fully-charged capacitors
        return 12.0

    # -- mock-only helpers for manual testing via API ---------------------

    def simulate_trigger_press(self) -> None:
        """Programmatically fire the external trigger (for dev/test use)."""
        if self._trigger_callback:
            log.info("[MockHW] Simulated external trigger press")
            threading.Thread(
                target=self._trigger_callback, daemon=True
            ).start()

    def simulate_gate_break(self, gate_num: int) -> None:
        """Programmatically trigger a gate beam-break (for dev/test use)."""
        self._schedule_simulated_gate(gate_num)
