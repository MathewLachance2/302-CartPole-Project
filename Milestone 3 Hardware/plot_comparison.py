"""Compare baseline, quash, tune, and maintain in vertically stacked panels.

Change CSV_FILES below, then run this file in your editor. Keep plot_csv.py
beside it: its reader and peak analysis are reused. No output files are saved.
Time zero is each trial's first control-phase sample.
Default traces cover the entire control phase; the initial launch/hold are skipped.
"""

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True  # Avoid a __pycache__ folder beside the scripts.
import matplotlib.pyplot as plt
import numpy as np
import plot_csv as analysis


# -------------------- SETTINGS YOU CAN CHANGE --------------------
CSV_FILES = {
    "baseline": "200_baseline_1.csv",
    "quash": "200_quash.csv",
    "tune": "200_tune_3.csv",
    "maintain": "200_maintain_1.csv",
}
SHOW_FULL_TRIAL = False        # False: entire control phase, including transients.
NORMALIZE_AMPLITUDE = False    # True divides angle and cycle amplitude by A0.
CART_FROM_CONTROL_START = True # Zero = cart position at control start, after launch.
# Peak/stability settings come from plot_csv.py.
# -----------------------------------------------------------------

COLORS = {"baseline": "0.25", "quash": "tab:green", "tune": "tab:orange", "maintain": "tab:blue"}


def get_options():
    parser = argparse.ArgumentParser(description=__doc__)
    for mode, filename in CSV_FILES.items():
        parser.add_argument("--" + mode, default=filename, help=f"{mode.title()} CSV path")
    parser.add_argument("--full-trial", action="store_true", default=SHOW_FULL_TRIAL)
    parser.add_argument("--normalize", action="store_true", default=NORMALIZE_AMPLITUDE)
    parser.add_argument("--no-show", action="store_true", help="Check data and print results without opening windows")
    return parser.parse_args()


def read_trial(mode, path):
    options = SimpleNamespace(
        start=0, end=None, phase="control", prominence=analysis.PEAK_PROMINENCE_DEG,
        min_distance=analysis.MIN_PEAK_DISTANCE_S, skip_cycles=analysis.SKIP_FIRST_CYCLES,
        min_cycles=analysis.MIN_STEADY_CYCLES, steady_cv=analysis.STEADY_CV_PERCENT,
        steady_drift=analysis.STEADY_DRIFT_PERCENT, manual_window=False,
        metadata=None, target_amplitude=None, target_frequency=None,
    )
    result = analysis.analyze(path, options)
    time, angle, cart, phase, selected, peaks, cycles, steady, summary = result
    if summary["mode"] not in (mode, "unknown"):
        raise ValueError(f"{mode.title()} points to a {summary['mode']} trial: {path.name}")
    control_start = int(np.flatnonzero(phase == "control")[0])
    control_time = float(time[control_start])
    return dict(mode=mode, path=path, time=time - time[control_start], angle=angle,
                cart=cart, phase=phase, control_start=control_start, control_time=control_time,
                cycles=cycles, steady=steady, summary=summary)


def scale_for(trial, normalize):
    initial = trial["summary"]["initial_amplitude_deg"]
    if normalize and (initial is None or initial <= 0):
        raise ValueError(f"Cannot normalize {trial['path'].name}: initial amplitude is unavailable.")
    return initial if normalize else 1.0


