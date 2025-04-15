import sqlite3
from flask import Flask, render_template, g, abort, url_for
import os
import logging
from urllib.parse import unquote # Needed for handling URL-encoded values
from collections import defaultdict # Import defaultdict

# --- Configuration ---
DATABASE = 'panres_ontology.db' # Make sure this file is in the same directory or provide the correct path

# Define how categories are presented and queried on the index page
# 'query_type': 'type' -> count/list subjects with rdf:type = value
# 'query_type': 'predicate_object' -> count/list distinct objects for predicate = value
# 'query_type': 'predicate_subject' -> count/list distinct subjects for predicate = value (less common)
INDEX_CATEGORIES = {
    "PanRes Genes": {'query_type': 'type', 'value': 'PanGene', 'description': 'Unique gene sequences curated in PanRes.'},
    "Source Genes": {'query_type': 'type', 'value': 'OriginalGene', 'description': 'Genes as found in their original source databases.'},
    "Source Databases": {'query_type': 'predicate_object', 'value': 'is_from_database', 'description': 'Databases contributing genes to PanRes.'},
    "Antibiotic Classes": {'query_type': 'predicate_object', 'value': 'has_resistance_class', 'description': 'Classes of antibiotics genes confer resistance to.'},
    "Predicted Phenotypes": {'query_type': 'predicate_object', 'value': 'has_predicted_phenotype', 'description': 'Specific antibiotic resistances predicted for genes.'},
    # Add more if needed, e.g., for Mechanisms, Metals, Biocides if predicates exist
    # "Resistance Mechanisms": {'query_type': 'predicate_object', 'value': 'has_resistance_mechanism'},
    # "Metal Resistance": {'query_type': 'predicate_object', 'value': 'confers_resistance_to_metal'},
    # "Biocide Resistance": {'query_type': 'predicate_object', 'value': 'confers_resistance_to_biocide'},
}

# Define common predicates and a mapping for display names
RDF_TYPE = 'rdf:type'
RDFS_LABEL = 'rdfs:label'
RDFS_COMMENT = 'rdfs:comment'
DESCRIPTION_PREDICATES = [RDFS_COMMENT, 'description', 'dc:description', 'skos:definition']

PREDICATE_DISPLAY_NAMES = {
    RDF_TYPE: "Is Type Of",
    RDFS_LABEL: "Label",
    RDFS_COMMENT: "Description",
    "is_from_database": "Source Database",
    "has_resistance_class": "Resistance Class",
    "has_predicted_phenotype": "Predicted Phenotype",
    "has_resistance_mechanism": "Resistance Mechanism", # Example
    "confers_resistance_to_metal": "Confers Resistance To Metal", # Example
    "confers_resistance_to_biocide": "Confers Resistance To Biocide", # Example
    "same_as": "Equivalent To / Also Known As",
    "accession": "Accession",
    "original_fasta_header": "Original FASTA Header",
    "has_length": "Length (bp)",
    "translates_to": "Translates To Protein", # Assuming this based on example
    "card_link": "CARD Ontology Link", # Example
    "member_of": "Member Of Cluster", # Assuming this based on example
    # Add more mappings as needed based on your ontology predicates
}

# --- NEW: Define Colors for Pie Chart ---
# Use hex codes matching the CSS pastel variables for consistency
PIE_CHART_COLORS = [
    '#a5d8ff', # pastel-blue
    '#b2f2bb', # pastel-green
    '#ffec99', # pastel-yellow
    '#d0bfff', # pastel-purple
    '#ffc9c9', # pastel-pink
    '#a3e1d4', # pastel-cyan (corrected)
    '#ffd8a8', # pastel-orange
    '#d4f8d4', # pastel-lime
    '#dbe4ff', # pastel-indigo
    '#a0e9e5', # pastel-teal
    '#bac8d3', # pastel-gray (for 'Others')
]


# --- Flask App Setup ---
app = Flask(__name__)
app.config['DATABASE'] = DATABASE
app.config['INDEX_CATEGORIES'] = INDEX_CATEGORIES # Make categories accessible
app.config['PREDICATE_DISPLAY_NAMES'] = PREDICATE_DISPLAY_NAMES # Make predicate names accessible

# Configure logging
logging.basicConfig(level=logging.INFO) # Log INFO level messages and above
app.logger.setLevel(logging.INFO) # Ensure Flask's logger also respects INFO level

