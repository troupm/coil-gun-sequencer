from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ConfigSnapshot(db.Model):
    __tablename__ = "config_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_sequence_id = db.Column(db.String(36), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Projectile
    projectile_length_mm = db.Column(db.Float, nullable=False)
    projectile_mass_grams = db.Column(db.Float, nullable=False)

    # Voltage thresholds
    v_coil_floor = db.Column(db.Float, nullable=False)
    v_coil_ceiling = db.Column(db.Float, nullable=False)

    # Timing – gate-to-coil delays
    gate_1_coil_2_delay_us = db.Column(db.Float, nullable=False)
    gate_2_coil_3_delay_us = db.Column(db.Float, nullable=False)

    # Timing – coil pulse durations
    coil_1_pulse_duration_us = db.Column(db.Float, nullable=False)
    coil_2_pulse_duration_us = db.Column(db.Float, nullable=False)
    coil_3_pulse_duration_us = db.Column(db.Float, nullable=False)

    # Physical distances
    gate_1_to_gate_2_distance_mm = db.Column(db.Float, nullable=False)
    gate_2_to_gate_3_distance_mm = db.Column(db.Float, nullable=False)

    # Power source (metadata for analysis; not read by the firing path).
    # rail_source_active is a continuous feature, not a boolean: stores the
    # effective rail voltage (= v_coil_ceiling when on, 0 when off) so ML
    # regressors get a meaningful magnitude instead of a 0/1 indicator.
    capacitor_bank_size_uf = db.Column(db.Float, nullable=False, default=1000.0)
    rail_source_active = db.Column(db.Float, nullable=False, default=0.0)

    # Flyback module brake resistors (ohms). Metadata only — not read by
    # the firing path. One dedicated SiC flyback+brake module per coil;
    # defaults reflect the currently-installed hardware. Logged per
    # snapshot so the velocity analyzer can correlate it with outcomes.
    coil_1_brake_resistor_ohms = db.Column(db.Float, nullable=False, default=10.0)
    coil_2_brake_resistor_ohms = db.Column(db.Float, nullable=False, default=1.0)

    # Coil electrical ratings (metadata only — not read by firing path).
    # DC resistance (ohms) and air-core inductance (µH) per coil stage.
    # These are statistically significant for velocity optimisation:
    # L/R time-constant determines current rise and peak field strength.
    coil_1_resistance_ohms = db.Column(db.Float, nullable=False, default=1.3)
    coil_1_inductance_uh = db.Column(db.Float, nullable=False, default=476.0)
    coil_2_resistance_ohms = db.Column(db.Float, nullable=False, default=2.8)
    coil_2_inductance_uh = db.Column(db.Float, nullable=False, default=1900.0)
    coil_3_resistance_ohms = db.Column(db.Float, nullable=False, default=5.0)
    coil_3_inductance_uh = db.Column(db.Float, nullable=False, default=1000.0)

    def to_dict(self):
        return {
            "id": self.id,
            "run_sequence_id": self.run_sequence_id,
            "created_at": self.created_at.isoformat(),
            "projectile_length_mm": self.projectile_length_mm,
            "projectile_mass_grams": self.projectile_mass_grams,
            "v_coil_floor": self.v_coil_floor,
            "v_coil_ceiling": self.v_coil_ceiling,
            "gate_1_coil_2_delay_us": self.gate_1_coil_2_delay_us,
            "gate_2_coil_3_delay_us": self.gate_2_coil_3_delay_us,
            "coil_1_pulse_duration_us": self.coil_1_pulse_duration_us,
            "coil_2_pulse_duration_us": self.coil_2_pulse_duration_us,
            "coil_3_pulse_duration_us": self.coil_3_pulse_duration_us,
            "gate_1_to_gate_2_distance_mm": self.gate_1_to_gate_2_distance_mm,
            "gate_2_to_gate_3_distance_mm": self.gate_2_to_gate_3_distance_mm,
            "capacitor_bank_size_uf": self.capacitor_bank_size_uf,
            "rail_source_active": self.rail_source_active,
            "coil_1_brake_resistor_ohms": self.coil_1_brake_resistor_ohms,
            "coil_2_brake_resistor_ohms": self.coil_2_brake_resistor_ohms,
            "coil_1_resistance_ohms": self.coil_1_resistance_ohms,
            "coil_1_inductance_uh": self.coil_1_inductance_uh,
            "coil_2_resistance_ohms": self.coil_2_resistance_ohms,
            "coil_2_inductance_uh": self.coil_2_inductance_uh,
            "coil_3_resistance_ohms": self.coil_3_resistance_ohms,
            "coil_3_inductance_uh": self.coil_3_inductance_uh,
        }

    # Parameter keys that map 1:1 to column names. All are numeric (float)
    # for ML friendliness — rail_source_active looks boolean in the UI but
    # is stored as a continuous voltage value.
    PARAM_KEYS = [
        "projectile_length_mm",
        "projectile_mass_grams",
        "v_coil_floor",
        "v_coil_ceiling",
        "gate_1_coil_2_delay_us",
        "gate_2_coil_3_delay_us",
        "coil_1_pulse_duration_us",
        "coil_2_pulse_duration_us",
        "coil_3_pulse_duration_us",
        "gate_1_to_gate_2_distance_mm",
        "gate_2_to_gate_3_distance_mm",
        "capacitor_bank_size_uf",
        "rail_source_active",
        "coil_1_brake_resistor_ohms",
        "coil_2_brake_resistor_ohms",
        "coil_1_resistance_ohms",
        "coil_1_inductance_uh",
        "coil_2_resistance_ohms",
        "coil_2_inductance_uh",
        "coil_3_resistance_ohms",
        "coil_3_inductance_uh",
    ]


