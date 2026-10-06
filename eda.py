import csv
import io
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zipfile import ZipFile


LIMIT = 0  #Use 0 for all rows, or a smaller number for a quick sample.

row_count = 0
trip_ids = set()
taxi_ids = set()
gps_counts = []
first_timestamp = None
last_timestamp = None
missing_data_count = 0
trajectory_sizes = Counter({"0 points": 0, "1-2 points": 0, "3+ points": 0})
call_types = Counter()
day_types = Counter()
blank_origin_calls = Counter()
blank_origin_stands = Counter()

#Read the CSV directly from the ZIP beside this script.
dataset_path = Path(__file__).resolve().parent / "porto.zip"

with ZipFile(dataset_path) as archive:
    with archive.open("porto/porto.csv") as file:
        reader = csv.DictReader(io.TextIOWrapper(file, encoding="utf-8"))
        for row in reader:
            row_count += 1
            trip_ids.add(row["TRIP_ID"].strip())
            taxi_ids.add(row["TAXI_ID"].strip())

            timestamp = int(row["TIMESTAMP"])
            if first_timestamp is None or timestamp < first_timestamp:
                first_timestamp = timestamp
            if last_timestamp is None or timestamp > last_timestamp:
                last_timestamp = timestamp

            point_count = len(json.loads(row["POLYLINE"]))
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
            if not row["ORIGIN_CALL"].strip():
                blank_origin_calls[call_type] += 1
            if not row["ORIGIN_STAND"].strip():
                blank_origin_stands[call_type] += 1

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

print("\nBlank origin fields by call type (percentage within each call type):")
for call_type, count in sorted(call_types.items()):
    calls = blank_origin_calls[call_type]
    stands = blank_origin_stands[call_type]
    print(f"  Call type {call_type or '(blank)'} ({count:,} rows):")
    print(f"    Blank ORIGIN_CALL: {calls:,} ({calls / count:.2%})")
    print(f"    Blank ORIGIN_STAND: {stands:,} ({stands / count:.2%})")