# Make INDEX_CATEGORIES and PREDICATE_DISPLAY_NAMES available to all templates
@app.context_processor
def inject_global_data():
    return dict(
        index_categories=app.config['INDEX_CATEGORIES'],
        predicate_map=app.config['PREDICATE_DISPLAY_NAMES']
    )

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
    """Shows the main index page with visualizations and browseable categories."""
    db = get_db()
    category_data = {}
    source_db_counts_rows = []
    antibiotic_class_counts_rows = []
    antibiotic_chart_data = None # Initialize chart data as None
    app.logger.info(f"Loading index page. Categories configured: {list(INDEX_CATEGORIES.keys())}")

    # --- Fetch Category Counts (as before, but maybe hide them later) ---
    for display_name, config in INDEX_CATEGORIES.items():
        query_type = config['query_type']
        value = config['value']
        count = 0
        error_msg = None

        try:
            if query_type == 'type':
                # Count distinct subjects for a given rdf:type
                cursor = db.execute(
                    "SELECT COUNT(DISTINCT subject) FROM Triples WHERE predicate = ? AND object = ?",
                    (RDF_TYPE, value)
                )
            elif query_type == 'predicate_object':
                # Count distinct non-literal objects for a given predicate
                cursor = db.execute(
                    "SELECT COUNT(DISTINCT object) FROM Triples WHERE predicate = ? AND object_is_literal = 0",
                    (value,)
                )
            # Add elif for 'predicate_subject' if needed later
            else:
                 app.logger.warning(f"Unknown query_type '{query_type}' for category '{display_name}'")
                 error_msg = "Config Error"

            if error_msg is None:
                count_result = cursor.fetchone()
                count = count_result[0] if count_result else 0
                app.logger.debug(f"Count for {display_name} ({query_type}={value}): {count}")

        except sqlite3.Error as e:
            app.logger.error(f"Error counting items for category '{display_name}' (value: {value}): {e}")
            error_msg = "DB Error"

        category_data[display_name] = {
            'config': config,
            'count': count if error_msg is None else error_msg
        }

    # --- Fetch Source Database Counts for Bar Plot ---
    try:
        cursor_db = db.execute("""
            SELECT object AS database_name, COUNT(DISTINCT subject) AS gene_count
            FROM Triples
            WHERE predicate = 'is_from_database' AND object_is_literal = 0
            GROUP BY object
            ORDER BY gene_count DESC
        """)
        source_db_counts_rows = cursor_db.fetchall()
        app.logger.info(f"Fetched {len(source_db_counts_rows)} source database counts.")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching source database counts: {e}")
        # Handle error appropriately, maybe pass an error message to the template

    # --- Fetch Antibiotic Class Counts for Visualization ---
    try:
        cursor_class = db.execute("""
            SELECT object AS class_name, COUNT(DISTINCT subject) AS gene_count
            FROM Triples
            WHERE predicate = 'has_resistance_class' AND object_is_literal = 0
            GROUP BY object
            ORDER BY gene_count DESC
        """)
        antibiotic_class_counts = [dict(row) for row in cursor_class.fetchall()]
        app.logger.info(f"Fetched {len(antibiotic_class_counts)} antibiotic class counts.")

        # --- Process Antibiotic Class Counts for Pie Chart (Top 7 + Others) ---
        if antibiotic_class_counts:
            top_n = 7
            chart_labels = []
            chart_data_points = []
            chart_colors = []

            # Take top N
            top_classes = antibiotic_class_counts[:top_n]
            for i, row in enumerate(top_classes):
                chart_labels.append(row['class_name'])
                chart_data_points.append(row['gene_count'])
                # Cycle through colors, use the last color for 'Others' later
                chart_colors.append(PIE_CHART_COLORS[i % (len(PIE_CHART_COLORS) -1)])

            # Group remaining into "Others"
            if len(antibiotic_class_counts) > top_n:
                other_classes = antibiotic_class_counts[top_n:]
                other_count = sum(row['gene_count'] for row in other_classes)
                if other_count > 0:
                    chart_labels.append("Others")
                    chart_data_points.append(other_count)
                    chart_colors.append(PIE_CHART_COLORS[-1]) # Use the last defined color for Others

            antibiotic_chart_data = {
                'labels': chart_labels,
                'data': chart_data_points,
                'colors': chart_colors
            }
            app.logger.info(f"Processed antibiotic counts for pie chart: {len(chart_labels)} slices.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching or processing antibiotic class counts: {e}")
        # Handle error appropriately, antibiotic_chart_data remains None

    # --- Convert Row objects to Dictionaries ---
    # Convert source_db_counts_rows to a list of dicts
    source_db_counts = [dict(row) for row in source_db_counts_rows]

    # Calculate max counts for scaling the bar plot
    max_db_count = max(row['gene_count'] for row in source_db_counts) if source_db_counts else 1

    return render_template(
        'index.html',
        category_data=category_data,
        source_db_counts=source_db_counts,
        max_db_count=max_db_count,
        antibiotic_chart_data=antibiotic_chart_data # Pass the processed data
    )


