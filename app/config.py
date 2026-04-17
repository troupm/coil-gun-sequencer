import os

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


# --- Default user-configurable parameters ---

DEFAULTS = {
    "projectile_length_mm": 10.0,
    "projectile_mass_grams": 2.08,
    "v_coil_floor": 2.0,
    "v_coil_ceiling": 12.0,
    "gate_1_coil_2_delay_us": 500.0,
    "coil_1_pulse_duration_us": 1500.0,
    "gate_2_coil_3_delay_us": 2000.0,
    "coil_2_pulse_duration_us": 1200.0,
    "coil_3_pulse_duration_us": 1000.0,
    "gate_1_to_gate_2_distance_mm": 100.0,
    "gate_2_to_gate_3_distance_mm": 100.0,
    # Power-source parameters. Metadata only — not read by the firing path;
    # tracked per-snapshot so the velocity analysis can correlate them.
    "capacitor_bank_size_uf": 1000.0,  # 1000 µF = smallest available module,
                                       # used as the fallback for unset/empty
    "rail_source_active": 0.0,         # 0 = rail off; >0 = rail on at that
                                       # voltage (the UI fills this from
                                       # v_coil_ceiling when the box is checked
                                       # so ML tools get a continuous feature
                                       # instead of a 0/1 indicator)
    # Flyback module brake-resistor values (ohms). Metadata only — the
    # firing path doesn't read them. Each coil has a dedicated
    # `SiC flyback diode + brake resistor` module; swapping the module
    # trades V_CE spike magnitude for freewheel decay speed (sharper
    # turn-off reduces projectile suck-back). Defaults reflect the
    # resistors currently installed on the rig.
    "coil_1_brake_resistor_ohms": 10.0,
    "coil_2_brake_resistor_ohms": 1.0,
    # Per-coil capacitor banks (metadata only — not read by firing path).
    # Each coil stage has its own dedicated bank. rail_source_active
    # indicates the transition: False = one shared bank, True = dedicated.
    "coil_1_capacitor_uf": 4000.0,
    "coil_2_capacitor_uf": 4000.0,
    "coil_3_capacitor_uf": 4000.0,
    # Projectile starting position: how far the projectile tip protrudes
    # from the muzzle-facing end of Coil 1 at launch (mm). Metadata only.
    "projectile_start_offset_mm": 2.0,
    # Coil electrical ratings (metadata only — not read by firing path).
    # DC resistance (ohms) and air-core inductance (µH) per coil stage.
    # L/R time-constant governs current rise and peak field strength.
    "coil_1_resistance_ohms": 1.3,
    "coil_1_inductance_uh": 476.0,
    "coil_2_resistance_ohms": 2.8,
    "coil_2_inductance_uh": 1900.0,
    "coil_3_resistance_ohms": 5.0,
    "coil_3_inductance_uh": 1000.0,
}


# --- GPIO pin assignments (BCM numbering) ---

GPIO_COIL_1 = 17
GPIO_COIL_2 = 27
GPIO_COIL_3 = 22

GPIO_MONITOR_1 = 5
GPIO_MONITOR_2 = 6
GPIO_MONITOR_3 = 13

GPIO_GATE_1 = 23
GPIO_GATE_2 = 24
GPIO_GATE_3 = 25

EXTERNAL_TRIGGER_PIN = 26


# --- ADC configuration (placeholder until hardware is purchased) ---

ADC_TYPE = os.environ.get("ADC_TYPE", "stub")  # "mcp3008", "ads1115", or "stub"
ADC_CHANNELS = {1: 0, 2: 1, 3: 2}  # coil_num -> ADC channel


# --- Flask configuration ---

class FlaskConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "coilgun-dev-key")
    SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + os.path.join(BASE_DIR, "data", "sequencer.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
