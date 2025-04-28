import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app, jsonify, request
import os
import logging
from urllib.parse import unquote, quote
from collections import defaultdict
import datetime
import itertools
import json

# --- Configuration ---
DATABASE = 'panres_ontology.db'
CITATION_TEXT = "Hannah-Marie Martiny, Nikiforos Pyrounakis, Thomas N Petersen, Oksana Lukjančenko, Frank M Aarestrup, Philip T L C Clausen, Patrick Munk, ARGprofiler—a pipeline for large-scale analysis of antimicrobial resistance genes and their flanking regions in metagenomic datasets, <i>Bioinformatics</i>, Volume 40, Issue 3, March 2024, btae086, <a href=\"https://doi.org/10.1093/bioinformatics/btae086\" target=\"_blank\" rel=\"noopener noreferrer\" class=\"text-dtu-red hover:underline\">https://doi.org/10.1093/bioinformatics/btae086</a>"
SITE_NAME = "PanRes 2.0 Database"

# Define how categories are presented and queried
# 'query_type': 'type' -> count/list subjects with rdf:type = value
# 'query_type': 'predicate_object' -> count/list distinct objects for predicate = value
# 'query_type': 'predicate_subject' -> count/list distinct subjects for predicate = value
# 'filter_subject_type': Only count/list subjects of this type (used for Source DB)
INDEX_CATEGORIES = {
    "PanRes Genes": {'query_type': 'type', 'value': 'PanGene', 'description': 'Unique gene sequences curated in PanRes.'},
    "Source Databases": {'query_type': 'predicate_object', 'value': 'is_from_database', 'description': 'Databases contributing genes to PanRes.', 'filter_subject_type': 'OriginalGene'},
    "Antibiotic Classes": {'query_type': 'predicate_object', 'value': 'has_resistance_class', 'description': 'Classes of antibiotics genes confer resistance to.'},
    "Predicted Phenotypes": {'query_type': 'predicate_object', 'value': 'has_predicted_phenotype', 'description': 'Specific antibiotic resistances predicted for genes.'},
}

# Define common predicates and a mapping for display names
RDF_TYPE = 'rdf:type'
RDFS_LABEL = 'rdfs:label'
RDFS_COMMENT = 'rdfs:comment'
HAS_RESISTANCE_CLASS = 'has_resistance_class'
HAS_PREDICTED_PHENOTYPE = 'has_predicted_phenotype'
IS_FROM_DATABASE = 'is_from_database'
DESCRIPTION_PREDICATES = [RDFS_COMMENT, 'description', 'dc:description', 'skos:definition']
PREDICATE_MAP = {
    RDF_TYPE: "Type",
    RDFS_LABEL: "Label",
    RDFS_COMMENT: "Comment",
    'description': "Description",
    'dc:description': "Description",
    'skos:definition': "Definition",
    'has_length': "Length",
    'same_as': "Same As",
    'card_link': "CARD Link",
    'accession': "Accession",
    IS_FROM_DATABASE: "Source Database",
    HAS_RESISTANCE_CLASS: "Resistance Class",
    HAS_PREDICTED_PHENOTYPE: "Predicted Phenotype",
    'translates_to': "Translates To",
    'member_of': "Member Of",
    'subClassOf': "Subclass Of",
    'subPropertyOf': "Subproperty Of",
    'domain': "Domain",
    'range': "Range",
}

# Define a nicer color palette
# (Example using Tableau 10 colors - feel free to replace with your preferred palette)
COLOR_PALETTE = [
    '#4E79A7', '#F28E2B', '#E15759', '#76B7B2', '#59A14F',
    '#EDC948', '#B07AA1', '#FF9DA7', '#9C755F', '#BAB0AC'
]
color_cycler = itertools.cycle(COLOR_PALETTE) # Create a cycler

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)

# --- Flask App Setup ---
app = Flask(__name__)
app.config['DATABASE'] = DATABASE
app.config['SITE_NAME'] = SITE_NAME
app.config['CITATION_TEXT'] = CITATION_TEXT
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key_for_development')

