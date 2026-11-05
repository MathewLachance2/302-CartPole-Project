"""Compare requested and measured pendulum amplitude/frequency.

Run in your editor, or: python plot_csv.py trial.csv --no-show
Requires numpy, scipy and matplotlib. Measurements use the Arduino timestamps.
Frequency describes the controlled oscillation, not its free natural frequency.
Cart position is estimated from commanded steps, not measured cart feedback.
"""

# !! NEW FUCNTIONALTIY TO IMPLIMENT
# 3 plots (quash, maintain, tune) 
# overlaid over one another?
# Also need baseline control case data

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks

# -------------------- SETTINGS YOU CAN CHANGE --------------------
CSV_FILE = "200_maintain.csv"       # Relative to this script; "" asks at startup.
START_TIME_S, END_TIME_S = 0.0, None
PHASE = "control"               # Excludes launch/hold/braking from the analysis.
PEAK_PROMINENCE_DEG = 1.2
MIN_PEAK_DISTANCE_S = 0.05       # Allows up to 10 Hz without assuming the target.
SKIP_FIRST_CYCLES = 3           # Skip three complete cycles after control begins.
MIN_STEADY_CYCLES = 5
STEADY_CV_PERCENT = 5.0         # Max amplitude and period coefficient of variation.
STEADY_DRIFT_PERCENT = 5.0      # Max fitted change over the whole steady window.
MIN_QUIET_TIME_S = 0.70         # Quash: require a full natural period below 10% A0.
TARGET_AMPLITUDE_DEG = None     # None: read A0 from metadata or the hold phase.
TARGET_FREQUENCY_HZ = None      # None: read the tune target / maintain reference from the CSV.
NOMINAL_NATURAL_PERIOD_S = 0.70 # Older maintain CSVs only; labelled as a nominal reference.
SAVE_RESULTS = False             # Show plots/print results without creating folders or files.
# -----------------------------------------------------------------


def get_options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", nargs="?", default=CSV_FILE)
    parser.add_argument("--start", type=float, default=START_TIME_S)
    parser.add_argument("--end", type=float, default=END_TIME_S)
    parser.add_argument("--phase", default=PHASE)
    parser.add_argument("--prominence", type=float, default=PEAK_PROMINENCE_DEG)
    parser.add_argument("--min-distance", type=float, default=MIN_PEAK_DISTANCE_S)
    parser.add_argument("--skip-cycles", "--skip-peaks", type=int, default=SKIP_FIRST_CYCLES)
    parser.add_argument("--min-cycles", type=int, default=MIN_STEADY_CYCLES)
    parser.add_argument("--steady-cv", type=float, default=STEADY_CV_PERCENT)
    parser.add_argument("--steady-drift", type=float, default=STEADY_DRIFT_PERCENT)
    parser.add_argument("--manual-window", action="store_true",
                        help="Use the selected range after skipping, without claiming automatic stability")
    parser.add_argument("--metadata", type=Path, help="Explicit trial JSON, if not found automatically")
    parser.add_argument("--target-amplitude", type=float, default=TARGET_AMPLITUDE_DEG)
    parser.add_argument("--target-frequency", type=float, default=TARGET_FREQUENCY_HZ)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--no-show", action="store_true")
    args = parser.parse_args()
    for name in ("start", "min_distance", "steady_drift"):
        if not np.isfinite(getattr(args, name)) or getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and nonnegative")
    for name in ("prominence", "steady_cv", "target_amplitude", "target_frequency"):
        value = getattr(args, name)
        if value is not None and (not np.isfinite(value) or value <= 0):
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if args.end is not None and (not np.isfinite(args.end) or args.end <= args.start):
        parser.error("--end must be finite and greater than --start")
    if args.skip_cycles < 0 or args.min_cycles < 1:
        parser.error("--skip-cycles must be nonnegative; --min-cycles must be at least one")
    return args


def csv_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [[cell.strip() for cell in row] for row in csv.reader(
            line for line in stream if line.strip() and not line.lstrip().startswith("#"))]


