from flask import Flask, jsonify, render_template, abort
import sqlite3
import os

app = Flask(__name__)
DATABASE = 'ontology.db'
BASE_IRI = "http://myonto.com/PanResOntology.owl#" # Match the one used in preprocessing

def get_db():
    """Connects to the specific database."""
    if not os.path.exists(DATABASE):
         # Handle case where DB doesn't exist - maybe raise an error or log
         print(f"ERROR: Database file '{DATABASE}' not found. Run preprocess_ontology.py first.")
         return None # Or raise an exception
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
    return conn

def query_db(query, args=(), one=False):
    """Helper function to query the database."""
    conn = get_db()
    if not conn:
        return None # DB not found
    cur = conn.cursor()
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.close()
    return (rv[0] if rv else None) if one else rv

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/toplevel-classes')
def get_toplevel_classes():
    """Gets classes that have no parent or whose parent is owl:Thing or the base Resistance class."""
    # Adjust the parent check based on your ontology's root structure if needed
    # This example assumes top-level classes have NULL parent_uri in our simplified schema
    # Or parent is the base 'Resistance' class if you have one defined at the root
    resistance_uri = BASE_IRI + "Resistance" # Example base class URI
    classes = query_db('''
        SELECT uri, name, label, description
        FROM classes
        WHERE parent_uri IS NULL OR parent_uri = ? OR parent_uri = ?
        ORDER BY label
    ''', (str(OWL.Thing), resistance_uri))
    if classes is None:
        return jsonify({"error": "Database not found"}), 500
    return jsonify([dict(ix) for ix in classes])

@app.route('/api/class/<string:class_name>')
def get_class_details(class_name):
    """Gets details for a specific class, including children and individuals."""
    class_info = query_db('SELECT * FROM classes WHERE name = ?', [class_name], one=True)
    if class_info is None:
        abort(404, description=f"Class '{class_name}' not found.")

    class_uri = class_info['uri']

    # Find direct subclasses
    subclasses = query_db('''
        SELECT uri, name, label FROM classes WHERE parent_uri = ? ORDER BY label
    ''', [class_uri])

    # Find direct individuals
    individuals = query_db('''
        SELECT uri, name, label FROM individuals WHERE class_uri = ? ORDER BY label
    ''', [class_uri])

    # Find properties/relationships *directly* associated with the class URI itself (less common)
    properties = query_db('''
        SELECT predicate_name, value_literal, value_type FROM properties WHERE subject_uri = ?
    ''', [class_uri])
    relationships = query_db('''
        SELECT r.predicate_name, r.object_uri, COALESCE(o.name, o_cls.name, SUBSTR(r.object_uri, INSTR(r.object_uri, '#') + 1)) as object_name
        FROM relationships r
        LEFT JOIN individuals o ON r.object_uri = o.uri
        LEFT JOIN classes o_cls ON r.object_uri = o_cls.uri
        WHERE r.subject_uri = ?
    ''', [class_uri])


    return jsonify({
        "class": dict(class_info),
        "subclasses": [dict(ix) for ix in subclasses],
        "individuals": [dict(ix) for ix in individuals],
        "properties": [dict(ix) for ix in properties],
        "relationships": [dict(ix) for ix in relationships]
    })

@app.route('/api/individual/<string:individual_name>')
def get_individual_details(individual_name):
    """Gets details for a specific individual, including properties and relationships."""
    individual_info = query_db('SELECT * FROM individuals WHERE name = ?', [individual_name], one=True)
    if individual_info is None:
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

    return jsonify({
        "individual": dict(individual_info),
        "class": dict(class_info) if class_info else None,
        "properties": [dict(ix) for ix in properties],
        "relationships": [dict(ix) for ix in relationships]
    })


if __name__ == '__main__':
    # Check if DB exists before running
    if not os.path.exists(DATABASE):
         print(f"ERROR: Database file '{DATABASE}' not found.")
         print("Please run 'python preprocess_ontology.py' first.")
    else:
        app.run(debug=True) # debug=True for development, remove for production
