"""Answer Assignment 2, Part 2 with readable SQL and Python.

Run: python queries.py. Requires MySQL 8 and the packages in requirements.txt.
Python calculates trajectory metrics; SQL filters, groups and ranks the answers.
Each question reads Trip directly. No tables are created or modified.
AI assistance: Codex helped refactor this file.
"""
import csv
import json
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from haversine import Unit, haversine
from DbConnector import DbConnector

FETCH_SIZE = 1000
OUTPUT = Path(__file__).resolve().parent / "part2_results" / "results.txt"
PORTO = ZoneInfo("Europe/Lisbon")
CITY_HALL = [-8.62911, 41.15794]  # Dataset coordinates: longitude, latitude.

POLICY = """PART 2 - ANALYSIS RULES
Answers describe stored trips after import cleaning and duplicate resolution.
Taxi counts and average trips use taxis with at least one stored trip.
The import's >200 km/h rejection is separate from Q7's <3-point invalid definition.
Q1-Q4a and Q7 include short and incomplete stored trips.
Q4a excludes NULL call types and returns every tied most-used call type.
Q4b time-band shares include all trips, with NULL call types shown as UNKNOWN.
Time bands use Porto local time: [00,06), [06,12), [12,18), [18,24).
Q4b duration/distance, Q5, Q8 and previous end times in Q10 require complete
trajectories with at least 3 points. Other estimates are NULL, never zero.
Duration = (point_count - 1) * 15 seconds; route distance sums GPS segments.
Q6 tests any recorded point within 100 m, including short/incomplete trips.
No interpolation is performed; missing points can hide proximity to City Hall.
Q8 requires the end date to be exactly the next Porto local calendar date.
Q9 compares recorded endpoints only for complete trajectories with at least 3 points.
These are recorded endpoints, not guaranteed actual endpoints.
Q6 and Q9 thresholds are inclusive (<=100 m and <=50 m respectively).
Q10 orders ALL trips by UTC start time, then trip ID, within each taxi.
An unknown previous end excludes that gap; intervening trips are never bridged.
Negative idle gaps are counted and excluded; zero gaps are included.
Taxis without usable gaps have NULL averages and are excluded from the top 20.
Times displayed include UTC offsets; durations/distances are sample estimates.
"""

SECTIONS = [
    ("q1_counts", "1. Number of taxis, trips and GPS points"),
    ("q2_average_trips", "2. Average trips per taxi"),
    ("q3_top_20_taxis", "3. Top 20 taxis by trip count"),
    ("q4a_most_used_call_type", "4a. Most-used call type per taxi (including ties)"),
    ("q4b_call_statistics", "4b. Duration, distance and starting-time bands by call type"),
    ("q5_taxi_totals", "5. Total hours and distance per taxi, ordered by hours"),
    ("q6_city_hall", "6. Trips within 100 m of Porto City Hall"),
    ("q7_invalid_trips", "7. Invalid trips (fewer than 3 points)"),
    ("q8_midnight_crossers", "8. Trips ending on the next calendar day"),
    ("q9_circular_trips", "9. Recorded endpoints within 50 m"),
    ("q10_all_taxis_idle", "10. Average idle time per taxi"),
    ("q10_top_20_idle", "10. Top 20 taxis by average idle time"),
]


def distance_m(first, second):
    """Convert [longitude, latitude] to Haversine's (latitude, longitude)."""
    return haversine((first[1], first[0]), (second[1], second[0]), unit=Unit.METERS)


def passed_city_hall(points):
    """A recorded point establishes proximity; there is no interpolation."""
    return any(distance_m(point, CITY_HALL) <= 100 for point in points)


def porto_time(timestamp):
    """Stored MySQL DATETIME values represent UTC."""
    return timestamp.replace(tzinfo=timezone.utc).astimezone(PORTO)


def route_km(points):
    return sum(distance_m(a, b) for a, b in zip(points, points[1:])) / 1000


def query_rows(cursor, query):
    """Read one question's result in batches; consume it before another query."""
    cursor.execute(query)
    while True:
        rows = cursor.fetchmany(FETCH_SIZE)
        if not rows:
            break
        yield from rows


def sql_result(cursor, results, name, query):
    print(f"Running {name}...", flush=True)
    cursor.execute(query)
    rows = cursor.fetchall()
    results[name] = (cursor.column_names, rows)
    return rows


def question_1(cursor, results):
    """Count taxis represented in Trip, trips, and recorded GPS points."""
    return sql_result(cursor, results, "q1_counts", """
        SELECT COUNT(DISTINCT taxi_id) AS taxis, COUNT(*) AS trips,
               COALESCE(SUM(JSON_LENGTH(polyline)), 0) AS gps_points FROM Trip
    """)


def question_2(cursor, results):
    sql_result(cursor, results, "q2_average_trips", """
        SELECT COUNT(*) / NULLIF(COUNT(DISTINCT taxi_id), 0) AS average_trips_per_taxi
        FROM Trip
    """)