# --- Database Helper Functions ---
def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(
                current_app.config['DATABASE'],
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            g.db.row_factory = sqlite3.Row
            app.logger.info(f"Database connection opened: {current_app.config['DATABASE']}")
        except sqlite3.Error as e:
            app.logger.error(f"Database connection error: {e}")
            abort(500, description="Database connection failed.")
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    if hasattr(g, 'db'):
        g.db.close()
        app.logger.info("Database connection closed.")
    if error:
        app.logger.error(f"Application context teardown error: {error}")

# Modified query_db to accept an optional connection (for use within autocomplete)
def query_db(query, args=(), one=False, db_conn=None):
    """Helper function to query the database. Can use a provided connection."""
    db = db_conn or get_db() # Use provided connection or get a new one from context
    cur = None # Initialize cur to None
    try:
        cur = db.execute(query, args)
        rv = cur.fetchall()
        # Important: Only close the cursor here, let the context manager handle the connection
        cur.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        app.logger.error(f"Database query error: {e}\nQuery: {query}\nArgs: {args}")
        if cur:
            cur.close() # Ensure cursor is closed on error
        # Depending on the context, you might want to return None, an empty list, or re-raise
        return None # Or return []
    except Exception as e: # Catch other potential errors
        app.logger.error(f"Unexpected error during query: {e}\nQuery: {query}\nArgs: {args}")
        if cur:
            cur.close()
        return None


# --- Data Fetching Logic ---

def get_label(item_id, db_conn=None):
    """Fetches the rdfs:label for a given item ID. Can use provided db connection."""
    result = query_db("SELECT object FROM triples WHERE subject = ? AND predicate = ?", (item_id, RDFS_LABEL), one=True, db_conn=db_conn)
    return result['object'] if result else item_id # Fallback to ID if no label

def get_item_details(item_id):
    """Fetches all properties and referencing items for a given item ID."""
    db = get_db() # Use context-managed connection for this function
    predicate_map = PREDICATE_MAP
    details = {
        'id': item_id,
        'label': get_label(item_id, db_conn=db), # Pass connection
        'properties': defaultdict(list), # Properties the item *has*
        'raw_properties': defaultdict(list), # Temp storage
        'referencing_items': [], # Flat list of items *linking to* this item
        'grouped_referencing_items': None, # Grouped items *linking to* this item (used for DB view)
        'primary_type': None,
        'primary_type_display': None,
        'primary_type_category_key': None,
        'view_item_type': None, # Specific type for view logic (e.g., 'SourceDatabase', 'AntibioticClass')
        'grouping_basis': None, # How referencing items are grouped (e.g., 'Antibiotic Class')
        'is_pangen': False,
        'description': None
    }
    # Properties to hide in category views
    TECHNICAL_PROPS_DISPLAY = ["Type", "Subclass Of", "Domain", "Range", "Subproperty Of"]

    # 1. Fetch outgoing properties (subject -> predicate -> object)
    # Use try-finally to ensure cursor is closed
    properties_cursor = None
    try:
        properties_cursor = db.execute("SELECT predicate, object FROM triples WHERE subject = ?", (item_id,))
        for row in properties_cursor:
            predicate, obj = row['predicate'], row['object']
            details['raw_properties'][predicate].append(obj)
            # Try to determine primary type and description
            if predicate == RDF_TYPE:
                # Store the first type found as primary, check for PanGene specifically
                if not details['primary_type']:
                     details['primary_type'] = obj
                if obj == 'PanGene':
                    details['is_pangen'] = True
                    details['view_item_type'] = 'PanGene' # Explicitly set for genes
            elif predicate in DESCRIPTION_PREDICATES and not details['description']:
                 details['description'] = obj # Take the first description found
    finally:
        if properties_cursor:
            properties_cursor.close()


    # 1b. Process properties for display (check links, get labels)
    check_cur = None
    try:
        check_cur = db.cursor()
        for predicate, objects in details['raw_properties'].items():
            pred_display = predicate_map.get(predicate, predicate)
            processed_values = []
            for obj_val in objects:
                # Check if the object exists as a subject (heuristic for being a resource/link)
                check_cur.execute("SELECT 1 FROM triples WHERE subject = ? LIMIT 1", (obj_val,))
                is_link = check_cur.fetchone() is not None
                # Get label if it's a link, pass connection
                display_val = get_label(obj_val, db_conn=db) if is_link else obj_val

                # Prepare info for potential "list related" link
                list_link_info = None
                # Only add "list related" for predicates that make sense to group by (like class, phenotype, db)
                if is_link and predicate in [HAS_RESISTANCE_CLASS, HAS_PREDICTED_PHENOTYPE, IS_FROM_DATABASE, RDF_TYPE]:
                     list_link_info = {
                         'predicate_key': predicate, # Use the raw predicate key
                         'predicate_display': pred_display,
                         'object_value_encoded': quote(obj_val) # Pass encoded value for URL
                     }

                processed_values.append({
                    'value': obj_val, # Raw value needed for link generation
                    'display': display_val,
                    'is_link': is_link,
                    'list_link_info': list_link_info
                })
            # Only add non-technical properties OR if it's not a category view later
            # We will filter details['properties'] later based on view_item_type
            details['properties'][pred_display] = sorted(processed_values, key=lambda x: x['display']) # Sort values by display name
    finally:
        if check_cur:
            check_cur.close()

    del details['raw_properties'] # Remove temporary raw storage

    # 2. Fetch incoming references (subject -> predicate -> item_id)
    # Store these temporarily, we might group them later
    raw_referencing_items = []
    references_cursor = None
    try:
        references_cursor = db.execute("SELECT subject, predicate FROM triples WHERE object = ?", (item_id,))
        for row in references_cursor:
            # ref_pred_display = predicate_map.get(row['predicate'], row['predicate']) # Not needed for gene lists
            raw_referencing_items.append({
                'ref_id': row['subject'],
                'predicate': row['predicate'], # Keep predicate for potential filtering/logic
                # 'predicate_display': ref_pred_display, # Not shown directly in new design
                'ref_label': get_label(row['subject'], db_conn=db) # Fetch label for display, pass connection
            })
    finally:
        if references_cursor:
            references_cursor.close()

    # Sort raw references primarily by label for consistent ordering before grouping
    raw_referencing_items.sort(key=lambda x: x['ref_label'])

    # 3. Determine Display Type, Category Key, and Specific View Type
    # This section now also sets details['view_item_type'] used for template logic
    if details['primary_type']:
        details['primary_type_display'] = details['primary_type'] # Default display
        # Check against index categories first
        for cat_key, cat_info in INDEX_CATEGORIES.items():
            if cat_info['query_type'] == 'type' and cat_info['value'] == details['primary_type']:
                details['primary_type_display'] = cat_key
                details['primary_type_category_key'] = cat_key
                # Set specific view type if it's a known primary type category (like PanGene)
                if details['primary_type'] == 'PanGene':
                     details['view_item_type'] = 'PanGene'
                # Add other primary type categories here if needed
                break # Found primary type category

        # If not found via primary type, check if it's an *object* of known category predicates
        if not details['view_item_type']: # Only check if not already identified (e.g., as PanGene)
            # Pass connection to query_db
            is_class = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (HAS_RESISTANCE_CLASS, item_id), one=True, db_conn=db)
            if is_class:
                 details['primary_type_display'] = "Antibiotic Class"
                 details['primary_type_category_key'] = "Antibiotic Classes"
                 details['view_item_type'] = 'AntibioticClass'
            else:
                 is_phenotype = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (HAS_PREDICTED_PHENOTYPE, item_id), one=True, db_conn=db)
                 if is_phenotype:
                     details['primary_type_display'] = "Predicted Phenotype"
                     details['primary_type_category_key'] = "Predicted Phenotypes"
                     details['view_item_type'] = 'PredictedPhenotype'
                 else:
                     is_database = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (IS_FROM_DATABASE, item_id), one=True, db_conn=db)
                     if is_database:
                         details['primary_type_display'] = "Source Database"
                         details['primary_type_category_key'] = "Source Databases"
                         details['view_item_type'] = 'SourceDatabase'
            # Add more checks here if other category types exist (e.g., based on different predicates)

    # 4. Filter Properties and Process Referencing Items based on View Type
    if details['view_item_type'] in ['SourceDatabase', 'AntibioticClass', 'PredictedPhenotype']:
        # Filter out technical properties for category views
        details['properties'] = {k: v for k, v in details['properties'].items() if k not in TECHNICAL_PROPS_DISPLAY}
        # Keep description if present
        if details['description']:
            details['properties']['Description'] = [{'value': details['description'], 'display': details['description'], 'is_link': False, 'list_link_info': None}]

        if details['view_item_type'] == 'SourceDatabase':
            # Group referencing genes by Antibiotic Class
            details['grouping_basis'] = 'Antibiotic Class'
            grouped = defaultdict(list)
            # Ensure they are linked via the correct predicate and are OriginalGenes
            # Fetch OriginalGenes linked to this database
            original_gene_query = """
                SELECT T1.subject
                FROM triples T1
                JOIN triples T2 ON T1.subject = T2.subject
                WHERE T1.predicate = ? AND T1.object = ? AND T2.predicate = ? AND T2.object = 'OriginalGene'
            """
            gene_results = query_db(original_gene_query, (IS_FROM_DATABASE, item_id, RDF_TYPE), db_conn=db)
            gene_ids = [row['subject'] for row in gene_results] if gene_results else []


            if gene_ids:
                # Fetch classes for these genes
                placeholders = ','.join('?' * len(gene_ids))
                class_query = f"""
                    SELECT subject, object FROM triples
                    WHERE predicate = ? AND subject IN ({placeholders})
                """
                class_results = query_db(class_query, (HAS_RESISTANCE_CLASS, *gene_ids), db_conn=db)
                gene_to_classes = defaultdict(list)
                if class_results:
                    for row in class_results:
                        gene_to_classes[row['subject']].append(row['object'])

                # Populate grouped dictionary using the original raw_referencing_items map
                gene_info_map = {item['ref_id']: item for item in raw_referencing_items}
                for gene_id in gene_ids:
                    gene_item = gene_info_map.get(gene_id)
                    if not gene_item: continue # Should not happen, but safety check

                    classes = gene_to_classes.get(gene_id)
                    if classes:
                        for class_name in classes:
                            # Use class label for grouping key
                            class_label = get_label(class_name, db_conn=db)
                            grouped[class_label].append(gene_item)
                    else:
                        grouped['No Class Assigned'].append(gene_item)

            # Sort groups by name, and items within groups already sorted by label
            details['grouped_referencing_items'] = dict(sorted(grouped.items()))

        else: # AntibioticClass or PredictedPhenotype - show flat list of genes
            # Filter raw_referencing_items to only include PanGenes linked via the correct predicate
            relevant_predicate = HAS_RESISTANCE_CLASS if details['view_item_type'] == 'AntibioticClass' else HAS_PREDICTED_PHENOTYPE
            details['referencing_items'] = [
                item for item in raw_referencing_items if item['predicate'] == relevant_predicate
            ]
            # No grouping basis needed, template will show flat list

    else: # Default view (e.g., for PanGene or other types)
        # Keep all properties (already processed)
        # Keep referencing items flat
        details['referencing_items'] = raw_referencing_items
        # No grouping basis needed

    # Final check for existence if nothing was found
    if not details['properties'] and not details['referencing_items'] and not details['grouped_referencing_items']:
         exists = query_db("SELECT 1 FROM triples WHERE subject = ? OR object = ? LIMIT 1", (item_id, item_id), one=True, db_conn=db)
         if not exists:
             app.logger.warning(f"Item ID {item_id} not found as subject or object.")
             return None

    return details

