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
