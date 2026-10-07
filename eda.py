import csv
import io
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zipfile import ZipFile

import numpy as np
import matplotlib.pyplot as plt

from trajectory_utils import excessive_speed, MAX_SPEED_KMH


LIMIT = 0  #Use 0 for all rows, or a smaller number for a quick sample.

plot_trajectories = []
speed_rejected_count = 0
plot_point_count = 0
plot_latitude_sum = 0.0

row_count = 0
trip_ids = set()
taxi_ids = set()
speeding_taxi_ids = set()
measurable_taxi_ids = set()
gps_counts = []
first_timestamp = None
last_timestamp = None
missing_data_count = 0
trajectory_sizes = Counter({"0 points": 0, "1-2 points": 0, "3+ points": 0})
call_types = Counter()
day_types = Counter()

#Read the CSV directly from the ZIP beside this script.
dataset_path = Path(__file__).resolve().parent / "porto.zip"

with ZipFile(dataset_path) as archive:
    with archive.open("porto/porto.csv") as file:
        reader = csv.DictReader(io.TextIOWrapper(file, encoding="utf-8"))
        for row in reader:
            row_count += 1
            trip_ids.add(row["TRIP_ID"].strip())
            taxi_id = row["TAXI_ID"].strip()
            taxi_ids.add(taxi_id)

            timestamp = int(row["TIMESTAMP"])
            if first_timestamp is None or timestamp < first_timestamp:
                first_timestamp = timestamp
            if last_timestamp is None or timestamp > last_timestamp:
                last_timestamp = timestamp

            points = json.loads(row["POLYLINE"])
            if len(points) >= 2:
                measurable_taxi_ids.add(taxi_id)
            violation = excessive_speed(points)
            if violation is not None:
                speed_rejected_count += 1
                speeding_taxi_ids.add(taxi_id)
            if points:
                # Compact arrays keep all trajectories without millions of Python point lists.
                coordinates = np.asarray(points, dtype=np.float32)
                plot_trajectories.append(coordinates)
                plot_point_count += len(points)
                plot_latitude_sum += float(coordinates[:, 1].sum(dtype=np.float64))
            point_count = len(points)
            gps_counts.append(point_count)
            if point_count == 0:
                trajectory_sizes["0 points"] += 1
            elif point_count < 3:
                trajectory_sizes["1-2 points"] += 1
            else:
                trajectory_sizes["3+ points"] += 1

            if row["MISSING_DATA"].strip().lower() == "true":
                missing_data_count += 1

            call_type = row["CALL_TYPE"].strip()
            call_types[call_type] += 1
            day_types[row["DAY_TYPE"].strip()] += 1

            if LIMIT and row_count >= LIMIT:
                break

print("\nEDA SUMMARY")
print(f"Scope: first {LIMIT:,} rows at most" if LIMIT else "Scope: full dataset")
print("Statistics describe source rows, before resolving repeated trip IDs.")
print(f"Total rows: {row_count:,}")

if row_count == 0:
    raise SystemExit("No data rows found.")

print(f"Unique trip IDs: {len(trip_ids):,}")
print(f"Extra rows with repeated trip IDs: {row_count - len(trip_ids):,}")
print(f"Unique taxis: {len(taxi_ids):,}")
print(f"Earliest trip (UTC): {datetime.fromtimestamp(first_timestamp, timezone.utc)}")
print(f"Latest trip (UTC): {datetime.fromtimestamp(last_timestamp, timezone.utc)}")
print(f"Rows flagged with missing data: {missing_data_count:,} ({missing_data_count / row_count:.2%})")

print("\nTrajectory sizes:")
for label, count in trajectory_sizes.items():
    print(f"  {label}: {count:,} ({count / row_count:.2%})")

#The nearest-rank 95th percentile is the value at 95% of the sorted list.
gps_counts.sort()
p95_index = math.ceil(0.95 * row_count) - 1
print("\nGPS points per row:")
print(f"  Median: {median(gps_counts):,.1f}")
print(f"  95th percentile: {gps_counts[p95_index]:,}")
print(f"  Maximum: {gps_counts[-1]:,}")