def get_category_counts():
    """Calculates counts for categories defined in INDEX_CATEGORIES."""
    db = get_db() # Use context-managed connection
    counts = {}
    for key, config in INDEX_CATEGORIES.items():
        query_type = config['query_type']
        value = config['value']
        filter_subject_type = config.get('filter_subject_type') # Get optional filter

        count = 0
        if query_type == 'type':
            # Count subjects of a specific type
            result = query_db("SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ? AND object = ?", (RDF_TYPE, value), one=True, db_conn=db)
            count = result['count'] if result else 0
        elif query_type == 'predicate_object':
            # Count distinct objects for a given predicate
            if filter_subject_type:
                 # Apply filter: count distinct objects where the subject is of a specific type
                 query = f"""
                     SELECT COUNT(DISTINCT t1.object) as count
                     FROM triples t1
                     JOIN triples t2 ON t1.subject = t2.subject
                     WHERE t1.predicate = ? AND t2.predicate = ? AND t2.object = ?
                 """
                 result = query_db(query, (value, RDF_TYPE, filter_subject_type), one=True, db_conn=db)
            else:
                 # No filter, just count distinct objects for the predicate
                 result = query_db("SELECT COUNT(DISTINCT object) as count FROM triples WHERE predicate = ?", (value,), one=True, db_conn=db)
            count = result['count'] if result else 0
        elif query_type == 'predicate_subject':
             # Count distinct subjects for a given predicate (less common)
             result = query_db("SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ?", (value,), one=True, db_conn=db)
             count = result['count'] if result else 0

        counts[key] = count
        app.logger.debug(f"Category '{key}': Count = {count}")
    return counts

