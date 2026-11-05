# Milestone 2 recording workflow

Run `csv_code_MK2.py`, press Enter for COM8, and keep this console open between trials. Close Arduino Serial Monitor first. Moving these files does not change the COM port or data format. The Arduino sketch remains inside its matching `MS2_MK1` folder.

Saving starts OFF. Enter each command separately, and wait for `# READY` between board operations:

1. `49 baseline` — run the calibration without saving a CSV. After a normally completed report, Python waits for the board's `# READY` and automatically sends `XXX`. If the Arduino already returned before becoming ready, Python skips the extra command. Wait for the return result and final `# READY`, then let the pendulum settle. Check `baseline_fit_updated=1` and `model_ready=1` afterward.
2. No `XXX` is needed after a normally completed baseline. An interrupted baseline stays stopped; use `XXX` later if appropriate.
3. `track next` — arm one recording in Python. `start csv tracking` is an alias.
4. `49 quash` — the next accepted trial is selected and its full report is saved.
5. After a quash/maintain/tune report, use `XXX`, then `track next`, then another trial such as `49 tune 2.0`.

`AUTO_RETURN_BASELINE = True` in `csv_code_MK2.py` enables the Python fallback. It works with existing firmware that supports `XXX`, including the previous sketch. Stop commands cancel pending automatic returns, and an interrupted baseline does not trigger one. Restart Python to load this change; an Arduino upload is not needed for this fallback. The separate automatic-return feature in the Arduino sketch remains supported without duplicate return commands.

The console supports as many commands/trials as needed. Commands entered while the board is busy are rejected locally rather than queued; `stop` and `!` remain available. `track next` can arm a future run while another run is active without selecting the active run. A trial rejected by the Arduino does not consume the arm. `track off` cancels future/pending tracking; a trial already acknowledged by `# RUNNING` continues to be saved. `tracking` displays the current state; `help` lists commands. `XXX`, `status`, settings commands and `csv` replay do not consume the arm.

Invalid commands (for example, `tracking start`), malformed arguments, busy-board refusals and Arduino rejection messages print a yellow `Warning:` in terminals that support color. Invalid commands are rejected before sending anything to the board, and the warning suggests the accepted syntax or `help`. Redirected output stays plain text. Restart the Python console after updating the script to use this feedback.

Only CSV files are saved, beside `csv_code_MK2.py` by default: the root of your `MILESTONE 2` folder. The full path is printed at startup. Change `SAVE_FOLDER` or use `--output` to choose another existing folder; the console never creates folders.

Names follow `distance_mode_counter.csv`, or `distance_tune_frequency_counter.csv`. For example, repeated `200 quash` trials produce `200_quash_1.csv`, `200_quash_2.csv`, etc. A `200 tune 3` trial produces `200_tune_3_1.csv`. The counter continues above the highest existing number for that command, including incomplete captures, without overwriting files. Equivalent numbers such as `200`, `200.0`, and `2e2` share a counter.

The CSV is a standard header-first file. The newest firmware supplies these columns:

```text
time_s,phase,cart_mm_est,angle_deg,gp_mm_s2_rad,gd_mm_s_rad,cart_kp_per_s2,cart_kd_per_s,encoder_counts
```

`time_s` is the actual Arduino sample time relative to the trial's initial move, including launch, hold, control and braking. It is not the time Python receives the report. `encoder_counts` is the measured pendulum encoder position relative to trial zero; `angle_deg` is its converted angle. `cart_mm_est` is estimated motor/cart displacement from commanded steps, not independent cart feedback.

Older firmware that supplies the same eight columns without `encoder_counts` is also accepted. Only `time_s`, `cart_mm_est` and `angle_deg` are required; all columns actually received are preserved. The logger never invents encoder counts from angle. The compact three-column `csv` replay lacks estimated cart position and is therefore not a tracked full-trial report.

Python uses a `.partial.csv` while a tracked trial is active. Data arrive after the Arduino stops. A valid `# END` finalizes it as `.csv`; an interrupted or malformed report with received data keeps `.partial.csv` and prints the reason. Empty captures are removed. There are no JSON or serial-log sidecars. A complete report can still describe a stopped trial: check its `stop_reason` column.

The CSV adds `distance_mm`, `trial_mode`, `initial_amplitude_deg`, `requested_frequency_Hz`, `stop_reason`, and `limit_events` as constant columns, so each file carries its own plotting targets. Read it directly with `csv.DictReader` or `pandas.read_csv`; no comment-skipping option is required.

`initial_amplitude_deg` is the initial swing measured during that trial's hold phase. Baseline calibration estimates damping; later maintain/tune trials measure their own initial amplitude. The plot labels whether A0 was saved or reconstructed from older hold-phase data. Maintain uses its saved `requested_frequency_Hz` as the reference. Older maintain files without this value use the configured 0.70 s period, explicitly labelled as a nominal reference rather than a measured baseline frequency.

To check whether the return is native to the Arduino, send `status` while idle and look for `# Baseline automatic return: on`. The current sketch also emits `encoder_counts` as its ninth data column. If the new status line is absent, the Python fallback still handles the baseline return using `XXX`.

`plot_csv.py` reads those columns, shows the figures, and prints its results. Automatic analysis-file saving is off (`SAVE_RESULTS = False`), so plotting creates no analysis folders. Older CSVs with their original sidecars can still be read. This update requires restarting the Python console, with no Arduino upload.
