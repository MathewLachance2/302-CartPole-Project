"""Arduino console: baseline returns automatically; 'track next' saves one CSV trial."""

## ! OPERATION
# Distance (mm) comamand
# ? Ex, 150 baseline
# baseline returns automatically; wait for the return result and # READY
# * track next
# * 150 quash/maintain/tune X
# * track off

import argparse
import csv
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import struct
import sys
import threading

try:
    import serial
except ImportError:
    raise SystemExit("Install pyserial first: python -m pip install pyserial")

DEFAULT_PORT = "COM8"
AUTO_RETURN_BASELINE = True      # Send XXX after a completed baseline if the board hasn't returned.
SAVE_FOLDER = Path(__file__).resolve().parent  # Save beside this Python file by default.
FIELDS = {
    "time_s", "phase", "cart_mm_est", "angle_deg", "encoder_counts",
    "gp_mm_s2_rad", "gd_mm_s_rad", "cart_kp_per_s2", "cart_kd_per_s",
}
MIN_FIELDS = {"time_s", "cart_mm_est", "angle_deg"}
TRIAL_FIELDS = ("distance_mm", "trial_mode", "initial_amplitude_deg",
                "requested_frequency_Hz", "stop_reason", "limit_events")
REJECTIONS = (
    "Distance must", "Run a baseline first", "Let the pendulum hang still",
    "Tune frequency must", "No valid recorded start", "Position estimate is outside",
    "Already at the recorded start", "No recorded samples", "Command discarded",
)


def console_printer():
    """Highlight warnings in terminals; keep redirected output plain text."""
    stream = sys.stdout
    color = stream.isatty()
    if color and os.name == "nt":
        try:
            import ctypes
            import msvcrt
            handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
            mode = ctypes.c_ulong()
            kernel = ctypes.windll.kernel32
            color = bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                         and kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except (AttributeError, OSError, ValueError):
            color = False

    def emit(message):
        if color and message.startswith("Warning:"):
            message = f"\033[93m{message}\033[0m"
        print(message, file=stream, flush=True)

    return emit


def command_error(command):
    """Catch commands for which the firmware would only print its help."""
    parts = command.split()
    name = parts[0]
    if name in ("XXX", "xxx", "csv", "status", "auto", "stop", "!"):
        return None if len(parts) == 1 else f"Use {name} without arguments."

    values = parts[1:]
    if name in ("amp", "center", "damping", "gains"):
        usage = {"amp": "amp 0|1", "center": "center 0|1",
                 "damping": "damping c_per_s (0 to 5)",
                 "gains": "gains gp gd (|gp| <= 10000, |gd| <= 1000)"}[name]
        expected = 2 if name == "gains" else 1
    elif len(parts) >= 2 and parts[1] in ("baseline", "quash", "maintain", "tune"):
        usage = "distance_mm mode, or distance_mm tune frequency_Hz"
        values = [parts[0], *parts[2:]]
        expected = 2 if parts[1] == "tune" else 1
    else:
        hint = " Use track next to arm recording, or track off to cancel." if name.lower() in ("track", "tracking") else ""
        return f"Invalid command: {command!r}.{hint} Type help for accepted commands."

    try:
        if len(values) != expected or any("_" in value for value in values):
            raise ValueError
        # Match the firmware's float32 parsing, including overflow and rounding.
        numbers = [struct.unpack("f", struct.pack("f", float(value)))[0] for value in values]
        if not all(math.isfinite(value) for value in numbers):
            raise ValueError
        if name in ("amp", "center") and numbers[0] not in (0, 1):
            raise ValueError
        if name == "damping" and not 0 <= numbers[0] <= 5:
            raise ValueError
        if name == "gains" and (abs(numbers[0]) > 10000 or abs(numbers[1]) > 1000):
            raise ValueError
    except (ValueError, OverflowError):
        return f"Invalid command: {command!r}. Use {usage}."
    return None


def command_stem(command):
    """Normalize equivalent numeric commands to the same filename/counter."""
    parts = command.split()
    def number(text):
        value = Decimal(text)
        return "0" if value.is_zero() else format(value.normalize(), "f")
    name = f"{number(parts[0])}_{parts[1]}"
    return name + "_" + number(parts[2]) if parts[1] == "tune" else name


