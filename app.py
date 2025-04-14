from flask import Flask, jsonify, render_template, abort
import sqlite3
import os
import urllib.parse # Needed for decoding URIs from path

app = Flask(__name__)
DATABASE = os.environ.get('RENDER_DISK_PATH', 'panres_ontology.db')

def get_db():
    """Connects to the specific database."""
    db_path = DATABASE
    if not os.path.exists(db_path):
         print(f"CRITICAL ERROR: Database file '{db_path}' not found. Preprocessing might have failed or check path.")
         return None
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) # Read-only connection
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None

def query_db(query, args=(), one=False):
    """Helper function to query the database."""
    conn = get_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        print(f"Database query error: {e}\nQuery: {query}\nArgs: {args}")
        if conn: conn.close()
        return None

def get_label_or_uri_end(uri, label):
    """Helper to return label or fallback to URI fragment."""
    if label:
        return label
    if uri:
        return uri.split('#')[-1].split('/')[-1] # Get fragment or last path segment
    return "Unknown"

@app.route('/')
def index():
    """Serves the main HTML page."""
    if not os.path.exists(DATABASE):
         return "Error: Ontology database not found. Please run the preprocessing step.", 503
    return render_template('index.html')

@app.route('/api/toplevel-classes')
def get_toplevel_classes():
    """Gets classes that are not children in the ClassHierarchy table."""
    # Find classes in Classes that do NOT appear as child_uri in ClassHierarchy
    classes = query_db('''
        SELECT c.class_uri, c.label
        FROM Classes c
        LEFT JOIN ClassHierarchy ch ON c.class_uri = ch.child_uri
        WHERE ch.child_uri IS NULL
        ORDER BY c.label COLLATE NOCASE
    ''')
    if classes is None:
        return jsonify({"error": "Database query failed"}), 500
    # Use helper for fallback labels
    return jsonify([{
        "uri": row['class_uri'],
        "label": get_label_or_uri_end(row['class_uri'], row['label'])
        } for row in classes])

@app.route('/api/class-children/<path:class_uri>')
def get_class_children(class_uri):
    """Gets direct subclasses (children) for a given class URI."""
    # Decode the URI passed in the path
    decoded_uri = urllib.parse.unquote(class_uri)

    children = query_db('''
        SELECT c.class_uri, c.label
        FROM ClassHierarchy ch
        JOIN Classes c ON ch.child_uri = c.class_uri
        WHERE ch.parent_uri = ?
        ORDER BY c.label COLLATE NOCASE
    ''', [decoded_uri])

    if children is None:
        return jsonify({"error": "Database query failed"}), 500

    return jsonify([{
        "uri": row['class_uri'],
        "label": get_label_or_uri_end(row['class_uri'], row['label'])
        } for row in children])

@app.route('/api/class/<path:class_uri>')
def get_class_details(class_uri):
    """Gets details for a specific class URI."""
    decoded_uri = urllib.parse.unquote(class_uri)

    # Fetch class info
    class_info = query_db('SELECT class_uri, label FROM Classes WHERE class_uri = ?', [decoded_uri], one=True)

    if class_info is None:
        # Check if DB query failed or class not found
        conn = get_db()
        if not conn: return jsonify({"error": "Database connection failed"}), 500
        conn.close()
        abort(404, description=f"Class with URI '{decoded_uri}' not found.")

    # Find direct parents
    parents = query_db('''
        SELECT p.class_uri, p.label
        FROM ClassHierarchy ch
        JOIN Classes p ON ch.parent_uri = p.class_uri
        WHERE ch.child_uri = ?
        ORDER BY p.label COLLATE NOCASE
    ''', [decoded_uri])

    # Find direct children (subclasses) - reusing the children endpoint logic
    children = query_db('''
        SELECT c.class_uri, c.label
        FROM ClassHierarchy ch
        JOIN Classes c ON ch.child_uri = c.class_uri
        WHERE ch.parent_uri = ?
        ORDER BY c.label COLLATE NOCASE
    ''', [decoded_uri])

    # Find individuals belonging to this class
    individuals = query_db('''
        SELECT i.individual_uri, i.label
        FROM IndividualTypes it
        JOIN Individuals i ON it.individual_uri = i.individual_uri
        WHERE it.class_uri = ?
        ORDER BY i.label COLLATE NOCASE
    ''', [decoded_uri])

    # Handle potential DB errors during sub-queries
    if parents is None or children is None or individuals is None:
         return jsonify({"error": "Database query failed while fetching class details"}), 500

    return jsonify({
        "class": {
            "uri": class_info['class_uri'],
            "label": get_label_or_uri_end(class_info['class_uri'], class_info['label'])
        },
        "parents": [{
            "uri": row['class_uri'],
            "label": get_label_or_uri_end(row['class_uri'], row['label'])
            } for row in parents],
        "children": [{
            "uri": row['class_uri'],
            "label": get_label_or_uri_end(row['class_uri'], row['label'])
            } for row in children],
        "individuals": [{
            "uri": row['individual_uri'],
            "label": get_label_or_uri_end(row['individual_uri'], row['label'])
            } for row in individuals]
        # Properties/relationships directly on classes are less common, focus on individuals
    })

