##  Device Configuration & Logging

### User Configurable Parameters

These are configurable in the Configuration UI Page. All values are persisted
per-run in the `config_snapshots` table so the velocity analyzer can correlate
any of them with outcomes. Defaults live in `app/config.py::DEFAULTS` and the
column list lives in `app/models.py::ConfigSnapshot.PARAM_KEYS` — if those
two and this table drift, the code is authoritative (per
`.claude/lessons-learned.md`).

Coil 1 fires immediately on the `fire()` command (no gate involvement). Gate 1
fires **coil 2** after `GATE_1_COIL_2_DELAY_US`; gate 2 fires **coil 3** after
`GATE_2_COIL_3_DELAY_US`. Coil 3 and gate 3 are not physically attached yet;
their config rows still exist but their values have no effect on the rig.

#### Projectile

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `PROJECTILE_LENGTH_MM` | `float` | `10.0` | Projectile length (mm). Used for transit-velocity calculations. |
| `PROJECTILE_MASS_GRAMS` | `float` | `2.08` | Projectile mass (g). Metadata for kinetic-energy calculations. |

#### Voltage thresholds

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `V_COIL_FLOOR` | `float` | `2.0` | Minimum active voltage ("depleted" cap bank). Metadata for the UI Ready indicator; ADC not yet wired. |
| `V_COIL_CEILING` | `float` | `12.0` | Maximum active voltage ("fully charged"). Same note as V_COIL_FLOOR. Also feeds `RAIL_SOURCE_ACTIVE` when the rail is enabled. |

#### Firing timing

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `GATE_1_COIL_2_DELAY_US` | `float` | `500.0` | Delay (µs) after gate 1 leading edge before firing **coil 2**. |
| `GATE_2_COIL_3_DELAY_US` | `float` | `2000.0` | Delay (µs) after gate 2 leading edge before firing **coil 3**. |
| `COIL_1_PULSE_DURATION_US` | `float` | `1500.0` | Coil 1 pulse width (µs). Coil 1 fires immediately on the fire command. |
| `COIL_2_PULSE_DURATION_US` | `float` | `1200.0` | Coil 2 pulse width (µs). |
| `COIL_3_PULSE_DURATION_US` | `float` | `1000.0` | Coil 3 pulse width (µs). Not in original spec — added when coil 3 hardware was planned. |

#### Gate distances

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `GATE_1_TO_GATE_2_DISTANCE_MM` | `float` | `100.0` | Physical distance (mm) between gates 1 and 2. Used for G1→G2 flight-velocity calculations. |
| `GATE_2_TO_GATE_3_DISTANCE_MM` | `float` | `100.0` | Physical distance (mm) between gates 2 and 3. Used for muzzle-velocity (G2→G3) calculations. |

#### Power source (metadata only — not read by firing path)

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `CAPACITOR_BANK_SIZE_UF` | `float` | `1000.0` | Installed capacitor bank size (µF). Logged per-snapshot so velocity analysis can correlate it. |
| `RAIL_SOURCE_ACTIVE` | `float` | `0.0` | Effective rail voltage: `0.0` when the rail supply is off, `V_COIL_CEILING` when on. Stored as a continuous voltage (not boolean) so ML tooling gets a meaningful magnitude. The UI exposes it as a checkbox and fills the voltage on save. |

#### Flyback / brake modules (metadata only — not read by firing path)

One dedicated `SiC flyback diode + brake resistor` module per coil. Swapping the
module trades V_CE spike magnitude at switch turn-off for a harder clamp on the
freewheel current, reducing projectile "suck-back" from the collapsing field.
Available discrete modules: `{0, 1, 2, 4, 10} Ω`. The defaults reflect the
resistors currently installed on the test rig.

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `COIL_1_BRAKE_RESISTOR_OHMS` | `float` | `10.0` | Series brake resistor value (Ω) in coil 1's flyback module. |
| `COIL_2_BRAKE_RESISTOR_OHMS` | `float` | `1.0` | Series brake resistor value (Ω) in coil 2's flyback module. |

