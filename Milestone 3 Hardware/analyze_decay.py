"""Estimate damping and compare the three Milestone 3 bolt configurations."""

import argparse
import csv
from pathlib import Path

import matplotlib
import numpy as np
from scipy.stats import permutation_test, rankdata

matplotlib.use("Agg")  # Save files even on a computer without a display.
import matplotlib.pyplot as plt

from plot_csv import detect_cycles, load_csv
from decay_settings import AnalysisSettings
from record_decay import DEFAULT_SETTINGS, RUN_FOLDER, read_run_settings, run_name


DEFAULT_ANALYSIS = AnalysisSettings()


def estimate_decay(path, distance, settings=DEFAULT_SETTINGS, analysis=DEFAULT_ANALYSIS):
    try:
        time, angle, cart, phase = load_csv(path)
        free = phase == "free"
        free_time = time[free]
        sample_s = settings.sample_ms / 1000
        expected_count = max(1, int(settings.free_s / sample_s) - 1)
        if (len(free_time) < expected_count
                or free_time[-1] - free_time[0] < settings.free_s - 2 * sample_s
                or not np.all(free[free.argmax():]) or phase[0] != "launch"):
            raise ValueError(f"expected a launch followed by {settings.free_s:g} s of free oscillation")
        if (cart is None or not np.isfinite(angle).all()
                or abs(abs(cart[-1]) - distance) > 0.05 or np.any(cart[free] != cart[-1])):
            raise ValueError("expected a finite angle trace and a stationary cart at the target")

        _, cycles = detect_cycles(time, angle, free, prominence=analysis.prominence_deg,
                                  min_distance=analysis.peak_spacing_s)
        if len(cycles) < 3:
            raise ValueError("fewer than three complete free-oscillation cycles")

        midpoints = [cycle["mid_s"] - free_time[0] for cycle in cycles]
        amplitudes = [cycle["amplitude_deg"] for cycle in cycles]
        decay_rate = np.polyfit(midpoints, np.log(amplitudes), 1)[0]
        return decay_rate, (time, angle, free_time, cycles)
    except ValueError as error:
        raise ValueError(f"{path.name}: {error}") from error


def plot_example(path, trace, decay_rate, name):
    time, angle, free_time, cycles = trace
    angle = np.deg2rad(angle)
    peaks = sorted({cycle[key] for cycle in cycles for key in ("first", "last")})
    troughs = [cycle["trough"] for cycle in cycles]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(time, angle, label="Measured angle")
    ax.axvspan(free_time[0], free_time[-1], color="0.92", label="Free oscillation")
    ax.scatter(time[peaks], angle[peaks], color="tab:orange", label="Upper peaks used", zorder=3)
    ax.scatter(time[troughs], angle[troughs], color="tab:green", label="Troughs used", zorder=3)
    ax.set(xlabel="Time from launch (s)", ylabel="Pendulum angle (rad)",
           title=f"{name}   |   $\\lambda$ = {decay_rate:#.3g} s$^{{-1}}$")
    ax.legend(loc="upper right", fontsize="small")
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def rank_spread(*groups, axis):
    # For three groups the null mean rank is 2.
    return np.sum((np.mean(groups, axis=axis) - 2) ** 2, axis=0)


def compare(groups):
    # Columns hold runs matched by travel distance.
    ranks = rankdata(groups, axis=0)
    return permutation_test(ranks, rank_spread, permutation_type="samples",
                            alternative="greater", n_resamples=9999, rng=302)


def plot_comparison(path, groups, label):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.boxplot(groups.T, showfliers=False)
    for bolts, values in enumerate(groups, start=1):
        ax.scatter(bolts + np.linspace(-0.10, 0.10, len(values)), values,
                   color="tab:blue", alpha=0.7, s=16, zorder=3)
    ax.set(xlabel="Bolt count", ylabel=label)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def analyze_experiment(runs, output=None, analysis=DEFAULT_ANALYSIS, emit=print):
    settings = read_run_settings(runs)
    distances = settings.distances
    inertias = analysis.inertias
    if len(distances) < 2:
        raise ValueError("Collect at least two matched travel distances to compare bolt configurations.")
    output = output or runs / "analysis"
    example_run = (2, distances[len(distances) // 2])
    trials = [(bolts, distance, runs / run_name(bolts, distance))
              for bolts in inertias for distance in distances]
    missing = [path.name for _, _, path in trials if not path.is_file()]
    if missing:
        raise ValueError(f"Missing {len(missing)} of {len(trials)} runs: {', '.join(missing)}")

    rows = []
    for bolts, distance, path in trials:
        decay_rate, trace = estimate_decay(path, distance, settings, analysis)
        b = -2 * decay_rate
        c = b * inertias[bolts]
        rows.append((bolts, distance, inertias[bolts], decay_rate, b, c))
        if (bolts, distance) == example_run:
            example = trace, decay_rate, path.stem

    output.mkdir(parents=True, exist_ok=True)
    with (output / "damping.csv").open("w", newline="", encoding="ascii") as stream:
        writer = csv.writer(stream)
        writer.writerow(["bolt_count", "distance_mm", "I_P_kg_m2", "lambda_per_s",
                         "b_per_s", "c_N_m_s_per_rad"])
        for bolts, distance, *values in rows:
            writer.writerow([bolts, distance, *(f"{value:#.3g}" for value in values)])

    plot_example(output / "example_angle.png", *example)
    results = [f"Distance-blocked rank permutation tests ({len(distances)} matched distances)",
               "Up to 9,999 permutations. Bonferroni adjustment across b and c."]
    for symbol, label, column in (("b", "Normalized damping b (1/s)", 4),
                                  ("c", "Rotational damping c (N m s/rad)", 5)):
        groups = np.array([row[column] for row in rows]).reshape(len(inertias), len(distances))
        plot_comparison(output / f"{symbol}_vs_bolt_count.png", groups, label)
        test = compare(groups)
        adjusted_p = min(1, 2 * test.pvalue)
        finding = "evidence of a bolt-count difference" if adjusted_p <= 0.05 else "no significant difference"
        results.append(f"{symbol}  rank statistic = {test.statistic:#.3g}, p = {test.pvalue:#.3g}, "
                       f"adjusted p = {adjusted_p:#.3g} ({finding})")

    summary = "\n".join(results) + "\n"
    (output / "statistics.txt").write_text(summary, encoding="ascii")
    emit(summary.rstrip())
    emit(f"Saved results in {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="?", default=RUN_FOLDER)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    analyze_experiment(args.runs, args.output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