def get_chart_data():
    """Fetches data specifically formatted for the homepage charts."""
    chart_data = {
        'source_db': None,
        'phenotype': None,
        'antibiotic': None,
    }
    db = get_db() # Use context-managed connection
    # Reset color cycler for each request to ensure consistency if data changes slightly
    global color_cycler
    color_cycler = itertools.cycle(COLOR_PALETTE)

    # 1. Source Database Data (Bar Chart - based on OriginalGene sources)
    try:
        # Count OriginalGenes per database
        query = f"""
            SELECT T1.object AS database_name, COUNT(DISTINCT T1.subject) AS gene_count
            FROM triples T1
            JOIN triples T2 ON T1.subject = T2.subject
            WHERE T1.predicate = ? AND T2.predicate = ? AND T2.object = 'OriginalGene'
            GROUP BY T1.object
            ORDER BY gene_count DESC;
        """
        results = query_db(query, (IS_FROM_DATABASE, RDF_TYPE), db_conn=db)
        if results:
            total_genes = sum(row['gene_count'] for row in results)
            chart_data['source_db'] = {
                'segments': [
                    {
                        'name': get_label(row['database_name'], db_conn=db), # Get label for display
                        'count': row['gene_count'],
                        'percentage': (row['gene_count'] / total_genes * 100) if total_genes else 0,
                        'color': next(color_cycler)
                    } for row in results
                ],
                'total_count': total_genes
            }
    except Exception as e:
        app.logger.error(f"Error fetching source database chart data: {e}")

    # 2. Predicted Phenotype Data (Stacked Bar Chart - based on PanGene phenotypes)
    try:
        # Count PanGenes per phenotype
        query = f"""
            SELECT T1.object AS phenotype_name, COUNT(DISTINCT T1.subject) AS gene_count
            FROM triples T1
            JOIN triples T2 ON T1.subject = T2.subject
            WHERE T1.predicate = ? AND T2.predicate = ? AND T2.object = 'PanGene'
            GROUP BY T1.object
            ORDER BY gene_count DESC;
        """
        results = query_db(query, (HAS_PREDICTED_PHENOTYPE, RDF_TYPE), db_conn=db)
        if results:
            total_genes = sum(row['gene_count'] for row in results)
            chart_data['phenotype'] = {
                'segments': [
                    {
                        'name': get_label(row['phenotype_name'], db_conn=db), # Get label for display
                        'count': row['gene_count'],
                        'percentage': (row['gene_count'] / total_genes * 100) if total_genes else 0,
                        'color': next(color_cycler)
                    } for row in results
                ],
                'total_count': total_genes
            }
    except Exception as e:
        app.logger.error(f"Error fetching phenotype chart data: {e}")


    # 3. Antibiotic Class Data (Pie Chart - based on PanGene classes)
    try:
        # Count PanGenes per class
        query = f"""
            SELECT T1.object AS class_name, COUNT(DISTINCT T1.subject) AS gene_count
            FROM triples T1
            JOIN triples T2 ON T1.subject = T2.subject
            WHERE T1.predicate = ? AND T2.predicate = ? AND T2.object = 'PanGene'
            GROUP BY T1.object
            ORDER BY gene_count DESC;
        """
        results = query_db(query, (HAS_RESISTANCE_CLASS, RDF_TYPE), db_conn=db)
        if results:
            labels = [get_label(row['class_name'], db_conn=db) for row in results] # Get labels
            data_points = [row['gene_count'] for row in results]
            colors = [next(color_cycler) for _ in labels] # Generate colors

            chart_data['antibiotic'] = {
                'labels': labels,
                'data': data_points,
                'colors': colors, # Pass colors to template
                'total_count': sum(data_points)
            }
    except Exception as e:
        app.logger.error(f"Error fetching antibiotic class chart data: {e}")


    return chart_data