#### Coil electrical ratings (metadata only — not read by firing path)

DC resistance and air-core inductance per coil stage. The L/R time-constant
determines current rise-rate and peak magnetic field strength, both of which
directly affect projectile velocity. Logged per-snapshot so the velocity
analyser can correlate coil parameters with outcomes.

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `COIL_1_RESISTANCE_OHMS` | `float` | `1.3` | DC winding resistance (Ω) of coil 1. |
| `COIL_1_INDUCTANCE_UH` | `float` | `476.0` | Air-core inductance (µH) of coil 1. |
| `COIL_2_RESISTANCE_OHMS` | `float` | `2.8` | DC winding resistance (Ω) of coil 2. |
| `COIL_2_INDUCTANCE_UH` | `float` | `1900.0` | Air-core inductance (µH) of coil 2. |
| `COIL_3_RESISTANCE_OHMS` | `float` | `5.0` | DC winding resistance (Ω) of coil 3. |
| `COIL_3_INDUCTANCE_UH` | `float` | `1000.0` | Air-core inductance (µH) of coil 3. |

### Database Schema
| Table | Description |
|-------|-------------|
| `config_snapshots` | Configuration values and constants |
| `event_logs` | Discrete events (coil activations, gate triggers, etc.) |

### Events to be logged

| Field | Type | Description |
|-------|------|-------------|
| `t_coil_0` | `Optional[int]` | Timestamp of +5v signal to energize first coil |
| `t_gate_1_on` | `Optional[int]` | Rising edge (0 -> +5v) of first beam-break gate |
| `t_gate_1_off` | `Optional[int]` | Trailing edge (+5v -> 0) of first beam-break gate |
| `t_gate_2_on` | `Optional[int]` | Rising edge (0 -> +5v) of second beam-break gate |
| `t_gate_2_off` | `Optional[int]` | Trailing edge (+5v -> 0) of second beam-break gate |
| `t_gate_3_on` | `Optional[int]` | Rising edge (0 -> +5v) of third beam-break gate |
| `t_gate_3_off` | `Optional[int]` | Trailing edge (+5v -> 0) of third beam-break gate |
| `t_coil_1_on` | `Optional[int]` | Rising edge (0 -> +5v) of first coil |
| `t_coil_1_off` | `Optional[int]` | Trailing edge (+5v -> 0) of first coil |
| `t_coil_2_on` | `Optional[int]` | Rising edge (0 -> +5v) of second coil |
| `t_coil_2_off` | `Optional[int]` | Trailing edge (+5v -> 0) of second coil |
| `t_coil_3_on` | `Optional[int]` | Rising edge (0 -> +5v) of third coil |
| `t_coil_3_off` | `Optional[int]` | Trailing edge (+5v -> 0) of third coil |



### GPIO Pin Configuration (BCM Numbering, use gpiozero library)

| Category | Pin Name | GPIO | Description |
|----------|----------|------|-------------|
| Coil Outputs | `GPIO_COIL_1` | 17 | Coil 1 driver output |
| | `GPIO_COIL_2` | 27 | Coil 2 driver output |
| | `GPIO_COIL_3` | 22 | Coil 3 driver output |
| Monitor Inputs | `GPIO_MONITOR_1` | 5 | Coil 1 voltage monitor |
| | `GPIO_MONITOR_2` | 6 | Coil 2 voltage monitor |
| | `GPIO_MONITOR_3` | 13 | Coil 3 voltage monitor |
| Gate Inputs | `GPIO_GATE_1` | 23 | Beam-break gate 1 (Normally HIGH)|
| | `GPIO_GATE_2` | 24 | Beam-break gate 2 (Normally HIGH)|
| | `GPIO_GATE_3` | 25 | Beam-break gate 3 (Normally HIGH)|
| External Controls | `EXTERNAL_TRIGGER_PIN` | 26 | Mechanical fire trigger button (pull-down, active HIGH) |