@app.route('/list/<category_key>')
def list_items(category_key):
    """Lists items belonging to a specific category (e.g., PanGenes, Source Databases)."""
    db = get_db()

    # Find the category config based on the key (display name)
    if category_key not in INDEX_CATEGORIES:
        app.logger.warning(f"Attempted to list items for an unrecognized category key: {category_key}")
        abort(404, description=f"Category '{category_key}' not recognized.")

    config = INDEX_CATEGORIES[category_key]
    query_type = config['query_type']
    value = config['value'] # This is the type_id or predicate depending on query_type
    list_title = category_key
    items = []
    list_description = config.get('description', f"Items related to {category_key}")
    link_target_template = 'details' # Default: links go to details page
    link_params = {} # Default: item itself is the parameter

    app.logger.info(f"Listing items for category: {category_key} (Query Type: {query_type}, Value: {value})")

    try:
        if query_type == 'type':
            # List distinct subjects for the given rdf:type
            cursor = db.execute(
                "SELECT DISTINCT subject FROM Triples WHERE predicate = ? AND object = ? ORDER BY subject",
                (RDF_TYPE, value)
            )
            items = [{'id': row['subject'], 'link': url_for('details', item_id=row['subject'])} for row in cursor.fetchall()]
            list_description = f"Listing all {category_key} (Type: <code>{value}</code>)."

        elif query_type == 'predicate_object':
            # List distinct non-literal objects for the given predicate
            cursor = db.execute(
                "SELECT DISTINCT object FROM Triples WHERE predicate = ? AND object_is_literal = 0 ORDER BY object",
                (value,)
            )
            # For these items (Databases, Classes, etc.), link to a page showing related genes
            items = [{'id': row['object'], 'link': url_for('genes_related_to', predicate=value, object_value=row['object'])} for row in cursor.fetchall()]
            list_description = f"Listing all {category_key} found in the data (via predicate <code>{value}</code>). Click an item to see associated genes."

        # Add elif for 'predicate_subject' if needed

        app.logger.info(f"Found {len(items)} items for category {category_key}")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching list for category '{category_key}': {e}")
        abort(500, description=f"Error retrieving list for {category_key}")

    return render_template('list.html',
                           items=items,
                           list_title=list_title,
                           list_description=list_description,
                           category_key=category_key)


@app.route('/related/<predicate>/<path:object_value>')
def genes_related_to(predicate, object_value):
    """Lists genes (subjects) related to a specific predicate and object value."""
    db = get_db()
    # Decode the object_value which might contain URL-encoded characters like '/'
    decoded_object_value = unquote(object_value)

    app.logger.info(f"Fetching genes related to predicate='{predicate}', object='{decoded_object_value}'")

    # Find a display name for the predicate
    predicate_display = PREDICATE_DISPLAY_NAMES.get(predicate, predicate)
    page_title = f"Genes related to {predicate_display}: {decoded_object_value}"
    description = f"Showing genes where <code>{predicate}</code> is <code>{decoded_object_value}</code>."

    try:
        cursor = db.execute(
            """SELECT DISTINCT T.subject
               FROM Triples T
               JOIN Triples type_t ON T.subject = type_t.subject
               WHERE T.predicate = ?
                 AND T.object = ?
                 AND type_t.predicate = ?
                 AND type_t.object IN (?, ?) -- Look for PanGene or OriginalGene types
               ORDER BY T.subject""",
            (predicate, decoded_object_value, RDF_TYPE, 'PanGene', 'OriginalGene') # Ensure we list actual genes
        )
        genes = [{'id': row['subject'], 'link': url_for('details', item_id=row['subject'])} for row in cursor.fetchall()]
        app.logger.info(f"Found {len(genes)} related genes.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching related genes for {predicate}={decoded_object_value}: {e}")
        abort(500, description="Error retrieving related genes.")

    # Use a different template for this specific view
    return render_template('related_genes.html',
                           genes=genes,
                           page_title=page_title,
                           description=description,
                           predicate=predicate,
                           object_value=decoded_object_value,
                           index_categories=app.config['INDEX_CATEGORIES']) # Pass categories for back link


