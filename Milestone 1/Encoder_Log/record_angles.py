"""Print/save encoder angles until Ctrl+C, then display a Matplotlib graph."""

import csv
from datetime import datetime
from pathlib import Path
import sys
import time

# Allows this computer's launcher to use a local copy of pySerial.
sys.path.insert(0, str(Path(__file__).parent / "_serial_deps"))
import serial
from serial.tools import list_ports

COUNTS_PER_DEGREE = 1.667


def record(source, csv_file, duration_s=None):
    writer = csv.writer(csv_file)
    writer.writerow(["time_s", "arduino_time_us", "encoder_counts", "angle_deg", "sample_gap_us", "time_source"])
    csv_file.flush()
    previous_us = None
    first_pc_ns = previous_pc_ns = None
    zero_count = elapsed_us = samples = largest_gap_us = 0
    started = time.monotonic()
    deadline = started + duration_s if duration_s is not None else None
    last_received = last_notice = started
    time_source = None
    rejected = 0
    pending = b""

    try:
        while deadline is None or time.monotonic() < deadline:
            pending += source.readline()
            if pending.endswith(b"\n"):
                line, pending = pending, b""
                try:
                    fields = line.decode("ascii").strip().split(",")
                    if len(fields) == 1:
                        counts = int(fields[0])
                        timestamp_us = None
                        row_time_source = "pc_receipt"
                    elif len(fields) == 2:
                        timestamp_us, counts = map(int, fields)
                        if not 0 <= timestamp_us <= 0xFFFFFFFF:
                            raise ValueError("Timestamp out of range")
                        row_time_source = "arduino"
                    else:
                        raise ValueError("Expected count or timestamp,count")
                except (ValueError, UnicodeDecodeError):
                    rejected += 1
                    if rejected <= 3:
                        print(f"Ignoring unrecognized serial line: {line[:100]!r}", flush=True)
                else:
                    last_received = time.monotonic()
                    pc_ns = time.monotonic_ns()
                    if samples == 0:
                        zero_count = counts
                        time_source = row_time_source
                        first_pc_ns = pc_ns
                        print(f"Receiving encoder counts. Timing: {time_source}. Starting count {zero_count} = 0 degrees.", flush=True)
                    elif row_time_source != time_source:
                        raise RuntimeError("Serial format changed. Start a new recording for the new sketch.")

                    if timestamp_us is None:
                        elapsed_us = (pc_ns - first_pc_ns) // 1000
                        gap_us = 0 if previous_pc_ns is None else (pc_ns - previous_pc_ns) // 1000
                        previous_pc_ns = pc_ns
                    else:
                        gap_us = 0 if previous_us is None else (timestamp_us - previous_us) & 0xFFFFFFFF
                        if previous_us is not None and timestamp_us < previous_us and previous_us - timestamp_us < 0x80000000:
                            raise RuntimeError("Arduino clock restarted. Start a new recording.")
                        elapsed_us += gap_us
                        previous_us = timestamp_us
                    largest_gap_us = max(largest_gap_us, gap_us)
                    angle = (counts - zero_count) / COUNTS_PER_DEGREE
                    writer.writerow([f"{elapsed_us / 1e6:.6f}", "" if timestamp_us is None else timestamp_us, counts, f"{angle:.4f}", gap_us, time_source])
                    samples += 1
                    csv_file.flush()
                    print(f"{elapsed_us / 1e6:9.3f} s | angle {angle:8.2f} deg | count {counts:7d} | saved {samples}", flush=True)
            now = time.monotonic()
            if now - last_received > 5 and now - last_notice > 5:
                print("Waiting for encoder readings; recording is still open. Check the Arduino port/sketch. Ctrl+C stops.", flush=True)
                last_notice = now
    except KeyboardInterrupt:
        pass
    finally:
        csv_file.flush()
        print(f"Saved {samples} samples. Largest observed gap: {largest_gap_us} us. Ignored lines: {rejected}.")
    return samples


def plot_recording(csv_path, show=True):
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if not rows:
        print("No samples to plot.")
        return None

    import matplotlib.pyplot as plt

    times = [float(row["time_s"]) for row in rows]
    angles = [float(row["angle_deg"]) for row in rows]
    timing = "PC receipt time" if rows[0].get("time_source") == "pc_receipt" else "Arduino time"
    figure, axes = plt.subplots(figsize=(10, 5), layout="constrained")
    axes.plot(times, angles, color="#1766ad", linewidth=1.3)
    axes.axhline(0, color="gray", linewidth=0.7)
    axes.set_title("Encoder angle vs time")
    axes.set_xlabel(f"Time (s) - {timing}")
    axes.set_ylabel("Angle relative to start (degrees)")
    axes.grid(True, alpha=0.25)

    if show:
        plt.show()
    plt.close(figure)
    return figure


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--plot":
        plot_recording(Path(sys.argv[2]))
        return

    for port in list_ports.comports():
        print(f"{port.device}: {port.description}")
    port_name = sys.argv[1] if len(sys.argv) > 1 else input("Arduino COM port: ").strip()
    print("Close Arduino Serial Monitor and Serial Plotter before recording.")
    input("Let the pendulum hang still, then press Enter to start. Ctrl+C saves and opens the graph. ")
    output_dir = Path(__file__).resolve().parent / "recordings"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"encoder_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"

    with serial.Serial(port_name, 115200, timeout=0.25) as source:
        time.sleep(2)
        source.reset_input_buffer()
        source.readline()  # Discard any partial line at the connection boundary.
        with output.open("x", newline="", encoding="utf-8") as csv_file:
            print(f"Recording to: {output}", flush=True)
            record(source, csv_file)

    plot_recording(output)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (serial.SerialException, OSError, RuntimeError) as error:
        print(f"Recording stopped: {error}", file=sys.stderr)
        sys.exit(1)
