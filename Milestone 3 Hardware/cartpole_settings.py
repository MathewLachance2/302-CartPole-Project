"""Version 1 runtime settings shared with MS2_MK1.ino, in wire order."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Setting:
    label: str
    default: float
    low: float
    high: float
    choices: tuple = ()


GROUPS = {
    "Motion and travel": {
        "speed": Setting("Maximum speed (steps/s)", 7000, 100, 7000),
        "acceleration": Setting("Maximum acceleration (steps/s²)", 100000, 1000, 100000),
        "track": Setting("Track travel (mm)", 300, 20, 300),
        "margin": Setting("End clearance (mm)", 5, 5, 50),
        "steps": Setting("Motor calibration (steps/mm)", 40, 1, 200),
        "counts": Setting("Encoder calibration (counts/°)", 1.667, 0.1, 100),
        "sign": Setting("Encoder direction", 1, -1, 1, (-1, 1)),
        "angle_limit": Setting("Angle limit (°)", 60, 5, 60),
    },
    "Trial timing": {
        "period": Setting("Natural period (s)", 0.7, 0.2, 3),
        "hold": Setting("Hold duration (s)", 0.7, 0.1, 3),
        "duration": Setting("Control duration (s)", 10, 0.1, 30),
        "sample": Setting("Sample interval (ms)", 10, 5, 50),
        "tune_limit": Setting("Maximum tune frequency (Hz)", 10, 0.1, 10),
        "auto_return": Setting("Baseline automatic return", 1, 0, 1, (0, 1)),
    },
    "Feedback": {
        "rate_filter": Setting("Rate filter time constant (s)", 0.03, 0, 1),
        "envelope_filter": Setting("Envelope filter time constant (s)", 0.2, 0, 2),
        "cart_w2": Setting("Cart frequency squared (s⁻²)", 1, 0.01, 10),
        "cart_damping": Setting("Cart damping coefficient (s⁻¹)", 2, 0, 10),
        "centering": Setting("Cart centering", 1, 0, 1, (0, 1)),
        "amplitude": Setting("Amplitude correction strength", 2, 0, 5),
        "quash": Setting("Quash damping multiplier", 1.4, 0.1, 3),
        "manual": Setting("Pendulum gains", 0, 0, 1, (0, 1)),
        "gp": Setting("Pendulum proportional gain (mm/s²/rad)", 0, -10000, 10000),
        "gd": Setting("Pendulum derivative gain (mm/s/rad)", 0, -1000, 1000),
    },
}
SETTINGS = {key: item for group in GROUPS.values() for key, item in group.items()}
DEFAULTS = {key: item.default for key, item in SETTINGS.items()}


def parse_settings(values):
    if len(values) != len(SETTINGS):
        raise ValueError(f"Expected {len(SETTINGS)} board settings.")
    result = {}
    for (key, item), raw in zip(SETTINGS.items(), values):
        try:
            value = float(raw)
            if ("_" in str(raw) or not math.isfinite(value) or not item.low <= value <= item.high
                    or (item.choices and value not in item.choices)):
                raise ValueError
        except ValueError:
            allowed = " or ".join(map(str, item.choices)) if item.choices else f"{item.low:g} to {item.high:g}"
            raise ValueError(f"{item.label} must be {allowed}.") from None
        result[key] = value
    result["sample"] = math.floor(result["sample"] * 1000 + 0.5) / 1000
    if 2 * result["margin"] >= result["track"]:
        raise ValueError("The track must be longer than twice the end clearance.")
    if result["track"] * result["steps"] > 12000.001:
        raise ValueError("Track travel × steps per mm must be at most 12,000 steps.")
    if result["tune_limit"] <= 1 / result["period"]:
        raise ValueError("Maximum tune frequency must exceed the natural frequency.")
    if result["tune_limit"] * result["sample"] > 100.00001:
        raise ValueError("Allow at least 10 samples per cycle at maximum tune frequency.")
    if (result["duration"] + result["hold"]) * 1000 >= 1498 * result["sample"]:
        raise ValueError("The hold and control durations exceed the sample buffer.")
    return result