for title, counts in (("Call types", call_types), ("Day types", day_types)):
    print(f"\n{title}:")
    for value, count in sorted(counts.items()):
        print(f"  {value or '(blank)'}: {count:,} ({count / row_count:.2%})")

print(f"\nRows with a segment above {MAX_SPEED_KMH} km/h: {speed_rejected_count:,}")

print(f"Unique taxis with a segment above {MAX_SPEED_KMH} km/h: "
      f"{len(speeding_taxi_ids):,} ({len(speeding_taxi_ids) / len(taxi_ids):.2%})")


def plot_pie(ax, labels, counts, colors, title):
    # Omit empty categories from the pie but retain their counts in the legend.
    nonzero = [(label, count, color) for label, count, color in zip(labels, counts, colors)
               if count > 0]
    ax.pie(
        [count for _, count, _ in nonzero],
        colors=[color for _, _, color in nonzero],
        autopct=lambda percent: f"{percent:.2f}%", startangle=90,
        pctdistance=0.75, wedgeprops={"edgecolor": "white", "linewidth": 1},
    )
    from matplotlib.patches import Patch
    ax.legend(
        handles=[Patch(facecolor=color, label=f"{label}: {count:,}")
                 for label, count, color in zip(labels, counts, colors)],
        loc="upper center", bbox_to_anchor=(0.5, -0.05), frameon=False,
    )
    ax.set_title(title)
    ax.set_aspect("equal")


fig, axes = plt.subplots(1, 2, figsize=(13, 7))
call_labels = {"A": "A: Central dispatch", "B": "B: Taxi stand", "C": "C: Street hail"}
call_values = sorted(call_types)
plot_pie(
    axes[0], [call_labels.get(code, code or "Blank / unknown") for code in call_values],
    [call_types[code] for code in call_values],
    [plt.get_cmap("tab10")(index % 10) for index in range(len(call_values))],
    f"Call types ({row_count:,} source trips)",
)
plot_pie(
    axes[1],
    [f"At least one segment >{MAX_SPEED_KMH} km/h",
     f"No recorded segment >{MAX_SPEED_KMH} km/h", "No measurable GPS segments"],
    [len(speeding_taxi_ids), len(measurable_taxi_ids - speeding_taxi_ids),
     len(taxi_ids - measurable_taxi_ids)],
    ["#d9534f", "#4c9f70", "#b0b0b0"],
    f"Speed flags ({len(taxi_ids):,} unique taxis)",
)
fig.suptitle("Porto taxi dataset: call types and speed flags", fontsize=15)
fig.text(0.5, 0.02, "Speed estimates use consecutive GPS points 15 seconds apart; missing points can affect estimates.",
         ha="center", fontsize=9)
fig.tight_layout(rect=(0, 0.16, 1, 0.94))

if plot_trajectories:

    from matplotlib.collections import LineCollection

    fig, ax = plt.subplots(figsize=(10, 8))
    # A collection draws every trajectory without a separate plot object per trip.
    paths = LineCollection(plot_trajectories, colors="tab:blue", linewidths=0.25,
                           alpha=0.25, rasterized=True)
    ax.add_collection(paths)
    # Include every GPS sample, including trajectories containing only one point.
    all_points = np.concatenate(plot_trajectories)
    ax.scatter(all_points[:, 0], all_points[:, 1], s=0.1, color="tab:blue",
               alpha=0.25, linewidths=0, rasterized=True)
    del all_points
    ax.autoscale_view()
    mean_latitude = plot_latitude_sum / plot_point_count
    ax.set_aspect(1 / math.cos(math.radians(mean_latitude)))
    ax.set(xlabel="Longitude (degrees)", ylabel="Latitude (degrees)",
           title=f"All {len(plot_trajectories):,} nonempty trajectories ({plot_point_count:,} GPS points)")
    ax.grid(alpha=0.2)
    fig.tight_layout()

plt.show()
