"""Background serial connection for the desktop app."""

import csv
import io
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from csv_code_MK2 import CaptureSession, REJECTIONS


class DesktopConnection:
    def __init__(self, serial_factory, port, folder, events):
        self.events = events
        self.serial_factory = serial_factory
        self.commands = Queue()
        self.closing = Event()
        self.emergency = Event()
        self.port, self.folder = port, folder
        self.replay_path = self.replay = None
        Thread(target=self.run, daemon=True).start()

    def log(self, line):
        self.events.put(("log", line))

    def send(self, command, argument=None):
        if command == "!":
            self.emergency.set()
        else:
            self.commands.put((command, argument))

    def close(self):
        self.closing.set()

    def publish(self, session):
        state = session.busy, session.armed, bool(session.current and session.current["selected"]), session.status.copy()
        self.events.put(("state", state))
        return state

    def receive(self, line, session):
        if self.replay_path is not None:
            if line.strip() == "# BEGIN":
                self.replay = io.StringIO(newline="")
            elif line.strip() == "# END":
                path, self.replay_path = self.replay_path, None
                replay, self.replay = self.replay, None
                if replay is not None:
                    rows = csv.reader(io.StringIO(replay.getvalue()))
                    if next(rows, []) == ["time_s", "encoder_counts", "angle_deg"] and next(rows, []):
                        try:
                            path.write_text(replay.getvalue(), encoding="utf-8")
                            self.log(f"Exported encoder CSV: {path}")
                        except OSError as error:
                            self.log(f"Warning: Could not export encoder CSV. {error}")
                    else:
                        self.log("Warning: The board did not return an encoder CSV.")
            elif line.startswith("#") and line[1:].strip().startswith(REJECTIONS):
                self.replay_path = self.replay = None
            elif self.replay is not None and line.strip() and not line.startswith("#"):
                self.replay.write(line + "\n")
        session.receive_line(line)

    def run(self):
        session = None
        try:
            session = CaptureSession(self.folder, self.port, emit=self.log)
            with self.serial_factory(self.port, 115200, timeout=0.08, write_timeout=0.5) as wire:
                self.log(f"Connected to {self.port}. CSV folder: {self.folder}")
                self.events.put(("connected", None))
                pending = b""
                status_probe = monotonic() + 0.7
                published = None
                while not self.closing.is_set():
                    if self.emergency.is_set():
                        self.emergency.clear()
                        session.submit("!", wire.write)
                        # Discard any click that had yet to reach the board.
                        while True:
                            try:
                                self.commands.get_nowait()
                            except Empty:
                                break
                        published = self.publish(session)
                    else:
                        try:
                            command, argument = self.commands.get_nowait()
                        except Empty:
                            pass
                        else:
                            if command == "output":
                                session.output = Path(argument)
                                self.log(f"CSV folder: {session.output}")
                            else:
                                if command == "csv" and not session.busy:
                                    self.replay_path = Path(argument)
                                    self.replay = None
                                session.submit(command, wire.write)
                                published = self.publish(session)
                    pending += wire.readline()
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        self.receive(line.decode("utf-8", errors="replace").rstrip("\r"), session)
                    if not self.closing.is_set() and not self.emergency.is_set():
                        session.send_automatic_return(wire.write)
                    if status_probe and monotonic() >= status_probe:
                        if session.busy:
                            wire.write(b"status\n")
                        status_probe = None
                    # Wait for READY to publish a full configuration response.
                    flags = session.busy, session.armed, bool(session.current and session.current["selected"])
                    if not published or flags != published[:3] or (not session.busy and session.status != published[3]):
                        published = self.publish(session)
                if session.busy:
                    session.submit("!", wire.write)
        except (OSError, ValueError) as error:
            self.log(f"Warning: Serial connection closed. {error}")
        finally:
            if session is not None:
                try:
                    session.close()
                except OSError as error:
                    self.log(f"Warning: Could not finish CSV capture. {error}")
            self.events.put(("disconnected", None))
