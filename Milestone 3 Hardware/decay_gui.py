"""Desktop controls for collecting and analyzing Milestone 3 runs."""

from dataclasses import fields
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cartpole_widgets import append_text, field, text_box
from decay_settings import AnalysisSettings, RunSettings
from record_decay import (CONFIGURATIONS, RUN_FOLDER, CollectionCancelled, collect_batch,
                          open_board, stop_board)


PLOTS = {
    "Example angle trace": "example_angle.png",
    "Normalized damping b": "b_vs_bolt_count.png",
    "Rotational damping c": "c_vs_bolt_count.png",
}


class DecayApp:
    def __init__(self, root):
        self.root = root
        self.board = None
        self.job = None
        self.cancel = None
        self.closing = False
        self.events = Queue()

        root.title("MECH 302 · Milestone 3")
        root.geometry("860x690")
        root.minsize(750, 600)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Milestone 3 pendulum damping",
                  font=("Segoe UI", 19, "bold")).pack(anchor="w", pady=(0, 10))

        connection = ttk.Frame(frame)
        connection.pack(fill="x")
        self.port = tk.StringVar()
        self.status = tk.StringVar()
        ttk.Label(connection, text="Serial port").pack(side="left", padx=(0, 8))
        self.port_box = ttk.Combobox(connection, textvariable=self.port, width=12)
        self.port_box.pack(side="left")
        self.refresh_button = ttk.Button(connection, text="Refresh", command=self.refresh_ports)
        self.refresh_button.pack(side="left", padx=5)
        self.connect_button = ttk.Button(connection, command=self.toggle_connection)
        self.connect_button.pack(side="left")
        ttk.Label(connection, textvariable=self.status).pack(side="left", padx=12)

        storage = ttk.Frame(frame)
        storage.pack(fill="x", pady=12)
        self.folder = tk.StringVar(value=str(RUN_FOLDER))
        ttk.Label(storage, text="CSV folder").pack(side="left", padx=(0, 8))
        ttk.Entry(storage, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(storage, text="Browse…", command=self.choose_folder).pack(side="left", padx=(6, 0))

        tabs = ttk.Notebook(frame)
        tabs.pack(fill="x")
        experiment = ttk.Frame(tabs, padding=12)
        analysis = ttk.Frame(tabs, padding=12)
        tabs.add(experiment, text="Experiment")
        tabs.add(analysis, text="Analysis")
        self.build_experiment(experiment)
        self.build_analysis(analysis)

        activity = ttk.Frame(frame)
        activity.pack(fill="both", expand=True, pady=(12, 0))
        ttk.Label(activity, text="Status and results").pack(anchor="w")
        self.log = text_box(activity, 8)
        self.log.tag_configure("warning", foreground="#a72b39")
        self.refresh_ports()
        self.update_controls()
        root.after(50, self.poll)

    def settings_fields(self, parent, defaults, labels, start=0):
        values = {}
        for row, (name, label) in enumerate(labels, start=start):
            value = getattr(defaults, name)
            values[name] = tk.StringVar(value=f"{value:g}")
            field(parent, row, label, values[name], width=12)
        return values

    def build_experiment(self, panel):
        batch = ttk.LabelFrame(panel, text="Batch", padding=8)
        motor = ttk.LabelFrame(panel, text="Motion and timing", padding=8)
        batch.pack(side="left", anchor="n", fill="x", expand=True, padx=(0, 10))
        motor.pack(side="left", anchor="n", fill="x", expand=True)
        batch.columnconfigure(1, weight=1)
        motor.columnconfigure(1, weight=1)
        bolt_choices = [f"{count} · {name}" for count, name in CONFIGURATIONS.items()]
        self.bolts = tk.StringVar(value=bolt_choices[0])
        self.direction = tk.StringVar(value="+1")
        field(batch, 0, "Bolts installed", self.bolts, bolt_choices, width=21)
        field(batch, 1, "Travel direction", self.direction, ("+1", "-1"))
        defaults = RunSettings()
        self.run_fields = self.settings_fields(batch, defaults, (
            ("first_mm", "First distance (mm)"),
            ("last_mm", "Last distance (mm)"),
            ("step_mm", "Distance step (mm)"),
        ), start=2)
        self.run_fields.update(self.settings_fields(motor, defaults, (
            ("speed", "Speed (steps/s)"),
            ("acceleration", "Acceleration (steps/s²)"),
            ("sample_ms", "Sample interval (ms)"),
            ("free_s", "Free oscillation (s)"),
            ("settle_s", "Settling time (s)"),
        )))
        self.run_fields["brake_return"] = tk.BooleanVar(value=defaults.brake_return)
        self.brake_return_checkbox = ttk.Checkbutton(
            motor, text="Brake over the last 20% of the return",
            variable=self.run_fields["brake_return"],
        )
        self.brake_return_checkbox.grid(row=5, columnspan=2, sticky="w", pady=(8, 0))
        actions = ttk.Frame(batch)
        actions.grid(row=5, columnspan=2, sticky="w", pady=(8, 0))
        self.run_button = ttk.Button(actions, text="Run experiment", command=self.run_experiment)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop)
        self.stop_button.pack(side="left", padx=6)

    def build_analysis(self, panel):
        settings = ttk.Frame(panel)
        settings.pack(anchor="w")
        self.analysis_fields = self.settings_fields(settings, AnalysisSettings(), (
            ("inertia_1", "Pivot inertia, 1 bolt (kg·m²)"),
            ("inertia_2", "Pivot inertia, 2 bolts (kg·m²)"),
            ("inertia_3", "Pivot inertia, 3 bolts (kg·m²)"),
            ("prominence_deg", "Peak prominence (°)"),
            ("peak_spacing_s", "Minimum peak spacing (s)"),
        ))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(8, 0))
        self.analyze_button = ttk.Button(actions, text="Analyze runs", command=self.analyze)
        self.analyze_button.pack(side="left")
        self.plot = tk.StringVar(value=next(iter(PLOTS)))
        ttk.Combobox(actions, textvariable=self.plot, values=list(PLOTS), state="readonly",
                     width=22).pack(side="left", padx=(12, 4))
        ttk.Button(actions, text="View plot", command=self.show_plot).pack(side="left")
        ttk.Button(actions, text="Open results folder", command=self.show_results).pack(side="left", padx=5)

    def read_settings(self, kind, inputs):
        values = {}
        for field in fields(kind):
            try:
                values[field.name] = field.type(inputs[field.name].get())
            except ValueError:
                raise ValueError(f"Enter a valid number for {field.name.replace('_', ' ')}.") from None
        return kind(**values)

    def selected_folder(self):
        if not self.folder.get().strip():
            raise ValueError("Choose a CSV folder.")
        return Path(self.folder.get().strip()).expanduser().resolve()

    def choose_folder(self):
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self.folder.get())
        if chosen:
            self.folder.set(chosen)

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
        except ImportError:
            self.append("Warning: Install the packages in requirements.txt to list and connect to ports.")
            return
        ports = sorted(list_ports.comports())
        self.port_box["values"] = [port.device for port in ports]
        usb_ports = [port.device for port in ports if port.vid is not None]
        if not self.port.get() and len(usb_ports) == 1:
            self.port.set(usb_ports[0])

    def update_controls(self):
        idle = self.job is None
        connected = self.board is not None
        self.status.set(self.job or (f"Connected to {self.board.port}" if connected else "Disconnected"))
        self.connect_button.configure(text="Disconnect" if connected else "Connect",
                                      state="normal" if idle else "disabled")
        port_state = "normal" if idle and not connected else "disabled"
        self.port_box.configure(state=port_state)
        self.refresh_button.configure(state=port_state)
        self.run_button.configure(state="normal" if idle and connected else "disabled")
        self.brake_return_checkbox.configure(state="disabled" if self.cancel is not None else "normal")
        self.stop_button.configure(state="normal" if self.cancel and not self.cancel.is_set() else "disabled")
        self.analyze_button.configure(state="normal" if idle else "disabled")

    def append(self, message):
        append_text(self.log, message)

    def error(self, message):
        self.append(f"Warning: {message}")

    def start_job(self, label, work, done=None):
        self.job = label
        self.update_controls()

        def worker():
            try:
                result = work(self.emit)
            except Exception as error:
                self.events.put(("done", done, None, str(error)))
            else:
                self.events.put(("done", done, result, None))

        Thread(target=worker, daemon=True).start()

    def emit(self, message):
        self.events.put(("log", message))

    def toggle_connection(self):
        if self.board is not None:
            self.board.close()
            self.board = None
            self.append("Disconnected.")
            self.update_controls()
            return
        port = self.port.get().strip()
        if not port:
            self.error("Select the Arduino's serial port.")
            return
        self.append(f"Connecting to {port}…")
        self.start_job("Connecting…", lambda emit: open_board(port), self.connected)

    def connected(self, board):
        self.board = board
        self.append(f"Connected to {board.port}. Board ready.")

    def run_experiment(self):
        try:
            settings = self.read_settings(RunSettings, self.run_fields)
            output = self.selected_folder()
        except (OSError, ValueError) as error:
            self.error(error)
            return
        bolts = int(self.bolts.get()[0])
        direction = int(self.direction.get())
        if not messagebox.askokcancel(
            "Start batch",
            f"Install {CONFIGURATIONS[bolts]} and leave all three nuts in place.\n\n"
            f"At the marked start, confirm {settings.last_mm} mm plus clearance in direction "
            f"{direction:+d}. Let the pendulum hang still.",
            parent=self.root,
        ):
            return
        board = self.board
        cancel = self.cancel = Event()
        self.append(f"Starting batch for {CONFIGURATIONS[bolts]} in {output}")

        def collect(emit):
            try:
                collect_batch(board, output, bolts, direction, settings, emit, cancel)
            except (CollectionCancelled, OSError, RuntimeError, UnicodeError):
                if cancel.is_set():
                    emit("Stopping and closing the serial connection.")
                stop_board(board)
                board.close()
                raise

        self.start_job("Collecting runs…", collect)

    def stop(self):
        self.cancel.set()
        self.update_controls()
        self.status.set("Stopping…")

    def analyze(self):
        try:
            settings = self.read_settings(AnalysisSettings, self.analysis_fields)
            folder = self.selected_folder()
        except (OSError, ValueError) as error:
            self.error(error)
            return

        def analyze_runs(emit):
            from analyze_decay import analyze_experiment

            analyze_experiment(folder, analysis=settings, emit=emit)

        self.append(f"Analyzing runs in {folder}")
        self.start_job("Analyzing…", analyze_runs)

    def show_plot(self):
        try:
            path = self.selected_folder() / "analysis" / PLOTS[self.plot.get()]
            if not path.is_file():
                raise ValueError("Analyze the runs to generate this plot.")
            picture = tk.PhotoImage(file=str(path))
        except (OSError, ValueError, tk.TclError) as error:
            self.error(error)
            return
        scale = max(1, (picture.width() - 1) // (self.root.winfo_screenwidth() - 80) + 1,
                    (picture.height() - 1) // (self.root.winfo_screenheight() - 100) + 1)
        window = tk.Toplevel(self.root)
        window.title(self.plot.get())
        window.picture = picture.subsample(scale) if scale > 1 else picture
        ttk.Label(window, image=window.picture).pack()

    def show_results(self):
        try:
            folder = self.selected_folder() / "analysis"
            if not folder.is_dir():
                raise ValueError("Analyze the runs to generate the results.")
            os.startfile(folder)
        except (OSError, ValueError) as error:
            self.error(error)

    def poll(self):
        while True:
            try:
                kind, *event = self.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self.append(*event)
                continue
            done, result, error = event
            self.job = self.cancel = None
            if self.board is not None and not self.board.is_open:
                self.board = None
            if error is not None:
                self.error(error)
            elif done is not None:
                done(result)
            if self.closing:
                self.close()
                return
            self.update_controls()
        self.root.after(50, self.poll)

    def close(self):
        if self.job is not None:
            self.closing = True
            if self.cancel is not None:
                self.stop()
            return
        if self.board is not None:
            self.board.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    DecayApp(root)
    root.mainloop()