def get_items_for_category(category_key):
    """Fetches items belonging to a specific index category (for flat lists)."""
    db = get_db() # Use context-managed connection
    if category_key not in INDEX_CATEGORIES:
        return [], 0

    config = INDEX_CATEGORIES[category_key]
    query_type = config['query_type']
    value = config['value']
    filter_subject_type = config.get('filter_subject_type')
    items = []
    total_count = 0

    if query_type == 'type':
        # List subjects of a specific type
        results = query_db("SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject", (RDF_TYPE, value), db_conn=db)
        if results:
            items = [{'id': row['subject'], 'display_name': get_label(row['subject'], db_conn=db), 'link': url_for('details', item_id=quote(row['subject']))} for row in results]
            total_count = len(items)
    elif query_type == 'predicate_object':
        # List distinct objects for a given predicate
        if filter_subject_type:
            # Apply filter: list distinct objects where the subject is of a specific type
            query = f"""
                 SELECT DISTINCT t1.object
                 FROM triples t1
                 JOIN triples t2 ON t1.subject = t2.subject
                 WHERE t1.predicate = ? AND t2.predicate = ? AND t2.object = ?
                 ORDER BY t1.object
             """
            results = query_db(query, (value, RDF_TYPE, filter_subject_type), db_conn=db)
        else:
            results = query_db("SELECT DISTINCT object FROM triples WHERE predicate = ? ORDER BY object", (value,), db_conn=db)

        if results:
            # Determine if the object itself is likely a resource (has details) or just a literal
            check_cur = None
            try:
                check_cur = db.cursor()
                for row in results:
                    obj_id = row['object']
                    # Check if this object appears as a subject in any triple
                    check_cur.execute("SELECT 1 FROM triples WHERE subject = ? LIMIT 1", (obj_id,))
                    is_resource = check_cur.fetchone() is not None
                    link = url_for('details', item_id=quote(obj_id)) if is_resource else None
                    # Use the object ID itself as the display name for categories like databases/classes
                    items.append({'id': obj_id, 'display_name': get_label(obj_id, db_conn=db), 'link': link})
            finally:
                if check_cur:
                    check_cur.close()
            total_count = len(items)
            # Sort items by display name after fetching all
            items.sort(key=lambda x: x['display_name'])


    # Add other query_type handling if necessary

    return items, total_count

