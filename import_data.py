import csv
import io
import json
import math
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from zipfile import ZipFile

from DbConnector import DbConnector


LIMIT = 0 #Start with a sample. Set to 0 to import the full dataset.
BATCH_SIZE = 1000  #Maximum trips per insert/commit; large JSON batches flush sooner.
INPUT_FILE = "porto.zip"  #Or "porto/porto.csv" if you extract it first.
folder = Path(__file__).resolve().parent


def optional_integer(value, maximum, field, warnings):
    """Blank optional IDs become NULL; malformed ones are logged and become NULL."""
    value = value.strip()
    if not value:
        return None
    try:
        number = int(value)
        if not 0 <= number <= maximum:
            raise ValueError()
        return number
    except ValueError:
        warnings.append(f"{field}: invalid identifier changed to NULL")
        return None


def clean_trip(row):
    """Return database values, point count, and warnings for one CSV row."""
    warnings = []
    trip_id = row["TRIP_ID"].strip()
    if not trip_id or len(trip_id) > 30:
        raise ValueError("Missing or too long trip ID")

    taxi_id = int(row["TAXI_ID"])
    if not 0 <= taxi_id <= 9223372036854775807:
        raise ValueError("Taxi ID outside BIGINT range")

    #Unknown optional codes do not discard an otherwise usable trip.
    call_type = row["CALL_TYPE"].strip().upper()
    if call_type not in ("A", "B", "C"):
        warnings.append("Unknown call type changed to NULL")
        call_type = None
    day_type = row.get("DAY_TYPE", row.get("DAYTYPE", "")).strip().upper()
    if day_type not in ("A", "B", "C"):
        warnings.append("Unknown day type changed to NULL")
        day_type = None

    origin_call = optional_integer(row["ORIGIN_CALL"], 9223372036854775807, "ORIGIN_CALL", warnings)
    origin_stand = optional_integer(row["ORIGIN_STAND"], 2147483647, "ORIGIN_STAND", warnings)
    #Keep unexpected populated IDs for inspection rather than silently erasing them.
    if origin_call is not None and call_type != "A":
        warnings.append("ORIGIN_CALL populated for a call type other than A; retained")
    if origin_stand is not None and call_type != "B":
        warnings.append("ORIGIN_STAND populated for a call type other than B; retained")

    start_time = datetime.fromtimestamp(int(row["TIMESTAMP"]), timezone.utc)
    if start_time.year < 1000:
        raise ValueError("Timestamp outside MySQL DATETIME range")
    #MySQL DATETIME has no timezone: consistently store the UTC value.
    start_time = start_time.replace(tzinfo=None)

    missing_text = row["MISSING_DATA"].strip().lower()
    if missing_text not in ("true", "false"):
        raise ValueError("Unknown missing-data flag")
    missing_data = missing_text == "true"

    points = json.loads(row["POLYLINE"])
    if not isinstance(points, list):
        raise ValueError("POLYLINE must be a list")
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Each GPS point must contain longitude and latitude")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in point):
            raise ValueError("GPS coordinates must be finite numbers")
        longitude, latitude = point
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("GPS coordinate outside valid range")

    # Preserve every point in its original order, including repeated coordinates.
    # Empty and short trajectories and MISSING_DATA=True trips are all retained.
    polyline = json.dumps(points, separators=(",", ":"), allow_nan=False)
    values = (
        trip_id, taxi_id, call_type, origin_call, origin_stand,
        start_time, day_type, missing_data, polyline,
    )
    return values, len(points), warnings


