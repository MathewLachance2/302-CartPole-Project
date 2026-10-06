# 302-CartPole-Project

Arduino firmware, Python recording tools, and pendulum measurements for the MECH302 cart-pole project.

## Files

| Path | Purpose |
| --- | --- |
| `STEPPER3_TEMP/STEPPER3_TEMP.ino` | Cart motion and post-stop pendulum amplitude measurements. |
| `Encoder_Log/Encoder_Log.ino` | Encoder-only readings with Arduino timestamps; no motor control. |
| `Encoder_Log/record_angles.py` | Records serial readings to CSV, prints angles, and plots the recording when stopped. |
| `Encoder_Log/period_finding.py` | Finds oscillation peaks after a chosen start time and estimates the period. |
| `Encoder_Log/recordings/` | Two supplied example recordings. |
| `figures/` | Supplied period-analysis plots for both recordings. |

## Setup

- Board: **Adafruit ItsyBitsy M0 Express**, using the Adafruit SAMD board package in Arduino IDE.
- Arduino libraries: **AccelStepper** and **RotaryEncoder** by Matthias Hertel.
- Connections: STEP **9**, DIR **7**, encoder A **10**, encoder B **11**.
- Encoder conversion: **1.667 counts per degree**, approximately **0.6 degrees per reported count**.
- The motion sketch assumes **40 steps/mm** for the configured mechanism and microstepping.

Install the Python dependencies from the repository folder:

```sh
python -m pip install -r requirements.txt
```

## Cart motion experiment

Upload `STEPPER3_TEMP/STEPPER3_TEMP.ino`. Open Serial Monitor at **115200 baud** with **Newline** enabled. Begin with the pendulum still and enough track for the commanded distance.

- `50`: one 50 mm trial, followed by return to the starting position.
- `50*`: four 50 mm trials, each followed by return and automatic settling.
- `-50*`: the same four-trial sequence in the negative direction.

The cart accelerates, cruises, decelerates, and stops. The sketch then waits for the first zero crossing and records the following peak, printing one final amplitude per trial. Failed measurements print `N/A`. If settling times out, `c` rechecks settling and `x` ends the batch.

Sampling is configured by `sampleRateHz = 200`. Motor position is based on commanded steps; there is no physical homing or cart-position feedback.

## Encoder recording and period analysis

Upload `Encoder_Log/Encoder_Log.ino` for encoder-only recording, then close Arduino Serial Monitor and Serial Plotter so Python can access the port.

```sh
python Encoder_Log/record_angles.py
```

Enter the Arduino COM port when prompted. Let the pendulum hang still, then press Enter. Stop with **Ctrl+C** to finish the CSV and display a Matplotlib plot. Recordings are saved in `Encoder_Log/recordings/`.

```sh
python Encoder_Log/period_finding.py
```

Enter a CSV path and the time in seconds after which peak detection should begin. The script reports the median peak-to-peak period and plots the selected peaks. It uses a peak prominence of 1.2 degrees, which may need adjustment for different signals.

The Python logger reads the encoder-only sketch's serial output; it does not upload Arduino code or parse the motion sketch's trial summaries. The encoder sketch targets one reading every **5000 microseconds (200 Hz)**; use recorded timestamp gaps to assess actual timing.

The two supplied CSV files are earlier recordings with `time_source=pc_receipt`, not Arduino timestamps. They do not verify the current firmware's 200 Hz sampling rate.
