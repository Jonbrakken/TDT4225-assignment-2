from DbConnector import DbConnector


connection = DbConnector()
cursor = connection.cursor

try:
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
            missing_data BOOLEAN NOT NULL,
            polyline JSON NOT NULL,

            FOREIGN KEY (taxi_id) REFERENCES Taxi(taxi_id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            INDEX idx_taxi_start (taxi_id, start_time)
        ) ENGINE=InnoDB
    """)

    connection.db_connection.commit()
finally:
    connection.close_connection()
