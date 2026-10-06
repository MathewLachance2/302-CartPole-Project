import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

csv_path = input("Paste the CSV file path: ").strip().strip('"')
start_time = float(input("Analyze after time (seconds, Enter for 0): ").strip() or "0")
if not np.isfinite(start_time) or start_time < 0:
    raise SystemExit("Start time must be a finite, nonnegative number.")

data = np.genfromtxt(
    csv_path,
    delimiter=",",
    names=True,
    ndmin=1
)

time = data["time_s"]
angle = data["angle_deg"]

# Remove missing or invalid readings.
valid = np.isfinite(time) & np.isfinite(angle)
time = time[valid]
angle = angle[valid]

# Only use the selected part of the recording for peak detection.
selected = time >= start_time
analysis_time = time[selected]
analysis_angle = angle[selected]
if len(analysis_time) < 3:
    raise SystemExit("Not enough readings after that start time. Choose an earlier time.")

# Starting value in degrees; adjust after inspecting the plot.
peaks, _ = find_peaks(analysis_angle, prominence=1.2)

if len(peaks) >= 2:
    periods = np.diff(analysis_time[peaks])
    period = np.median(periods)

    print("Individual periods (s):", periods)
    print(f"Estimated period: {period:.3f} s")
    print(f"Estimated frequency: {1 / period:.3f} Hz")
else:
    print("Not enough peaks detected to calculate a period.")

plt.plot(time, angle, label="Encoder angle")
plt.plot(analysis_time[peaks], analysis_angle[peaks], "rx", label="Peaks used for period")
plt.axvline(start_time, color="gray", linestyle="--", label="Analysis starts")
plt.xlabel("Time (s)")
plt.ylabel("Angle (degrees)")
plt.title("Pendulum oscillation")
plt.grid(True)
plt.legend()
plt.show()