class CaptureSession:
    """Shared console/receiver state. A selected trial cannot change mid-run."""

    def __init__(self, output, port, emit=print):
        self.output, self.port, self.emit = Path(output), port, emit
        if not self.output.is_dir():
            raise FileNotFoundError(f"Choose an existing CSV folder: {self.output}")
        self.lock = threading.Lock()
        self.command_lock = threading.RLock()  # Serialize user commands and automatic returns.
        self.armed, self.busy = False, True  # Wait for the initial firmware READY.
        self.pending = self.current = self.capture = None
        self.report_open = False
        self.arm_version = 0
        self.baseline_return_pending = self.automatic_return_queued = False

    def warn(self, message):
        self.emit(f"Warning: {message}")

    def submit(self, command, write):
        with self.command_lock:
            self._submit(command, write)

    def _submit(self, command, write):
        command = command.strip()
        if not command:
            return
        local = " ".join(command.lower().split())
        with self.lock:
            if local in ("track next", "start csv tracking"):
                if not self.armed:
                    self.arm_version += 1
                self.armed = True
                self.emit("Tracking armed: the next newly accepted trial will be saved.")
                return
            if local == "track off":
                self.armed = False
                if self.pending:
                    self.pending["selected"] = False
                self.emit("Future tracking cancelled; an already selected trial is unchanged.")
                return
            if local == "tracking":
                saving = bool(self.current and self.current["selected"])
                self.emit(f"Tracking armed={self.armed}; selected run={saving}; board busy={self.busy}")
                return
            if local in ("help", "?"):
                self.emit("Local: track next | start csv tracking | track off | tracking")
                self.emit(f"Baseline auto-return: {'ON' if AUTO_RETURN_BASELINE else 'OFF'} (Python fallback).")
                self.emit("Arduino: 49 baseline | XXX | 49 quash | 49 maintain | 49 tune 2.0 | csv | status | stop | !")
                self.emit("Settings: amp 0|1 | center 0|1 | damping 0..5 | gains gp gd | auto")
                return
            if not command.isascii() or any(ord(char) < 32 and char != "\t" for char in command):
                self.warn("Use plain ASCII Arduino commands, one command per line.")
                return
            error = command_error(command)
            if error:
                self.warn(error)
                return
            if command in ("stop", "!"):
                self.baseline_return_pending = self.automatic_return_queued = False
                for trial in (self.pending, self.current):
                    if trial:
                        trial["cancel_auto_return"] = True
            if command not in ("stop", "!"):
                if self.busy:
                    self.warn("Board busy: wait for # READY. Commands are not queued.")
                    return
                parts = command.split()
                if len(parts) >= 2 and parts[1] in ("baseline", "quash", "maintain", "tune"):
                    self.pending = {
                        "command": command, "mode": parts[1], "selected": self.armed,
                        "arm_version": self.arm_version,
                    }
                self.busy = True
        # Publish pending state before writing; an acknowledgement can arrive immediately.
        try:
            write((command + "\n").encode("ascii"))
        except UnicodeEncodeError:
            with self.lock:
                self.pending, self.busy = None, False
            self.warn("Use plain ASCII Arduino commands.")
        except (serial.SerialException, OSError):
            with self.lock:
                self.pending, self.busy = None, False
            raise

    def send_automatic_return(self, write):
        """Called by the receiver after READY, outside the state lock."""
        with self.command_lock:
            with self.lock:
                if not self.automatic_return_queued:
                    return
                self.automatic_return_queued = False
            self.emit("# Baseline complete; Python is returning the cart to its recorded start.")
            try:
                write(b"XXX\n")
            except (serial.SerialException, OSError):
                with self.lock:
                    self.busy = False
                raise

    def _new_capture(self):
        stem = command_stem(self.current["command"])
        used = [0]
        for path in self.output.glob(stem + "_*.csv"):
            suffix = path.name[len(stem) + 1:].removesuffix(".csv").removesuffix(".partial")
            if suffix.isdigit():
                used.append(int(suffix))
        counter = max(used) + 1
        while True:
            final = self.output / f"{stem}_{counter}.csv"
            partial = self.output / f"{stem}_{counter}.partial.csv"
            if final.exists():
                counter += 1
                continue
            try:
                stream = partial.open("x", encoding="utf-8", newline="")
            except FileExistsError:
                counter += 1
                continue
            if final.exists():
                stream.close()
                partial.unlink()  # Only the empty file just created by this capture.
                counter += 1
                continue
            break
        self.capture = {
            "partial": partial, "final": final, "file": stream,
            "writer": csv.writer(stream), "extra_fields": [],
            "command": self.current["command"], "mode": self.current["mode"],
            "metadata": {}, "header": None, "received_columns": None, "rows": 0, "last_time": None,
            "error": None,
        }
        self.emit(f"Tracking {self.current['command']}; CSV will save as {final}")

    def _finish_capture(self, complete=False, error=None):
        if not self.capture:
            return
        capture = self.capture
        complete = bool(complete and capture["header"] and capture["rows"] and not capture["error"])
        error = capture["error"] or error
        if not complete and not error:
            error = "Report had no complete full-data CSV"
        capture["file"].close()
        path = capture["partial"]
        if complete:
            try:
                if capture["final"].exists():
                    raise FileExistsError("The final CSV already exists")
                path = capture["partial"].rename(capture["final"])
            except OSError as failure:
                complete, error = False, str(failure)
        if not capture["rows"]:
            path.unlink()
            self.emit("No CSV samples received; no file saved.")
        else:
            self.emit(f"{'Saved' if complete else 'Kept incomplete capture'}: {path}")
        if not complete:
            self.emit(f"Capture incomplete: {error}")
            self.emit(f"Received CSV columns: {capture['received_columns'] or []}")
            missing = sorted(MIN_FIELDS - set(capture["received_columns"] or []))
            if missing:
                self.emit(f"Missing required CSV columns: {', '.join(missing)}")
        self.capture = None

    def receive_line(self, line):
        original = line.rstrip("\r\n")
        text = original.strip()
        with self.lock:
            if text.startswith("#"):
                message = text[1:].strip()
                if message.startswith(REJECTIONS):
                    self.warn(text)
                elif message != "READY" or not self.baseline_return_pending:
                    self.emit(text)
                if message.startswith("RUNNING"):
                    self.baseline_return_pending = self.automatic_return_queued = False
                    self._finish_capture(error="A new trial started before the previous report finished")
                    self.current = self.pending or {"command": "unknown", "mode": "unknown", "selected": False}
                    self.pending, self.busy = None, True
                    if self.current["selected"]:
                        if self.current["arm_version"] == self.arm_version:
                            self.armed = False
                        self._new_capture()
                elif message == "BEGIN":
                    if self.report_open:
                        self._finish_capture(error="New report arrived before END")
                        self.current = None
                    self.report_open = True
                elif message == "END":
                    self.baseline_return_pending = bool(
                        AUTO_RETURN_BASELINE and self.report_open and self.current
                        and self.current["mode"] == "baseline"
                        and self.current.get("stop_reason") == "complete"
                        and not self.current.get("cancel_auto_return")
                    )
                    self._finish_capture(complete=self.report_open,
                                         error=None if self.report_open else "END arrived before BEGIN")
                    self.report_open, self.current = False, None
                elif message == "READY":
                    if self.current:
                        self._finish_capture(error="READY arrived before the report finished")
                        self.current, self.report_open = None, False
                    # A delayed startup/status READY must not cancel a submitted trial.
                    if self.baseline_return_pending:
                        self.baseline_return_pending = False
                        self.automatic_return_queued = True
                        self.busy = True
                    elif self.pending is None:
                        self.busy = False
                elif message.startswith(("Returning", "Return:")):
                    # Native firmware may return between END and READY; do not duplicate it.
                    self.baseline_return_pending = self.automatic_return_queued = False
                elif message.startswith(REJECTIONS):
                    if self.pending is not None:
                        self.warn("Trial rejected; tracking remains armed if previously enabled.")
                    self.pending, self.busy = None, False
                    self.baseline_return_pending = self.automatic_return_queued = False
                elif self.report_open and "=" in message:
                    key, value = message.split("=", 1)
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                    key = key.strip()
                    if self.current and key == "stop_reason":
                        self.current["stop_reason"] = value
                    if self.capture:
                        self.capture["metadata"][key] = value
                return

            if not text or not self.report_open or not self.capture:
                return
            capture = self.capture
            try:
                fields = next(csv.reader([original]))
            except csv.Error as error:
                capture["error"] = str(error)
                return
            if capture["received_columns"] is None:
                capture["received_columns"] = fields
                missing = sorted(MIN_FIELDS - set(fields))
                if missing:
                    capture["error"] = f"Missing required CSV columns: {', '.join(missing)}; received header: {fields!r}"
                    return
                capture["header"] = fields
                capture["extra_fields"] = [name for name in TRIAL_FIELDS if name not in fields]
                capture["writer"].writerow(fields + capture["extra_fields"])
                optional_missing = sorted((FIELDS - MIN_FIELDS) - set(fields))
                if optional_missing:
                    self.emit(f"Firmware omitted optional CSV columns: {', '.join(optional_missing)}. Saving the received columns without inventing values.")
                return
            else:
                if capture["header"] is None:
                    return
                try:
                    if len(fields) != len(capture["header"]):
                        raise ValueError("CSV column count changed")
                    time_s = float(fields[capture["header"].index("time_s")])
                    if not math.isfinite(time_s) or time_s < 0 or (capture["last_time"] is not None and time_s <= capture["last_time"]):
                        raise ValueError("Invalid or non-increasing firmware timestamp")
                    capture["last_time"] = time_s
                except (ValueError, IndexError) as error:
                    capture["error"] = str(error)
                    return
            if not capture["error"]:
                parts = capture["command"].split()
                values = {**capture["metadata"], "distance_mm": parts[0], "trial_mode": capture["mode"]}
                if capture["mode"] == "tune":
                    values.setdefault("requested_frequency_Hz", parts[2])
                capture["writer"].writerow(fields + [values.get(name, "") for name in capture["extra_fields"]])
                capture["rows"] += 1

    def close(self):
        with self.command_lock, self.lock:
            self.baseline_return_pending = self.automatic_return_queued = False
            self._finish_capture(error="Connection closed before a complete report")
            self.pending = self.current = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", help="Arduino port; prompts when omitted")
    parser.add_argument("--output", type=Path, default=SAVE_FOLDER)
    args = parser.parse_args()
    try:
        port = (args.port or input(f"COM port [{DEFAULT_PORT}]: ")).strip() or DEFAULT_PORT
        port = "COM" + port if port.isdigit() else port.upper() if port.lower().startswith("com") else port
        args.output = args.output.expanduser().resolve()
        if not args.output.is_dir():
            raise FileNotFoundError(f"Choose an existing CSV folder: {args.output}")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return
    except OSError as error:
        print(f"Cannot use the save folder: {error}")
        return
    try:
        connection = serial.Serial(port, 115200, timeout=0.25, write_timeout=1)
    except (serial.SerialException, OSError) as error:
        print(f"Cannot open {port}: {error}\nClose Arduino Serial Monitor and check the port.")
        return
    stopped = threading.Event()
    session = CaptureSession(args.output, port, emit=console_printer())
    with connection:
        def receive():
            pending = b""
            try:
                while not stopped.is_set():
                    pending += connection.readline()
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        session.receive_line(line.decode("utf-8", errors="replace"))
                        session.send_automatic_return(connection.write)
            except (serial.SerialException, OSError) as error:
                print(f"Serial capture stopped: {error}. Press Enter to exit.", flush=True)
                stopped.set()
            finally:
                session.close()
        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        print(f"Connected to {port}. CSV folder: {args.output}")
        print(f"Baseline auto-return: {'ON' if AUTO_RETURN_BASELINE else 'OFF'} (Python fallback).")
        print("Saving is OFF. Use track next before a trial you want to save; help lists commands.")
        print("Wait for # READY. Ctrl+C sends ! and exits.")
        try:
            connection.write(b"status\n")  # Read readiness/calibration state; never starts motion.
            while not stopped.is_set():
                command = input("> ")
                if not stopped.is_set():
                    session.submit(command, connection.write)
        except (KeyboardInterrupt, EOFError):
            try:
                connection.write(b"!\n")
            except (serial.SerialException, OSError):
                pass
        except (serial.SerialException, OSError) as error:
            session.warn(f"Serial command failed: {error}")
        finally:
            stopped.set()
            receiver.join(timeout=1)
            session.close()


if __name__ == "__main__":
    main()
