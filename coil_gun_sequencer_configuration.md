##  Device Configuration & Logging

### User Configurable Parameters 
These are configurable in the Configuration UI Page, and 

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `PROJECTILE_LENGTH_MM` | `float` | `10.0` | Projectile length for FPS calculations |
| `PROJECTILE_MASS_GRAMS` | `float` | `2.08` | Projectile mass for energy calculations |
| `V_COIL_FLOOR` | `float` | `2.0` | Minimum active voltage (coil "depleted") |
| `V_COIL_CEILING` | `float` | `12.0` | Maximum active voltage (coil "ready") |
| `T_DELAY_COIL_3` | `float` | `2000.0` | Delay time in uS for Coil 3 start |
| `GATE_1_COIL_1_DELAY_US` | `float` | `500.0` | Delay after gate_1 trigger before firing coil_1 |
| `COIL_1_PULSE_DURATION_US` | `float` | `1500.0` | Coil_1 pulse duration in uS |
| `GATE_2_COIL_2_DELAY_US` | `float` | `400.0` | Delay after gate_2 trigger before firing coil_2 |
| `COIL_2_PULSE_DURATION_US` | `float` | `1200.0` | Coil_2 pulse duration in uS |

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