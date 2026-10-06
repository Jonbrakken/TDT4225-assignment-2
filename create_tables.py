from DbConnector import DbConnector
from tabulate import tabulate


connection = DbConnector()
cursor = connection.cursor

try:
    cursor.execute("SHOW TABLES")
    tables = {row[0] for row in cursor.fetchall()}

    #Replace the old schema only when Trip and GPSPoint are empty.
    old_schema = "GPSPoint" in tables
    if "Trip" in tables:
        cursor.execute("SHOW COLUMNS FROM Trip")
        old_schema = old_schema or "polyline" not in {row[0] for row in cursor.fetchall()}

    if old_schema:
        for table in ("GPSPoint", "Trip"):
            if table in tables:
                cursor.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
                if cursor.fetchone():
                    raise RuntimeError(
                        "The old tables contain data. Empty them intentionally before "
                        "running this script; no existing data has been deleted."
                    )
        cursor.execute("DROP TABLE IF EXISTS GPSPoint")
        cursor.execute("DROP TABLE IF EXISTS Trip")
        print("Replaced the empty tables from the old schema.")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Taxi (
            taxi_id BIGINT PRIMARY KEY
        ) ENGINE=InnoDB
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Trip (
            trip_id VARCHAR(30) PRIMARY KEY,
            taxi_id BIGINT NOT NULL,
            call_type CHAR(1),
            origin_call BIGINT,
            origin_stand INT,
            start_time DATETIME NOT NULL COMMENT 'UTC',
            day_type CHAR(1),
            missing_data BOOLEAN NOT NULL,
            polyline JSON NOT NULL,

            FOREIGN KEY (taxi_id) REFERENCES Taxi(taxi_id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            INDEX idx_taxi_start (taxi_id, start_time)
        ) ENGINE=InnoDB
    """)

    #JSON_LENGTH(polyline) gives the number of points, including 0 for [].
    #Calculate distances and durations in the Part 2 program.
    connection.db_connection.commit()
    cursor.execute("SHOW TABLES")
    print(tabulate(cursor.fetchall(), headers=cursor.column_names))
finally:
    connection.close_connection()
