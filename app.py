import sqlite3
from flask import Flask, render_template, g, abort, url_for
import os
import logging

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
# Define common predicates (adjust if your ontology uses different ones)
RDF_TYPE = 'rdf:type'
RDFS_LABEL = 'rdfs:label'
RDFS_COMMENT = 'rdfs:comment'
# Add other potential description predicates if used in your OWL file
DESCRIPTION_PREDICATES = [RDFS_COMMENT, 'description', 'dc:description', 'skos:definition']

# --- Flask App Setup ---
app = Flask(__name__)
app.config['DATABASE'] = DATABASE

# Configure logging
logging.basicConfig(level=logging.INFO) # Log INFO level messages and above
app.logger.setLevel(logging.INFO) # Ensure Flask's logger also respects INFO level

# --- Database Helper Functions ---
def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    db_path = app.config['DATABASE']
    if 'db' not in g:
        # Log the expected path and check existence
        abs_db_path = os.path.abspath(db_path)
        app.logger.info(f"Attempting to connect to database at: {abs_db_path}")
        if not os.path.exists(db_path):
             app.logger.error(f"!!!!!!!! Database file not found at expected path: {abs_db_path}")
             # Log CWD and contents for debugging on Render
             try:
                 cwd = os.getcwd()
                 app.logger.info(f"Current working directory: {cwd}")
                 files_in_cwd = os.listdir('.')
                 app.logger.info(f"Files in CWD: {files_in_cwd}")
             except OSError as list_e:
                 app.logger.error(f"Could not list files in CWD: {list_e}")
             abort(500, description="Database file not found. Check build logs.") # Abort if DB doesn't exist

        try:
            g.db = sqlite3.connect(
                db_path,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            g.db.row_factory = sqlite3.Row
            app.logger.info(f"Successfully connected to database: {abs_db_path}")
        except sqlite3.Error as e:
            app.logger.error(f"Database connection error: {e}")
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
    """Shows the main index page with browseable types and their counts."""
    db = get_db()
    counts = {}
    app.logger.info(f"Loading index page. Browseable types configured: {BROWSEABLE_TYPES}")

    for display_name, type_id in BROWSEABLE_TYPES.items():
        try:
            # Query to count distinct subjects for each type_id
            cursor = db.execute(
                f"SELECT COUNT(DISTINCT subject) FROM Triples WHERE predicate = ? AND object = ?",
                (RDF_TYPE, type_id) # Use constant
            )
            count_result = cursor.fetchone()
            counts[display_name] = count_result[0] if count_result else 0
            app.logger.debug(f"Count for {type_id} ({display_name}): {counts[display_name]}")
        except sqlite3.Error as e:
            app.logger.error(f"Error counting items for type '{type_id}': {e}")
            counts[display_name] = 'Error' # Indicate error on the page

    # Pass both BROWSEABLE_TYPES and counts to the template
    return render_template('index.html', browseable_types=BROWSEABLE_TYPES, counts=counts)

@app.route('/list/<item_type_id>')
def list_items(item_type_id):
    """Lists all items of a specific type."""
    db = get_db()

    # Find the display name corresponding to the type_id for the title
    display_name = "Unknown Type"
    found_type = False
    for name, type_val in BROWSEABLE_TYPES.items():
        if type_val == item_type_id:
            display_name = name
            found_type = True
            break

    app.logger.info(f"Listing items for type ID: {item_type_id} (Display Name: {display_name})")

    # Optional: Check if the type_id is even known before querying
    if not found_type:
         app.logger.warning(f"Attempted to list items for an unrecognized type_id: {item_type_id}")
         # Abort with 404 if the type isn't in our browseable list
         abort(404, description=f"Type '{item_type_id}' not recognized or configured for browsing.")

    try:
        cursor = db.execute(
            f"SELECT DISTINCT subject FROM Triples WHERE predicate = ? AND object = ? ORDER BY subject",
            (RDF_TYPE, item_type_id) # Use constant
        )
        # Use fetchall() which is fine for moderate lists, but consider pagination for very large ones
        items = [row['subject'] for row in cursor.fetchall()]
        app.logger.info(f"Found {len(items)} items for type {item_type_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching list for type '{item_type_id}': {e}")
        abort(500, description=f"Error retrieving list for {item_type_id}")

    return render_template('list.html', items=items, item_type_display=display_name, item_type_id=item_type_id)

@app.route('/details/<path:item_id>') # Use path converter to handle potential slashes in IDs
def details(item_id):
    """Shows details (properties and references) for a specific item."""
    db = get_db()
    app.logger.info(f"Fetching details for item ID: {item_id}")

    # Fetch outgoing properties (where item_id is the subject)
    try:
        cursor_props = db.execute(
            "SELECT predicate, object, object_is_literal, object_datatype FROM Triples WHERE subject = ?",
            (item_id,)
        )
        properties = cursor_props.fetchall()
        app.logger.debug(f"Found {len(properties)} outgoing properties for {item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching properties for '{item_id}': {e}")
        abort(500, description=f"Error retrieving properties for {item_id}")

    # Fetch incoming references (where item_id is the object and is not a literal)
    try:
        cursor_refs = db.execute(
            "SELECT subject, predicate FROM Triples WHERE object = ? AND object_is_literal = 0",
            (item_id,)
        )
        references = cursor_refs.fetchall()
        app.logger.debug(f"Found {len(references)} incoming references for {item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching references for '{item_id}': {e}")
        abort(500, description=f"Error retrieving references for {item_id}")

    # Prepare data for template (e.g., extract common properties)
    item_details = {
        'label': None,
        'comment': None,
        'primary_type': None,
        'primary_type_display': None,
        'other_properties': []
    }
    processed_predicates = set()

    for prop in properties:
        predicate = prop['predicate']
        # Find primary type
        if predicate == RDF_TYPE and not item_details['primary_type']:
             item_details['primary_type'] = prop['object']
             # Find display name for the type
             for name, type_val in BROWSEABLE_TYPES.items():
                 if type_val == item_details['primary_type']:
                     item_details['primary_type_display'] = name
                     break
             processed_predicates.add(predicate)
        # Find label
        elif predicate == RDFS_LABEL and not item_details['label']:
            item_details['label'] = prop['object']
            processed_predicates.add(predicate)
        # Find comment/description (first one found from the list)
        elif predicate in DESCRIPTION_PREDICATES and not item_details['comment']:
            item_details['comment'] = prop['object']
            processed_predicates.add(predicate)

    # Add remaining properties to 'other_properties'
    item_details['other_properties'] = [p for p in properties if p['predicate'] not in processed_predicates]


    # Check if item exists (at least has some properties or references)
    if not properties and not references:
        app.logger.warning(f"No data found for item_id: {item_id}. Returning 404.")
        abort(404, description=f"Item '{item_id}' not found in the ontology data.")


    return render_template(
        'details.html',
        item_id=item_id,
        details=item_details, # Pass the structured details
        references=references,
        browseable_types_map=BROWSEABLE_TYPES # Pass map for potential back links
    )


# --- Run the App ---
if __name__ == '__main__':
    # Use 0.0.0.0 to be accessible externally, Render uses $PORT
    port = int(os.environ.get('PORT', 5000))
    # Turn off debug mode for production/deployment
    app.run(host='0.0.0.0', port=port, debug=False) 