def question_3(cursor, results):
    sql_result(cursor, results, "q3_top_20_taxis", """
        SELECT taxi_id, COUNT(*) AS trips FROM Trip
        GROUP BY taxi_id ORDER BY trips DESC, taxi_id LIMIT 20
    """)


def question_4a(cursor, results):
    sql_result(cursor, results, "q4a_most_used_call_type", """
        WITH frequencies AS (
            SELECT taxi_id, call_type, COUNT(*) AS trips FROM Trip
            WHERE call_type IS NOT NULL GROUP BY taxi_id, call_type
        ), ranked AS (
            SELECT *, DENSE_RANK() OVER (PARTITION BY taxi_id ORDER BY trips DESC) AS ranking
            FROM frequencies
        )
        SELECT taxi_id, call_type, trips FROM ranked
        WHERE ranking = 1 ORDER BY taxi_id, call_type
    """)


def question_4b(cursor, results):
    """SQL summarizes durations; Python measures routes and local-time bands."""
    print("Running q4b_call_statistics...", flush=True)
    cursor.execute("""
        SELECT COALESCE(call_type, 'UNKNOWN') AS call_type, COUNT(*) AS all_trips,
               SUM(NOT missing_data AND JSON_LENGTH(polyline) >= 3) AS eligible_trips,
               AVG(CASE WHEN NOT missing_data AND JSON_LENGTH(polyline) >= 3
                        THEN (JSON_LENGTH(polyline) - 1) * 15 END) / 60.0 AS avg_minutes
        FROM Trip GROUP BY COALESCE(call_type, 'UNKNOWN') ORDER BY call_type
    """)
    summaries = cursor.fetchall()
    distances = {row[0]: 0.0 for row in summaries}
    bands = {row[0]: [0, 0, 0, 0] for row in summaries}
    for call_type, start, missing, polyline in query_rows(cursor, """
        SELECT COALESCE(call_type, 'UNKNOWN'), start_time, missing_data, polyline FROM Trip
    """):
        bands[call_type][porto_time(start).hour // 6] += 1
        points = json.loads(polyline)
        if not missing and len(points) >= 3:
            distances[call_type] += route_km(points)
    rows = []
    for call_type, all_trips, eligible, avg_minutes in summaries:
        # MySQL SUM of integer expressions is returned as Decimal.
        eligible = int(eligible)
        all_trips = int(all_trips)
        avg_km = distances[call_type] / eligible if eligible else None
        shares = [100.0 * count / all_trips for count in bands[call_type]]
        rows.append([call_type, all_trips, eligible, avg_minutes, avg_km] + shares)
    results["q4b_call_statistics"] = (
        ["call_type", "all_trips", "eligible_trips", "avg_minutes", "avg_km",
         "00-06_percent", "06-12_percent", "12-18_percent", "18-24_percent"], rows,
    )


def question_5(cursor, results):
    """SQL totals driving hours and sorts taxis; Python totals route distances."""
    print("Running q5_taxi_totals...", flush=True)
    cursor.execute("""
        SELECT taxi_id,
               SUM(NOT missing_data AND JSON_LENGTH(polyline) >= 3) AS eligible_trips,
               SUM(CASE WHEN NOT missing_data AND JSON_LENGTH(polyline) >= 3
                        THEN (JSON_LENGTH(polyline) - 1) * 15 END) / 3600.0 AS total_hours
        FROM Trip GROUP BY taxi_id ORDER BY total_hours DESC, taxi_id
    """)
    summaries = cursor.fetchall()
    distances = {row[0]: 0.0 for row in summaries}
    for taxi_id, polyline in query_rows(cursor, """
        SELECT taxi_id, polyline FROM Trip
        WHERE NOT missing_data AND JSON_LENGTH(polyline) >= 3
    """):
        distances[taxi_id] += route_km(json.loads(polyline))
    rows = [[taxi_id, eligible, hours, distances[taxi_id] if eligible else None]
            for taxi_id, eligible, hours in summaries]
    results["q5_taxi_totals"] = (
        ["taxi_id", "eligible_trips", "total_hours", "total_km"], rows,
    )


def question_6(cursor, results):
    """Check recorded points for City Hall proximity, including incomplete trips."""
    print("Running q6_city_hall...", flush=True)
    rows = []
    for trip_id, taxi_id, start, missing, polyline in query_rows(cursor, """
        SELECT trip_id, taxi_id, start_time, missing_data, polyline FROM Trip
        WHERE JSON_LENGTH(polyline) > 0 ORDER BY taxi_id, start_time, trip_id
    """):
        points = json.loads(polyline)
        if passed_city_hall(points):
            rows.append([trip_id, taxi_id, porto_time(start).isoformat(), len(points), bool(missing)])
    results["q6_city_hall"] = (
        ["trip_id", "taxi_id", "start_porto", "point_count", "missing_data"], rows,
    )


def question_7(cursor, results):
    return sql_result(cursor, results, "q7_invalid_trips", """
        SELECT COUNT(*) AS invalid_trips,
               COALESCE(SUM(JSON_LENGTH(polyline) = 0), 0) AS empty_trajectories
        FROM Trip WHERE JSON_LENGTH(polyline) < 3
    """)


def question_8(cursor, results):
    """SQL estimates reliable end times; Python compares Porto calendar dates."""
    print("Running q8_midnight_crossers...", flush=True)
    rows = []
    for trip_id, taxi_id, start, end in query_rows(cursor, """
        SELECT trip_id, taxi_id, start_time,
               TIMESTAMPADD(SECOND, (JSON_LENGTH(polyline) - 1) * 15, start_time) AS end_time
        FROM Trip WHERE NOT missing_data AND JSON_LENGTH(polyline) >= 3
        ORDER BY taxi_id, start_time, trip_id
    """):
        local_start, local_end = porto_time(start), porto_time(end)
        if local_end.date() == local_start.date() + timedelta(days=1):
            rows.append([trip_id, taxi_id, local_start.isoformat(), local_end.isoformat()])
    results["q8_midnight_crossers"] = (
        ["trip_id", "taxi_id", "start_porto", "end_porto"], rows,
    )


def question_9(cursor, results):
    """Compare endpoints of complete trajectories containing at least three points."""
    print("Running q9_circular_trips...", flush=True)
    rows = []
    for trip_id, taxi_id, count, missing, first, last in query_rows(cursor, """
        SELECT trip_id, taxi_id, JSON_LENGTH(polyline) AS point_count, missing_data,
               JSON_EXTRACT(polyline, '$[0]') AS first_point,
               JSON_EXTRACT(polyline, CONCAT('$[', JSON_LENGTH(polyline) - 1, ']')) AS last_point
        FROM Trip WHERE NOT missing_data AND JSON_LENGTH(polyline) >= 3
        ORDER BY taxi_id, start_time, trip_id
    """):
        endpoint_m = distance_m(json.loads(first), json.loads(last))
        if endpoint_m <= 50:
            rows.append([trip_id, taxi_id, endpoint_m, count, bool(missing)])
    results["q9_circular_trips"] = (
        ["trip_id", "taxi_id", "endpoint_distance_m", "point_count", "missing_data"], rows,
    )


IDLE_QUERY = """
    WITH trip_ends AS (
        SELECT trip_id, taxi_id, start_time,
               CASE WHEN NOT missing_data AND JSON_LENGTH(polyline) >= 3
                    THEN TIMESTAMPADD(SECOND, (JSON_LENGTH(polyline) - 1) * 15, start_time)
               END AS end_time
        FROM Trip
    ), ordered AS (
        SELECT taxi_id, start_time,
               ROW_NUMBER() OVER w AS trip_position,
               LAG(end_time) OVER w AS previous_end
        FROM trip_ends
        WINDOW w AS (PARTITION BY taxi_id ORDER BY start_time, trip_id)
    ), gaps AS (
        SELECT *, TIMESTAMPDIFF(SECOND, previous_end, start_time) AS idle_seconds
        FROM ordered
    ), averages AS (
        SELECT taxi_id,
               AVG(CASE WHEN idle_seconds >= 0 THEN idle_seconds END) / 60.0 AS avg_idle_minutes,
               SUM(CASE WHEN idle_seconds >= 0 THEN 1 ELSE 0 END) AS usable_gaps,
               SUM(CASE WHEN idle_seconds < 0 THEN 1 ELSE 0 END) AS negative_gaps_excluded,
               SUM(trip_position > 1 AND previous_end IS NULL) AS unknown_end_gaps_excluded
        FROM gaps GROUP BY taxi_id
    )
    SELECT * FROM averages
"""


def question_10(cursor, results):
    """Window functions retain incomplete trips in the chronological sequence."""
    sql_result(cursor, results, "q10_all_taxis_idle",
               IDLE_QUERY + " ORDER BY avg_idle_minutes DESC, taxi_id")
    sql_result(cursor, results, "q10_top_20_idle",
               IDLE_QUERY + " WHERE avg_idle_minutes IS NOT NULL"
               " ORDER BY avg_idle_minutes DESC, taxi_id LIMIT 20")


def write_results(results):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as file:
        file.write(POLICY + "\n")
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        for name, title in SECTIONS:
            headers, rows = results[name]
            file.write(f"\n{title}\nRows: {len(rows)}\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(["NULL" if value is None else value for value in row])


def main():
    connection = DbConnector()
    results = {}
    try:
        cursor = connection.cursor
        connection.db_connection.commit()
        connection.db_connection.start_transaction(
            isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True
        )
        question_1(cursor, results)
        question_2(cursor, results)
        question_3(cursor, results)
        question_4a(cursor, results)
        question_4b(cursor, results)
        question_5(cursor, results)
        question_6(cursor, results)
        question_7(cursor, results)
        question_8(cursor, results)
        question_9(cursor, results)
        question_10(cursor, results)
        write_results(results)
        print(f"All questions completed. Results saved in {OUTPUT}")
    finally:
        connection.db_connection.rollback()
        connection.close_connection()


if __name__ == "__main__":
    main()
