# Porto taxi assignment

Run these commands in PowerShell from the project directory. The default input
is `data/train.csv`. Input paths are relative to the project. `--input porto.zip`
also supports the original ZIP with `porto/porto.csv` inside it.

## Local setup

Docker Desktop must be running. The helper finds Docker even when it is not on PATH.

```powershell
./local_db.ps1 up
./local_db.ps1 status
# Only needed if there is no working virtual environment:
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

MySQL runs at `127.0.0.1:3308`. Compose creates database `ex2_db`, user
`jonhbrae`, and the local development password in `compose.yaml`.
`DbConnector.py` uses matching defaults. Override them with `MYSQL_HOST`,
`MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, or `MYSQL_PASSWORD` if needed.

The current container uses MySQL 8.4; the assignment specifies 8.0.39.
If switching versions, use a separate new volume and reimport the CSV instead
of downgrading an existing database volume.

## Explore, import, and analyse

For an interactive SQL console, run `./local_db.ps1 sql` and enter the local
development password `porto_local_only`. At the `mysql>` prompt, try:

```sql
SHOW TABLES;
DESCRIBE Trip;
SELECT trip_id, taxi_id, call_type, start_time,
       JSON_LENGTH(polyline) AS gps_points
FROM Trip LIMIT 5;
```

Finish each SQL statement with a semicolon. Type `exit` to leave the console;
the database keeps running.

```powershell
./.venv/Scripts/python.exe eda.py --limit 1000
./.venv/Scripts/python.exe create_tables.py
./.venv/Scripts/python.exe import_data.py --limit 1000
```

After the trial succeeds, run the full dataset. The importer handles already
imported IDs, so the full import can follow the trial.

```powershell
./.venv/Scripts/python.exe eda.py | Tee-Object -FilePath eda_results.txt
./.venv/Scripts/python.exe import_data.py
./.venv/Scripts/python.exe queries.py
```

Import logs are `import_issues_*.csv` and `import_summary.txt`.
Query results go to `part2_results/results.txt`; running queries replaces it.
Preserve the previous results during a trial by choosing another output:

```powershell
$env:RESULTS_FILE = 'part2_results/trial_results.txt'
./.venv/Scripts/python.exe queries.py
Remove-Item Env:RESULTS_FILE
```

Record whether results describe a sample or the full dataset. The EDA, schema,
cleaning rules and verified results provide the material for the report.
`./local_db.ps1 stop` stops MySQL while preserving data in the Docker volume.

## Verified local run, 5 October 2026

The full CSV has been explored and imported, and all ten questions have been run
against the local MySQL 8.4.11 database. The CSV contains 1,710,670 rows;
after resolving repeated trip IDs the database contains 1,710,589 trips,
448 taxis and 83,409,283 GPS points. No malformed source rows were rejected.

Full EDA output is in `eda_results.txt`; the import summary is in
`import_summary.txt`. The newly generated full analysis is in
`part2_results/local_results.txt`. `part2_results/trial_results.txt` only
describes the initial 1,000-row trial. The previous `part2_results/results.txt`
was preserved.
