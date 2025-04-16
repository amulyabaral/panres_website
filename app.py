import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app
import os
import logging
from urllib.parse import unquote # Needed for handling URL-encoded values
from collections import defaultdict # Import defaultdict

# --- Configuration ---
DATABASE = 'panres_ontology.db' # Make sure this file is in the same directory or provide the correct path
CITATION_TEXT = "Hannah-Marie Martiny, Nikiforos Pyrounakis, Thomas N Petersen, Oksana Lukjančenko, Frank M Aarestrup, Philip T L C Clausen, Patrick Munk, ARGprofiler—a pipeline for large-scale analysis of antimicrobial resistance genes and their flanking regions in metagenomic datasets, <i>Bioinformatics</i>, Volume 40, Issue 3, March 2024, btae086, <a href=\"https://doi.org/10.1093/bioinformatics/btae086\" target=\"_blank\" rel=\"noopener noreferrer\">https://doi.org/10.1093/bioinformatics/btae086</a>"
SITE_NAME = "PanRes 2.0 Database"

# Define how categories are presented and queried on the index page
# 'query_type': 'type' -> count/list subjects with rdf:type = value
# 'query_type': 'predicate_object' -> count/list distinct objects for predicate = value
# 'query_type': 'predicate_subject' -> count/list distinct subjects for predicate = value (less common)
INDEX_CATEGORIES = {
    "PanRes Genes": {'query_type': 'type', 'value': 'PanGene', 'description': 'Unique gene sequences curated in PanRes.'},
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

# --- Define Colors for Pie Chart ---
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
# --- NEW: Define Colors for Donut Chart (can reuse or define separately) ---
# Let's reuse the same colors for simplicity, but shifted slightly
DONUT_CHART_COLORS = PIE_CHART_COLORS[1:] + PIE_CHART_COLORS[:1]

# Ensure RDF_TYPE, RDFS_LABEL, HAS_RESISTANCE_CLASS, HAS_PREDICTED_PHENOTYPE are defined globally
IS_FROM_DATABASE = 'is_from_database' # Define constant for clarity

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

# Make INDEX_CATEGORIES and PREDICATE_DISPLAY_NAMES available to all templates
@app.context_processor
def inject_global_data():
    return dict(
        index_categories=app.config['INDEX_CATEGORIES'],
        predicate_map=app.config['PREDICATE_DISPLAY_NAMES'],
        citation_text=app.config['CITATION_TEXT'], # Pass citation
        site_name=app.config['SITE_NAME'], # Pass site name
        # Make constants available for templates if needed directly
        RDF_TYPE=RDF_TYPE,
        RDFS_LABEL=RDFS_LABEL,
        RDFS_COMMENT=RDFS_COMMENT,
        # --- ADD CONSTANTS TO CONTEXT IF NEEDED IN TEMPLATES (Optional) ---
        # Not strictly needed for current templates, but good practice if used directly
        # HAS_RESISTANCE_CLASS=HAS_RESISTANCE_CLASS,
        # HAS_PREDICTED_PHENOTYPE=HAS_PREDICTED_PHENOTYPE
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
    phenotype_counts_rows = [] # <-- New: Initialize list for phenotype counts
    antibiotic_chart_data = None # Initialize chart data as None
    phenotype_chart_data = None # <-- New: Initialize phenotype chart data as None
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

        # --- Process Antibiotic Class Counts for Pie Chart (Top N + Others) ---
        if antibiotic_class_counts:
            top_n_class = 7 # Keep this specific to antibiotic classes if needed
            chart_labels_class = []
            chart_data_points_class = []
            chart_colors_class = []

            # Take top N
            top_classes = antibiotic_class_counts[:top_n_class]
            for i, row in enumerate(top_classes):
                chart_labels_class.append(row['class_name'])
                chart_data_points_class.append(row['gene_count'])
                chart_colors_class.append(PIE_CHART_COLORS[i % (len(PIE_CHART_COLORS) -1)])

            # Group remaining into "Others"
            if len(antibiotic_class_counts) > top_n_class:
                other_classes = antibiotic_class_counts[top_n_class:]
                other_count_class = sum(row['gene_count'] for row in other_classes)
                if other_count_class > 0:
                    chart_labels_class.append("Others")
                    chart_data_points_class.append(other_count_class)
                    chart_colors_class.append(PIE_CHART_COLORS[-1]) # Use the last defined color for Others

            antibiotic_chart_data = {
                'labels': chart_labels_class,
                'data': chart_data_points_class,
                'colors': chart_colors_class
            }
            app.logger.info(f"Processed antibiotic counts for pie chart: {len(chart_labels_class)} slices.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching or processing antibiotic class counts: {e}")
        # Handle error appropriately, antibiotic_chart_data remains None

    # --- NEW: Fetch Predicted Phenotype Counts for Stacked Bar ---
    phenotype_chart_data = None # Initialize as None
    try:
        cursor_pheno = db.execute("""
            SELECT object AS phenotype_name, COUNT(DISTINCT subject) AS gene_count
            FROM Triples
            WHERE predicate = 'has_predicted_phenotype' AND object_is_literal = 0
            GROUP BY object
            ORDER BY gene_count DESC
        """)
        phenotype_counts = [dict(row) for row in cursor_pheno.fetchall()]
        app.logger.info(f"Fetched {len(phenotype_counts)} predicted phenotype counts.")

        # --- Process Phenotype Counts for Stacked Bar (Top 8 + Others) ---
        if phenotype_counts:
            top_n_pheno = 8
            pheno_segments = [] # List to hold data for each bar segment/legend item
            pheno_colors = DONUT_CHART_COLORS # Reuse colors

            # Take top N
            top_phenotypes = phenotype_counts[:top_n_pheno]
            total_count_top_n = sum(row['gene_count'] for row in top_phenotypes)

            # Calculate "Others" count
            other_count_pheno = 0
            if len(phenotype_counts) > top_n_pheno:
                other_phenotypes = phenotype_counts[top_n_pheno:]
                other_count_pheno = sum(row['gene_count'] for row in other_phenotypes)

            # Total count for percentage calculation
            total_pheno_count = total_count_top_n + other_count_pheno

            if total_pheno_count > 0: # Avoid division by zero
                # Add top N segments
                for i, row in enumerate(top_phenotypes):
                    percentage = (row['gene_count'] / total_pheno_count * 100)
                    pheno_segments.append({
                        'name': row['phenotype_name'],
                        'count': row['gene_count'],
                        'percentage': percentage,
                        'color': pheno_colors[i % (len(pheno_colors) -1)] # Cycle colors, excluding last for 'Others'
                    })

                # Add "Others" segment if applicable
                if other_count_pheno > 0:
                    percentage = (other_count_pheno / total_pheno_count * 100)
                    pheno_segments.append({
                        'name': "Others",
                        'count': other_count_pheno,
                        'percentage': percentage,
                        'color': pheno_colors[-1] # Use the last color for Others
                    })

                phenotype_chart_data = {
                    'segments': pheno_segments,
                    'total_count': total_pheno_count
                }
                app.logger.info(f"Processed phenotype counts for stacked bar: {len(pheno_segments)} segments.")

    except sqlite3.Error as e:
        app.logger.error(f"Error fetching or processing phenotype counts: {e}")
        phenotype_chart_data = None # Ensure it's None on error
    # --- End Phenotype Section ---

    # --- Convert Row objects to Dictionaries ---
    # Convert source_db_counts_rows to a list of dicts
    source_db_counts = [dict(row) for row in source_db_counts_rows]

    # --- Calculate max counts for scaling the bar plot ---
    # Keep the fixed max count for the source DB plot if desired, or calculate dynamically
    max_db_count = max(row['gene_count'] for row in source_db_counts) if source_db_counts else 1
    # max_db_count = 15000 # Or keep the fixed maximum value if preferred

    return render_template(
        'index.html',
        category_data=category_data,
        source_db_counts=source_db_counts,
        max_db_count=max_db_count,
        antibiotic_chart_data=antibiotic_chart_data,
        phenotype_chart_data=phenotype_chart_data # Pass new structure
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


@app.route('/related/<predicate>/<path:object_value>')
def genes_related_to(predicate, object_value):
    """
    Lists genes (subjects) related to a specific object via a predicate.
    Example: /related/is_from_database/CARD lists genes from CARD.
    Enhancement: If predicate is 'is_from_database', group genes by resistance class.
    """
    db = get_db()
    decoded_object_value = unquote(object_value)

    app.logger.info(f"Fetching subjects related to object '{decoded_object_value}' via predicate '{predicate}'")

    # --- FIX: Get predicate map from app config ---
    predicate_map = current_app.config.get('PREDICATE_DISPLAY_NAMES', {})
    index_categories = current_app.config.get('INDEX_CATEGORIES', {}) # Also get index_categories here
    # --- END FIX ---

    # Basic query to find related subjects (genes)
    cursor_subjects = db.execute(
        "SELECT DISTINCT subject FROM Triples WHERE predicate = ? AND object = ?",
        (predicate, decoded_object_value)
    )
    genes_raw = cursor_subjects.fetchall()
    gene_ids = [row['subject'] for row in genes_raw]
    # --- Calculate total count ---
    total_gene_count = len(gene_ids)
    # --- End calculation ---

    page_title = f"Source Genes from {predicate_map.get(predicate, predicate)}: {decoded_object_value}"
    description = f"Listing Source Genes (<code>OriginalGene</code>) originating from the {predicate_map.get(predicate, predicate)} <code>{decoded_object_value}</code>."

    grouped_genes = None
    genes_list_for_template = []

    # --- Grouping Logic for 'is_from_database' ---
    if predicate == IS_FROM_DATABASE and gene_ids:
        grouped_genes = defaultdict(lambda: {'id': None, 'genes': []})

        # Fetch resistance class for each gene
        placeholders = ','.join('?' for _ in gene_ids)
        query_classes = f"""
            SELECT subject, object
            FROM Triples
            WHERE predicate = ? AND subject IN ({placeholders})
        """
        try:
            cursor_classes = db.execute(query_classes, [HAS_RESISTANCE_CLASS] + gene_ids)
            gene_class_pairs = cursor_classes.fetchall()

            # Map genes to their classes
            gene_to_classes = defaultdict(list)
            for row in gene_class_pairs:
                gene_to_classes[row['subject']].append(row['object'])

            # Populate the grouped_genes structure
            for gene_id in gene_ids:
                classes = gene_to_classes.get(gene_id)
                gene_data = {'id': gene_id, 'link': url_for('details', item_id=gene_id)}

                if classes:
                    for resistance_class in classes:
                        display_label = resistance_class # Use ID as label for now
                        grouped_genes[display_label]['id'] = resistance_class
                        grouped_genes[display_label]['genes'].append(gene_data)
                else:
                    grouped_genes['No Class Assigned']['genes'].append(gene_data)

            # Sort groups
            grouped_genes = dict(sorted(grouped_genes.items(), key=lambda item: (item[0] == 'No Class Assigned', item[0].lower())))

        except sqlite3.Error as e:
             app.logger.error(f"Error fetching resistance classes for genes from {decoded_object_value}: {e}")
             grouped_genes = None
             # Prepare flat list only if grouping fails
             genes_list_for_template = [{'id': gid, 'link': url_for('details', item_id=gid)} for gid in gene_ids]

    # Prepare flat list if not grouping or if grouping failed
    if not grouped_genes:
         genes_list_for_template = [{'id': gid, 'link': url_for('details', item_id=gid)} for gid in gene_ids]


    return render_template(
        'related_genes.html',
        page_title=page_title,
        description=description,
        predicate=predicate,
        object_value=decoded_object_value,
        genes=genes_list_for_template, # Pass the flat list (used if not grouping or if grouping failed)
        grouped_genes=grouped_genes,   # Pass the grouped structure (can be None)
        is_grouped_view=(predicate == IS_FROM_DATABASE and grouped_genes is not None),
        # --- Pass the total count ---
        total_gene_count=total_gene_count,
        # --- End passing count ---
        index_categories=index_categories,
        predicate_map=predicate_map
    )


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
        # Convert rows to dictionaries immediately for easier processing
        properties = [dict(row) for row in cursor_props.fetchall()]
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
        # Convert rows to dictionaries
        references = [dict(row) for row in cursor_refs.fetchall()]
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
    # Define predicates for the PanGene specific layout
    pangen_key_info_preds = ['has_length', 'same_as', 'card_link', 'accession', 'is_from_database']
    pangen_right_col_preds = ['has_resistance_class', 'has_predicted_phenotype', 'translates_to', 'member_of']
    processed_predicates_for_grouping = set() # Track predicates handled as key info or PanGene specific

    # Prioritize specific types
    preferred_types = ['PanGene', 'OriginalGene']
    found_preferred_type = None

    # First pass: Extract Key Info (Type, Label, Comment) and identify primary type
    for prop in properties:
        predicate = prop['predicate']
        obj = prop['object']

        if predicate == RDF_TYPE:
            item_details['types'].append(obj)
            if obj in preferred_types and not found_preferred_type:
                found_preferred_type = obj
            # Find the display name for the type
            for cat_key, cat_config in INDEX_CATEGORIES.items():
                 if cat_config['query_type'] == 'type' and cat_config['value'] == obj:
                     if not item_details['primary_type_display']: # Set the first one found
                         item_details['primary_type_display'] = cat_key
                         item_details['primary_type_category_key'] = cat_key
                     # Don't break here, might find a preferred type later
            processed_predicates_for_grouping.add(predicate) # Mark rdf:type as processed for general grouping

        elif predicate == RDFS_LABEL and not item_details['label']:
            item_details['label'] = obj
            processed_predicates_for_grouping.add(predicate)
        elif predicate in DESCRIPTION_PREDICATES and not item_details['comment']:
            item_details['comment'] = obj
            processed_predicates_for_grouping.add(predicate)

    # Ensure preferred type (PanGene/OriginalGene) is set as primary if found
    if found_preferred_type:
         for cat_key, cat_config in INDEX_CATEGORIES.items():
             if cat_config['query_type'] == 'type' and cat_config['value'] == found_preferred_type:
                 item_details['primary_type_display'] = cat_key
                 item_details['primary_type_category_key'] = cat_key
                 break

    # Determine if this is a PanGene for special handling
    is_pangen = 'PanGene' in item_details['types']

    # Second pass: Group remaining properties and fetch extra data if needed
    for prop in properties:
        predicate = prop['predicate']
        # Skip already handled key info (label, comment, type)
        # Also skip predicates handled by the specific PanGene layout if this IS a PanGene
        if predicate in processed_predicates_for_grouping or \
           (is_pangen and predicate in pangen_key_info_preds + pangen_right_col_preds):
            continue

        prop_data = {
            'value': prop['object'],
            'is_literal': bool(prop['object_is_literal']),
            'datatype': prop['object_datatype'],
            'link': None,
            'extra_info': None
        }

        if not prop_data['is_literal']:
            prop_data['link'] = url_for('details', item_id=prop['object'])

            # Enhancement: Fetch source DB for 'same_as' OriginalGene links
            if predicate == 'same_as':
                try:
                    cursor_orig_db = db.execute(
                        """SELECT T_db.object
                           FROM Triples T_type
                           JOIN Triples T_db ON T_type.subject = T_db.subject
                           WHERE T_type.subject = ?
                             AND T_type.predicate = ? AND T_type.object = ?
                             AND T_db.predicate = ?
                           LIMIT 1""",
                        (prop['object'], RDF_TYPE, 'OriginalGene', 'is_from_database')
                    )
                    db_info = cursor_orig_db.fetchone()
                    if db_info:
                        prop_data['extra_info'] = f"(from {db_info['object']})"
                except sqlite3.Error as e_extra:
                    app.logger.warning(f"Could not fetch extra DB info for {prop['object']}: {e_extra}")

        item_details['grouped_properties'][predicate].append(prop_data)

    # --- Prepare PanGene specific data structure if applicable ---
    if is_pangen:
        item_details['pangen_key_info'] = defaultdict(list)
        item_details['pangen_right_col'] = defaultdict(list)

        for prop in properties:
            predicate = prop['predicate']
            prop_data = {
                'value': prop['object'],
                'is_literal': bool(prop['object_is_literal']),
                'datatype': prop['object_datatype'],
                'link': None,
                'extra_info': None
            }
            if not prop_data['is_literal']:
                 prop_data['link'] = url_for('details', item_id=prop['object'])
                 # Add extra info for 'same_as' again specifically for PanGene view
                 if predicate == 'same_as':
                    try:
                        cursor_orig_db = db.execute(
                            """SELECT T_db.object
                               FROM Triples T_type
                               JOIN Triples T_db ON T_type.subject = T_db.subject
                               WHERE T_type.subject = ?
                                 AND T_type.predicate = ? AND T_type.object = ?
                                 AND T_db.predicate = ?
                               LIMIT 1""",
                            (prop['object'], RDF_TYPE, 'OriginalGene', 'is_from_database')
                        )
                        db_info = cursor_orig_db.fetchone()
                        if db_info:
                            prop_data['extra_info'] = f"(from {db_info['object']})"
                    except sqlite3.Error as e_extra:
                        app.logger.warning(f"Could not fetch extra DB info for {prop['object']} (PanGene view): {e_extra}")


            if predicate in pangen_key_info_preds:
                item_details['pangen_key_info'][predicate].append(prop_data)
            elif predicate in pangen_right_col_preds:
                item_details['pangen_right_col'][predicate].append(prop_data)

    # Group incoming references (always do this, but display conditionally in template)
    for ref in references:
         ref_data = {
             'subject': ref['subject'],
             'link': url_for('details', item_id=ref['subject'])
         }
         item_details['grouped_references'][ref['predicate']].append(ref_data)


    # Check if item exists (basic check)
    if not item_details['types'] and not references:
        app.logger.warning(f"No data found for item_id: {decoded_item_id}. Returning 404.")
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")


    return render_template(
        'details.html',
        item_id=decoded_item_id,
        details=item_details, # Pass the structured details
        is_pangen=is_pangen, # Pass the flag
        # Pass predicate constants needed for PanGene layout checks
        pangen_key_info_preds=pangen_key_info_preds,
        pangen_right_col_preds=pangen_right_col_preds
    )


# --- Run the App ---
if __name__ == '__main__':
    # Use 0.0.0.0 to be accessible externally, Render uses $PORT
    port = int(os.environ.get('PORT', 5000))
    # Turn off debug mode for production/deployment
    app.run(host='0.0.0.0', port=port, debug=False) 