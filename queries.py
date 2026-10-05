"""Part 2: SQL summaries and one streamed pass over the stored trajectories.

Run: python queries.py (keep the local MySQL container running).
No database rows are changed. All answers are written to part2_results/results.txt.
"""
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from haversine import haversine, Unit
from DbConnector import DbConnector


FETCH_SIZE = 1000
OUTPUT = Path(__file__).resolve().parent / os.getenv("RESULTS_FILE", "part2_results/results.txt")
results = {}
PORTO = ZoneInfo("Europe/Lisbon")
CITY_HALL = (41.15794, -8.62911)  # Haversine expects (latitude, longitude).
POLICY = """PART 2 - ANALYSIS RULES
Counts and call-type frequencies include all stored trips, including short trips.
Q4a reports all tied most-used call types; NULL call types are excluded.
Q4b time-band percentages use all trips in each call type, in Porto local time.
Q4b duration/distance and Q5/Q8/Q9 use complete trajectories with >=3 points.
Duration = (point_count - 1) * 15 seconds; distance sums consecutive GPS segments.
These are sampled-trajectory estimates, not measured meter readings.
Q6 uses recorded points within 100 m, including short/incomplete trajectories.
It does not interpolate between points; missing points can hide a visit.
Q8 means end date is exactly the next local calendar date.
Q9 compares the first and last recorded points, with a 50 m inclusive threshold.
Q10 orders ALL trips per taxi by UTC start time, then trip ID to break ties.
An idle gap needs a complete >=3-point previous trip; the next start is used even
if its trajectory is incomplete. Ineligible trips are not silently bridged.
Negative gaps are excluded and counted; zero gaps are included.
Taxis with no usable idle gaps have NULL averages and are excluded from top 20.
Missing numeric values are shown as NULL. Times include UTC offsets.
"""


def distance_m(first, second):
    """Convert the dataset's [longitude, latitude] order for haversine."""
    return haversine((first[1], first[0]), (second[1], second[0]), unit=Unit.METERS)


def trajectory_metrics(points, missing_data, start_utc):
    """Return duration, route distance, endpoint distance, and end time."""
    if missing_data or len(points) < 3:
        return None
    seconds = (len(points) - 1) * 15
    kilometres = sum(distance_m(a, b) for a, b in zip(points, points[1:])) / 1000
    return seconds, kilometres, distance_m(points[0], points[-1]), start_utc + timedelta(seconds=seconds)


def passed_city_hall(points):
    for longitude, latitude in points:
        # A deliberately generous box avoids expensive calculations far away.
        if abs(latitude - CITY_HALL[0]) <= 0.002 and abs(longitude - CITY_HALL[1]) <= 0.002:
            if haversine((latitude, longitude), CITY_HALL, unit=Unit.METERS) <= 100:
                return True
    return False


def save_result(name, headers, rows):
    results[name] = (headers, rows)