def signature(path):
    body = json.dumps(csv_rows(path), separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def load_csv(path):
    rows = csv_rows(path)
    if len(rows) < 4 or not {"time_s", "angle_deg"}.issubset(rows[0]):
        raise ValueError("CSV needs time_s, angle_deg, and at least three samples.")
    names = rows[0]
    if any(len(row) != len(names) for row in rows[1:]):
        raise ValueError("CSV column count changes between rows.")
    data = [dict(zip(names, row)) for row in rows[1:]]
    time = np.array([float(row["time_s"]) for row in data])
    angle = np.array([float(row["angle_deg"]) for row in data])
    phase = np.array([row.get("phase", "") for row in data])
    cart = np.array([float(row["cart_mm_est"]) for row in data]) if "cart_mm_est" in names else None
    if not np.all(np.isfinite(time)) or np.any(np.diff(time) <= 0):
        raise ValueError("time_s must be finite and strictly increasing.")
    return time, angle, cart, phase


def filename_metadata(path):
    """Recover only command settings from recognizable older filenames."""
    numeric = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    match = re.fullmatch(rf"{numeric}_(baseline|quash|maintain)(?:_\d+)?", path.stem)
    if match:
        return {"mode": match[1], "metadata": {}}
    match = re.fullmatch(rf"{numeric}_tune_({numeric})(?:_\d+)?", path.stem)
    if match:
        return {"mode": "tune", "metadata": {"requested_frequency_Hz": float(match[1])}}
    return None


def find_metadata(path, explicit=None):
    """Read targets from the CSV; older captures can still use their JSON reports."""
    if explicit is not None:
        if not explicit.is_absolute():
            explicit = path.parent / explicit
        return json.loads(explicit.read_text(encoding="utf-8-sig")), explicit, "explicit file"
    rows = csv_rows(path)
    if rows and "trial_mode" in rows[0]:
        embedded = {}
        for key in ("trial_mode", "initial_amplitude_deg", "requested_frequency_Hz", "stop_reason", "limit_events"):
            if key not in rows[0]:
                continue
            column = rows[0].index(key)
            values = {row[column] for row in rows[1:] if row[column]}
            if len(values) > 1:
                raise ValueError(f"Trial setting {key} changes within the CSV.")
            if values:
                value = values.pop()
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    pass
                embedded[key] = value
        return {"mode": embedded.pop("trial_mode", "unknown"), "metadata": embedded}, None, "embedded CSV columns"
    direct = path.with_suffix(".json")
    if direct.is_file():
        return json.loads(direct.read_text(encoding="utf-8-sig")), direct, "matching filename"
    wanted, matches = signature(path), []
    for candidate in sorted(path.parent.glob("*.json")):
        try:
            details = json.loads(candidate.read_text(encoding="utf-8-sig"))
            if not isinstance(details, dict):
                continue
            raw_name = details.get("raw_serial_file")
            if not raw_name:
                continue
            raw = Path(raw_name)
            if not raw.is_file():
                raw = candidate.parent / raw.name
            if raw.is_file() and signature(raw) == wanted:
                matches.append((details, candidate, "matching raw-report contents"))
        except (OSError, ValueError, TypeError):
            continue
    if len(matches) == 1:
        return matches[0]
    inferred = filename_metadata(path)
    if inferred is not None:
        return inferred, None, "command filename (no saved trial metadata)"
    return {}, None, "unavailable; use --metadata if needed"


def detect_cycles(time, angle, selected, prominence, min_distance):
    """Use upper-to-upper periods and the intervening trough for each full cycle."""
    indices = np.flatnonzero(selected & np.isfinite(angle))
    if not len(indices):
        return [], []
    typical_dt = np.median(np.diff(time))
    breaks = (np.diff(indices) > 1) | (np.diff(time[indices]) > 3 * typical_dt)
    segments = np.split(indices, np.flatnonzero(breaks) + 1)
    peaks, cycles = [], []
    for segment_id, segment in enumerate(segments):
        if len(segment) < 3:
            continue
        local, properties = find_peaks(angle[segment], prominence=prominence, plateau_size=True)
        candidates, kept = segment[local], []
        for candidate in candidates[np.argsort(-properties["prominences"], kind="stable")]:
            if all(abs(time[candidate] - time[other]) >= min_distance for other in kept):
                kept.append(int(candidate))
        kept.sort()
        for number, sample in enumerate(kept):
            period = float(time[sample] - time[kept[number - 1]]) if number else None
            peaks.append({"sample": sample, "segment": segment_id, "number": number,
                          "time_s": float(time[sample]), "angle_deg": float(angle[sample]),
                          "period_s": period})
        for number, (first, last) in enumerate(zip(kept, kept[1:])):
            trough = first + int(np.argmin(angle[first:last + 1]))
            if trough in (first, last):
                continue
            upper = float((angle[first] + angle[last]) / 2)
            lower, period = float(angle[trough]), float(time[last] - time[first])
            cycles.append({
                "segment": segment_id, "number": number, "first": first, "last": last,
                "trough": trough, "start_s": float(time[first]), "end_s": float(time[last]),
                "mid_s": float((time[first] + time[last]) / 2), "period_s": period,
                "frequency_hz": 1 / period, "upper_deg": upper, "lower_deg": lower,
                "amplitude_deg": (upper - lower) / 2, "center_deg": (upper + lower) / 2,
            })
    return peaks, cycles


def consistency(values):
    values = np.asarray(values, dtype=float)
    mean = float(np.mean(values))
    if mean <= 0:
        return float("inf"), float("inf")
    cv = float(np.std(values) / mean)
    slope = float(np.polyfit(np.arange(len(values)), values, 1)[0]) if len(values) > 1 else 0.0
    return cv, abs(slope * (len(values) - 1) / mean)


def select_window(cycles, args):
    eligible = [cycle for cycle in cycles if cycle["number"] >= args.skip_cycles]
    groups = []
    for cycle in eligible:
        if not groups or groups[-1][-1]["last"] != cycle["first"]:
            groups.append([])
        groups[-1].append(cycle)
    if not groups:
        return [], "No complete cycles remain after the startup skip."
    tail = groups[-1]  # Do not bridge gaps or choose an early stable patch before later drift.
    required = args.min_cycles if args.manual_window else max(3, args.min_cycles)
    if len(tail) < required:
        return [], f"Need {required} complete cycles in the final continuous segment; found {len(tail)}."
    if args.manual_window:
        return tail, "User-selected window; automatic stability checks disabled."
    for first in range(len(tail) - required + 1):
        candidate = tail[first:]
        checks = [consistency([c[key] for c in candidate]) for key in ("amplitude_deg", "period_s")]
        if all(cv <= args.steady_cv / 100 and drift <= args.steady_drift / 100 for cv, drift in checks):
            return candidate, "Automatic steady suffix: amplitude and period meet CV and fitted-drift limits."
    return [], "No steady region satisfies the chosen variability/drift limits."


def percent_error(actual, target):
    return None if actual is None or target is None else 100 * (actual - target) / target


def analyze(path, args):
    time, angle, cart, phase = load_csv(path)
    details, metadata_path, match = find_metadata(path, args.metadata)
    metadata = details.get("metadata", {})
    mode = details.get("mode", "unknown")
    selected = time >= args.start
    if args.end is not None:
        selected &= time <= args.end
    if args.phase != "all":
        if args.phase not in phase:
            raise ValueError(f"Phase {args.phase!r} unavailable. Use --phase all with an explicit time window.")
        selected &= phase == args.phase
    if np.count_nonzero(selected & np.isfinite(angle)) < 3:
        raise ValueError("Fewer than three valid samples in the analysis range.")

    initial = metadata.get("initial_amplitude_deg")
    if initial is not None:
        initial = float(initial)
        if not np.isfinite(initial) or initial <= 0:
            initial = None
    initial_source = "saved trial metadata" if initial is not None else "unavailable"
    hold = (phase == "hold") & np.isfinite(angle)
    if initial is None and np.any(hold):
        initial = float(np.max(np.abs(angle[hold])))
        initial_source = "maximum absolute angle during the recorded hold phase"
    reference = args.target_amplitude if args.target_amplitude is not None else initial
    target_frequency = args.target_frequency
    frequency_source = "user override" if target_frequency is not None else "unavailable"
    frequency_label = "Target f"
    if target_frequency is None and mode in ("tune", "maintain"):
        target_frequency = metadata.get("requested_frequency_Hz")
        if target_frequency is not None:
            frequency_source = "command filename" if match.startswith("command filename") else "saved trial metadata"
    if mode == "maintain":
        frequency_label = "Maintain reference f"
        if target_frequency is None:
            if not np.isfinite(NOMINAL_NATURAL_PERIOD_S) or NOMINAL_NATURAL_PERIOD_S <= 0:
                raise ValueError("NOMINAL_NATURAL_PERIOD_S must be finite and positive.")
            target_frequency = 1 / NOMINAL_NATURAL_PERIOD_S
            frequency_source = f"nominal configured period ({NOMINAL_NATURAL_PERIOD_S:g} s); not a measured baseline frequency"
            frequency_label = "Nominal reference f"
    elif mode == "tune":
        frequency_label = "Tune target f"
    if reference is not None:
        reference = float(reference)
    if target_frequency is not None:
        target_frequency = float(target_frequency)
    if reference is not None and (not np.isfinite(reference) or reference <= 0):
        raise ValueError("Amplitude reference must be finite and positive.")
    if target_frequency is not None and (not np.isfinite(target_frequency) or target_frequency <= 0):
        raise ValueError("Target frequency must be finite and positive.")
    peaks, cycles = detect_cycles(time, angle, selected, args.prominence, args.min_distance)
    steady, reason = select_window(cycles, args)
    amplitude = float(np.mean([c["amplitude_deg"] for c in steady])) if steady else None
    frequency = len(steady) / sum(c["period_s"] for c in steady) if steady else None
    amplitude_std = float(np.std([c["amplitude_deg"] for c in steady], ddof=1)) if len(steady) > 1 else None
    center = float(np.mean([c["center_deg"] for c in steady])) if steady else None
    control = np.flatnonzero(phase == "control")
    settling = None
    if mode == "quash" and reference is not None and len(control):
        bad = np.flatnonzero(~np.isfinite(angle[control]) | (np.abs(angle[control]) > 0.1 * reference))
        first_quiet = int(bad[-1] + 1) if len(bad) else 0
        if first_quiet < len(control) and time[control[-1]] - time[control[first_quiet]] >= MIN_QUIET_TIME_S:
            settling = float(time[control[first_quiet]] - time[control[0]])
    summary = {
        "csv_file": str(path), "mode": mode,
        "metadata_file": str(metadata_path) if metadata_path else None, "metadata_match": match,
        "initial_amplitude_deg": initial, "initial_amplitude_source": initial_source,
        "target_amplitude_deg": reference if mode != "quash" else None,
        "quash_threshold_deg": 0.1 * reference if mode == "quash" and reference is not None else None,
        "target_frequency_hz": target_frequency,
        "target_frequency_source": frequency_source, "frequency_reference_label": frequency_label,
        "steady_amplitude_deg": amplitude, "steady_amplitude_std_deg": amplitude_std,
        "steady_center_deg": center, "measured_frequency_hz": frequency,
        "median_period_s": float(np.median([c["period_s"] for c in steady])) if steady else None,
        "median_period_frequency_hz": 1 / float(np.median([c["period_s"] for c in steady])) if steady else None,
        "period_std_s": float(np.std([c["period_s"] for c in steady], ddof=1)) if len(steady) > 1 else None,
        "amplitude_cv_percent": 100 * consistency([c["amplitude_deg"] for c in steady])[0] if steady else None,
        "period_cv_percent": 100 * consistency([c["period_s"] for c in steady])[0] if steady else None,
        "amplitude_error_percent": percent_error(amplitude, reference) if mode != "quash" else None,
        "frequency_error_percent": percent_error(frequency, target_frequency),
        "steady_start_s": steady[0]["start_s"] if steady else None,
        "steady_end_s": steady[-1]["end_s"] if steady else None,
        "steady_cycles": len(steady), "steady_region_found": bool(steady) and not args.manual_window,
        "window_reason": reason, "analysis_phase": args.phase,
        "skip_first_cycles": args.skip_cycles, "minimum_cycles": args.min_cycles,
        "cv_limit_percent": args.steady_cv, "drift_limit_percent": args.steady_drift,
        "prominence_deg": args.prominence, "min_peak_distance_s": args.min_distance,
        "frequency_method": "complete cycles divided by their total upper-peak-to-upper-peak duration",
        "amplitude_method": "mean half peak-to-peak per cycle; upper level is the mean of adjacent upper peaks",
        "settling_time_s_from_control": settling,
        "motor_limit_events": metadata.get("limit_events"),
        "median_sample_interval_ms": float(1000 * np.median(np.diff(time))),
    }
    return time, angle, cart, phase, selected, peaks, cycles, steady, summary


def number(value, digits=3):
    return "unavailable" if value is None else f"{value:.{digits}f}"


def make_plots(path, result):
    time, angle, cart, phase, selected, peaks, cycles, steady, summary = result
    amplitude, frequency = summary["steady_amplitude_deg"], summary["measured_frequency_hz"]
    reference, target_f = summary["target_amplitude_deg"], summary["target_frequency_hz"]
    window_label = "Steady analysis window" if summary["steady_region_found"] else "Chosen analysis window"
    fig, ax = plt.subplots(figsize=(11, 6.8), constrained_layout=True)
    ax.plot(time, angle, color="tab:blue", linewidth=1.3, label="Pendulum angle")
    used = {c[key] for c in steady for key in ("first", "last")}
    excluded = [p["sample"] for p in peaks if p["sample"] not in used]
    if excluded:
        ax.plot(time[excluded], angle[excluded], "x", color="gray", markersize=6, label="Excluded upper peaks")
    if used:
        indices = sorted(used)
        troughs = [c["trough"] for c in steady]
        ax.plot(time[indices], angle[indices], "rx", markersize=7, label="Upper peaks used")
        ax.plot(time[troughs], angle[troughs], "v", color="tab:purple", markersize=4, label="Troughs used for amplitude")
    if reference is not None:
        for sign in (-1, 1):
            ax.axhline(sign * reference, color="tab:orange", linestyle="--", linewidth=1.1,
                       label=f"Target amplitude +/- {reference:.2f} deg" if sign == 1 else None)
    threshold = summary["quash_threshold_deg"]
    if threshold is not None:
        for sign in (-1, 1):
            ax.axhline(sign * threshold, color="tab:orange", linestyle="--",
                       label=f"Quash threshold +/- {threshold:.2f} deg" if sign == 1 else None)
    if steady:
        center = summary["steady_center_deg"]
        for sign in (-1, 1):
            ax.axhline(center + sign * amplitude, color="tab:green", linestyle=":", linewidth=1.2,
                       label=f"Mean measured amplitude {amplitude:.2f} deg about {center:.2f} deg" if sign == 1 else None)
    control = np.flatnonzero(phase == "control")
    if len(control):
        ax.axvline(time[control[0]], color="black", linestyle=":", linewidth=1, label="Control begins")
    ax.axhline(0, color="gray", linewidth=0.6)
    initial_source = "hold estimate" if "hold phase" in summary["initial_amplitude_source"] else "saved"
    if summary["initial_amplitude_deg"] is None:
        initial_source = "unavailable"
    ax.set(xlabel="Time since launch (s)", ylabel="Pendulum angle (degrees)",
           title=f"Pendulum angle - {path.name}\n"
                 f"Initial amplitude A0: {number(summary['initial_amplitude_deg'])} deg ({initial_source})\n"
                 f"Target A: {number(reference)} deg | steady A: {number(amplitude)} deg\n"
                 f"{summary['frequency_reference_label']}: {number(target_f)} Hz | measured f: {number(frequency)} Hz")

    metrics, metric_axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, constrained_layout=True)
    cycle_time = [c["mid_s"] for c in cycles]
    metric_axes[0].plot(cycle_time, [c["amplitude_deg"] for c in cycles], ".-", label="Measured cycle amplitude")
    metric_axes[1].plot(cycle_time, [c["frequency_hz"] for c in cycles], ".-", label="Measured cycle frequency")
    for axis, target, label in zip(metric_axes, (reference, target_f),
                                   ("Amplitude target", summary["frequency_reference_label"])):
        if target is not None:
            axis.axhline(target, color="tab:orange", linestyle="--", label=label)
    metric_axes[0].set(ylabel="Half peak-to-peak amplitude (deg)", title=f"Cycle measurements - {path.name}")
    metric_axes[1].set(xlabel="Time since launch (s)", ylabel="Frequency (Hz)")

    cart_fig = None
    axes = [ax, *metric_axes]
    if cart is not None:
        cart_fig, cart_ax = plt.subplots(figsize=(11, 5.5), constrained_layout=True)
        cart_ax.plot(time, cart, color="tab:orange", label="Cart position estimated from commanded steps")
        cart_ax.set(xlabel="Time since launch (s)", ylabel="Estimated cart position (mm)",
                    title=f"Estimated cart position - {path.name}")
        axes.append(cart_ax)
    for axis in axes:
        if steady:
            axis.axvspan(summary["steady_start_s"], summary["steady_end_s"], color="tab:green", alpha=0.10,
                         label=window_label)
        axis.grid(True, alpha=0.3)
        axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, fontsize=9)
    return {"pendulum_angle": fig, "cycle_metrics": metrics, **({"cart_position": cart_fig} if cart_fig else {})}