def get_grouped_pangen_data():
    """
    Fetches all PanGenes and groups them by resistance class and phenotype.
    Returns two dictionaries:
    - grouped_by_class: {'class_name': [('gene_id', 'gene_display_name'), ...], ...}
    - grouped_by_phenotype: {'phenotype_name': [('gene_id', 'gene_display_name'), ...], ...}
    And the total count of PanGenes.
    """
    db = get_db() # Use context-managed connection
    grouped_by_class = defaultdict(list)
    grouped_by_phenotype = defaultdict(list)
    all_pangen_ids = set()
    gene_labels = {} # Cache labels {gene_id: label}

    # 1. Get all PanGene IDs and their labels
    pangen_cursor = None
    try:
        pangen_cursor = db.execute("""
            SELECT t1.subject, t2.object AS label
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ?
            WHERE t1.predicate = ? AND t1.object = 'PanGene'
        """, (RDFS_LABEL, RDF_TYPE))
        for row in pangen_cursor:
            gene_id = row['subject']
            all_pangen_ids.add(gene_id)
            gene_labels[gene_id] = row['label'] if row['label'] else gene_id # Fallback to ID if no label
    finally:
        if pangen_cursor:
            pangen_cursor.close()

    total_count = len(all_pangen_ids)

    if not all_pangen_ids:
        return {}, {}, 0

    # Create placeholders for efficient querying
    placeholders = ','.join('?' * len(all_pangen_ids))
    pangen_list = list(all_pangen_ids)

    # 2. Get class associations for all PanGenes
    class_query = f"""
        SELECT subject, object
        FROM triples
        WHERE predicate = ? AND subject IN ({placeholders})
    """
    class_results = query_db(class_query, (HAS_RESISTANCE_CLASS, *pangen_list), db_conn=db)
    pangen_to_class = defaultdict(list)
    if class_results:
        for row in class_results:
            pangen_to_class[row['subject']].append(row['object'])


    # 3. Get phenotype associations for all PanGenes
    phenotype_query = f"""
        SELECT subject, object
        FROM triples
        WHERE predicate = ? AND subject IN ({placeholders})
    """
    phenotype_results = query_db(phenotype_query, (HAS_PREDICTED_PHENOTYPE, *pangen_list), db_conn=db)
    pangen_to_phenotype = defaultdict(list)
    if phenotype_results:
        for row in phenotype_results:
            pangen_to_phenotype[row['subject']].append(row['object'])


    # 4. Populate the grouped dictionaries with (id, display_name) tuples
    for gene_id in all_pangen_ids:
        gene_display_name = gene_labels[gene_id]
        gene_entry = (gene_id, gene_display_name) # Tuple (id, display_name)

        # Group by class
        classes = pangen_to_class.get(gene_id)
        if classes:
            for class_id in classes:
                class_label = get_label(class_id, db_conn=db) # Get class label
                grouped_by_class[class_label].append(gene_entry)
        else:
            grouped_by_class['No Class Assigned'].append(gene_entry)

        # Group by phenotype
        phenotypes = pangen_to_phenotype.get(gene_id)
        if phenotypes:
            for phenotype_id in phenotypes:
                phenotype_label = get_label(phenotype_id, db_conn=db) # Get phenotype label
                grouped_by_phenotype[phenotype_label].append(gene_entry)
        else:
            grouped_by_phenotype['No Phenotype Assigned'].append(gene_entry)

    # Sort the groups by label and genes within groups by display name
    def sort_grouped_data(grouped_dict):
        sorted_dict = {}
        # Sort keys, putting "No..." last
        sorted_keys = sorted(grouped_dict.keys(), key=lambda k: (k.startswith("No "), k))
        for key in sorted_keys:
            # Sort genes by display name (the second element in the tuple)
            sorted_genes = sorted(grouped_dict[key], key=lambda g: g[1])
            sorted_dict[key] = sorted_genes
        return sorted_dict

    return sort_grouped_data(grouped_by_class), sort_grouped_data(grouped_by_phenotype), total_count


def get_related_subjects(predicate, object_value):
    """Fetches subjects related to a given object via a specific predicate."""
    db = get_db() # Use context-managed connection
    items = []
    total_count = 0
    # Find subjects where the given predicate points to the object_value
    query = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject"
    results = query_db(query, (predicate, object_value), db_conn=db)

    if results:
        # Fetch labels for the subjects efficiently
        subject_ids = [row['subject'] for row in results]
        labels = {}
        if subject_ids:
            placeholders = ','.join('?' * len(subject_ids))
            label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
            label_results = query_db(label_query, (RDFS_LABEL, *subject_ids), db_conn=db)
            if label_results:
                labels = {row['subject']: row['object'] for row in label_results}

        items = [{'id': row['subject'],
                  'display_name': labels.get(row['subject'], row['subject']), # Use label or fallback to ID
                  'link': url_for('details', item_id=quote(row['subject']))}
                 for row in results]
        total_count = len(items)
        # Sort by display name
        items.sort(key=lambda x: x['display_name'])

    return items, total_count


# --- Context Processors ---
@app.context_processor
def inject_global_vars():
    """Inject variables into all templates."""
    return {
        'site_name': SITE_NAME,
        'current_year': datetime.datetime.now().year,
        'citation_text': CITATION_TEXT # Make citation available globally
    }

# --- Routes ---
@app.route('/')
def index():
    """Render the homepage with category counts and charts."""
    category_counts = get_category_counts()
    chart_data = get_chart_data()
    return render_template('index.html',
                           index_categories=INDEX_CATEGORIES,
                           category_counts=category_counts,
                           source_db=chart_data.get('source_db'),
                           phenotype=chart_data.get('phenotype'),
                           antibiotic=chart_data.get('antibiotic'),
                           show_error=False)