class EventLog(db.Model):
    __tablename__ = "event_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_sequence_id = db.Column(db.String(36), nullable=False, index=True)
    run_number = db.Column(db.Integer, nullable=False)
    config_snapshot_id = db.Column(
        db.Integer, db.ForeignKey("config_snapshots.id"), nullable=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # All timestamps are nanoseconds from time.perf_counter_ns().
    # They are relative to session start, not wall-clock.  Store as Integer.
    t_coil_0 = db.Column(db.BigInteger, nullable=True)

    t_gate_1_on = db.Column(db.BigInteger, nullable=True)
    t_gate_1_off = db.Column(db.BigInteger, nullable=True)
    t_gate_2_on = db.Column(db.BigInteger, nullable=True)
    t_gate_2_off = db.Column(db.BigInteger, nullable=True)
    t_gate_3_on = db.Column(db.BigInteger, nullable=True)
    t_gate_3_off = db.Column(db.BigInteger, nullable=True)

    t_coil_1_on = db.Column(db.BigInteger, nullable=True)
    t_coil_1_off = db.Column(db.BigInteger, nullable=True)
    t_coil_2_on = db.Column(db.BigInteger, nullable=True)
    t_coil_2_off = db.Column(db.BigInteger, nullable=True)
    t_coil_3_on = db.Column(db.BigInteger, nullable=True)
    t_coil_3_off = db.Column(db.BigInteger, nullable=True)

    TIMESTAMP_FIELDS = [
        "t_coil_0",
        "t_gate_1_on", "t_gate_1_off",
        "t_gate_2_on", "t_gate_2_off",
        "t_gate_3_on", "t_gate_3_off",
        "t_coil_1_on", "t_coil_1_off",
        "t_coil_2_on", "t_coil_2_off",
        "t_coil_3_on", "t_coil_3_off",
    ]

    def to_dict(self):
        d = {
            "id": self.id,
            "run_sequence_id": self.run_sequence_id,
            "run_number": self.run_number,
            "config_snapshot_id": self.config_snapshot_id,
            "created_at": self.created_at.isoformat(),
        }
        for f in self.TIMESTAMP_FIELDS:
            d[f] = getattr(self, f)
        return d
