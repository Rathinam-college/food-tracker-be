import psycopg2
import sys

def test_connection(dbname):
    params = {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "9789474804",
        "dbname": dbname
    }
    try:
        print(f"Attempting to connect to '{dbname}'...")
        conn = psycopg2.connect(**params)
        print(f"SUCCESS: Connected to '{dbname}'!")
        
        # Check if we can run a simple query
        cur = conn.cursor()
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        print(f"Database version: {db_version[0]}")
        
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"FAILURE: Could not connect to '{dbname}'.")
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    success_postgres = test_connection("postgres")
    print("-" * 30)
    success_food_tracker = test_connection("food_tracker")
    
    if success_postgres and success_food_tracker:
        sys.exit(0)
    else:
        sys.exit(1)
