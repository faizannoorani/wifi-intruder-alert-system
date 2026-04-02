# Database and known devices configuration
import mysql.connector

db_config = {
    'host': 'localhost',
    'user': 'root',          # your MySQL username
    'password': 'faizan123@A1',  # your MySQL password
    'database': 'wifi_monitors'
}

def get_connection():
    """Return a new MySQL connection using the config."""
    return mysql.connector.connect(**db_config) 