def save_results(output, result, figures):
    *_, peaks, cycles, steady, summary = result
    output.mkdir(parents=True, exist_ok=True)
    for name, figure in figures.items():
        figure.savefig(output / (name + ".png"), dpi=180)
    (output / "analysis.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    selected_cycles = {c["first"] for c in steady}
    with (output / "cycles.csv").open("w", newline="", encoding="utf-8") as stream:
        names = ["start_s", "end_s", "mid_s", "period_s", "frequency_hz", "upper_deg", "lower_deg", "amplitude_deg", "center_deg"]
        writer = csv.DictWriter(stream, fieldnames=names + ["used_for_summary"])
        writer.writeheader()
        for cycle in cycles:
            writer.writerow({**{key: cycle[key] for key in names}, "used_for_summary": cycle["first"] in selected_cycles})
    used_peaks = {c[key] for c in steady for key in ("first", "last")}
    with (output / "peaks_and_periods.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["peak_time_s", "peak_angle_deg", "period_since_previous_peak_s", "frequency_Hz", "used_for_summary"])
        for peak in peaks:
            period = peak["period_s"]
            writer.writerow([peak["time_s"], peak["angle_deg"], period, 1 / period if period else None, peak["sample"] in used_peaks])


def main():
    args = get_options()
    raw = args.csv_file or input("Paste the CSV path or filename: ")
    path = Path(raw.strip().strip('"').strip("'")).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.is_file():
        raise ValueError(f"CSV not found: {path}")
    result = analyze(path, args)
    summary = result[-1]
    report = [
        f"CSV: {path.name}", f"Trial settings: {summary['metadata_file'] or summary['metadata_match']}",
        f"Initial amplitude A0: {number(summary['initial_amplitude_deg'])} deg ({summary['initial_amplitude_source']})",
        f"Amplitude target: {number(summary['target_amplitude_deg'])} deg",
        f"Mean measured amplitude: {number(summary['steady_amplitude_deg'])} +/- {number(summary['steady_amplitude_std_deg'])} deg (cycle SD)",
        f"Amplitude error: {number(summary['amplitude_error_percent'], 2)} %",
        f"{summary['frequency_reference_label']}: {number(summary['target_frequency_hz'])} Hz ({summary['target_frequency_source']})",
        f"Measured peak-to-peak frequency: {number(summary['measured_frequency_hz'])} Hz",
        f"Median-period frequency estimate: {number(summary['median_period_frequency_hz'])} Hz",
        f"Frequency error: {number(summary['frequency_error_percent'], 2)} %",
        f"Window: {number(summary['steady_start_s'])} to {number(summary['steady_end_s'])} s; {summary['steady_cycles']} complete cycles",
        summary["window_reason"],
        f"Skipped first {args.skip_cycles} cycles per valid segment; CV <= {args.steady_cv:g}%, fitted total drift <= {args.steady_drift:g}%.",
        f"Median sample interval: {summary['median_sample_interval_ms']:.3f} ms; firmware limit events: {summary['motor_limit_events']}",
        "Amplitude is half peak-to-peak, not mean absolute angle. Frequency is cycles divided by their total elapsed time.",
    ]
    if summary["settling_time_s_from_control"] is not None:
        report.append(f"Quash settling time from control onset: {summary['settling_time_s_from_control']:.3f} s")
    figures = make_plots(path, result)
    print("\n".join(report))
    if SAVE_RESULTS and not args.no_save:
        output = args.output or path.parent / (path.stem + "_analysis")
        save_results(output, result, figures)
        (output / "summary.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"Saved analysis: {output.resolve()}")
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
