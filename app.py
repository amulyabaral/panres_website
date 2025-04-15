import sqlite3
from flask import Flask, render_template, g, abort

# --- Configuration ---
DATABASE = 'panres_ontology.db' # Make sure this file is in the same directory or provide the correct path
# Define the 'types' we want to be able to browse easily from the index page.
# The 'key' is the display name, the 'value' is the exact identifier used in the 'object'
# column for 'rdf:type' predicates in your database.
# IMPORTANT: Double-check these values match the output of owl2sqlite.py exactly!
# Example: If your script outputs 'myonto:PanGene', use that instead of 'PanGene'.
BROWSEABLE_TYPES = {
    "Pan Genes": "PanGene", # Verify this matches the DB output
    "Original Genes": "OriginalGene", # Verify this matches the DB output
    "Databases": "Database", # Verify this matches the DB output
    "Antibiotic Classes": "AntibioticResistanceClass", # Verify this matches the DB output
    "Antibiotic Phenotypes": "AntibioticResistancePhenotype", # Verify this matches the DB output
    "Antibiotic Mechanisms": "AntibioticResistanceMechanism", # Verify this matches the DB output
    "Metals": "Metal", # Verify this matches the DB output
    "Biocides": "Biocide", # Verify this matches the DB output
    # Add/modify types based on your model.md and owl2sqlite.py output
}

# --- Flask App Setup ---
app = Flask(__name__)
app.config['DATABASE'] = DATABASE

# --- Database Helper Functions ---
def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(
                app.config['DATABASE'],
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            # Return rows as dictionaries (easier to access columns by name)
            g.db.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            app.logger.error(f"Database connection error: {e}")
            # In a real app, you might want to show a user-friendly error page
            abort(500, description="Database connection failed.")
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()
    if error:
        app.logger.error(f"Application context teardown error: {error}")


def query_db(query, args=(), one=False):
    """Helper function to query the database and return results."""
    try:
        cur = get_db().execute(query, args)
        rv = cur.fetchall()
        cur.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        app.logger.error(f"Database query error: {e} \nQuery: {query} \nArgs: {args}")
        # Depending on the error, you might want to abort or return None/empty list
        abort(500, description="Database query failed.")


# --- Flask Routes ---
@app.route('/')
def index():
    """Homepage: Shows links to browse different types of entities."""
    return render_template('index.html', browse_types=BROWSEABLE_TYPES)

@app.route('/list/<item_type_id>')
def list_items(item_type_id):
    """Lists all items of a specific type (e.g., all PanGenes)."""
    # Find the display name for the title, default to the ID if not found
    display_name = item_type_id
    for name, type_id in BROWSEABLE_TYPES.items():
        if type_id == item_type_id:
            display_name = name
            break

    # Query for subjects that have the specified rdf:type
    items = query_db("""
        SELECT DISTINCT subject
        FROM Triples
        WHERE predicate = 'rdf:type' AND object = ?
        ORDER BY subject
    """, (item_type_id,))

    item_list = [row['subject'] for row in items]

    return render_template('list_items.html',
                           item_type_display=display_name,
                           item_type_id=item_type_id,
                           items=item_list)

@app.route('/details/<item_id>')
def details(item_id):
    """Shows details for a specific item (subject or object)."""
    # Query for all triples where the item is the SUBJECT
    outgoing_triples = query_db("""
        SELECT predicate, object, object_is_literal, object_datatype
        FROM Triples
        WHERE subject = ?
        ORDER BY predicate, object
    """, (item_id,))

    # Query for all triples where the item is the OBJECT (and is a resource, not a literal)
    incoming_triples = query_db("""
        SELECT subject, predicate
        FROM Triples
        WHERE object = ? AND object_is_literal = 0
        ORDER BY predicate, subject
    """, (item_id,))

    # Check if we found anything for this item_id either as subject or object
    if not outgoing_triples and not incoming_triples:
         # You could render a specific "Not Found" template or just show the details page empty
         app.logger.warning(f"No details found for item_id: {item_id}")
         # return render_template('not_found.html', item_id=item_id), 404

    return render_template('details.html',
                           item_id=item_id,
                           outgoing=outgoing_triples,
                           incoming=incoming_triples)


# --- Run the App ---
if __name__ == '__main__':
    # Use debug=True for development (auto-reloads, provides debugger)
    # Turn off debug mode for production deployment
    app.run(debug=True) 