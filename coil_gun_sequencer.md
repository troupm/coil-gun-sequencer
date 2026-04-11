

## Raspberry Pi 5 Coil Gun Sequencer

### Motivation
I have a functional prototype for a multi-stage electromagnetic accelerator track, or "coil gun". Given the precise timing and sensor feedback handling required to control such a system efficiently, I need a interactive application with which to configure, test, log and improve this prototype through field testing and design iterations. The app should be suitable to test conditions, such that the user may configure settings in a pre-flight context then arm & fire the device with one hand, from the (available) 5-inch touchscreen interface.   

### Resources

 - `coil_gun_sequencer_configuration.md`: File containing desired GPIO pin assignments, key configuration parameters, and broad logging requirements
### Coil Gun Design
- The device to be controlled by this sequencer (the Coil Gun) is a series of Beam Break Gate sensors and electromagnetic coils arranged in order along a rigid tube, as follows:
	- coil_1 -> gate_1 -> coil_2 -> gate_2 -> coil_3 -> gate_3
- Only Gate 3 and Coil 3 are not yet attached, so the app design should handle missing components gracefully, without affecting operation of the partially assembled system
- Gate 1 will be triggered by the user, gates 2 & 3 will be triggered automatically, `gate_1_coil_2_delay_us` (for example) after the leading edge of the preceding gate trigger is detected


### Implementation Requirements
- Assume the physical configuration 	`coil_1 -> gate_1 -> coil_2 -> gate_2 -> coil_3 -> gate_3`
- I recommend a flask app supporting two UI pages:
	- Touchscreen: 
		- Coil 1 Arm & Fire controls
		- Quick-reference statistics on the last test run
			- Transit Time for each triggered gate (how long each beam was broken)
			- Flight time between triggered gates (Leading edge to Leading Edge in cases where two adjacent gates were triggered in one run)
			- Calculated velocities
				- Transit velocities calculated from projectile length & transit time
				- Flight Velocities calculated from Gate Timestamps and user specified Gate 1 -> Gate 2 Distance
			- A `Ready` indicator, which indicates that the system can be armed
			- A `Save` button, which the user can use to end a run and save the statistics (useful when the third gate is not connected, or when the projectile gets stuck in the track & the system is still expecting a Gate 2 event) - disarms Coils & enters Ready state
			- `Clear` button: Clears measurements & Aborts current Run (disarms Coils & enters Ready state)
	- Configuration Screen
		- Configurable UI elements for all `User Configurable Parameters` listed in `coil_gun_sequencer_configuration.md`
			- These should be persisted in the `config_snapshots` table, with one snapshot saved on each parameter change
			- On app startup, the most recent configuration should be loaded
		- Interactive Elements
			- `Save` button: This ends the active firing test (Run), saves any unsaved data to the logs, and starts a new Run (disarms Coils & enters Ready state)
			- `Clear` button: Clears measurements & Aborts current Run (enters Ready state)
			- `New Sequence`: Generates a new UUID for use in grouping sets of Run Logs together for like-to-like analysis. `run_sequence_id` should be included in all logs & database records. On startup, the most recent `run_sequence_id` or, if no records exist, a new UUID will be randomly generated & assigned
- Test Run Behavior
	- User Arms Coil 1
	- User Fires Coil 1
	- Gate 1 Trigger Leading Edge detected
		- Arm Gate 2 : Gate 2 will now automatically fire in `gate_1_coil_2_delay_us`
	- Gate 1 Trigger Falling Edge detected
		- Log Gate 1 Transit Time & calculate Transit Velocity & m/s
	- Gate 2 Trigger Leading Edge detected
		- Arm Gate 3 : Gate 3 will now automatically fire in `gate_2_coil_3_delay_us`
	- Gate 2 Trigger Falling Edge detected
		- Log Gate 2 Transit Time & calculate Transit Velocity in m/s
		- Log Gate 1 Gate 2 Flight Time (leading edge to leading edge)
		- Calculate Gate 1 -> Gate 2 Flight Velocity in m/s
	- Gate 3 Trigger Leading Edge detected
	- Gate 3 Trigger Falling Edge detected
		- Log Gate 3 Transit Time & calculate Transit Velocity in m/s
		- Log Gate 2 Gate 3 Flight Time (leading edge to leading edge)
		- Calculate Gate 2 -> Gate 3 Flight Velocity in m/s (This is also "Muzzle Velocity")


### Considerations
- This is a real-time system control application, so precise state management is paramount - you must capture every event, every time, with as little latency as reasonably possible
	- Use dedicated threads & interrupts for gate monitoring
	- Avoid "blind" sleep delay loops
	- Avoid timeout, cooldown &| debounce handlers with durations longer than 10us
- Backend persistence
	- Since there are two UI pages that share some components, care must be taken to route all critical values through the backend to prevent "Stale" data or value drift when both pages are open simultaneously (which will usually be the case) 
