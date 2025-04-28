import os
from app import create_and_populate_fts, DATABASE # Import necessary items

def on_starting(server):
    """Gunicorn hook called once on master process start."""
    print("Gunicorn starting - Initializing FTS index...")
    if os.path.exists(DATABASE):
        try:
            create_and_populate_fts(DATABASE)
            print("FTS index setup check completed.")
        except Exception as e:
            print(f"ERROR: Failed to initialize FTS index: {e}")
            # Decide if the server should exit or continue
            # import sys
            # sys.exit("Exiting due to FTS initialization failure.")
    else:
        print(f"ERROR: Database file '{DATABASE}' not found. Cannot initialize FTS index.")
        # import sys
        # sys.exit("Exiting due to missing database file.")

# Other Gunicorn settings
bind = "0.0.0.0:5001"
workers = 4 # Example
# ... 