def csv_rows():
    """Stream either the ZIP or an extracted CSV, closing files after each pass."""
    with ExitStack() as files:
        path = folder / INPUT_FILE
        if path.suffix.lower() == ".zip":
            archive = files.enter_context(ZipFile(path))
            source = files.enter_context(archive.open("porto/porto.csv"))
            text = files.enter_context(io.TextIOWrapper(source, encoding="utf-8"))
        else:
            text = files.enter_context(path.open(encoding="utf-8", newline=""))
        reader = csv.DictReader(text)
        required = {"TRIP_ID", "TAXI_ID", "CALL_TYPE", "ORIGIN_CALL", "ORIGIN_STAND",
                    "TIMESTAMP", "MISSING_DATA", "POLYLINE"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required columns")
        if not {"DAY_TYPE", "DAYTYPE"}.intersection(reader.fieldnames):
            raise ValueError("CSV is missing its day-type column")
        rows = islice(reader, LIMIT) if LIMIT else reader
        for row in rows:
            yield reader.line_num, row


def commit_changes():
    """Send many trips in one INSERT, then commit taxis and trips together."""
    global batch_bytes
    new_taxis = {values[1] for values in inserts + updates} - known_taxis
    if new_taxis:
        cursor.executemany(
            "INSERT INTO Taxi (taxi_id) VALUES (%s) "
            "ON DUPLICATE KEY UPDATE taxi_id = Taxi.taxi_id",
            [(taxi_id,) for taxi_id in new_taxis],
        )
    if inserts:
        # Connector/Python combines these rows into one multi-row INSERT.
        cursor.executemany(
            """INSERT INTO Trip
               (trip_id, taxi_id, call_type, origin_call, origin_stand,
                start_time, day_type, missing_data, polyline)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", inserts,
        )
    # Only conflicting IDs need updates, so keep this small loop straightforward.
    for values in updates:
        cursor.execute(
            """UPDATE Trip SET taxi_id=%s, call_type=%s, origin_call=%s,
               origin_stand=%s, start_time=%s, day_type=%s,
               missing_data=%s, polyline=%s WHERE trip_id=%s""",
            values[1:] + (values[0],),
        )
    connection.db_connection.commit()
    known_taxis.update(new_taxis)
    counts["Trips inserted"] += len(inserts)
    counts["Trips updated"] += len(updates)
    inserts.clear()
    updates.clear()
    batch_bytes = 0
    print(f"Committed {counts['Trips inserted']:,} inserts and "
          f"{counts['Trips updated']:,} updates", flush=True)


def save_trip(values, check_existing=False):
    """Queue a new trip or a correction, and flush when the batch is full."""
    global batch_bytes
    trip_id = values[0]
    action = "Trips inserted"
    if trip_id in known_ids:
        if not check_existing:
            counts["Existing trips unchanged"] += 1
            return
        cursor.execute(
            """SELECT trip_id, taxi_id, call_type, origin_call, origin_stand,
               start_time, day_type, missing_data, polyline FROM Trip WHERE trip_id=%s""",
            (trip_id,),
        )
        stored = cursor.fetchone()
        # JSON whitespace can differ after storage in MySQL.
        if stored[:-1] == values[:-1] and json.loads(stored[-1]) == json.loads(values[-1]):
            counts["Existing trips unchanged"] += 1
            return
        log.writerow(["", trip_id, "database replacement planned",
                      "Previous database values; replacement will use the selected CSV row",
                      json.dumps(dict(zip(cursor.column_names, stored)), default=str)])
        action = "Trips updated"

    # Allow for SQL quoting/escaping as well as JSON size. Leave packet headroom.
    row_bytes = 1024 + 2 * sum(len(str(value).encode("utf-8")) for value in values)
    if row_bytes > packet_limit:
        raise ValueError(f"Trip {trip_id} exceeds the conservative packet limit; "
                         "increase MySQL max_allowed_packet before retrying.")
    if inserts or updates:
        if batch_bytes + row_bytes > batch_byte_limit:
            commit_changes()
    if action == "Trips inserted":
        inserts.append(values)
    else:
        updates.append(values)
    batch_bytes += row_bytes
    if len(inserts) + len(updates) >= BATCH_SIZE or batch_bytes >= batch_byte_limit:
        commit_changes()


counts = Counter()
inserts = []
updates = []
batch_bytes = 0
run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
log_path = folder / f"import_issues_{run_id}.csv"
status = "Interrupted or failed"
connection = DbConnector()
cursor = connection.cursor

try:
    cursor.execute("SHOW COLUMNS FROM Trip")
    if "polyline" not in {row[0] for row in cursor.fetchall()}:
        raise RuntimeError("Run create_tables.py to set up the JSON schema first.")

    # 1. Find repeated IDs. Only their records need to be kept in memory.
    cursor.execute("SELECT @@max_allowed_packet")
    packet_limit = int(cursor.fetchone()[0]) // 2
    batch_byte_limit = min(1024 * 1024, packet_limit)
    cursor.execute("SELECT taxi_id FROM Taxi")
    known_taxis = {taxi_id for (taxi_id,) in cursor}
    print(f"Reading {INPUT_FILE}; up to {BATCH_SIZE} trips or about "
          f"{batch_byte_limit // 1024} KiB per batch", flush=True)
    print("Checking the CSV for repeated trip IDs...", flush=True)
    id_counts = Counter((row.get("TRIP_ID") or "").strip() for _, row in csv_rows())
    duplicates = {trip_id: [] for trip_id, number in id_counts.items() if number > 1}
    counts["Repeated ID groups"] = len(duplicates)
    del id_counts
    cursor.execute("SELECT trip_id FROM Trip")
    known_ids = {trip_id for (trip_id,) in cursor}

    with log_path.open("w", newline="", encoding="utf-8") as log_file:
        log = csv.writer(log_file)
        log.writerow(["csv_line", "trip_id", "action", "reason", "original_row_json"])

        # 2. Clean each row. Import unique IDs and set duplicate groups aside.
        for line, row in csv_rows():
            counts["Rows read"] += 1
            trip_id = (row.get("TRIP_ID") or "").strip()
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV row has missing or extra fields")
                values, point_count, warnings = clean_trip(row)
            except (ValueError, TypeError, OverflowError, OSError) as error:
                counts["Malformed rows rejected"] += 1
                log.writerow([line, trip_id, "rejected", str(error), json.dumps(row)])
                continue
            if warnings:
                counts["Source rows with warnings"] += 1
                log.writerow([line, trip_id, "warning", "; ".join(warnings), json.dumps(row)])

            if trip_id in duplicates:
                duplicates[trip_id].append({
                    "line": line, "original": row, "values": values, "points": point_count,
                })
            else:
                save_trip(values)

        # 3. For repeated IDs, choose the most points. Keep the first on a tie.
        for trip_id, records in duplicates.items():
            if not records:  # All occurrences failed validation.
                continue
            best = records[0]
            for record in records[1:]:
                if record["points"] > best["points"]:
                    best = record
            counts["Groups choosing a later row"] += best["line"] != records[0]["line"]

            seen = []
            for record in records:
                original = record["original"]
                if original in seen:
                    action = "Exact duplicate rows"
                elif seen:
                    action = "Conflicting rows"
                else:
                    action = "First occurrence"
                if seen:
                    counts[action] += 1
                seen.append(original)
                reason = (f"{record['points']} points; selected line {best['line']} "
                          f"with {best['points']} points. First wins ties.")
                log.writerow([record["line"], trip_id, action, reason, json.dumps(original)])

            # Also correct records saved by an earlier import using the old rule.
            save_trip(best["values"], check_existing=True)
        if inserts or updates:
            commit_changes()
    status = "Completed"

except BaseException:
    connection.db_connection.rollback()
    print("Import stopped. Earlier commits remain saved; rerun to continue.")
    raise
finally:
    summary = f"\nIMPORT SUMMARY — {status}\nRun: {run_id}\nLimit: {LIMIT or 'full dataset'}\n"
    for label in ("Rows read", "Trips inserted", "Trips updated", "Existing trips unchanged",
                  "Repeated ID groups", "Exact duplicate rows", "Conflicting rows",
                  "Groups choosing a later row", "Malformed rows rejected", "Source rows with warnings"):
        summary += f"{label}: {counts[label]:,}\n"
    summary += f"Issue log: {log_path.name}\n"
    print(summary)
    try:
        with (folder / "import_summary.txt").open("a", encoding="utf-8") as report:
            report.write(summary)
    finally:
        connection.close_connection()
