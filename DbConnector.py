import os

import mysql.connector as mysql


class DbConnector:
    """Connect to local MySQL; MYSQL_* environment variables override defaults."""

    def __init__(self, HOST=None, DATABASE=None, USER=None, PASSWORD=None, PORT=None):
        host = HOST if HOST is not None else os.getenv("MYSQL_HOST", "127.0.0.1")
        database = DATABASE if DATABASE is not None else os.getenv("MYSQL_DATABASE", "ex2_db")
        user = USER if USER is not None else os.getenv("MYSQL_USER", "jonhbrae")
        password = PASSWORD if PASSWORD is not None else os.getenv("MYSQL_PASSWORD", "porto_local_only")
        port = int(PORT if PORT is not None else os.getenv("MYSQL_PORT", "3308"))
        try:
            self.db_connection = mysql.connect(
                host=host, database=database, user=user, password=password,
                port=port, connection_timeout=10,
            )
        except mysql.Error as error:
            raise RuntimeError(
                f"Cannot connect to MySQL at {host}:{port}, database {database}. "
                "Start it with ./local_db.ps1 up and check your MYSQL_* settings."
            ) from error
        self.cursor = self.db_connection.cursor()
        print(f"Connected to MySQL {self.db_connection.get_server_info()}, database {database}")

    def close_connection(self):
        self.cursor.close()
        self.db_connection.close()
        print("Database connection closed.")
