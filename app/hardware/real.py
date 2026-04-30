"""Real hardware backend using gpiozero on the Raspberry Pi.

Gate sensors  : Normally HIGH, active LOW — projectile breaking the beam pulls
                the line LOW (falling edge = leading edge / beam break).
                Beam restore returns to HIGH (rising edge = trailing).
External trigger: Pull-down, active HIGH (rising edge = press)
Coil outputs  : Active HIGH to energise
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from app.hardware.base import HardwareInterface
from app import config as cfg

log = logging.getLogger(__name__)

# Imports deferred to setup() so the module can be imported (but not used) on
# non-RPi platforms without crashing at import time.
_gpiozero = None


def _ensure_gpiozero():
    global _gpiozero
    if _gpiozero is None:
        import gpiozero
        _gpiozero = gpiozero


class RealHardware(HardwareInterface):
    def __init__(self) -> None:
        self._coils: Dict[int, object] = {}
        self._gates: Dict[int, object] = {}
        self._trigger: Optional[object] = None
        self._gate_callbacks: Dict[Tuple[int, str], List[Callable]] = {}
        self._trigger_callback: Optional[Callable] = None

    # -- lifecycle --------------------------------------------------------

    def setup(self) -> None:
        _ensure_gpiozero()
        OD = _gpiozero.OutputDevice
        DID = _gpiozero.DigitalInputDevice
        Button = _gpiozero.Button

        self._coils = {
            1: OD(cfg.GPIO_COIL_1, initial_value=False),
            2: OD(cfg.GPIO_COIL_2, initial_value=False),
            3: OD(cfg.GPIO_COIL_3, initial_value=False),
        }

        # Gates: normally HIGH, active LOW. pull_up=True keeps the input HIGH
        # when idle and matches the sensor's normal state. With gpiozero's
        # default active_state for pull_up=True:
        #   when_activated   = line went LOW  → falling edge → beam break
        #   when_deactivated = line went HIGH → rising edge → beam restore
        self._gates = {
            1: DID(cfg.GPIO_GATE_1, pull_up=True, bounce_time=None),
            2: DID(cfg.GPIO_GATE_2, pull_up=True, bounce_time=None),
            3: DID(cfg.GPIO_GATE_3, pull_up=True, bounce_time=None),
        }

        # External trigger: pull-down, active HIGH
        self._trigger = Button(
            cfg.EXTERNAL_TRIGGER_PIN, pull_up=False, bounce_time=0.01
        )

        log.info("[RealHW] setup complete – GPIO pins initialised")

    def cleanup(self) -> None:
        for coil in self._coils.values():
            coil.off()
        for coil in self._coils.values():
            coil.close()
        for gate in self._gates.values():
            gate.close()
        if self._trigger:
            self._trigger.close()
        log.info("[RealHW] cleanup – all pins released")

    # -- coil outputs -----------------------------------------------------

    def set_coil(self, coil_num: int, state: bool) -> None:
        coil = self._coils[coil_num]
        if state:
            coil.on()
        else:
            coil.off()

    # -- gate inputs ------------------------------------------------------

    def register_gate_callback(
        self,
        gate_num: int,
        edge: str,
        callback: Callable[[], None],
    ) -> None:
        gate = self._gates[gate_num]
        key = (gate_num, edge)
        self._gate_callbacks.setdefault(key, []).append(callback)

        # Wire up gpiozero callbacks.
        # DigitalInputDevice with pull_up=True (normally-HIGH sensors):
        #   when_activated   = falling edge (line went LOW = beam break)
        #   when_deactivated = rising edge (line went HIGH = beam restore)
        # The `edge` argument names the *physical* edge direction on the
        # wire; the sequencer decides which physical edge maps to
        # leading/trailing (see sequencer._register_gate_callbacks).
        if edge == "falling":
            existing = gate.when_activated

            def _on_activated():
                if existing:
                    existing()
                for cb in self._gate_callbacks.get(key, []):
                    cb()

            gate.when_activated = _on_activated
        elif edge == "rising":
            existing = gate.when_deactivated

            def _on_deactivated():
                if existing:
                    existing()
                for cb in self._gate_callbacks.get(key, []):
                    cb()

            gate.when_deactivated = _on_deactivated

    def unregister_gate_callbacks(self) -> None:
        for gate in self._gates.values():
            gate.when_activated = None
            gate.when_deactivated = None
        self._gate_callbacks.clear()

    # -- external trigger -------------------------------------------------

    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        self._trigger_callback = callback
        self._trigger.when_pressed = callback

    def unregister_trigger_callback(self) -> None:
        self._trigger_callback = None
        if self._trigger:
            self._trigger.when_pressed = None

    # -- voltage monitoring (ADC) -----------------------------------------

    def read_coil_voltage(self, coil_num: int) -> Optional[float]:
        # ADC not yet purchased – return None to signal "unavailable"
        return None
