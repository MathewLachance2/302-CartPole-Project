# Milestone 3 damping workflow

## Desktop app

1. Upload the updated `MS3_MK1/MS3_MK1.ino` to the **Adafruit ItsyBitsy M0 Express** with **AccelStepper** and **RotaryEncoder** installed. Close Serial Monitor after uploading.
2. From the Milestone 3 folder, install the Python packages with `python -m pip install -r requirements.txt`. Double-click `CartPole.pyw` or run `python record_decay.py` to open the app.
3. Select your Arduino's serial port and click **Connect**. Choose the CSV folder. Mark the cart start and verify that the selected direction has enough clear travel.
4. Install one centre bolt, select that configuration, and click **Run experiment**. Repeat for two outer bolts and then all three bolts. Leave all three nuts installed throughout.
5. On the **Analysis** tab, click **Analyze runs** after all three batches are finished. The statistics appear in **Status and results**. Use **View plot** to see a figure or **Open results folder** for the full output.

The defaults produce the required 63 runs. The Experiment tab applies the visible motion, timing and distance settings when you start a batch. Use a fresh CSV folder when changing the outbound settings after recording a batch. The app saves those settings in `experiment.json` so subsequent batches use a matching profile and analysis uses the recorded timing and distances. To resume an interrupted batch, use the same folder and outbound settings. The recorder skips complete files.

In **Motion and timing**, select **Brake over the last 20% of the return** before starting a batch to begin braking with one fifth of the return distance left. The checkbox is unchecked by default and can change between batches in the same folder. The app records each batch’s return mode in **Status and results** and `batches.jsonl`.

The Analysis tab applies the visible inertias and peak settings each time you click **Analyze runs**. **Stop** cancels the active batch and disconnects the board. Check the cart is back at the marked start before reconnecting and resuming.

## Command line collection

1. Upload `MS3_MK1/MS3_MK1.ino` to the **Adafruit ItsyBitsy M0 Express** using Arduino IDE with **AccelStepper** and **RotaryEncoder** installed. Close Serial Monitor and other apps using the port.
2. Open a terminal in the Milestone 3 folder and install the Python packages with `python -m pip install -r requirements.txt`.
3. Mark one cart start and verify which travel direction gives at least 70 mm of clear travel plus clearance. Use the same start and direction for all three batches. Leave all three nuts installed throughout.
4. Install the specified bolts before each command. Replace `COM8` with the Arduino's port and use `--direction -1` if negative motor travel takes the cart away from the marked start.

| Installed bolts | Command |
| --- | --- |
| One centre bolt | `python record_decay.py COM8 1 --direction 1` |
| Two outer bolts | `python record_decay.py COM8 2 --direction 1` |
| All three bolts | `python record_decay.py COM8 3 --direction 1` |

Each command records the 50–70 mm runs in 1 mm steps and saves them in `decay_runs`. The default sketch profile launches with `moveTo()` at 7,000 steps/s and 100,000 steps/s². It samples at 100 Hz from launch through the first sample at or after five seconds of free oscillation. The `free` phase starts after AccelStepper finishes the outbound move. Cart displacement comes from commanded step counts and angle is zeroed with the pendulum hanging still.

The recorder saves each run before returning the cart to the marked start. The board waits for the encoder to stay still for one second before accepting the next launch. Use Ctrl+C to stop the batch. Before resuming, check the cart is back at the marked start. Running the same command skips completed files.

## Fit and compare damping

Run `python analyze_decay.py` after collecting all 63 CSVs. It writes `damping.csv`, `b_vs_bolt_count.png`, `c_vs_bolt_count.png`, `example_angle.png`, and `statistics.txt` in `decay_runs/analysis`. The default example uses `B2_D060.csv`. Pass a CSV folder to analyze a batch that was saved elsewhere.

For each full free cycle, the script averages the two bounding upper peaks and takes half the difference between that average and the intervening trough. It fits the natural log of these amplitudes against cycle midpoint time. The slope is `lambda`, which is negative for decay. The script computes `b = -2 * lambda` and `c = b * I_P` with the inertia for each bolt configuration. The CSV reports three significant figures and the plots and tests use the unrounded values.

The two tests rank the three results at each distance and permute bolt labels within each matched distance. They compare how far the mean ranks separate across bolt configurations for `b` and `c` independently. The analysis uses 9,999 permutations and a Bonferroni adjustment for the two outcomes at an overall 5% significance level. The tests assume independent distance blocks and exchangeable labels within each block under the null. A difference applies to the bolt configurations tested, which also change mass and the centre of mass. [SciPy paired permutation test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html)

## Milestone 2 desktop and console tools

### Desktop app

Run `python csv_code_MK2.py` with no arguments to open the legacy feedback-trial app. If prompted, use **Install Python packages** at the bottom of the window.

1. Close Arduino Serial Monitor, select the board's COM port, and click **Connect**. Wait for **Board ready**.
2. Choose the trial mode and enter the signed launch distance. For Tune, enter the target frequency. Run a Baseline or enter a known damping coefficient before running feedback trials.
3. Choose a CSV save folder and click **Record next trial** when you want a full trial recording. Use **Return to start** after a feedback trial. A completed Baseline returns automatically with factory settings.
4. Use **Analyze CSV** for a single trial or **Compare trials** to plot the four modes together. The plot toolbar can save each figure.

Upload the updated `MS2_MK1/MS2_MK1.ino` once using Arduino IDE with the **Adafruit ItsyBitsy M0 Express** board selected. The sketch uses the **AccelStepper** and **RotaryEncoder** libraries. Close Arduino Serial Monitor after uploading. The updated sketch takes motion, timing and feedback settings from the app while idle. Older sketches support the basic trial workflow and known damping input.

In **Board settings**, choose the group to edit and click **Apply to board**. Switching pendulum gains to **Manual** uses the proportional and derivative values in the form. This controller uses PD feedback. The firmware keeps its original maximum speed, acceleration and travel as upper bounds. Changes are held in memory until the board restarts. Changing the period or encoder calibration invalidates the damping calibration. Changing track travel, end clearance or motor calibration clears the recorded return point.

### Optional console

To use the original terminal workflow, run `python csv_code_MK2.py --cli` and press Enter for COM8. Close Arduino Serial Monitor first.

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