def make_plots(trials, full_trial=False, normalize=False):
    rows = len(trials)
    angle_fig, angle_axes = plt.subplots(rows, 1, figsize=(12, 9.2), sharex=True,
                                         sharey=True, squeeze=False, constrained_layout=True)
    cycle_fig, cycle_axes = plt.subplots(rows, 2, figsize=(12, 9.2), sharex="col",
                                         sharey="col", squeeze=False, constrained_layout=True)
    cart_fig = cart_axes = None
    if any(trial["cart"] is not None for trial in trials):
        cart_fig, cart_axes = plt.subplots(rows, 1, figsize=(12, 9.2), sharex=True,
                                           sharey=True, squeeze=False, constrained_layout=True)

    for row, trial in enumerate(trials):
        angle_ax = angle_axes[row, 0]
        amplitude_ax, frequency_ax = cycle_axes[row]
        mode = trial["mode"]
        color, style = COLORS[mode], "--" if mode == "baseline" else "-"
        scale = scale_for(trial, normalize)
        selected = np.ones(len(trial["time"]), dtype=bool) if full_trial else trial["phase"] == "control"
        time = trial["time"][selected]
        label = mode.title()
        target = trial["summary"]["target_frequency_hz"]
        if mode == "tune" and target is not None:
            label += f" ({target:g} Hz target)"
        angle_ax.plot(time, trial["angle"][selected] / scale, color=color,
                      linestyle=style, linewidth=1.3)
        angle_ax.set_title(label, loc="left", fontsize=11)
        angle_ax.axhline(0, color="gray", linewidth=.6)
        if normalize:
            for sign in (-1, 1):
                angle_ax.axhline(sign, color="gray", linestyle=":", linewidth=1)

        if cart_axes is not None:
            cart_ax = cart_axes[row, 0]
            cart_ax.set_title(label, loc="left", fontsize=11)
            if trial["cart"] is None:
                cart_ax.text(.5, .5, "Cart position unavailable", transform=cart_ax.transAxes, ha="center")
            else:
                offset = trial["cart"][trial["control_start"]] if CART_FROM_CONTROL_START else 0
                if not np.isfinite(offset):
                    raise ValueError(f"Cart position at control start is invalid: {trial['path'].name}")
                cart_ax.plot(time, trial["cart"][selected] - offset, color=color,
                             linestyle=style, linewidth=1.3)
                control_position = trial["cart"][trial["control_start"]]
                cart_ax.set_title(f"{label} | control starts at {control_position:.1f} mm (estimated)",
                                  loc="left", fontsize=11)

        cycle_time = [cycle["mid_s"] - trial["control_time"] for cycle in trial["cycles"]]
        amplitude_ax.plot(cycle_time, [cycle["amplitude_deg"] / scale for cycle in trial["cycles"]],
                          ".-", color=color)
        frequency_ax.plot(cycle_time, [cycle["frequency_hz"] for cycle in trial["cycles"]],
                          ".-", color=color, label="Measured")
        amplitude_ax.set(title=mode.title(), ylabel="Amplitude / A0" if normalize else "Amplitude (deg)")
        frequency_ax.set(title=mode.title(), ylabel="Frequency (Hz)")
        if not trial["cycles"]:
            for axis in (amplitude_ax, frequency_ax):
                axis.text(.5, .5, "No complete cycles", transform=axis.transAxes, ha="center")
        if mode in ("tune", "maintain") and target is not None:
            source = trial["summary"]["target_frequency_source"]
            reference = "nominal reference" if source.startswith("nominal") else "reference"
            kind = "target" if mode == "tune" else reference
            frequency_ax.axhline(target, color=color, linestyle="--", alpha=.65,
                                  label=f"{kind.title()}: {target:.3f} Hz")
            frequency_ax.legend(loc="upper right", fontsize=8)

    scope = "Full recorded trial" if full_trial else "Entire control phase (launch and hold excluded)"
    angle_fig.suptitle(f"Pendulum angle - {scope}")
    angle_fig.supylabel("Pendulum angle / initial amplitude A0" if normalize else "Pendulum angle (degrees)")
    cycle_fig.suptitle("Complete-cycle amplitude and frequency (including startup cycles)")
    figures = {"angle": angle_fig, "cycles": cycle_fig}
    if cart_fig is not None:
        reference = "zero = position at control start" if CART_FROM_CONTROL_START else "position from trial start"
        cart_fig.suptitle(f"Cart movement - {scope}\nCommanded-step estimate; {reference}")
        cart_fig.supylabel("Estimated cart displacement from control start (mm)" if CART_FROM_CONTROL_START
                          else "Estimated cart position from trial start (mm)")
        figures["cart"] = cart_fig
    for figure in figures.values():
        figure.supxlabel("Time since control begins (s)")
    for axis in [*angle_axes.flat, *cycle_axes.flat, *(cart_axes.flat if cart_axes is not None else [])]:
        axis.grid(True, alpha=.25)
        axis.axvline(0, color="gray", linestyle=":", linewidth=1)
        if not full_trial or axis in cycle_axes.flat:
            axis.set_xlim(left=0)
    return figures


def print_summary(trials, full_trial=False):
    def display(value):
        return "--" if value is None else f"{value:.3f}"
    print("Time zero = control activation. Cart positions are commanded-step estimates.\n")
    print("Displayed traces: full recorded trial." if full_trial else
          "Displayed traces: entire control phase, including transients; launch and hold excluded.")
    print("Cart zero = position at control start.\n" if CART_FROM_CONTROL_START else
          "Cart positions are relative to the trial's starting point.\n")
    print(f"{'Mode':<11} {'A0 (deg)':>9} {'Steady A':>10} {'f (Hz)':>9} {'Frequency window':<21} {'Quash settle (s)':>16}")
    for trial in trials:
        summary, cycles = trial["summary"], trial["cycles"]
        frequency = summary["measured_frequency_hz"]
        window = "steady cycles"
        if frequency is None:
            frequency = len(cycles) / sum(cycle["period_s"] for cycle in cycles) if cycles else None
            window = "all control cycles" if cycles else "no complete cycles"
        values = [summary["initial_amplitude_deg"], summary["steady_amplitude_deg"], frequency]
        initial, amplitude, measured = [display(value) for value in values]
        settling = display(summary["settling_time_s_from_control"])
        print(f"{trial['mode'].title():<11} {initial:>9} {amplitude:>10} {measured:>9} {window:<21} {settling:>16}")
        print(f"  {trial['path'].name}")
    print("\nA0 and steady A are in degrees. -- means no qualifying measurement/window.")
    print("Cycle plots include every detected complete control-phase cycle. No files are saved.")


def main():
    args = get_options()
    trials, paths = [], set()
    for mode in CSV_FILES:
        path = Path(getattr(args, mode)).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"{mode.title()} CSV not found: {path}. Change CSV_FILES at the top of this script.")
        if path in paths:
            raise ValueError(f"Each mode needs a different recording: {path.name}")
        paths.add(path)
        trials.append(read_trial(mode, path))
    figures = make_plots(trials, args.full_trial, args.normalize)
    print_summary(trials, args.full_trial)
    if args.no_show:
        for figure in figures.values():
            plt.close(figure)
    else:
        plt.show()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