@app.route('/details/<path:item_id>')
def details(item_id):
    """Shows details (properties and references) for a specific item."""
    db = get_db()
    decoded_item_id = unquote(item_id)
    app.logger.info(f"Fetching details for item ID: {decoded_item_id}")

    # Fetch outgoing properties
    try:
        cursor_props = db.execute(
            "SELECT predicate, object, object_is_literal, object_datatype FROM Triples WHERE subject = ?",
            (decoded_item_id,)
        )
        properties = cursor_props.fetchall()
        app.logger.debug(f"Found {len(properties)} outgoing properties for {decoded_item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching properties for '{decoded_item_id}': {e}")
        abort(500, description=f"Error retrieving properties for {decoded_item_id}")

    # Fetch incoming references
    try:
        cursor_refs = db.execute(
            "SELECT subject, predicate FROM Triples WHERE object = ? AND object_is_literal = 0",
            (decoded_item_id,)
        )
        references = cursor_refs.fetchall()
        app.logger.debug(f"Found {len(references)} incoming references for {decoded_item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Error fetching references for '{decoded_item_id}': {e}")
        abort(500, description=f"Error retrieving references for {decoded_item_id}")

    # --- Process Properties for Better Display ---
    item_details = {
        'label': None,
        'comment': None,
        'types': [],
        'primary_type_display': None,
        'primary_type_category_key': None,
        'grouped_properties': defaultdict(list), # Group properties by predicate
        'grouped_references': defaultdict(list) # Group references by predicate
    }
    processed_predicates_for_grouping = set() # Track predicates handled as key info

    # Prioritize specific types
    preferred_types = ['PanGene', 'OriginalGene']
    found_preferred_type = None

    # First pass: Extract Key Info (Type, Label, Comment)
    for prop in properties:
        predicate = prop['predicate']
        obj = prop['object']

        if predicate == RDF_TYPE:
            item_details['types'].append(obj)
            if obj in preferred_types and not found_preferred_type:
                found_preferred_type = obj
            for cat_key, cat_config in INDEX_CATEGORIES.items():
                 if cat_config['query_type'] == 'type' and cat_config['value'] == obj:
                     if not item_details['primary_type_display']:
                         item_details['primary_type_display'] = cat_key
                         item_details['primary_type_category_key'] = cat_key
                     # Don't break here, might find a preferred type later
            processed_predicates_for_grouping.add(predicate)

        elif predicate == RDFS_LABEL and not item_details['label']:
            item_details['label'] = obj
            processed_predicates_for_grouping.add(predicate)
        elif predicate in DESCRIPTION_PREDICATES and not item_details['comment']:
            item_details['comment'] = obj
            processed_predicates_for_grouping.add(predicate)

    # Ensure preferred type is set if found
    if found_preferred_type:
         for cat_key, cat_config in INDEX_CATEGORIES.items():
             if cat_config['query_type'] == 'type' and cat_config['value'] == found_preferred_type:
                 item_details['primary_type_display'] = cat_key
                 item_details['primary_type_category_key'] = cat_key
                 break

    # Second pass: Group remaining properties and fetch extra data if needed
    for prop in properties:
        predicate = prop['predicate']
        if predicate in processed_predicates_for_grouping:
            continue # Skip already handled key info

        prop_data = {
            'value': prop['object'],
            'is_literal': bool(prop['object_is_literal']),
            'datatype': prop['object_datatype'], # Keep for potential future use, but won't display directly
            'link': None,
            'extra_info': None # For adding "(from Database)" etc.
        }

        if not prop_data['is_literal']:
            prop_data['link'] = url_for('details', item_id=prop['object'])

            # --- Enhancement: Fetch source DB for 'same_as' OriginalGene links ---
            if predicate == 'same_as':
                try:
                    # Check if the linked object is an OriginalGene and get its database
                    cursor_orig_db = db.execute(
                        """SELECT T_db.object
                           FROM Triples T_type
                           JOIN Triples T_db ON T_type.subject = T_db.subject
                           WHERE T_type.subject = ?
                             AND T_type.predicate = ? AND T_type.object = ? -- Check type
                             AND T_db.predicate = ? -- Get database
                           LIMIT 1""",
                        (prop['object'], RDF_TYPE, 'OriginalGene', 'is_from_database')
                    )
                    db_info = cursor_orig_db.fetchone()
                    if db_info:
                        prop_data['extra_info'] = f"(from {db_info['object']})"
                        # Optionally, link the database name too
                        # prop_data['extra_info_link'] = url_for('genes_related_to', predicate='is_from_database', object_value=db_info['object'])
                except sqlite3.Error as e_extra:
                    app.logger.warning(f"Could not fetch extra DB info for {prop['object']}: {e_extra}")
            # --- End Enhancement ---

        item_details['grouped_properties'][predicate].append(prop_data)

    # Group incoming references
    for ref in references:
         ref_data = {
             'subject': ref['subject'],
             'link': url_for('details', item_id=ref['subject'])
         }
         item_details['grouped_references'][ref['predicate']].append(ref_data)


    # Check if item exists
    if not properties and not references:
        app.logger.warning(f"No data found for item_id: {decoded_item_id}. Returning 404.")
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")


    return render_template(
        'details.html',
        item_id=decoded_item_id,
        details=item_details, # Pass the structured details
        # references=references, # References are now inside details['grouped_references']
        predicate_map=PREDICATE_DISPLAY_NAMES # Pass the display name map
    )


# --- Run the App ---
if __name__ == '__main__':
    # Use 0.0.0.0 to be accessible externally, Render uses $PORT
    port = int(os.environ.get('PORT', 5000))
    # Turn off debug mode for production/deployment
    app.run(host='0.0.0.0', port=port, debug=False) 