"""Defaults and input limits for the Milestone 3 experiment."""

from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class RunSettings:
    first_mm: int = 50
    last_mm: int = 70
    step_mm: int = 1
    speed: float = 7000
    acceleration: float = 100000
    sample_ms: float = 10
    free_s: float = 5
    settle_s: float = 1
    brake_return: bool = False

    def __post_init__(self):
        if not 6 <= self.first_mm <= self.last_mm <= 294:
            raise ValueError("Travel distances must be in order and within 6–294 mm.")
        if self.step_mm < 1 or (self.last_mm - self.first_mm) % self.step_mm:
            raise ValueError("Use a positive distance step that reaches the last distance.")
        if not isinstance(self.brake_return, bool):
            raise ValueError("Return braking must be on or off.")
        for value, low, high, label in (
            (self.speed, 100, 7000, "Speed"),
            (self.acceleration, 1000, 100000, "Acceleration"),
            (self.sample_ms, 5, 50, "Sample interval"),
            (self.free_s, 0.1, 30, "Free oscillation"),
            (self.settle_s, 0.1, 10, "Settling time"),
        ):
            if not low <= value <= high:
                raise ValueError(f"{label} must be between {low:g} and {high:g}.")

    @property
    def distances(self):
        return range(self.first_mm, self.last_mm + 1, self.step_mm)

    def outbound_settings(self):
        settings = asdict(self)
        del settings["brake_return"]
        return settings

    def board_command(self):
        return (f"decay_config {self.speed} {self.acceleration} {self.sample_ms} "
                f"{self.free_s} {self.settle_s} {int(self.brake_return)}")


@dataclass(frozen=True)
class AnalysisSettings:
    inertia_1: float = 0.000554
    inertia_2: float = 0.000759
    inertia_3: float = 0.000963
    prominence_deg: float = 1.2
    peak_spacing_s: float = 0.35

    def __post_init__(self):
        if any(value <= 0 or not isfinite(value) for value in (
            self.inertia_1, self.inertia_2, self.inertia_3, self.prominence_deg, self.peak_spacing_s
        )):
            raise ValueError("The inertias, peak prominence, and peak spacing must be positive numbers.")

    @property
    def inertias(self):
        return {1: self.inertia_1, 2: self.inertia_2, 3: self.inertia_3}
