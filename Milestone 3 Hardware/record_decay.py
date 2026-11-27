"""Record a Milestone 3 batch for one installed bolt configuration."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic, sleep

from decay_settings import RunSettings


CONFIGURATIONS = {1: "one centre bolt", 2: "two outer bolts", 3: "all three bolts"}
DEFAULT_SETTINGS = RunSettings()
RUN_FOLDER = Path(__file__).resolve().parent / "decay_runs"
CSV_HEADER = "time_s,phase,cart_mm_est,angle_deg,encoder_counts"
SETTINGS_FILE = "experiment.json"
BATCH_LOG_FILE = "batches.jsonl"
MAX_TRACK_STEPS = 12000  # Firmware limit for track length times steps per mm.


class CollectionCancelled(Exception):
    pass


def run_name(bolts, distance):
    return f"B{bolts}_D{distance:03d}.csv"


def check_cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise CollectionCancelled("Collection cancelled. Check the cart's position before resuming.")


def command(board, text, timeout=20, cancel=None):
    check_cancelled(cancel)
    board.write((text + "\n").encode("ascii"))
    deadline = monotonic() + timeout
    report = []
    while monotonic() < deadline:
        check_cancelled(cancel)
        line = board.readline().decode("ascii").strip()
        if line == "# READY":
            return report
        if line:
            report.append(line)
    raise RuntimeError(f"No READY response to {text!r}. Last lines: {report[-3:]}")


def capture(board, distance, settings=DEFAULT_SETTINGS, cancel=None):
    timeout = max(settings.free_s + 20, 1.5 * settings.sample_ms + 10)
    settle_deadline = monotonic() + 30
    while True:
        report = command(board, f"{distance} decay", timeout=timeout, cancel=cancel)
        if not any(line.startswith("# Let the pendulum hang still") for line in report):
            break
        if monotonic() >= settle_deadline:
            raise RuntimeError("The pendulum did not settle within 30 seconds.")
        sleep(0.5)

    if "# BEGIN" not in report:
        raise RuntimeError("Board declined the trial. " + " ".join(report))
    if "# stop_reason=complete" not in report or "# missed_samples=0" not in report:
        details = [line for line in report if line.startswith(("# stop_reason", "# missed_samples"))]
        raise RuntimeError("Trial interrupted. " + " ".join(details))
    if CSV_HEADER not in report or "# END" not in report:
        raise RuntimeError("The board sent an incomplete CSV report.")
    rows = report[report.index(CSV_HEADER):report.index("# END")]
    expected = int(1000 * settings.free_s / settings.sample_ms)
    if sum(",free," in row for row in rows[1:]) < max(1, expected - 1):
        raise RuntimeError("The board sent too few free-oscillation samples.")
    return rows


def record_batch(board, trials, settings=DEFAULT_SETTINGS, emit=print, cancel=None):
    return_timeout = 20
    if settings.brake_return:
        # Braking from peak speed averages half that speed for the last fifth.
        return_timeout = max(return_timeout, 1.2 * MAX_TRACK_STEPS / settings.speed + 10)
    for distance, destination in trials:
        emit(f"Recording {destination.name} ({distance:+d} mm)")
        rows = capture(board, distance, settings, cancel)
        partial = destination.with_suffix(".partial")
        partial.write_text("\n".join(rows) + "\n", encoding="ascii")
        partial.rename(destination)
        emit(f"Saved {destination}")
        if "# Return: return complete" not in command(board, "XXX", timeout=return_timeout, cancel=cancel):
            raise RuntimeError("Return failed. Check the cart's position before resuming.")


def remaining_trials(output, bolts, direction, settings):
    trials = []
    for distance in settings.distances:
        path = output / run_name(bolts, distance)
        if not path.is_file():
            trials.append((direction * distance, path))
    return trials


def read_run_settings(folder):
    path = folder / SETTINGS_FILE
    if not path.exists():
        return DEFAULT_SETTINGS
    return RunSettings(**json.loads(path.read_text(encoding="utf-8"))["run"])


def collect_batch(board, output, bolts, direction, settings=DEFAULT_SETTINGS, emit=print, cancel=None):
    path = output / SETTINGS_FILE
    profile = {"run": settings.outbound_settings(), "direction": direction}
    has_runs = any(output.glob("B[123]_D[0-9][0-9][0-9].csv"))
    if has_runs:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "run": DEFAULT_SETTINGS.outbound_settings(), "direction": direction,
        }
        if previous != profile:
            raise ValueError("This folder contains runs with different settings. Choose a new CSV folder.")

    trials = remaining_trials(output, bolts, direction, settings)
    if not trials:
        emit(f"All {len(settings.distances)} runs for {CONFIGURATIONS[bolts]} already exist.")
        return

    report = command(board, settings.board_command(), cancel=cancel)
    if "# Decay settings applied" not in report:
        raise ValueError("Board declined the experiment settings. " + " ".join(report))

    output.mkdir(parents=True, exist_ok=True)
    if not has_runs or not path.exists():
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        pending.replace(path)

    return_mode = "Brake over the last 20% of the return" if settings.brake_return else "Standard return"
    batch = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "bolts": bolts,
        "return_mode": return_mode,
        "planned_runs": [destination.name for _, destination in trials],
    }
    with (output / BATCH_LOG_FILE).open("a", encoding="utf-8") as log:
        log.write(json.dumps(batch) + "\n")
    emit(f"Return mode: {return_mode}")
    record_batch(board, trials, settings, emit, cancel)
    emit(f"Finished the batch for {CONFIGURATIONS[bolts]}.")


def open_board(port):
    import serial

    board = serial.Serial(port, 115200, timeout=0.25, write_timeout=1)
    try:
        sleep(1)
        board.reset_input_buffer()
        if "# MS3 DECAY RETURN BRAKING" not in command(board, "status"):
            raise RuntimeError("Upload the updated Milestone 3 MS3_MK1 sketch before collecting data.")
    except BaseException:
        board.close()
        raise
    return board


def stop_board(board):
    try:
        board.write(b"!\n")
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="Arduino serial port, for example COM8")
    parser.add_argument("bolts", type=int, choices=CONFIGURATIONS)
    parser.add_argument("--direction", required=True, type=int, choices=(-1, 1),
                        help="sign of motor travel away from the starting end")
    parser.add_argument("--output", type=Path, default=RUN_FOLDER)
    args = parser.parse_args()

    trials = remaining_trials(args.output, args.bolts, args.direction, DEFAULT_SETTINGS)
    if not trials:
        print("All 21 runs already exist.")
        return

    print(f"Install {CONFIGURATIONS[args.bolts]} and leave all three nuts in place.")
    print(f"Use the marked start with at least 70 mm plus clearance in direction "
          f"{args.direction:+d}. Let the pendulum hang still.")
    input(f"Press Enter to collect the {len(trials)} remaining runs, or Ctrl+C to cancel. ")

    with open_board(args.port) as board:
        try:
            collect_batch(board, args.output, args.bolts, args.direction)
        except (KeyboardInterrupt, OSError, RuntimeError):
            stop_board(board)
            raise


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            from decay_gui import main as gui_main
            gui_main()
        else:
            main()
    except KeyboardInterrupt:
        raise SystemExit("Collection cancelled. Check the cart's position before resuming.")
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
