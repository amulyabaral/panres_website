import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app
import os
import logging
from urllib.parse import unquote # Needed for handling URL-encoded values
from collections import defaultdict # Import defaultdict
import datetime # <-- Import datetime

# --- Configuration ---
DATABASE = 'panres_ontology.db' # Make sure this file is in the same directory or provide the correct path
CITATION_TEXT = "Hannah-Marie Martiny, Nikiforos Pyrounakis, Thomas N Petersen, Oksana Lukjančenko, Frank M Aarestrup, Philip T L C Clausen, Patrick Munk, ARGprofiler—a pipeline for large-scale analysis of antimicrobial resistance genes and their flanking regions in metagenomic datasets, <i>Bioinformatics</i>, Volume 40, Issue 3, March 2024, btae086, <a href=\"https://doi.org/10.1093/bioinformatics/btae086\" target=\"_blank\" rel=\"noopener noreferrer\">https://doi.org/10.1093/bioinformatics/btae086</a>"
SITE_NAME = "PanRes 2.0 Database"

# Define how categories are presented and queried on the index page
# 'query_type': 'type' -> count/list subjects with rdf:type = value
# 'query_type': 'predicate_object' -> count/list distinct objects for predicate = value
# 'query_type': 'predicate_subject' -> count/list distinct subjects for predicate = value (less common)
# ADDED 'filter_subject_type': Only count/list subjects of this type (used for Source DB)
INDEX_CATEGORIES = {
    "PanRes Genes": {'query_type': 'type', 'value': 'PanGene', 'description': 'Unique gene sequences curated in PanRes.'},
    "Source Databases": {'query_type': 'predicate_object', 'value': 'is_from_database', 'description': 'Databases contributing genes to PanRes.', 'filter_subject_type': 'OriginalGene'}, # Filter subjects to be OriginalGene
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
# --- ADD THESE CONSTANTS BACK ---
HAS_RESISTANCE_CLASS = 'has_resistance_class'
HAS_PREDICTED_PHENOTYPE = 'has_predicted_phenotype'
# --- END ADDITION ---
DESCRIPTION_PREDICATES = [RDFS_COMMENT, 'description', 'dc:description', 'skos:definition']

PREDICATE_DISPLAY_NAMES = {
    RDF_TYPE: "Is Type Of",
    RDFS_LABEL: "Label",
    RDFS_COMMENT: "Description",
    "is_from_database": "Source Database",
    HAS_RESISTANCE_CLASS: "Resistance Class", # Use constant here too
    HAS_PREDICTED_PHENOTYPE: "Predicted Phenotype", # Use constant here too
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

# --- Define Predicates for PanGene Details Layout ---
pangen_key_info_preds = [
    'has_length',
    'same_as',
    'card_link',
    'accession',
    'is_from_database' # Include source DB in key info
]
pangen_right_col_preds = [
    HAS_RESISTANCE_CLASS, # Use constant
    HAS_PREDICTED_PHENOTYPE, # Use constant
    'translates_to',
    'member_of'
]

# --- Define Predicates that should link to related items list ---
# Restore this definition
LINK_TO_RELATED_PREDICATES = {
    'is_from_database',
    'has_resistance_class',
    'has_predicted_phenotype',
    # Add others if needed, e.g., 'member_of' if clusters should link to members
}

# --- Flask App Setup ---
app = Flask(__name__)
app.config['DATABASE'] = DATABASE
app.config['INDEX_CATEGORIES'] = INDEX_CATEGORIES # Make categories accessible
app.config['PREDICATE_DISPLAY_NAMES'] = PREDICATE_DISPLAY_NAMES # Make predicate names accessible
app.config['CITATION_TEXT'] = CITATION_TEXT # Add citation text
app.config['SITE_NAME'] = SITE_NAME # Add site name

# Configure logging
logging.basicConfig(level=logging.INFO) # Log INFO level messages and above
app.logger.setLevel(logging.INFO) # Ensure Flask's logger also respects INFO level

# --- Define a colorblind-friendly palette (e.g., Paul Tol's vibrant) ---
# Using 10 distinct colors. Will cycle if more categories exist.
COLOR_PALETTE = [
    '#EE7733', '#0077BB', '#33BBEE', '#EE3377', '#CC3311',
    '#009988', '#BBBBBB', '#DDAA33', '#BB5566', '#000000'
]
# --- End Color Palette ---

# Make INDEX_CATEGORIES and PREDICATE_DISPLAY_NAMES available to all templates
@app.context_processor
def inject_global_data():
    return dict(
        index_categories=app.config['INDEX_CATEGORIES'],
        predicate_map=app.config['PREDICATE_DISPLAY_NAMES'],
        citation_text=app.config['CITATION_TEXT'], # Pass citation
        site_name=app.config['SITE_NAME'], # Pass site name
        current_year=datetime.datetime.now().year, # <-- Add current year
        # Make constants available for templates if needed directly
        RDF_TYPE=RDF_TYPE,
        RDFS_LABEL=RDFS_LABEL,
        RDFS_COMMENT=RDFS_COMMENT,
        # --- ADD CONSTANTS TO CONTEXT IF NEEDED IN TEMPLATES (Optional) ---
        # Not strictly needed for current templates, but good practice if used directly
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


# --- NEW HELPER FUNCTION ---
def get_item_details(item_id):
    """Fetches and processes details for a given item ID."""
    db = get_db()
    app.logger.debug(f"Helper: Fetching details for item ID: {item_id}")

    # Fetch outgoing properties
    try:
        cursor_props = db.execute(
            "SELECT predicate, object, object_is_literal FROM Triples WHERE subject = ?",
            (item_id,)
        )
        raw_properties = cursor_props.fetchall()
        app.logger.debug(f"Helper: Found {len(raw_properties)} outgoing properties for {item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Helper: Error fetching properties for '{item_id}': {e}")
        return None # Indicate error or not found

    # Fetch incoming references
    try:
        cursor_refs = db.execute(
            "SELECT subject, predicate FROM Triples WHERE object = ? AND object_is_literal = 0",
            (item_id,)
        )
        raw_references = cursor_refs.fetchall()
        app.logger.debug(f"Helper: Found {len(raw_references)} incoming references for {item_id}")
    except sqlite3.Error as e:
        app.logger.error(f"Helper: Error fetching references for '{item_id}': {e}")
        # Decide if this is critical - maybe proceed without references? For now, return None.
        return None

    if not raw_properties and not raw_references:
        app.logger.warning(f"Helper: No properties or references found for '{item_id}'.")
        return None # Item exists but has no data? Or doesn't exist.

    # --- Process Properties ---
    grouped_properties = defaultdict(list)
    types = []
    primary_type_display = None
    primary_type_rdf = None
    primary_type_category_key = None
    is_pangen = False

    # Fetch labels for object URIs in a batch for efficiency
    object_uris = {prop['object'] for prop in raw_properties if not prop['object_is_literal']}
    object_labels = {}
    if object_uris:
        placeholders = ','.join('?' * len(object_uris))
        try:
            cursor_labels = db.execute(
                f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})",
                (RDFS_LABEL, *object_uris)
            )
            object_labels = {row['subject']: row['object'] for row in cursor_labels.fetchall()}
            app.logger.debug(f"Helper: Fetched {len(object_labels)} labels for object URIs.")
        except sqlite3.Error as e:
            app.logger.error(f"Helper: Error fetching labels for object URIs: {e}")
            # Continue without labels if there's an error

    for prop in raw_properties:
        predicate = prop['predicate']
        obj = prop['object']
        is_literal = prop['object_is_literal']

        # Handle rdf:type separately to determine primary type
        if predicate == RDF_TYPE:
            types.append(obj)
            if obj == 'PanGene':
                is_pangen = True
            continue # Don't add rdf:type to grouped_properties directly unless needed

        # Determine link and display value
        link = None
        display_value = obj
        extra_info = None # For things like database name on same_as links

        if not is_literal:
            # It's a resource (URI)
            display_value = object_labels.get(obj, obj) # Use label if found, else URI
            # Decide if it should link to details page or related items page
            if predicate in LINK_TO_RELATED_PREDICATES:
                 # --- FIX url_for PARAMETERS HERE ---
                 # Link to the page showing items related *to this object* via this predicate
                 try:
                     link = url_for('show_related_items', predicate=predicate, object_value=obj)
                 except Exception as url_err:
                      app.logger.error(f"Helper: Error building related_items URL for pred={predicate}, obj={obj}: {url_err}", exc_info=True)
                      link = "#error" # Indicate error in link
                 # --- END FIX ---
            else:
                 # Default link to the details page of the object URI
                 try:
                     link = url_for('details', item_id=obj)
                 except Exception as url_err:
                     app.logger.error(f"Helper: Error building details URL for obj={obj}: {url_err}", exc_info=True)
                     link = "#error" # Indicate error in link

            # Special handling for 'same_as' to add source DB info if available
            if predicate == 'same_as':
                # Try to find the 'is_from_database' for this 'same_as' object URI
                try:
                    cursor_db = db.execute(
                        "SELECT object FROM triples WHERE subject = ? AND predicate = ?",
                        (obj, IS_FROM_DATABASE) # IS_FROM_DATABASE should be defined
                    )
                    db_result = cursor_db.fetchone()
                    if db_result:
                        extra_info = f"(from {db_result['object']})"
                except sqlite3.Error as db_err:
                    app.logger.warning(f"Helper: Could not fetch source DB for same_as link {obj}: {db_err}")


        grouped_properties[predicate].append({
            'value': display_value,
            'raw_object': obj, # Keep original object value if needed
            'is_literal': is_literal,
            'link': link,
            'extra_info': extra_info
        })

    # --- Determine Primary Type Display ---
    # Prioritize PanGene, then OriginalGene, then others based on INDEX_CATEGORIES
    type_priority = ['PanGene', 'OriginalGene'] # Add more if needed
    found_primary = False
    for type_uri in type_priority:
        if type_uri in types:
            primary_type_rdf = type_uri
            # Find the display name and category key from INDEX_CATEGORIES
            for key, config in current_app.config['INDEX_CATEGORIES'].items():
                if config['query_type'] == 'type' and config['value'] == type_uri:
                    primary_type_display = key # Use the category key as display name
                    primary_type_category_key = key
                    found_primary = True
                    break
            if found_primary: break

    # Fallback if no priority type found but other types exist
    if not found_primary and types:
        primary_type_rdf = types[0] # Just take the first one found
        primary_type_display = primary_type_rdf # Display the RDF type directly
        # Try to find a category key for it
        for key, config in current_app.config['INDEX_CATEGORIES'].items():
             if config['query_type'] == 'type' and config['value'] == primary_type_rdf:
                 primary_type_category_key = key
                 break

    app.logger.debug(f"Helper: Determined primary type for {item_id}: {primary_type_display} (RDF: {primary_type_rdf}, Key: {primary_type_category_key})")


    # --- Process References ---
    referencing_items = []
    for ref in raw_references:
        referencing_items.append({
            'ref_id': ref['subject'],
            'predicate': ref['predicate']
            # Link will be generated in the template using url_for('details', item_id=ref['subject'])
        })

    # Sort properties within each group if needed (e.g., alphabetically by value)
    for predicate in grouped_properties:
        # Example: sort by display value
        grouped_properties[predicate].sort(key=lambda x: x['value'])

    return {
        'id': item_id,
        'grouped_properties': dict(grouped_properties), # Convert back to dict
        'referencing_items': referencing_items,
        'types': types, # List of all rdf:type URIs
        'primary_type_rdf': primary_type_rdf,
        'primary_type_display': primary_type_display,
        'primary_type_category_key': primary_type_category_key,
        'is_pangen': is_pangen,
        # 'all_types_display' is removed as it wasn't being populated correctly
    }

# --- END HELPER FUNCTION ---


# --- Flask Routes ---
@app.route('/')
def index():
    """Shows the main index page with visualizations and browseable categories."""
    db = get_db()
    category_data = {}
    source_db_counts_rows = []
    antibiotic_class_counts_rows = []
    phenotype_counts_rows = [] # <-- New: Initialize list for phenotype counts
    antibiotic_chart_data = None # Initialize chart data as None
    phenotype_chart_data = None # <-- New: Initialize phenotype chart data as None
    app.logger.info(f"Loading index page. Categories configured: {list(INDEX_CATEGORIES.keys())}")

    # --- Fetch Category Counts (as before, but maybe hide them later) ---
    for display_name, config in INDEX_CATEGORIES.items():
        query_type = config['query_type']
        value = config['value']
        filter_subject_type = config.get('filter_subject_type') # Get optional filter
        count = 0
        error_msg = None # Initialize error message tracking

        try:
            if query_type == 'type':
                # Count subjects with a specific rdf:type
                cursor = db.execute(f"SELECT COUNT(DISTINCT subject) FROM triples WHERE predicate = ? AND object = ?", (RDF_TYPE, value))
            elif query_type == 'predicate_object':
                # Count distinct objects for a specific predicate
                # Apply subject type filter if specified
                if filter_subject_type:
                     # Join to filter subjects by type before counting distinct objects linked via the predicate
                     # This counts the number of *databases* that have OriginalGenes.
                     cursor = db.execute(f"""
                        SELECT COUNT(*) FROM (
                            SELECT DISTINCT t1.object
                            FROM triples t1
                            JOIN triples t2 ON t1.subject = t2.subject
                            WHERE t1.predicate = ?
                              AND t2.predicate = ?
                              AND t2.object = ?
                        )
                     """, (value, RDF_TYPE, filter_subject_type))
                     # The count is fetched below, no need to assign here or continue

                else:
                    # Count distinct objects without subject filtering
                    cursor = db.execute(f"SELECT COUNT(DISTINCT object) FROM triples WHERE predicate = ?", (value,))
            elif query_type == 'predicate_subject':
                 # Count distinct subjects for a specific predicate
                 if filter_subject_type:
                     cursor = db.execute(f"""
                         SELECT COUNT(DISTINCT t1.subject)
                         FROM triples t1
                         JOIN triples t2 ON t1.subject = t2.subject
                         WHERE t1.predicate = ?
                           AND t2.predicate = ?
                           AND t2.object = ?
                     """, (value, RDF_TYPE, filter_subject_type))
                 else:
                     cursor = db.execute(f"SELECT COUNT(DISTINCT subject) FROM triples WHERE predicate = ?", (value,))
            else:
                current_app.logger.warning(f"Unknown query_type '{query_type}' for category '{display_name}'")
                cursor = None
                error_msg = "Config Error" # Set error message if config is wrong

            # Fetch the count if a cursor was successfully created
            if cursor:
                result = cursor.fetchone()
                count = result[0] if result else 0
                app.logger.debug(f"Count for {display_name} ({query_type}={value}, filter={filter_subject_type}): {count}")


        except sqlite3.Error as e:
            current_app.logger.error(f"Database error fetching count for {display_name}: {e}")
            count = 0 # Default to 0 on error
            error_msg = "DB Error" # Set error message on DB error

        # Assign the final dictionary structure for this category
        category_data[display_name] = {
            'config': config,
            'count': count if error_msg is None else error_msg # Use error message if set
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
            WHERE predicate = ? AND object_is_literal = 0
            GROUP BY object
            ORDER BY gene_count DESC
        """, (HAS_RESISTANCE_CLASS,)) # Use constant
        antibiotic_class_counts = [dict(row) for row in cursor_class.fetchall()]
        app.logger.info(f"Fetched {len(antibiotic_class_counts)} antibiotic class counts.")

        # --- Process Antibiotic Class Counts for Pie Chart (Top N + Others) ---
        if antibiotic_class_counts:
            top_n_class = 7 # Or adjust as needed
            chart_labels_class = []
            chart_data_points_class = []

            # Take top N
            top_classes = antibiotic_class_counts[:top_n_class]
            for i, row in enumerate(top_classes):
                chart_labels_class.append(row['class_name'])
                chart_data_points_class.append(row['gene_count'])

            # Group remaining into "Others"
            if len(antibiotic_class_counts) > top_n_class:
                other_classes = antibiotic_class_counts[top_n_class:]
                other_count_class = sum(row['gene_count'] for row in other_classes)
                if other_count_class > 0:
                    chart_labels_class.append("Others")
                    chart_data_points_class.append(other_count_class)

            antibiotic_chart_data = {
                'labels': chart_labels_class,
                'data': chart_data_points_class
            }
            app.logger.info(f"Processed antibiotic counts for pie chart: {len(chart_labels_class)} slices.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching or processing antibiotic class counts: {e}")
        antibiotic_chart_data = None

    # --- Fetch Predicted Phenotype Counts for Stacked Bar ---
    phenotype_chart_data = None
    try:
        cursor_pheno = db.execute("""
            SELECT object AS phenotype_name, COUNT(DISTINCT subject) AS gene_count
            FROM Triples
            WHERE predicate = ? AND object_is_literal = 0
            GROUP BY object
            ORDER BY gene_count DESC
        """, (HAS_PREDICTED_PHENOTYPE,)) # Use constant
        phenotype_counts = [dict(row) for row in cursor_pheno.fetchall()]
        app.logger.info(f"Fetched {len(phenotype_counts)} predicted phenotype counts.")

        # --- Process Phenotype Counts for Stacked Bar (Top N + Others) ---
        if phenotype_counts:
            top_n_pheno = 8 # Or adjust as needed
            pheno_segments = []

            # Take top N
            top_phenotypes = phenotype_counts[:top_n_pheno]
            total_count_top_n = sum(row['gene_count'] for row in top_phenotypes)
            other_count_pheno = 0
            if len(phenotype_counts) > top_n_pheno:
                other_phenotypes = phenotype_counts[top_n_pheno:]
                other_count_pheno = sum(row['gene_count'] for row in other_phenotypes)
            total_pheno_count = total_count_top_n + other_count_pheno

            if total_pheno_count > 0: # Avoid division by zero
                # Add top N segments
                for i, row in enumerate(top_phenotypes):
                    percentage = (row['gene_count'] / total_pheno_count * 100)
                    color = COLOR_PALETTE[i % len(COLOR_PALETTE)] # Assign color from palette
                    pheno_segments.append({
                        'name': row['phenotype_name'],
                        'count': row['gene_count'],
                        'percentage': percentage,
                        'color': color # <-- Add color here
                    })

                # Add "Others" segment if applicable
                if other_count_pheno > 0:
                    percentage = (other_count_pheno / total_pheno_count * 100)
                    # Assign the next color in the cycle, or a default like gray/black if palette is exhausted
                    color_index = len(top_phenotypes) % len(COLOR_PALETTE)
                    color = COLOR_PALETTE[color_index]
                    pheno_segments.append({
                        'name': "Others",
                        'count': other_count_pheno,
                        'percentage': percentage,
                        'color': color # <-- Add color here
                    })

                phenotype_chart_data = {
                    'segments': pheno_segments,
                    'total_count': total_pheno_count
                }
                app.logger.info(f"Processed phenotype counts for stacked bar: {len(pheno_segments)} segments with colors.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching or processing phenotype counts: {e}")
        phenotype_chart_data = None
    # --- End Phenotype Section ---

    # --- Convert Row objects to Dictionaries ---
    source_db_counts = [dict(row) for row in source_db_counts_rows]

    # --- Calculate max counts for scaling the bar plot ---
    max_db_count = max(row['gene_count'] for row in source_db_counts) if source_db_counts else 1

    return render_template(
        'index.html',
        category_data=category_data,
        source_db_counts=source_db_counts,
        max_db_count=max_db_count,
        antibiotic_chart_data=antibiotic_chart_data,
        phenotype_chart_data=phenotype_chart_data, # Now includes colors
    )


@app.route('/list/<category_key>')
@app.route('/list/<query_type>/<path:query_target_value>')
def list_items(category_key=None, query_type=None, query_target_value=None):
    """Lists all items belonging to a specific category."""
    db = get_db()
    decoded_category_key = unquote(category_key) if category_key else unquote(query_target_value)
    app.logger.info(f"Listing items for category key: {decoded_category_key}")

    # --- Get Configuration ---
    if decoded_category_key not in current_app.config['INDEX_CATEGORIES']:
        app.logger.warning(f"Unrecognized category key requested: {decoded_category_key}")
        abort(404, description=f"Category '{decoded_category_key}' not recognized.")

    category_config = current_app.config['INDEX_CATEGORIES'][decoded_category_key]
    category_display_name = decoded_category_key
    query_type = query_type or category_config.get('query_type')
    query_target_value = query_target_value or category_config.get('value')

    if not query_type or not query_target_value:
         app.logger.error(f"Incomplete configuration for category key: {decoded_category_key}")
         abort(500, description="Server configuration error for this category.")

    app.logger.debug(f"Category Config - Display: {category_display_name}, Type: {query_type}, Target: {query_target_value}")

    items = []
    grouped_by_class = None
    grouped_by_phenotype = None
    grouped_data = None # Changed from grouped_by_database
    is_pangen_list = (query_type == 'type' and query_target_value == 'PanGene')
    is_sourcegen_list = (query_type == 'type' and query_target_value == 'OriginalGene')

    try:
        # --- PanGene Grouping (remains the same) ---
        if is_pangen_list:
            # --- Revised PanGene Grouping Strategy ---
            app.logger.debug(f"Fetching PanGenes and grouping (Revised Strategy)")

            # 1. Get all PanGene IDs
            cursor_genes = db.execute(
                "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ?",
                (RDF_TYPE, query_target_value)
            )
            all_genes_in_category = {row['subject'] for row in cursor_genes.fetchall()}
            items = sorted(list(all_genes_in_category)) # Keep the total list for count
            app.logger.debug(f"Found {len(all_genes_in_category)} PanGene IDs.")

            if not all_genes_in_category:
                 app.logger.info("No PanGenes found, skipping grouping.")
                 # Render template will show "No items found" based on empty items list

            else:
                # Use IN clause for potentially better performance than many ORs
                # Create placeholders for the IN clause
                gene_placeholders = ','.join('?' * len(all_genes_in_category))

                # 2. Fetch Gene -> Class ID relationships
                query_classes = f"""
                    SELECT subject AS gene_id, object AS class_id
                    FROM triples
                    WHERE predicate = ? AND subject IN ({gene_placeholders})
                """
                cursor_classes = db.execute(query_classes, (HAS_RESISTANCE_CLASS, *all_genes_in_category))
                gene_to_class_pairs = cursor_classes.fetchall()
                app.logger.debug(f"Found {len(gene_to_class_pairs)} gene-class relationships.")

                # 3. Fetch Gene -> Phenotype ID relationships
                query_phenotypes = f"""
                    SELECT subject AS gene_id, object AS phenotype_id
                    FROM triples
                    WHERE predicate = ? AND subject IN ({gene_placeholders})
                """
                cursor_phenotypes = db.execute(query_phenotypes, (HAS_PREDICTED_PHENOTYPE, *all_genes_in_category))
                gene_to_phenotype_pairs = cursor_phenotypes.fetchall()
                app.logger.debug(f"Found {len(gene_to_phenotype_pairs)} gene-phenotype relationships.")

                # 4. Get unique Class and Phenotype IDs
                unique_class_ids = {pair['class_id'] for pair in gene_to_class_pairs if pair['class_id']}
                unique_phenotype_ids = {pair['phenotype_id'] for pair in gene_to_phenotype_pairs if pair['phenotype_id']}
                all_related_ids = unique_class_ids.union(unique_phenotype_ids)
                app.logger.debug(f"Unique Class IDs: {len(unique_class_ids)}, Phenotype IDs: {len(unique_phenotype_ids)}")

                # 5. Fetch Labels for these unique IDs (if any)
                labels = {}
                if all_related_ids:
                    id_placeholders = ','.join('?' * len(all_related_ids))
                    query_labels = f"""
                        SELECT subject AS id, object AS label
                        FROM triples
                        WHERE predicate = ? AND subject IN ({id_placeholders})
                    """
                    cursor_labels = db.execute(query_labels, (RDFS_LABEL, *all_related_ids))
                    labels = {row['id']: row['label'] for row in cursor_labels.fetchall()}
                    app.logger.debug(f"Fetched {len(labels)} labels for related IDs.")

                # 6. Group in Python using the fetched data
                grouped_by_class_temp = defaultdict(lambda: {'id': None, 'genes': set()})
                for pair in gene_to_class_pairs:
                    class_id = pair['class_id']
                    if not class_id: continue # Skip if class_id is null/empty
                    # Use label if available, otherwise use the ID itself as the key
                    display_key = labels.get(class_id, class_id)
                    grouped_by_class_temp[display_key]['id'] = class_id
                    grouped_by_class_temp[display_key]['genes'].add(pair['gene_id'])

                grouped_by_phenotype_temp = defaultdict(lambda: {'id': None, 'genes': set()})
                for pair in gene_to_phenotype_pairs:
                    phenotype_id = pair['phenotype_id']
                    if not phenotype_id: continue
                    display_key = labels.get(phenotype_id, phenotype_id)
                    grouped_by_phenotype_temp[display_key]['id'] = phenotype_id
                    grouped_by_phenotype_temp[display_key]['genes'].add(pair['gene_id'])

                # Convert sets to sorted lists and sort groups
                grouped_by_class = {
                    label: {'id': data['id'], 'genes': sorted(list(data['genes']))}
                    for label, data in sorted(grouped_by_class_temp.items())
                }
                grouped_by_phenotype = {
                    label: {'id': data['id'], 'genes': sorted(list(data['genes']))}
                    for label, data in sorted(grouped_by_phenotype_temp.items())
                }
                app.logger.debug(f"Finished grouping (Revised). Found {len(grouped_by_class)} classes and {len(grouped_by_phenotype)} phenotypes.")

        # --- NEW: Source Gene Nested Grouping Strategy ---
        elif is_sourcegen_list:
            app.logger.debug(f"Fetching SourceGenes (target: {query_target_value}) and grouping by database, class, and phenotype.")

            # 1. Get all OriginalGene IDs
            cursor_genes = db.execute(
                "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ?",
                (RDF_TYPE, query_target_value)
            )
            all_genes_in_category = {row['subject'] for row in cursor_genes.fetchall()}
            items = sorted(list(all_genes_in_category)) # Keep the total list for count
            app.logger.debug(f"Found {len(all_genes_in_category)} OriginalGene IDs.")

            if not all_genes_in_category:
                 app.logger.info("No OriginalGenes found, skipping grouping.")
            else:
                # 2. Fetch all relevant triples for these genes efficiently
                gene_placeholders = ','.join('?' * len(all_genes_in_category))
                query_triples = f"""
                    SELECT subject, predicate, object, object_is_literal
                    FROM triples
                    WHERE subject IN ({gene_placeholders})
                      AND predicate IN (?, ?, ?, ?) -- Fetch Type, DB, Class, Phenotype triples
                """
                # Ensure the order of predicates matches the placeholders
                predicates_to_fetch = [RDF_TYPE, IS_FROM_DATABASE, HAS_RESISTANCE_CLASS, HAS_PREDICTED_PHENOTYPE]
                cursor_triples = db.execute(query_triples, (*all_genes_in_category, *predicates_to_fetch))
                all_triples = cursor_triples.fetchall()
                app.logger.debug(f"Fetched {len(all_triples)} relevant triples for {len(all_genes_in_category)} genes.")

                # 3. Process triples to build the nested structure
                # { db_name: { 'genes': {gene1, gene2}, 'classes': {class_label: {gene1, gene3}}, 'phenotypes': {pheno_label: {gene2, gene3}} } }
                grouped_data_temp = defaultdict(lambda: {
                    'genes': set(),
                    'classes': defaultdict(set),
                    'phenotypes': defaultdict(set)
                })
                gene_to_db = {} # Helper to know which DB a gene belongs to

                # First pass: Assign genes to databases
                for triple in all_triples:
                    gene_id = triple['subject']
                    predicate = triple['predicate']
                    obj = triple['object']
                    if predicate == IS_FROM_DATABASE and triple['object_is_literal']:
                        db_name = obj
                        if db_name:
                            # Use a canonical name if needed, e.g., strip whitespace
                            db_name = db_name.strip()
                            grouped_data_temp[db_name]['genes'].add(gene_id)
                            gene_to_db[gene_id] = db_name # Store mapping

                # Second pass: Assign classes and phenotypes within the correct database
                for triple in all_triples:
                    gene_id = triple['subject']
                    predicate = triple['predicate']
                    obj = triple['object']
                    db_name = gene_to_db.get(gene_id)

                    if db_name: # Only process if gene belongs to a known database
                        if predicate == HAS_RESISTANCE_CLASS and not triple['object_is_literal']:
                            # Fetch label for the class URI (object)
                            class_label = get_label(db, obj) or obj # Fallback to URI if no label
                            if class_label:
                                grouped_data_temp[db_name]['classes'][class_label.strip()].add(gene_id)
                        elif predicate == HAS_PREDICTED_PHENOTYPE and not triple['object_is_literal']:
                            # Fetch label for the phenotype URI (object)
                            phenotype_label = get_label(db, obj) or obj # Fallback to URI if no label
                            if phenotype_label:
                                grouped_data_temp[db_name]['phenotypes'][phenotype_label.strip()].add(gene_id)

                # 4. Convert sets to sorted lists and sort groups
                grouped_data = {}
                for db_name, data in sorted(grouped_data_temp.items()):
                    # Only include databases that actually have genes associated
                    if data['genes']:
                        grouped_data[db_name] = {
                            'genes': sorted(list(data['genes'])), # Total genes in this DB group
                            'classes': {
                                class_label: sorted(list(genes))
                                for class_label, genes in sorted(data['classes'].items())
                            },
                            'phenotypes': {
                                phenotype_label: sorted(list(genes))
                                for phenotype_label, genes in sorted(data['phenotypes'].items())
                            }
                        }
                app.logger.debug(f"Finished nested grouping for SourceGenes. Found {len(grouped_data)} databases.")

        else:
            # --- Default handling for other categories (remains the same) ---
            app.logger.debug(f"Fetching simple list for Type: {query_type}, Target: {query_target_value}")
            if query_type == 'type':
                cursor = db.execute(
                    "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject",
                    (RDF_TYPE, query_target_value)
                )
                items = [row['subject'] for row in cursor.fetchall()]
            elif query_type == 'predicate_object':
                 cursor = db.execute(
                     "SELECT DISTINCT object FROM triples WHERE predicate = ? AND object_is_literal = 0 ORDER BY object",
                     (query_target_value,)
                 )
                 items = [row['object'] for row in cursor.fetchall()]
            else:
                 app.logger.error(f"Unsupported query_type '{query_type}' for category {decoded_category_key}")
                 abort(500, "Server configuration error.")
            app.logger.debug(f"Found {len(items)} items for category {decoded_category_key}.")

    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching list for {decoded_category_key}: {e}", exc_info=True)
        abort(500, description="Database error occurred.")
    except Exception as ex: # General exception handler
        app.logger.error(f"Unexpected error in list_items for {decoded_category_key}: {ex}", exc_info=True)
        abort(500, description="An unexpected server error occurred.")

    # Check if items were found
    # Adjusted condition to handle different grouping scenarios
    no_items_found = not items and not (
        (is_pangen_list and (grouped_by_class or grouped_by_phenotype)) or
        (is_sourcegen_list and grouped_data)
    )
    if no_items_found:
         app.logger.warning(f"No items found for category: {decoded_category_key}. Rendering empty list.")
         # Template will handle the display of "No items found"

    return render_template(
        'list.html',
        category_key=decoded_category_key,
        category_display_name=category_display_name,
        query_target_value=query_target_value,
        items=items,
        is_pangen_list=is_pangen_list,
        grouped_by_class=grouped_by_class,
        grouped_by_phenotype=grouped_by_phenotype,
        grouped_data=grouped_data, # Pass the new nested structure
        is_sourcegen_list=is_sourcegen_list,
        query_type=query_type
    )


# --- Helper function to get label (add if not already present) ---
def get_label(db, item_id):
    """Fetches the rdfs:label for a given item ID."""
    if not item_id:
        return None
    try:
        cursor = db.execute("SELECT object FROM triples WHERE subject = ? AND predicate = ?", (item_id, RDFS_LABEL))
        result = cursor.fetchone()
        return result['object'] if result else None
    except sqlite3.Error as e:
        current_app.logger.error(f"Error fetching label for {item_id}: {e}")
        return None
# --- End Helper ---


# --- REVISED show_related_items function ---
@app.route('/related/<predicate>/<path:object_value>', endpoint='show_related_items')
def show_related_items(predicate, object_value):
    """
    Displays items (subjects) that are related to a specific object_value
    via a given predicate. For example, show all genes (subjects) where
    predicate='is_from_database' and object_value='AMRFinderPlus'.
    """
    db = get_db()
    decoded_predicate = unquote(predicate)
    decoded_object_value = unquote(object_value)
    app.logger.info(f"Showing related items for predicate='{decoded_predicate}', object='{decoded_object_value}'")

    predicate_display_name = current_app.config['PREDICATE_DISPLAY_NAMES'].get(decoded_predicate, decoded_predicate)

    # --- Generate Title and Description ---
    # Attempt to get label for the object value if it's likely an entity (heuristic: not a literal number)
    object_display = decoded_object_value
    if not decoded_object_value.isdigit(): # Simple check if it might be an entity ID/label
        object_label = get_label(db, decoded_object_value)
        if object_label:
            object_display = object_label # Use label if found

    page_title = f"Items related to '{object_display}'"
    description = f"Listing items (subjects) where the property <code>{predicate_display_name}</code> points to <code>{object_display}</code>."

    genes = []
    grouped_genes = None
    total_gene_count = 0
    is_grouped_view = False # Flag for template

    try:
        # --- Fetch Related Subjects ---
        cursor = db.execute(
            "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject",
            (decoded_predicate, decoded_object_value)
        )
        related_subjects = [row['subject'] for row in cursor.fetchall()]
        total_gene_count = len(related_subjects)
        app.logger.debug(f"Found {total_gene_count} subjects related via '{decoded_predicate}' to '{decoded_object_value}'.")

        if not related_subjects:
            app.logger.warning(f"No subjects found for predicate='{decoded_predicate}', object='{decoded_object_value}'.")
            # Render template will show "No items found"

        else:
            # --- Grouping Logic (Example: Group genes from a DB by Resistance Class) ---
            # Check if the predicate suggests the subjects are genes and grouping is desired
            # We'll group if the predicate is 'is_from_database'
            if decoded_predicate == IS_FROM_DATABASE:
                is_grouped_view = True
                app.logger.debug("Grouping related genes by resistance class.")

                gene_placeholders = ','.join('?' * len(related_subjects))

                # Fetch Gene -> Class ID relationships for these specific genes
                query_classes = f"""
                    SELECT t_rel.subject AS gene_id, t_rel.object AS class_id, t_label.object AS class_label
                    FROM triples t_rel
                    LEFT JOIN triples t_label ON t_rel.object = t_label.subject AND t_label.predicate = ?
                    WHERE t_rel.predicate = ? AND t_rel.subject IN ({gene_placeholders})
                """
                cursor_classes = db.execute(query_classes, (RDFS_LABEL, HAS_RESISTANCE_CLASS, *related_subjects))
                gene_class_info = cursor_classes.fetchall()
                app.logger.debug(f"Fetched {len(gene_class_info)} class relationships for grouping.")

                # Group in Python
                grouped_genes_temp = defaultdict(lambda: {'id': None, 'genes': set()})
                genes_with_class = set()

                for row in gene_class_info:
                    class_id = row['class_id']
                    if not class_id: continue # Skip if no class assigned

                    # Use label if available, otherwise use the ID itself as the key
                    display_key = row['class_label'] or class_id
                    grouped_genes_temp[display_key]['id'] = class_id # Store the actual ID
                    grouped_genes_temp[display_key]['genes'].add(row['gene_id'])
                    genes_with_class.add(row['gene_id'])

                # Add genes without any class to a specific group
                genes_without_class = set(related_subjects) - genes_with_class
                if genes_without_class:
                    grouped_genes_temp["No Class Assigned"]['genes'].update(genes_without_class)
                    grouped_genes_temp["No Class Assigned"]['id'] = None # No ID for this group

                # Convert sets to sorted lists and sort groups by label
                grouped_genes = {
                    label: {'id': data['id'], 'genes': sorted([{'id': gene_id, 'link': url_for('details', item_id=gene_id)} for gene_id in data['genes']], key=lambda x: x['id'])}
                    for label, data in sorted(grouped_genes_temp.items())
                }
                app.logger.debug(f"Finished grouping. Found {len(grouped_genes)} class groups.")

            else:
                # --- No Grouping - Prepare simple list ---
                genes = sorted([{'id': subject, 'link': url_for('details', item_id=subject)} for subject in related_subjects], key=lambda x: x['id'])

    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching related items for predicate='{decoded_predicate}', object='{decoded_object_value}': {e}", exc_info=True)
        abort(500, description="Database error occurred.")
    except Exception as ex:
        app.logger.error(f"Unexpected error in show_related_items: {ex}", exc_info=True)
        abort(500, description="An unexpected server error occurred.")

    return render_template(
        'related_items.html', # Use the new template
        page_title=page_title,
        description=description,
        predicate=decoded_predicate, # Pass predicate/object for context
        object_value=decoded_object_value,
        genes=genes, # Pass the simple list (will be empty if grouped)
        grouped_genes=grouped_genes, # Pass grouped data (will be None if not grouped)
        total_gene_count=total_gene_count,
        is_grouped_view=is_grouped_view # Pass the flag
    )


@app.route('/details/<path:item_id>')
def details(item_id):
    """Shows details (properties and references) for a specific item."""
    decoded_item_id = unquote(item_id)
    app.logger.info(f"Details route: Fetching details for item ID: {decoded_item_id}")

    # --- Use the helper function ---
    item_details = get_item_details(decoded_item_id)
    # --- End use helper function ---

    # Check if item exists
    if not item_details:
        app.logger.warning(f"No data found for item_id: {decoded_item_id} via helper. Returning 404.")
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")

    # Define predicates for the PanGene specific layout (needed for template logic)
    pangen_key_info_preds = ['has_length', 'same_as', 'card_link', 'accession', 'is_from_database']
    pangen_right_col_preds = ['has_resistance_class', 'has_predicted_phenotype', 'translates_to', 'member_of']

    # Get predicate map from context processor (already available in template)

    return render_template(
        'details.html',
        item_id=decoded_item_id,
        details=item_details, # Pass the structured details from helper
        is_pangen=item_details['is_pangen'], # Pass the flag from helper
        # Pass predicate constants needed for PanGene layout checks in template
        pangen_key_info_preds=pangen_key_info_preds,
        pangen_right_col_preds=pangen_right_col_preds
        # predicate_map is available via context processor
    )


# --- Run the App ---
if __name__ == '__main__':
    # Use 0.0.0.0 to be accessible externally, Render uses $PORT
    port = int(os.environ.get('PORT', 5000))
    # Turn off debug mode for production/deployment
    app.run(host='0.0.0.0', port=port, debug=False) 