def write_results():
    """Write all answers in question order to one plain-text file."""
    sections = [
        ("q1_counts", "1. Number of taxis, trips and GPS points"),
        ("q2_average_trips", "2. Average trips per taxi"),
        ("q3_top_20_taxis", "3. Top 20 taxis by trip count"),
        ("q4a_most_used_call_type", "4a. Most-used call type per taxi (including ties)"),
        ("q4b_call_statistics", "4b. Duration, distance and starting-time bands by call type"),
        ("q5_taxi_totals", "5. Total hours and distance per taxi, ordered by hours"),
        ("q6_city_hall", "6. Trips within 100 m of Porto City Hall"),
        ("q7_invalid_trips", "7. Invalid trips (fewer than 3 points)"),
        ("q8_midnight_crossers", "8. Trips ending on the next calendar day"),
        ("q9_circular_trips", "9. Trips with endpoints within 50 m"),
        ("q10_all_taxis_idle", "10. Average idle time per taxi"),
        ("q10_top_20_idle", "10. Top 20 taxis by average idle time"),
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as file:
        file.write(POLICY + "\n")
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        for name, title in sections:
            headers, rows = results[name]
            file.write(f"\n{title}\nRows: {len(rows)}\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(["NULL" if value is None else value for value in row])


def sql_result(cursor, name, query):
    print(f"Running {name}...", flush=True)
    cursor.execute(query)
    rows = cursor.fetchall()
    save_result(name, cursor.column_names, rows)
    return rows


def question_1(cursor):
    return sql_result(cursor, "q1_counts", """
        SELECT COUNT(DISTINCT taxi_id) AS taxis, COUNT(*) AS trips,
               COALESCE(SUM(JSON_LENGTH(polyline)), 0) AS gps_points FROM Trip
    """)


def question_2(cursor):
    sql_result(cursor, "q2_average_trips", """
        SELECT COUNT(*) / NULLIF(COUNT(DISTINCT taxi_id), 0) AS average_trips_per_taxi
        FROM Trip
    """)


def question_3(cursor):
    sql_result(cursor, "q3_top_20_taxis", """
        SELECT taxi_id, COUNT(*) AS trips FROM Trip
        GROUP BY taxi_id ORDER BY trips DESC, taxi_id LIMIT 20
    """)


def question_4a(cursor):
    sql_result(cursor, "q4a_most_used_call_type", """
        WITH frequencies AS (
            SELECT taxi_id, call_type, COUNT(*) AS trips FROM Trip
            WHERE call_type IS NOT NULL GROUP BY taxi_id, call_type
        ), ranked AS (
            SELECT *, DENSE_RANK() OVER (PARTITION BY taxi_id ORDER BY trips DESC) AS position
            FROM frequencies
        )
        SELECT taxi_id, call_type, trips FROM ranked
        WHERE position = 1 ORDER BY taxi_id, call_type
    """)


def question_7(cursor):
    return sql_result(cursor, "q7_invalid_trips", """
        SELECT COUNT(*) AS invalid_trips,
               COALESCE(SUM(JSON_LENGTH(polyline) = 0), 0) AS empty_trajectories
        FROM Trip WHERE JSON_LENGTH(polyline) < 3
    """)


def analyse_trajectories(cursor):
    """Answer Q4b, Q5, Q6, Q8, Q9 and Q10 without retaining every trip in RAM."""
    calls = defaultdict(lambda: {"trips": 0, "eligible": 0, "seconds": 0, "km": 0.0, "bands": [0]*4})
    taxis = defaultdict(lambda: {"eligible": 0, "seconds": 0, "km": 0.0, "gaps": 0,
                                 "idle_seconds": 0.0, "negative_gaps": 0, "unknown_gaps": 0})
    totals = {"trips": 0, "points": 0, "short": 0, "missing": 0, "eligible": 0,
              "q6_city_hall": 0, "q8_midnight_crossers": 0, "q9_circular_trips": 0}
    headers = {
        "q6_city_hall": ["trip_id", "taxi_id", "start_porto", "point_count", "missing_data"],
        "q8_midnight_crossers": ["trip_id", "taxi_id", "start_porto", "end_porto"],
        "q9_circular_trips": ["trip_id", "taxi_id", "endpoint_distance_m"],
    }
    previous_taxi = previous_end = None
    print("Reading trajectories for Q4b, Q5, Q6, Q8, Q9 and Q10...", flush=True)
    cursor.execute("""
        SELECT trip_id, taxi_id, call_type, start_time, missing_data, polyline
        FROM Trip ORDER BY taxi_id, start_time, trip_id
    """)
    matches = {name: [] for name in headers}
    while True:
        rows = cursor.fetchmany(FETCH_SIZE)
        if not rows:
            break
        for trip_id, taxi_id, call_type, start, missing, polyline in rows:
            points = json.loads(polyline)
            start_utc = start.replace(tzinfo=timezone.utc)
            local_start = start_utc.astimezone(PORTO)
            taxi = taxis[taxi_id]
            call = calls[call_type or "UNKNOWN"]
            call["trips"] += 1
            call["bands"][local_start.hour // 6] += 1
            totals["trips"] += 1
            totals["points"] += len(points)
            totals["short"] += len(points) < 3
            totals["missing"] += bool(missing)
            metrics = trajectory_metrics(points, missing, start_utc)

            # Q10: compare with the immediately previous trip, not an earlier valid one.
            if taxi_id == previous_taxi:
                if previous_end is None:
                    taxi["unknown_gaps"] += 1
                else:
                    gap = (start_utc - previous_end).total_seconds()
                    if gap < 0:
                        taxi["negative_gaps"] += 1
                    else:
                        taxi["gaps"] += 1
                        taxi["idle_seconds"] += gap
            previous_taxi = taxi_id
            previous_end = metrics[3] if metrics else None

            # Q6: any recorded point can establish proximity, even in an incomplete trip.
            if passed_city_hall(points):
                totals["q6_city_hall"] += 1
                matches["q6_city_hall"].append([trip_id, taxi_id, local_start.isoformat(), len(points), bool(missing)])
            if metrics is None:
                continue
            seconds, km, endpoint_m, end_utc = metrics
            totals["eligible"] += 1
            for group in (call, taxi):
                group["eligible"] += 1
                group["seconds"] += seconds
                group["km"] += km
            local_end = end_utc.astimezone(PORTO)
            if local_end.date() == local_start.date() + timedelta(days=1):
                totals["q8_midnight_crossers"] += 1
                matches["q8_midnight_crossers"].append([trip_id, taxi_id, local_start.isoformat(), local_end.isoformat()])
            if endpoint_m <= 50:
                totals["q9_circular_trips"] += 1
                matches["q9_circular_trips"].append([trip_id, taxi_id, endpoint_m])
        if totals["trips"] % 10000 == 0:
            print(f"Analysed {totals['trips']:,} trips", flush=True)

    # Q4b: show both the all-trip and eligible-trip denominators explicitly.
    rows = []
    for call_type, data in sorted(calls.items()):
        n = data["eligible"]
        rows.append([call_type, data["trips"], n, data["seconds"]/n/60 if n else None,
                     data["km"]/n if n else None] + [100*b/data["trips"] for b in data["bands"]])
    save_result("q4b_call_statistics", ["call_type", "all_trips", "eligible_trips", "avg_minutes", "avg_km",
                "00-06_percent", "06-12_percent", "12-18_percent", "18-24_percent"], rows)
    rows = [[tid, d["eligible"], d["seconds"]/3600 if d["eligible"] else None,
             d["km"] if d["eligible"] else None] for tid, d in taxis.items()]
    rows.sort(key=lambda r: (-(r[2] if r[2] is not None else -1), r[0]))
    save_result("q5_taxi_totals", ["taxi_id", "eligible_trips", "total_hours", "total_km"], rows)
    for name, columns in headers.items():
        save_result(name, columns, matches[name])

    # Q10: save every taxi as well as the requested top 20.
    idle = [[tid, d["idle_seconds"]/d["gaps"]/60 if d["gaps"] else None,
             d["gaps"], d["negative_gaps"], d["unknown_gaps"]] for tid, d in taxis.items()]
    idle.sort(key=lambda r: (-(r[1] if r[1] is not None else -1), r[0]))
    columns = ["taxi_id", "avg_idle_minutes", "usable_gaps", "negative_gaps_excluded", "unknown_end_gaps_excluded"]
    save_result("q10_all_taxis_idle", columns, idle)
    save_result("q10_top_20_idle", columns, [r for r in idle if r[1] is not None][:20])
    return totals


# Run all questions, then write the results only after validation succeeds.
connection = DbConnector()
try:
    # One consistent read-only snapshot across the SQL and Python calculations.
    connection.db_connection.commit()
    connection.db_connection.start_transaction(isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
    cursor = connection.cursor
    counts = question_1(cursor)[0]
    question_2(cursor)
    question_3(cursor)
    question_4a(cursor)
    invalid = question_7(cursor)[0]
    totals = analyse_trajectories(cursor)
    assert totals["trips"] == counts[1] and totals["points"] == counts[2]
    assert totals["short"] == invalid[0]
    write_results()
    print(f"All questions completed. Results saved in {OUTPUT}")
finally:
    connection.db_connection.rollback()  # Release the read-only snapshot.
    connection.close_connection()
