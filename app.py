from flask import Flask, jsonify, render_template, abort
import sqlite3
import os
from rdflib import OWL

app = Flask(__name__)
DATABASE = os.environ.get('RENDER_DISK_PATH', '/var/data/ontology/ontology.db')
BASE_IRI = "http://myonto.com/PanResOntology.owl#" # Match the one used in preprocessing

def get_db():
    """Connects to the specific database."""
    if not os.path.exists(DATABASE):
         # This is a critical error if the API is running but DB is missing
         print(f"CRITICAL ERROR: Database file '{DATABASE}' not found. Preprocessing might have failed.")
         # Log this error prominently
         # In a real app, you might return a specific error state or abort
         return None # Or raise an exception
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
        # Enable foreign keys for this connection if needed (usually more for writes)
        # conn.execute("PRAGMA foreign_keys = ON;")
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None

def query_db(query, args=(), one=False):
    """Helper function to query the database."""
    conn = get_db()
    if not conn:
        # Return None or an empty list/dict to indicate DB issue upstream
        return None # Or [] if expecting a list
    try:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        print(f"Database query error: {e}\nQuery: {query}\nArgs: {args}")
        conn.close()
        return None # Indicate error

@app.route('/')
def index():
    """Serves the main HTML page."""
    # Check if DB exists before rendering page that relies on it
    if not os.path.exists(DATABASE):
         return "Error: Ontology database not found. Please run the preprocessing step.", 503 # Service Unavailable
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/toplevel-classes')
def get_toplevel_classes():
    """Gets classes that have no parent in the DB (parent_uri IS NULL)."""
    # This relies on the preprocessing script correctly identifying parent relationships.
    # Classes whose parent wasn't imported or was owl:Thing might appear here.
    classes = query_db('''
        SELECT uri, name, label, description
        FROM classes
        WHERE parent_uri IS NULL
        ORDER BY label COLLATE NOCASE
    ''')
    if classes is None:
        # query_db handles logging, return appropriate HTTP status
        return jsonify({"error": "Database query failed"}), 500
    return jsonify([dict(ix) for ix in classes])

@app.route('/api/class/<string:class_name>')
def get_class_details(class_name):
    """Gets details for a specific class, including parent, children, and individuals."""
    # Fetch class info including its parent's URI
    class_info = query_db('''
        SELECT c.uri, c.name, c.label, c.description, c.parent_uri, p.name as parent_name, p.label as parent_label
        FROM classes c
        LEFT JOIN classes p ON c.parent_uri = p.uri
        WHERE c.name = ?
    ''', [class_name], one=True)

    if class_info is None:
        # Check if the DB query itself failed or just class not found
        conn = get_db()
        if not conn:
             return jsonify({"error": "Database connection failed"}), 500
        conn.close()
        # If DB connected but class not found, it's 404
        abort(404, description=f"Class '{class_name}' not found.")

    class_uri = class_info['uri']

    # Find direct subclasses
    subclasses = query_db('''
        SELECT uri, name, label FROM classes WHERE parent_uri = ? ORDER BY label COLLATE NOCASE
    ''', [class_uri])

    # Find direct individuals
    individuals = query_db('''
        SELECT uri, name, label FROM individuals WHERE class_uri = ? ORDER BY label COLLATE NOCASE
    ''', [class_uri])

    # Find properties/relationships *directly* associated with the class URI itself
    # Note: The current preprocessing script focuses on individual properties/relationships.
    # If you need class-level ones, ensure they are populated in the DB.
    # These queries might return empty if only individuals have props/rels stored.
    properties = query_db('''
        SELECT predicate_name, value_literal, value_type FROM properties WHERE subject_uri = ?
    ''', [class_uri])
    relationships = query_db('''
        SELECT r.predicate_name, r.object_uri,
               COALESCE(o.name, o_cls.name, SUBSTR(r.object_uri, INSTR(r.object_uri, '#') + 1)) as object_name,
               CASE WHEN o.uri IS NOT NULL THEN 'individual' WHEN o_cls.uri IS NOT NULL THEN 'class' ELSE 'uri' END as object_type
        FROM relationships r
        LEFT JOIN individuals o ON r.object_uri = o.uri
        LEFT JOIN classes o_cls ON r.object_uri = o_cls.uri
        WHERE r.subject_uri = ?
    ''', [class_uri])

    # Handle potential DB errors during sub-queries
    if subclasses is None or individuals is None or properties is None or relationships is None:
         return jsonify({"error": "Database query failed while fetching class details"}), 500

    # Prepare parent info for JSON
    parent_info = None
    if class_info['parent_uri']:
        parent_info = {
            "uri": class_info['parent_uri'],
            "name": class_info['parent_name'],
            "label": class_info['parent_label'] or class_info['parent_name'] # Fallback label
        }

    return jsonify({
        "class": dict(class_info),
        "parent": parent_info, # Add parent info
        "subclasses": [dict(ix) for ix in subclasses],
        "individuals": [dict(ix) for ix in individuals],
        "properties": [dict(ix) for ix in properties], # May be empty
        "relationships": [dict(ix) for ix in relationships] # May be empty
    })