@app.route('/api/individual/<path:individual_uri>')
def get_individual_details(individual_uri):
    """Gets details for a specific individual URI."""
    decoded_uri = urllib.parse.unquote(individual_uri)

    # Fetch individual info (URI and Label)
    individual_info = query_db('SELECT individual_uri, label FROM Individuals WHERE individual_uri = ?', [decoded_uri], one=True)

    if individual_info is None:
        conn = get_db()
        if not conn: return jsonify({"error": "Database connection failed"}), 500
        conn.close()
        abort(404, description=f"Individual with URI '{decoded_uri}' not found.")

    # Get the class type(s) for this individual
    classes = query_db('''
        SELECT c.class_uri, c.label
        FROM IndividualTypes it
        JOIN Classes c ON it.class_uri = c.class_uri
        WHERE it.individual_uri = ?
        ORDER BY c.label COLLATE NOCASE
    ''', [decoded_uri])

    # Find datatype properties
    datatype_props = query_db('''
        SELECT dpa.property_uri, p.label as property_label, dpa.value, dpa.datatype_uri
        FROM DatatypePropertyAssertions dpa
        LEFT JOIN Properties p ON dpa.property_uri = p.property_uri
        WHERE dpa.individual_uri = ?
        ORDER BY p.label COLLATE NOCASE, dpa.value COLLATE NOCASE
    ''', [decoded_uri])

    # Find object properties (relationships)
    object_props = query_db('''
        SELECT
            opa.property_uri,
            p.label as property_label,
            opa.object_uri,
            COALESCE(i.label, c.label) as object_label, -- Get label from Individuals or Classes table
            CASE
                WHEN i.individual_uri IS NOT NULL THEN 'individual'
                WHEN c.class_uri IS NOT NULL THEN 'class'
                ELSE 'uri' -- Fallback if object is not in Individuals or Classes
            END as object_type
        FROM ObjectPropertyAssertions opa
        LEFT JOIN Properties p ON opa.property_uri = p.property_uri
        LEFT JOIN Individuals i ON opa.object_uri = i.individual_uri -- Check if object is an Individual
        LEFT JOIN Classes c ON opa.object_uri = c.class_uri       -- Check if object is a Class
        WHERE opa.subject_uri = ?
        ORDER BY p.label COLLATE NOCASE, object_label COLLATE NOCASE
    ''', [decoded_uri])

    # Handle potential DB errors
    if classes is None or datatype_props is None or object_props is None:
         return jsonify({"error": "Database query failed while fetching individual details"}), 500

    return jsonify({
        "individual": {
            "uri": individual_info['individual_uri'],
            "label": get_label_or_uri_end(individual_info['individual_uri'], individual_info['label'])
        },
        "classes": [{
            "uri": row['class_uri'],
            "label": get_label_or_uri_end(row['class_uri'], row['label'])
            } for row in classes],
        "datatype_properties": [{
            "property_uri": row['property_uri'],
            "property_label": get_label_or_uri_end(row['property_uri'], row['property_label']),
            "value": row['value'],
            "datatype": row['datatype_uri']
            } for row in datatype_props],
        "object_properties": [{
            "property_uri": row['property_uri'],
            "property_label": get_label_or_uri_end(row['property_uri'], row['property_label']),
            "object_uri": row['object_uri'],
            "object_label": get_label_or_uri_end(row['object_uri'], row['object_label']),
            "object_type": row['object_type']
            } for row in object_props]
    })

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
         print(f"ERROR: Database file '{DATABASE}' not found.")
         print("Please ensure the preprocessing script (`pan2db.py`) has run successfully.")
         import sys
         sys.exit(1) # Exit if DB is missing when running directly
    else:
        print(f"Database found at {DATABASE}. Starting Flask server.")
        port = int(os.environ.get("PORT", 5001))
        # Set debug=True for local development if desired, False for production
        app.run(host='0.0.0.0', port=port, debug=True) # Enable debug for development