@app.route('/list/<category_key>')
@app.route('/list/related/<predicate>/<path:object_value>')
def list_items(category_key=None, predicate=None, object_value=None):
    """
    Renders a list of items.
    - If category_key is provided, lists items of that category.
      - Special handling for "PanRes Genes" to show grouped view.
    - If predicate and object_value are provided, lists items (subjects)
      related via that predicate/object pair.
    """
    db = get_db() # Use context-managed connection
    predicate_map = PREDICATE_MAP
    items = []
    grouped_items = None
    total_item_count = 0
    page_title = "Item List"
    item_type = ""
    grouping_predicate_display = None
    grouping_value_display = None
    parent_category_key = None

    if predicate and object_value:
        # --- Listing related items ---
        decoded_object_value = unquote(object_value) # Decode URL-encoded value
        items, total_item_count = get_related_subjects(predicate, decoded_object_value)
        predicate_display = predicate_map.get(predicate, predicate)
        object_label = get_label(decoded_object_value, db_conn=db)

        page_title = f"Items where {predicate_display} is {object_label}"
        item_type = "Related Item" # Generic type for this view
        grouping_predicate_display = predicate_display
        grouping_value_display = object_label

        # Try to find the category this object belongs to for the back link
        details_for_object = get_item_details(decoded_object_value) # This uses its own db connection
        if details_for_object and details_for_object.get('primary_type_category_key'):
            parent_category_key = details_for_object['primary_type_category_key']

    elif category_key and category_key in INDEX_CATEGORIES:
        # --- Listing items by category ---
        category_info = INDEX_CATEGORIES[category_key]
        page_title = category_key
        item_type = category_info.get('value', category_key) # Use the underlying type if available

        if category_key == "PanRes Genes":
            # Special grouped view for PanGenes by Class and Phenotype
            # Let's decide which grouping to show by default, e.g., by Class
            grouped_by_class, _, total_item_count = get_grouped_pangen_data()
            grouped_items = grouped_by_class # Assign the desired grouping
            grouping_predicate_display = predicate_map.get(HAS_RESISTANCE_CLASS)
            item_type = "PanGene"
            page_title = "PanRes Genes grouped by Antibiotic Class" # More specific title
        else:
            # Standard category list (flat)
            items, total_item_count = get_items_for_category(category_key)
            # Add display names to items if not already done
            for item in items:
                if 'display_name' not in item:
                    item['display_name'] = get_label(item['id'], db_conn=db)
            # Sort flat list by display name
            items.sort(key=lambda x: x['display_name'])


    else:
        abort(404, description=f"Category or relationship '{category_key or object_value}' not recognized.")

    return render_template('list.html',
                           page_title=page_title,
                           item_type=item_type,
                           items=items,
                           grouped_items=grouped_items,
                           total_items=total_item_count,
                           grouping_predicate_display=grouping_predicate_display,
                           grouping_value_display=grouping_value_display,
                           parent_category_key=parent_category_key,
                           )


@app.route('/details/<path:item_id>')
def details(item_id):
    """Shows details (properties and references) for a specific item."""
    decoded_item_id = unquote(item_id)
    app.logger.info(f"Details route: Fetching details for item ID: {decoded_item_id}")

    item_details = get_item_details(decoded_item_id)

    if not item_details:
        app.logger.warning(f"No data found for item_id: {decoded_item_id}. Returning 404.")
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")

    # No need to pass predicate map, it's processed in get_item_details
    return render_template(
        'details.html',
        item_id=decoded_item_id,
        details=item_details,
    )


