"""Tk widgets shared by the decay and feedback-trial apps."""

import tkinter as tk
from tkinter import ttk


def text_box(parent, height):
    area = ttk.Frame(parent)
    area.pack(fill="both", expand=True, pady=(4, 0))
    box = tk.Text(area, height=height, wrap="word", font=("Consolas", 10), state="disabled",
                  relief="flat", padx=8, pady=5, background="#fff", foreground="#183047")
    scroll = ttk.Scrollbar(area, command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    box.pack(side="left", fill="both", expand=True)
    return box


def append_text(box, text):
    box.configure(state="normal")
    box.insert("end", text + "\n", "warning" if text.startswith("Warning:") else ())
    if int(box.index("end-1c").split(".")[0]) > 1600:
        box.delete("1.0", "401.0")
    box.see("end")
    box.configure(state="disabled")


def field(parent, row, label, value, choices=(), width=15):
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
    if choices:
        widget = ttk.Combobox(parent, textvariable=value, values=choices, state="readonly", width=width)
    else:
        widget = ttk.Entry(parent, textvariable=value, width=width)
    widget.grid(row=row, column=1, sticky="ew", pady=4)
    return widget
