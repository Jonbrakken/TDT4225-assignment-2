import mysql.connector as mysql


class DbConnector:
    """
    Connects to the local MySQL server running in Docker.
    Connector needs HOST, DATABASE, USER and PASSWORD to connect,
    using the Docker port published on the host: 3308.

    Example:
    HOST = "127.0.0.1" // Local Docker database host
    DATABASE = "ex2_db" // Database name, if you just want to connect to MySQL server, leave it empty
    USER = "jonhbrae" // This is the user you created and added privileges for
    PASSWORD = "porto_local_only" // The password you set for said user
    """

    def __init__(self,
                 HOST="127.0.0.1",
                 DATABASE="ex2_db",
                 USER="jonhbrae",
                 PASSWORD="porto_local_only"):
        #Connect to the database
        try:
            self.db_connection = mysql.connect(host=HOST, database=DATABASE, user=USER, password=PASSWORD, port=3308)
        except Exception as e:
            print("ERROR: Failed to connect to db:", e)

        #set the db cursor
        self.cursor = self.db_connection.cursor()

        print("Connected to:", self.db_connection.get_server_info())
        #get database information
        self.cursor.execute("select database();")
        database_name = self.cursor.fetchone()
        print("You are connected to the database:", database_name)
        print("-----------------------------------------------\n")

    def close_connection(self):
        server_info = self.db_connection.get_server_info()
        #close the cursor
        self.cursor.close()
        #close the DB connection
        self.db_connection.close()
        print("\n-----------------------------------------------")
        print("Connection to %s is closed" % server_info)