# --- Error Handlers ---
@app.errorhandler(404)
def handle_not_found(e):
    """Handle 404 Not Found errors by showing info on the index page."""
    path = request.path if request else "Unknown path"
    app.logger.warning(f"404 Not Found: {path} - {e.description}")
    error_message = e.description or f"The requested page '{path}' could not be found."
    # Render index page with error message
    return render_template('index.html',
                           show_error=True,
                           error_code=404,
                           error_message=error_message,
                           # Pass empty/default data for charts/categories to avoid template errors
                           index_categories=INDEX_CATEGORIES, # Still need this for layout
                           category_counts={},
                           # Pass chart variables expected by the template's JS, set to None
                           antibiotic=None,
                           source_db=None,
                           phenotype=None), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 Internal Server errors by showing info on the index page."""
    app.logger.error(f"500 Internal Server Error: {e}", exc_info=True) # Log exception info
    error_message = getattr(e, 'original_exception', None) or getattr(e, 'description', "An internal server error occurred. Please try again later.")
    # Render index page with error message
    return render_template('index.html',
                           show_error=True,
                           error_code=500,
                           error_message=str(error_message),
                           # Pass empty/default data
                           index_categories=INDEX_CATEGORIES, # Still need this for layout
                           category_counts={},
                           # Pass chart variables expected by the template's JS, set to None
                           antibiotic=None,
                           source_db=None,
                           phenotype=None), 500

# --- Utility Route (Example - can be removed) ---
@app.route('/testdb')
def test_db_connection():
    """A simple route to test database connection and fetch a few triples."""
    app.logger.info("Accessing /testdb route")
    try:
        results = query_db("SELECT * FROM triples LIMIT 5")
        if results is None:
             return "Error querying database.", 500
        output = "<h2>First 5 Triples:</h2><ul>"
        for row in results:
            output += f"<li>{row['subject']} - {row['predicate']} - {row['object']}</li>"
        output += "</ul>"
        return output
    except Exception as e:
        app.logger.error(f"Error in /testdb: {e}")
        return f"An error occurred: {e}", 500

# --- Autocomplete Route ---
@app.route('/autocomplete')
def autocomplete():
    """Endpoint for search suggestions."""
    search_term = request.args.get('q', '').strip()
    app.logger.debug(f"Autocomplete request for: '{search_term}'")
    suggestions = get_autocomplete_suggestions(search_term)
    return jsonify(suggestions)

# --- Autocomplete Function ---
def get_autocomplete_suggestions(term, limit=15):
    """
    Fetches suggestions for autocomplete based on labels and IDs.
    Includes an indicator for the type of item (Gene, Class, Phenotype, Database, Other).
    """
    if not term or len(term) < 2:
        return []

    db = get_db() # Get connection once for this function
    term_like = f'%{term}%'
    # Query fetches potential matches based on ID/Label, including rdf:type
    # Relevance scoring helps prioritize matches
    query = """
        SELECT subject, label, type, relevance
        FROM (
            -- Exact ID match (highest relevance)
            SELECT
                t1.subject,
                COALESCE(t2.object, t1.subject) AS label,
                t3.object AS type,
                1 AS relevance
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ? -- rdfs:label
            LEFT JOIN triples t3 ON t1.subject = t3.subject AND t3.predicate = ? -- rdf:type
            WHERE t1.subject = ?

            UNION

            -- Label starts with term (high relevance)
            SELECT
                t1.subject,
                t1.object AS label,
                t2.object AS type,
                2 AS relevance
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ? -- rdf:type
            WHERE t1.predicate = ? AND t1.object LIKE ? -- rdfs:label, term%

            UNION

            -- Label contains term (medium relevance)
            SELECT
                t1.subject,
                t1.object AS label,
                t2.object AS type,
                3 AS relevance
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ? -- rdf:type
            WHERE t1.predicate = ? AND t1.object LIKE ? AND t1.object NOT LIKE ? -- rdfs:label, %term%, NOT term%

            UNION

            -- ID contains term (lower relevance, exclude exact match)
            SELECT
                t1.subject,
                COALESCE(t2.object, t1.subject) AS label,
                t3.object AS type,
                4 AS relevance
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ? -- rdfs:label
            LEFT JOIN triples t3 ON t1.subject = t3.subject AND t3.predicate = ? -- rdf:type
            WHERE t1.subject LIKE ? AND t1.subject != ?
        )
        -- No specific type filter here, rely on post-processing for indicators
        ORDER BY relevance, label -- Order by relevance, then alphabetically
        LIMIT ?
    """
    try:
        # Parameters for the query
        params = (
            RDFS_LABEL, RDF_TYPE, term,                                     # Exact ID match
            RDF_TYPE, RDFS_LABEL, f'{term}%',                               # Label starts with
            RDF_TYPE, RDFS_LABEL, term_like, f'{term}%',                    # Label contains
            RDFS_LABEL, RDF_TYPE, term_like, term,                          # ID contains
            limit * 2 # Fetch slightly more initially to account for duplicates before unique check
        )
        results = query_db(query, params, db_conn=db) # Use the single connection

        if not results:
            return []

        # Post-process to add indicators and ensure uniqueness, limit final results
        final_suggestions = []
        seen_subjects = set()
        subject_ids_to_check = list({row['subject'] for row in results}) # Unique subjects from initial query

        # Pre-fetch usage data for efficiency using the single connection
        class_subjects = set()
        pheno_subjects = set()
        db_subjects = set()

        if subject_ids_to_check:
            placeholders = ','.join('?' * len(subject_ids_to_check))

            class_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
            class_res = query_db(class_q, (HAS_RESISTANCE_CLASS, *subject_ids_to_check), db_conn=db)
            if class_res: class_subjects = {row['object'] for row in class_res}

            pheno_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
            pheno_res = query_db(pheno_q, (HAS_PREDICTED_PHENOTYPE, *subject_ids_to_check), db_conn=db)
            if pheno_res: pheno_subjects = {row['object'] for row in pheno_res}

            db_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
            db_res = query_db(db_q, (IS_FROM_DATABASE, *subject_ids_to_check), db_conn=db)
            if db_res: db_subjects = {row['object'] for row in db_res}


        for row in results:
            subject_id = row['subject']
            # Apply final limit and uniqueness check
            if subject_id in seen_subjects or len(final_suggestions) >= limit:
                continue

            rdf_type = row['type']
            indicator = "Other" # Default

            # Determine indicator based on type and usage
            if rdf_type == 'PanGene':
                indicator = "Gene"
            elif subject_id in class_subjects:
                 indicator = "Class"
            elif subject_id in pheno_subjects:
                 indicator = "Phenotype"
            elif subject_id in db_subjects:
                 indicator = "Database"
            elif rdf_type == 'http://www.w3.org/2002/07/owl#Class':
                 indicator = "Ontology Class"
            elif rdf_type == 'http://www.w3.org/2000/01/rdf-schema#Resource':
                 indicator = "Resource"
            # Add more specific checks if needed (e.g., OriginalGene -> "Source Gene")

            final_suggestions.append({
                'id': subject_id,
                'display_name': row['label'],
                'link': url_for('details', item_id=quote(subject_id)),
                'type_indicator': indicator # Add the determined indicator
            })
            seen_subjects.add(subject_id)

        return final_suggestions

    except sqlite3.Error as e:
        app.logger.error(f"Autocomplete query error for '{term}': {e}")
        return []
    except Exception as e:
         app.logger.error(f"Unexpected error during autocomplete for '{term}': {e}")
         return []


# --- Run the App ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001)) # Use 5001 to avoid potential conflict if 5000 is busy
    # Set debug=True for development to see errors and auto-reload
    # Set debug=False for production/deployment
    app.run(host='0.0.0.0', port=port, debug=True) 