"""Tk desktop interface for the MECH 302 CartPole."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
from importlib.util import find_spec
import io
import math
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import csv_code_MK2 as recorder
from cartpole_connection import DesktopConnection
from cartpole_settings import DEFAULTS, GROUPS, SETTINGS, parse_settings
from cartpole_widgets import append_text, field, text_box


MODES = ("Baseline", "Quash", "Maintain", "Tune")
CSV_FILES = [("CSV files", "*.csv")]
REQUIREMENTS = Path(__file__).with_name("requirements.txt")


def numeric(text):
    if "_" in text:
        raise ValueError
    value = float(text)
    if not math.isfinite(value):
        raise ValueError
    return value


class CartPoleApp:
    def __init__(self, root):
        self.root = root
        self.events = Queue()
        self.connection = None
        self.connected = self.disconnecting = self.closing = self.installing = False
        self.busy, self.armed, self.capturing = True, False, False
        self.active_settings = None
        self.port = tk.StringVar(value=recorder.DEFAULT_PORT)
        self.folder = tk.StringVar(value=str(recorder.SAVE_FOLDER))
        self.connection_status = tk.StringVar(value="Disconnected")
        self.model_status = tk.StringVar(value="Connect to read the board calibration.")
        self.recording_status = tk.StringVar(value="Recording off")
        self.settings_status = tk.StringVar(value="Connect to read settings from the board.")
        self.port_controls = []
        self.idle_controls = []
        self.board_controls = []
        self.config_controls = []
        self.setting_vars = {}

        root.title("MECH 302 CartPole")
        root.geometry("1020x790")
        root.minsize(780, 745)
        root.configure(background="#edf2f6")
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#edf2f6")
        style.configure("TLabel", background="#edf2f6", foreground="#183047")
        style.configure("TButton", padding=(10, 5))
        style.configure("TNotebook.Tab", padding=(15, 7))
        style.configure("Primary.TButton", background="#14685e", foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#dce3e7"), ("active", "#0e544c")],
                  foreground=[("disabled", "#7a858b")])
        style.configure("Stop.TButton", background="#a72b39", foreground="white")
        style.map("Stop.TButton", background=[("disabled", "#dce3e7"), ("active", "#84212e")],
                  foreground=[("disabled", "#7a858b")])

        frame = ttk.Frame(root, padding=(20, 12))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="MECH 302 CartPole", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        connection = ttk.Frame(frame, padding=(0, 8))
        connection.pack(fill="x")
        self.port_box = ttk.Combobox(connection, textvariable=self.port, width=12)
        self.port_box.pack(side="left")
        self.port_controls.append(self.port_box)
        self.button(connection, "Refresh ports", self.refresh_ports, self.port_controls).pack(side="left", padx=5)
        self.connect_button = self.button(connection, "Connect", self.toggle_connection, style="Primary.TButton")
        self.connect_button.pack(side="left")
        ttk.Label(connection, textvariable=self.connection_status, font=("Segoe UI", 10, "bold")).pack(side="left", padx=12)
        self.button(connection, "Emergency stop", lambda: self.send("!"), self.board_controls,
                    style="Stop.TButton").pack(side="right")

        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True, pady=(4, 8))
        self.build_trials(self.tab("Trials"))
        self.build_settings(self.tab("Board settings"))
        self.build_analysis(self.tab("Analyze CSV"))
        self.build_comparison(self.tab("Compare trials"))

        activity = ttk.Frame(frame)
        activity.pack(fill="x")
        ttk.Label(activity, text="Board and recording activity", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_box = self.text_box(activity, 7)
        self.log_box.tag_configure("warning", foreground="#a72b39")
        self.dependencies = self.button(frame, "Install Python packages", self.install_dependencies)
        self.refresh_ports()
        self.refresh_dependencies()
        self.update_controls()
        root.after(40, self.poll)

    def tab(self, label):
        panel = ttk.Frame(self.tabs, padding=(15, 12))
        self.tabs.add(panel, text=label)
        return panel

    @staticmethod
    def button(parent, label, callback, collection=None, **options):
        button = ttk.Button(parent, text=label, command=callback, **options)
        if collection is not None:
            collection.append(button)
        return button

    text_box = staticmethod(text_box)
    append = staticmethod(append_text)
    field = staticmethod(field)

    def file_field(self, parent, row, label, variable, directory=False, filetypes=CSV_FILES):
        self.field(parent, row, label, variable, width=45).configure(state="readonly" if directory else "normal")
        parent.columnconfigure(1, weight=1)

        def browse():
            path = (filedialog.askdirectory(parent=self.root, initialdir=self.folder.get())
                    if directory else filedialog.askopenfilename(parent=self.root, filetypes=filetypes,
                                                                 initialdir=self.folder.get()))
            if path:
                variable.set(path)
                if directory and self.connection and not self.disconnecting:
                    self.connection.send("output", path)

        return self.button(parent, "Browse…", browse)

    def build_trials(self, panel):
        rows = ttk.Frame(panel)
        rows.pack(fill="x", anchor="w")
        self.mode = tk.StringVar(value="Baseline")
        self.distance = tk.StringVar(value="49")
        self.frequency = tk.StringVar(value="2.0")
        mode = self.field(rows, 0, "Trial mode", self.mode, MODES)
        self.field(rows, 1, "Signed launch distance (mm)", self.distance)
        tune = self.field(rows, 2, "Tune frequency (Hz)", self.frequency)
        mode.bind("<<ComboboxSelected>>", lambda _: tune.configure(
            state="normal" if self.mode.get() == "Tune" else "disabled"))
        tune.configure(state="disabled")
        note = "Place the cart at the launch end and let the pendulum settle before starting."
        ttk.Label(panel, text=note, wraplength=680).pack(anchor="w", pady=(12, 5))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(0, 12))
        self.button(actions, "Start trial", self.start_trial, self.idle_controls,
                    style="Primary.TButton").pack(side="left")
        self.button(actions, "Return to start", lambda: self.send("XXX"), self.idle_controls).pack(side="left", padx=6)
        self.button(actions, "Stop motion", lambda: self.send("stop"), self.board_controls).pack(side="left")
        self.button(actions, "Read board status", lambda: self.send("status"), self.idle_controls).pack(side="left", padx=6)
        ttk.Separator(panel).pack(fill="x", pady=(0, 9))

        recording = ttk.Frame(panel)
        recording.pack(fill="x")
        self.folder_picker = self.file_field(recording, 0, "CSV save folder", self.folder, directory=True)
        self.folder_picker.grid(row=0, column=2, padx=(6, 0))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        self.button(actions, "Record next trial", lambda: self.send("track next"), self.board_controls).pack(side="left")
        self.button(actions, "Cancel recording", lambda: self.send("track off"), self.board_controls).pack(side="left", padx=6)
        self.button(actions, "Export last encoder CSV…", self.export_csv, self.idle_controls).pack(side="left")
        ttk.Label(panel, textvariable=self.recording_status, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(panel, textvariable=self.model_status).pack(anchor="w", pady=5)

    def build_settings(self, panel):
        ttk.Label(panel, textvariable=self.settings_status, wraplength=700).pack(anchor="w", pady=(0, 6))
        pages = ttk.Notebook(panel)
        pages.pack(fill="x")
        labels = {"manual": ("Automatic", "Manual"), "sign": ("-1", "1")}
        for name, fields in GROUPS.items():
            group = ttk.Frame(pages, padding=(12, 8))
            pages.add(group, text=name)
            for row, (key, item) in enumerate(fields.items()):
                column, row = divmod(row, 5)
                if row == 0:
                    fields_column = ttk.Frame(group)
                    fields_column.grid(row=0, column=column, sticky="nw", padx=(0, 18))
                variable = self.setting_vars[key] = tk.StringVar()
                choices = labels.get(key, ("Off", "On") if item.choices else ())
                self.field(fields_column, row, item.label, variable, choices, width=11)
        self.fill_settings(DEFAULTS)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=8)
        self.button(buttons, "Apply to board", self.apply_settings, self.config_controls,
                    style="Primary.TButton").pack(side="left")
        self.button(buttons, "Load board values", lambda: self.fill_settings(self.active_settings),
                    self.config_controls).pack(side="left", padx=6)
        self.button(buttons, "Load factory defaults", lambda: self.fill_settings(DEFAULTS)).pack(side="left")
        ttk.Label(panel, text="Values apply while the board is idle and reset when it restarts. "
                  "Set both calibration values to match the rig.", wraplength=710).pack(anchor="w")

        damping = ttk.Frame(panel)
        damping.pack(fill="x", pady=(12, 0))
        self.damping = tk.StringVar()
        self.field(damping, 0, "Known damping c (s⁻¹)", self.damping)
        self.button(damping, "Set damping", self.set_damping, self.idle_controls).grid(row=0, column=2, padx=6)
        ttk.Label(panel, textvariable=self.model_status).pack(anchor="w", pady=4)

    def fill_settings(self, settings):
        for key, value in settings.items():
            if key == "manual":
                display = "Manual" if value else "Automatic"
            elif SETTINGS[key].choices == (0, 1):
                display = "On" if value else "Off"
            else:
                display = f"{value:.9g}"
            self.setting_vars[key].set(display)

    def build_analysis(self, panel):
        self.analysis_file = tk.StringVar(value=str(recorder.SAVE_FOLDER / "200_maintain.csv"))
        row = ttk.Frame(panel)
        row.pack(fill="x")
        self.file_field(row, 0, "Trial CSV", self.analysis_file).grid(row=0, column=2, padx=(6, 0))
        self.analysis_meta = tk.StringVar()
        self.file_field(row, 1, "Trial metadata (optional)", self.analysis_meta,
                        filetypes=[("JSON files", "*.json")]).grid(row=1, column=2, padx=(6, 0))
        options = ttk.Frame(panel)
        options.pack(fill="x", pady=7)
        self.analysis_options = {}
        fields = (
            ("Start time (s)", "start", "0"), ("End time (s)", "end", ""),
            ("Phase", "phase", "control"), ("Peak prominence (°)", "prominence", "1.2"),
            ("Min. peak spacing (s)", "min-distance", "0.05"),
            ("Skip cycles", "skip-cycles", "3"), ("Min. steady cycles", "min-cycles", "5"),
            ("Steady CV (%)", "steady-cv", "5"), ("Steady drift (%)", "steady-drift", "5"),
            ("Target amplitude (°)", "target-amplitude", ""), ("Target frequency (Hz)", "target-frequency", ""),
        )
        for i, (label, key, value) in enumerate(fields):
            col, index = divmod(i, 4)
            if index == 0:
                group = ttk.Frame(options)
                group.grid(row=0, column=col, padx=(0, 12), sticky="nw")
            variable = self.analysis_options[key] = tk.StringVar(value=value)
            self.field(group, index, label, variable, ("control", "all", "launch", "hold", "stop") if key == "phase" else (), width=9)
        self.manual_window = tk.BooleanVar()
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        self.button(actions, "Analyze and plot", self.analyze, style="Primary.TButton").pack(side="left")
        ttk.Checkbutton(actions, text="Use the selected window manually", variable=self.manual_window).pack(side="left", padx=8)
        self.analysis_results = self.text_box(panel, 6)

    def build_comparison(self, panel):
        files = ttk.Frame(panel)
        files.pack(fill="x")
        self.comparison_files = {}
        defaults = ("200_baseline_1.csv", "200_quash.csv", "200_maintain_1.csv", "200_tune_3.csv")
        for row, (mode, name) in enumerate(zip(MODES, defaults)):
            var = self.comparison_files[mode.lower()] = tk.StringVar(value=str(recorder.SAVE_FOLDER / name))
            self.file_field(files, row, f"{mode} CSV", var).grid(row=row, column=2, padx=(6, 0))
        self.full_trial, self.normalize = tk.BooleanVar(), tk.BooleanVar()
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        self.button(actions, "Compare and plot", self.compare, style="Primary.TButton").pack(side="left")
        ttk.Checkbutton(actions, text="Show full trials", variable=self.full_trial).pack(side="left", padx=8)
        ttk.Checkbutton(actions, text="Normalize by initial amplitude", variable=self.normalize).pack(side="left")
        self.comparison_results = self.text_box(panel, 6)

    def update_controls(self):
        active = self.connected and not self.disconnecting
        idle = active and not self.busy
        self.connect_button.configure(text="Disconnect" if self.connection else "Connect",
                                      state="disabled" if self.disconnecting or (self.connection and not self.connected) else "normal")
        self.folder_picker.configure(state="normal" if not self.connection or idle else "disabled")
        for widgets, enabled in ((self.port_controls, not self.connection), (self.board_controls, active),
                                 (self.idle_controls, idle), (self.config_controls, idle and self.active_settings is not None)):
            for widget in widgets:
                widget.configure(state="normal" if enabled else "disabled")
        status = ("Disconnecting…" if self.disconnecting else "Board busy / waiting" if active and self.busy
                  else "Board ready" if active else "Connecting…" if self.connection else "Disconnected")
        self.connection_status.set(status)
        recording = "Next trial armed" if self.armed else "Next trial recording off"
        self.recording_status.set(f"Current trial selected  |  {recording}" if self.capturing else recording)

    def refresh_ports(self):
        try:
            from serial.tools.list_ports import comports
            self.port_box.configure(values=[port.device for port in comports()])
        except (ImportError, OSError):
            self.port_box.configure(values=[])

    def toggle_connection(self):
        if self.connection:
            self.disconnecting = True
            self.connection.close()
        else:
            if recorder.serial is None:
                self.error("Use Install Python packages to add the serial connection package.")
                return
            port = self.port.get().strip()
            if not port:
                self.error("Select the Arduino serial port.")
                return
            if port.isdigit():
                port = f"COM{port}"
            self.busy = True
            self.connection = DesktopConnection(recorder.serial.Serial, port, self.folder.get(), self.events)
        self.update_controls()

    def send(self, command, argument=None):
        if not self.connection or not self.connected or self.disconnecting:
            return
        if command not in ("!", "stop", "track next", "track off"):
            if self.busy:
                return
            self.busy = True
            self.update_controls()
        self.connection.send(command, argument)

    def start_trial(self):
        settings = self.active_settings or DEFAULTS
        try:
            distance = numeric(self.distance.get())
        except ValueError:
            self.error("Enter a finite launch distance in millimetres.")
            return
        low, high = settings["margin"], settings["track"] - settings["margin"]
        if not low < abs(distance) < high:
            self.error(f"Enter a launch distance with magnitude above {low:g} and below {high:g} mm.")
            return
        mode = self.mode.get().lower()
        command = f"{distance:.9g} {mode}"
        if mode == "tune":
            minimum, maximum = 1 / settings["period"], settings["tune_limit"]
            try:
                frequency = numeric(self.frequency.get())
                if not minimum < frequency <= maximum:
                    raise ValueError
            except ValueError:
                self.error(f"Enter a tune frequency above {minimum:.5g} and at most {maximum:g} Hz.")
                return
            command += f" {frequency:.9g}"
        self.send(command)

    def apply_settings(self):
        choices = {"Automatic": "0", "Manual": "1", "Off": "0", "On": "1"}
        values = [choices.get(var.get(), var.get()) for var in self.setting_vars.values()]
        try:
            settings = parse_settings(values)
        except ValueError as error:
            self.error(str(error))
            return
        self.send("config " + " ".join(f"{value:.9g}" for value in settings.values()))

    def set_damping(self):
        try:
            value = numeric(self.damping.get())
            if not 0 <= value <= 5:
                raise ValueError
        except ValueError:
            self.error("Enter a damping coefficient from 0 to 5 s⁻¹.")
            return
        self.send(f"damping {value:.9g}")

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=self.folder.get(), filetypes=CSV_FILES,
                                           initialfile="encoder_replay.csv", defaultextension=".csv")
        if path:
            self.send("csv", path)

    def analyze(self):
        try:
            import plot_csv as analysis
            path = Path(self.analysis_file.get()).expanduser().resolve()
            if not path.is_file():
                raise ValueError("Choose an existing trial CSV.")
            argv = [str(path)]
            for key, var in self.analysis_options.items():
                if var.get().strip():
                    argv.append(f"--{key}={var.get().strip()}")
            if self.analysis_meta.get().strip():
                argv.append(f"--metadata={self.analysis_meta.get().strip()}")
            if self.manual_window.get():
                argv.append("--manual-window")
            errors = io.StringIO()
            try:
                with redirect_stderr(errors):
                    args = analysis.get_options(argv)
            except SystemExit:
                raise ValueError(errors.getvalue().splitlines()[-1].partition(": error: ")[-1]) from None
            result = analysis.analyze(path, args)
            analysis.make_plots(path, result)
            self.append(self.analysis_results, analysis.format_summary(path, result, args) + "\n")
            analysis.plt.show(block=False)
        except (ImportError, OSError, ValueError) as error:
            self.error(str(error))

    def compare(self):
        try:
            import plot_comparison as comparison
            paths = {mode: Path(var.get()).expanduser().resolve() for mode, var in self.comparison_files.items()}
            if len(set(paths.values())) != len(paths):
                raise ValueError("Choose a different CSV for each trial mode.")
            trials = [comparison.read_trial(mode, path) for mode, path in paths.items()]
            comparison.make_plots(trials, self.full_trial.get(), self.normalize.get())
            output = io.StringIO()
            with redirect_stdout(output):
                comparison.print_summary(trials, self.full_trial.get())
            self.append(self.comparison_results, output.getvalue())
            comparison.plt.show(block=False)
        except (ImportError, OSError, ValueError, IndexError) as error:
            self.error(str(error))

    def refresh_dependencies(self):
        if any(find_spec(module) is None for module in ("serial", "matplotlib", "numpy", "scipy")):
            self.dependencies.pack(anchor="e", pady=(5, 0))
        else:
            self.dependencies.pack_forget()

    def install_dependencies(self):
        def install():
            try:
                exe = Path(sys.executable)
                if exe.name.lower() == "pythonw.exe":
                    exe = exe.with_name("python.exe")
                completed = subprocess.run([str(exe), "-m", "pip", "install", "--user", "-r", str(REQUIREMENTS)],
                                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                           errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.events.put(("installed", (completed.returncode, completed.stdout + completed.stderr)))
            except OSError as error:
                self.events.put(("installed", (1, str(error))))

        self.installing = True
        self.dependencies.configure(state="disabled")
        self.append(self.log_box, "Installing CartPole Python packages…")
        Thread(target=install, daemon=True).start()

    def load_status(self, status):
        if "config_v1" in status:
            try:
                settings = parse_settings(status["config_v1"].split())
            except ValueError as error:
                self.settings_status.set(f"Board settings response is invalid. {error}")
            else:
                if settings != self.active_settings:
                    self.fill_settings(settings)
                self.active_settings = settings
                self.settings_status.set("Firmware settings are available. Apply sends the complete form to the board.")
        elif not self.busy:
            self.settings_status.set("Upload the updated MS2_MK1 sketch to edit runtime settings.")
        if "model_ready" in status and "damping_per_s" in status:
            damping = status["damping_per_s"]
            ready = "ready" if status["model_ready"] == "1" else "needs calibration"
            self.model_status.set(f"Model {ready}  |  Damping c = {damping} s⁻¹")
            if not self.damping.get():
                self.damping.set(damping)

    def poll(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                if not data.startswith("# config_v1="):
                    self.append(self.log_box, data)
            elif kind == "connected":
                self.connected = True
            elif kind == "state":
                self.busy, self.armed, self.capturing, status = data
                self.load_status(status)
            elif kind == "disconnected":
                self.connection = self.active_settings = None
                self.connected = self.disconnecting = self.armed = self.capturing = False
                self.busy = True
                self.settings_status.set("Connect to read settings from the board.")
                self.model_status.set("Connect to read the board calibration.")
                if self.closing:
                    self.root.destroy()
                    return
            elif kind == "installed":
                self.installing = False
                code, output = data
                if code:
                    self.append(self.log_box, "Warning: Python package installation failed.\n" + output)
                else:
                    import site
                    site.addsitedir(site.getusersitepackages())
                    importlib.invalidate_caches()
                    recorder.serial = importlib.import_module("serial")
                    self.refresh_ports()
                    self.refresh_dependencies()
                    self.append(self.log_box, "Python packages installed.")
                self.dependencies.configure(state="normal")
            self.update_controls()
        self.root.after(40, self.poll)

    def error(self, message):
        messagebox.showerror("CartPole", message, parent=self.root)

    def close(self):
        if self.connection:
            self.closing = self.disconnecting = True
            self.connection.close()
            self.update_controls()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    CartPoleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