@app.route('/api/individual/<string:individual_name>')
def get_individual_details(individual_name):
    """Gets details for a specific individual, including properties and relationships."""
    individual_info = query_db('SELECT * FROM individuals WHERE name = ?', [individual_name], one=True)

    if individual_info is None:
        conn = get_db()
        if not conn:
             return jsonify({"error": "Database connection failed"}), 500
        conn.close()
        abort(404, description=f"Individual '{individual_name}' not found.")

    individual_uri = individual_info['uri']

    # Get the class info for this individual
    class_info = query_db('SELECT uri, name, label FROM classes WHERE uri = ?', [individual_info['class_uri']], one=True)

    # Find properties
    properties = query_db('''
        SELECT predicate_uri, predicate_name, value_literal, value_type FROM properties WHERE subject_uri = ?
    ''', [individual_uri])

    # Find relationships (linking to other individuals or classes)
    relationships = query_db('''
        SELECT r.predicate_uri, r.predicate_name, r.object_uri,
               COALESCE(o.name, o_cls.name, SUBSTR(r.object_uri, INSTR(r.object_uri, '#') + 1)) as object_name, -- Get name if object is known individual/class
               CASE WHEN o.uri IS NOT NULL THEN 'individual' WHEN o_cls.uri IS NOT NULL THEN 'class' ELSE 'uri' END as object_type
        FROM relationships r
        LEFT JOIN individuals o ON r.object_uri = o.uri        -- Join if object is an individual
        LEFT JOIN classes o_cls ON r.object_uri = o_cls.uri    -- Join if object is a class
        WHERE r.subject_uri = ?
    ''', [individual_uri])

    # Handle potential DB errors
    if class_info is None or properties is None or relationships is None:
         # Check if class_info specifically failed vs other queries
         if class_info is None and individual_info['class_uri']:
             print(f"Warning: Could not find class with URI {individual_info['class_uri']} for individual {individual_name}")
             # Decide how to handle - return individual data without class, or error out?
             # Let's return data without class for now.
         elif properties is None or relationships is None:
              return jsonify({"error": "Database query failed while fetching individual details"}), 500


    return jsonify({
        "individual": dict(individual_info),
        "class": dict(class_info) if class_info else None, # Return class if found
        "properties": [dict(ix) for ix in properties],
        "relationships": [dict(ix) for ix in relationships]
    })


if __name__ == '__main__':
    # Check if DB exists before running Flask app
    if not os.path.exists(DATABASE):
         print(f"ERROR: Database file '{DATABASE}' not found.")
         print("Please ensure 'preprocess_ontology.py' has run successfully.")
         # Optionally exit here if running locally and DB is mandatory
         # import sys
         # sys.exit(1)
    else:
        print(f"Database found at {DATABASE}. Starting Flask server.")
        # For Render deployment, Gunicorn will bind the port.
        # Use host='0.0.0.0' to make it accessible externally.
        # The port is usually set by Render via the PORT env var.
        port = int(os.environ.get("PORT", 5001)) # Use different default port if needed
        # Set debug=False for production on Render
        # Set debug=True for local development if desired
        app.run(host='0.0.0.0', port=port, debug=False)
