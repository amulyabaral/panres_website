import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app, jsonify
import os
import logging
from urllib.parse import unquote, quote
from collections import defaultdict
import datetime

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

def query_db(query, args=(), one=False):
    """Helper function to query the database."""
    db = get_db()
    try:
        cur = db.execute(query, args)
        rv = cur.fetchall()
        cur.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        app.logger.error(f"Database query error: {e}\nQuery: {query}\nArgs: {args}")
        # Depending on the context, you might want to return None, an empty list, or re-raise
        return None # Or return []

# --- Data Fetching Logic ---

def get_predicate_map():
    """Returns the predefined predicate map."""
    return PREDICATE_MAP

def get_label(item_id):
    """Fetches the rdfs:label for a given item ID."""
    result = query_db("SELECT object FROM triples WHERE subject = ? AND predicate = ?", (item_id, RDFS_LABEL), one=True)
    return result['object'] if result else item_id # Fallback to ID if no label

def get_item_details(item_id):
    """Fetches all properties and referencing items for a given item ID."""
    db = get_db()
    details = {
        'id': item_id,
        'label': get_label(item_id),
        'properties': defaultdict(list),
        'referencing_items': [],
        'primary_type': None,
        'primary_type_display': None,
        'primary_type_category_key': None,
        'is_pangen': False,
        'description': None
    }

    # 1. Fetch outgoing properties (subject -> predicate -> object)
    properties_cursor = db.execute("SELECT predicate, object FROM triples WHERE subject = ?", (item_id,))
    for row in properties_cursor:
        predicate, obj = row['predicate'], row['object']
        details['properties'][predicate].append(obj)
        # Try to determine primary type and description
        if predicate == RDF_TYPE:
            details['primary_type'] = obj # Store the raw type
            if obj == 'PanGene':
                details['is_pangen'] = True
        elif predicate in DESCRIPTION_PREDICATES and not details['description']:
             details['description'] = obj # Take the first description found

    properties_cursor.close()

    # 2. Fetch incoming references (subject -> predicate -> item_id)
    references_cursor = db.execute("SELECT subject, predicate FROM triples WHERE object = ?", (item_id,))
    for row in references_cursor:
        details['referencing_items'].append({
            'ref_id': row['subject'],
            'predicate': row['predicate'],
            'ref_label': get_label(row['subject']) # Get label for referencing item
        })
    references_cursor.close()

    # 3. Determine Display Type and Category Key
    if details['primary_type']:
        details['primary_type_display'] = details['primary_type'] # Default display
        for cat_key, cat_info in INDEX_CATEGORIES.items():
            if cat_info['query_type'] == 'type' and cat_info['value'] == details['primary_type']:
                details['primary_type_display'] = cat_key # Use category name if it's a primary type category
                details['primary_type_category_key'] = cat_key
                break
            # Add checks for other types if needed (e.g., if object is an Antibiotic Class)
            elif cat_info['query_type'] == 'predicate_object' and cat_info['value'] == HAS_RESISTANCE_CLASS:
                 # Check if the item_id is one of the objects of has_resistance_class
                 # This requires checking if the item_id exists as an object for that predicate
                 is_class = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (HAS_RESISTANCE_CLASS, item_id), one=True)
                 if is_class:
                     details['primary_type_display'] = "Antibiotic Class" # Or use cat_key
                     details['primary_type_category_key'] = "Antibiotic Classes"
                     break # Stop checking once a match is found
            elif cat_info['query_type'] == 'predicate_object' and cat_info['value'] == HAS_PREDICTED_PHENOTYPE:
                 is_phenotype = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (HAS_PREDICTED_PHENOTYPE, item_id), one=True)
                 if is_phenotype:
                     details['primary_type_display'] = "Predicted Phenotype"
                     details['primary_type_category_key'] = "Predicted Phenotypes"
                     break
            elif cat_info['query_type'] == 'predicate_object' and cat_info['value'] == IS_FROM_DATABASE:
                 is_database = query_db("SELECT 1 FROM triples WHERE predicate = ? AND object = ? LIMIT 1", (IS_FROM_DATABASE, item_id), one=True)
                 if is_database:
                     details['primary_type_display'] = "Source Database"
                     details['primary_type_category_key'] = "Source Databases"
                     break


    # Sort properties for consistent display (optional)
    details['properties'] = dict(sorted(details['properties'].items()))
    details['referencing_items'] = sorted(details['referencing_items'], key=lambda x: (x['predicate'], x['ref_id']))

    # If no properties or references found, the item might still exist if it's only an object
    if not details['properties'] and not details['referencing_items']:
         # Check if the ID exists as a subject or object at all
         exists = query_db("SELECT 1 FROM triples WHERE subject = ? OR object = ? LIMIT 1", (item_id, item_id), one=True)
         if not exists:
             app.logger.warning(f"Item ID {item_id} not found as subject or object.")
             return None # Indicate item not found

    return details


def get_category_counts():
    """Calculates counts for categories defined in INDEX_CATEGORIES."""
    counts = {}
    for key, config in INDEX_CATEGORIES.items():
        query_type = config['query_type']
        value = config['value']
        filter_subject_type = config.get('filter_subject_type') # Get optional filter

        count = 0
        if query_type == 'type':
            # Count subjects of a specific type
            result = query_db("SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ? AND object = ?", (RDF_TYPE, value), one=True)
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
                 result = query_db(query, (value, RDF_TYPE, filter_subject_type), one=True)
            else:
                 # No filter, just count distinct objects for the predicate
                 result = query_db("SELECT COUNT(DISTINCT object) as count FROM triples WHERE predicate = ?", (value,), one=True)
            count = result['count'] if result else 0
        elif query_type == 'predicate_subject':
             # Count distinct subjects for a given predicate (less common)
             # Note: This might not be what's intended if 'value' is meant to be the *object*
             # Re-evaluate if this query_type is actually needed/correctly defined
             result = query_db("SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ?", (value,), one=True)
             count = result['count'] if result else 0

        counts[key] = count
        app.logger.debug(f"Category '{key}': Count = {count}")
    return counts

def get_chart_data():
    """Fetches data specifically formatted for the homepage charts."""
    chart_data = {
        'source_db_chart_data': None,
        'phenotype_chart_data': None,
        'antibiotic_chart_data': None,
    }
    db = get_db()

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
        results = query_db(query, (IS_FROM_DATABASE, RDF_TYPE))
        if results:
            total_genes = sum(row['gene_count'] for row in results)
            chart_data['source_db_chart_data'] = {
                'segments': [
                    {
                        'name': row['database_name'],
                        'count': row['gene_count'],
                        'percentage': (row['gene_count'] / total_genes * 100) if total_genes else 0,
                        'color': get_color_for_item(row['database_name'], index) # Simple color assignment
                    } for index, row in enumerate(results)
                ],
                'total_count': total_genes
            }
    except Exception as e:
        app.logger.error(f"Error fetching source database chart data: {e}")

    # 2. Predicted Phenotype Data (Bar Chart - based on PanGene phenotypes)
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
        results = query_db(query, (HAS_PREDICTED_PHENOTYPE, RDF_TYPE))
        if results:
            total_genes = sum(row['gene_count'] for row in results)
            chart_data['phenotype_chart_data'] = {
                'segments': [
                    {
                        'name': row['phenotype_name'],
                        'count': row['gene_count'],
                        'percentage': (row['gene_count'] / total_genes * 100) if total_genes else 0,
                        'color': get_color_for_item(row['phenotype_name'], index) # Simple color assignment
                    } for index, row in enumerate(results)
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
        results = query_db(query, (HAS_RESISTANCE_CLASS, RDF_TYPE))
        if results:
            labels = [row['class_name'] for row in results]
            data_points = [row['gene_count'] for row in results]
            colors = [get_color_for_item(label, i) for i, label in enumerate(labels)] # Generate colors

            chart_data['antibiotic_chart_data'] = {
                'labels': labels,
                'data': data_points,
                'colors': colors, # Pass colors to template
                'total_count': sum(data_points)
            }
    except Exception as e:
        app.logger.error(f"Error fetching antibiotic class chart data: {e}")


    return chart_data

# Simple color generation helper (replace with a better palette if needed)
def get_color_for_item(item_name, index):
    """Generates a deterministic-ish color based on index."""
    # Simple HSL-based color generation, varying hue
    hue = (index * 137.5) % 360 # Use golden angle approximation for distribution
    saturation = 70
    lightness = 50
    return f'hsl({hue}, {saturation}%, {lightness}%)'


def get_items_for_category(category_key):
    """Fetches items belonging to a specific index category (for flat lists)."""
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
        results = query_db("SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject", (RDF_TYPE, value))
        if results:
            items = [{'id': row['subject'], 'link': url_for('details', item_id=quote(row['subject']))} for row in results]
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
            results = query_db(query, (value, RDF_TYPE, filter_subject_type))
        else:
            results = query_db("SELECT DISTINCT object FROM triples WHERE predicate = ? ORDER BY object", (value,))

        if results:
            # Determine if the object itself is likely a resource (has details) or just a literal
            # Simple heuristic: check if it exists as a subject somewhere
            db = get_db()
            check_cur = db.cursor()
            for row in results:
                obj_id = row['object']
                # Check if this object appears as a subject in any triple
                check_cur.execute("SELECT 1 FROM triples WHERE subject = ? LIMIT 1", (obj_id,))
                is_resource = check_cur.fetchone() is not None
                link = url_for('details', item_id=quote(obj_id)) if is_resource else None
                items.append({'id': obj_id, 'link': link})
            check_cur.close()
            total_count = len(items)

    # Add other query_type handling if necessary

    return items, total_count

def get_grouped_pangen_data():
    """Fetches all PanGenes and groups them by resistance class and phenotype."""
    db = get_db()
    grouped_by_class = defaultdict(lambda: {'genes': [], 'id': None})
    grouped_by_phenotype = defaultdict(lambda: {'genes': [], 'id': None})
    all_pangen_ids = set()

    # 1. Get all PanGene IDs
    pangen_cursor = db.execute("SELECT subject FROM triples WHERE predicate = ? AND object = 'PanGene'", (RDF_TYPE,))
    for row in pangen_cursor:
        all_pangen_ids.add(row['subject'])
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
    class_cursor = db.execute(class_query, (HAS_RESISTANCE_CLASS, *pangen_list))
    pangen_to_class = defaultdict(list)
    all_classes = set()
    for row in class_cursor:
        pangen_to_class[row['subject']].append(row['object'])
        all_classes.add(row['object'])
    class_cursor.close()

    # 3. Get phenotype associations for all PanGenes
    phenotype_query = f"""
        SELECT subject, object
        FROM triples
        WHERE predicate = ? AND subject IN ({placeholders})
    """
    phenotype_cursor = db.execute(phenotype_query, (HAS_PREDICTED_PHENOTYPE, *pangen_list))
    pangen_to_phenotype = defaultdict(list)
    all_phenotypes = set()
    for row in phenotype_cursor:
        pangen_to_phenotype[row['subject']].append(row['object'])
        all_phenotypes.add(row['object'])
    phenotype_cursor.close()

    # 4. Populate the grouped dictionaries
    gene_link_cache = {} # Cache gene links
    def get_gene_entry(gene_id):
        if gene_id not in gene_link_cache:
            gene_link_cache[gene_id] = {'id': gene_id, 'link': url_for('details', item_id=quote(gene_id))}
        return gene_link_cache[gene_id]

    # Check which classes/phenotypes are resources (have detail pages)
    resource_ids = set()
    if all_classes or all_phenotypes:
        all_potential_resources = list(all_classes.union(all_phenotypes))
        res_placeholders = ','.join('?' * len(all_potential_resources))
        res_query = f"SELECT DISTINCT subject FROM triples WHERE subject IN ({res_placeholders})"
        res_cursor = db.execute(res_query, all_potential_resources)
        for row in res_cursor:
            resource_ids.add(row['subject'])
        res_cursor.close()

    # Group by class
    for gene_id in all_pangen_ids:
        classes = pangen_to_class.get(gene_id)
        gene_entry = get_gene_entry(gene_id)
        if classes:
            for class_label in classes:
                grouped_by_class[class_label]['genes'].append(gene_entry)
                if class_label in resource_ids and not grouped_by_class[class_label]['id']:
                     grouped_by_class[class_label]['id'] = quote(class_label) # Store encoded ID for URL
        else:
            grouped_by_class['No Class Assigned']['genes'].append(gene_entry)

    # Group by phenotype
    for gene_id in all_pangen_ids:
        phenotypes = pangen_to_phenotype.get(gene_id)
        gene_entry = get_gene_entry(gene_id)
        if phenotypes:
            for phenotype_label in phenotypes:
                grouped_by_phenotype[phenotype_label]['genes'].append(gene_entry)
                if phenotype_label in resource_ids and not grouped_by_phenotype[phenotype_label]['id']:
                     grouped_by_phenotype[phenotype_label]['id'] = quote(phenotype_label) # Store encoded ID for URL
        else:
            grouped_by_phenotype['No Phenotype Assigned']['genes'].append(gene_entry)

    # Sort the groups by label and genes within groups by ID
    def sort_grouped_data(grouped_dict):
        sorted_dict = {}
        # Sort keys (labels) alphabetically, handling 'No ... Assigned' specifically if needed
        sorted_keys = sorted(grouped_dict.keys(), key=lambda k: (k.startswith("No "), k))
        for key in sorted_keys:
            data = grouped_dict[key]
            data['genes'] = sorted(data['genes'], key=lambda g: g['id'])
            sorted_dict[key] = data
        return sorted_dict

    return sort_grouped_data(grouped_by_class), sort_grouped_data(grouped_by_phenotype), total_count


def get_related_subjects(predicate, object_value):
    """Fetches subjects related to a given object via a specific predicate."""
    items = []
    total_count = 0
    # Find subjects where the given predicate points to the object_value
    query = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject"
    results = query_db(query, (predicate, object_value))

    if results:
        items = [{'id': row['subject'], 'link': url_for('details', item_id=quote(row['subject']))} for row in results]
        total_count = len(items)

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
                           category_counts=category_counts,
                           index_categories=INDEX_CATEGORIES, # Pass category config for descriptions etc.
                           source_db_chart_data=chart_data.get('source_db_chart_data'),
                           phenotype_chart_data=chart_data.get('phenotype_chart_data'),
                           antibiotic_chart_data=chart_data.get('antibiotic_chart_data'),
                           show_error=False) # Add show_error flag

@app.route('/list/<category_key>')
@app.route('/list/related/<predicate>/<path:object_value>') # Changed route slightly for clarity
def list_items(category_key=None, predicate=None, object_value=None):
    """
    Renders a list of items.
    - If category_key is provided, lists items of that category.
      - Special handling for "PanRes Genes" to show grouped view.
    - If predicate and object_value are provided, lists items (subjects)
      related via that predicate/object pair.
    """
    predicate_map = get_predicate_map()
    items = []
    grouped_by_class = None
    grouped_by_phenotype = None
    total_item_count = 0
    is_grouped_view = False
    page_title = "Item List" # Default
    description = ""
    # Keep track of original request type for back links etc.
    original_category_key = category_key
    original_predicate = predicate
    original_object_value = object_value

    if predicate and object_value:
        # --- Listing related items ---
        decoded_object_value = unquote(object_value)
        items, total_item_count = get_related_subjects(predicate, decoded_object_value)
        predicate_display = predicate_map.get(predicate, predicate)
        # Try to get label for object_value if it's a resource
        object_label = get_label(decoded_object_value)

        page_title = f"Items with {predicate_display}: {object_label}"
        description = f"Listing items (subjects) where the property <code>{predicate_display}</code> is <code>{object_label}</code>. Found {total_item_count} item(s)."
        is_grouped_view = False
        # category_key might be passed in URL but isn't the primary driver here
        category_key = f"related_{predicate}" # Use a synthetic key for context

    elif category_key and category_key in INDEX_CATEGORIES:
        # --- Listing items by category ---
        category_info = INDEX_CATEGORIES[category_key]
        category_display_name = category_key
        page_title = category_display_name

        if category_key == "PanRes Genes":
            # Special grouped view for PanGenes
            grouped_by_class, grouped_by_phenotype, total_item_count = get_grouped_pangen_data()
            is_grouped_view = True
            description = f"Listing items of type <code>PanGene</code>, grouped by Resistance Class and Predicted Phenotype. Found {total_item_count} item(s)."
        else:
            # Standard category list (flat)
            items, total_item_count = get_items_for_category(category_key)
            is_grouped_view = False
            cat_desc = category_info.get('description', '')
            description = f"Listing items for category: {category_key}. Found {total_item_count} item(s). {cat_desc}"

    else:
        abort(404, description=f"Category or relationship '{category_key or object_value}' not recognized.")

    return render_template('list.html',
                           page_title=page_title,
                           description=description,
                           items=items, # For flat lists
                           grouped_by_class=grouped_by_class, # For grouped PanGene view
                           grouped_by_phenotype=grouped_by_phenotype, # For grouped PanGene view
                           total_item_count=total_item_count,
                           is_grouped_view=is_grouped_view,
                           category_key=original_category_key, # Original category for context
                           predicate=original_predicate, # Original predicate for context
                           object_value=original_object_value, # Original object value for context
                           # Pass other necessary vars directly as context processor handles them
                           )


@app.route('/details/<path:item_id>')
def details(item_id):
    """Shows details (properties and references) for a specific item."""
    # Ensure item_id is correctly quoted/unquoted if necessary, but Flask handles path parameters well.
    # However, IDs coming *from* the DB might need quoting for URL generation.
    # IDs coming *to* this route from URLs are automatically unquoted by Flask.
    decoded_item_id = unquote(item_id) # Usually not needed for path: converter, but safe.
    app.logger.info(f"Details route: Fetching details for item ID: {decoded_item_id}")

    item_details = get_item_details(decoded_item_id)

    if not item_details:
        app.logger.warning(f"No data found for item_id: {decoded_item_id}. Returning 404.")
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")

    # Define predicates for the PanGene specific layout (needed for template logic)
    pangen_key_info_preds = ['has_length', 'same_as', 'card_link', 'accession', IS_FROM_DATABASE]
    pangen_right_col_preds = [HAS_RESISTANCE_CLASS, HAS_PREDICTED_PHENOTYPE, 'translates_to', 'member_of']

    return render_template(
        'details.html',
        item_id=decoded_item_id, # Display the decoded ID
        encoded_item_id=quote(decoded_item_id), # Use encoded ID for potential future URL generation within the page
        details=item_details,
        is_pangen=item_details['is_pangen'],
        pangen_key_info_preds=pangen_key_info_preds,
        pangen_right_col_preds=pangen_right_col_preds
        # predicate_map, site_name, etc., are available via context processor
    )


# --- Error Handlers ---
@app.errorhandler(404)
def handle_not_found(e):
    """Handle 404 Not Found errors by showing info on the index page."""
    app.logger.warning(f"404 Not Found: {request.path} - {e.description}")
    error_message = e.description or f"The requested page '{request.path}' could not be found."
    # Render index page with error message
    return render_template('index.html',
                           show_error=True,
                           error_code=404,
                           error_message=error_message,
                           # Pass empty data for charts/categories to avoid errors
                           categories={},
                           antibiotic_class_data={'labels': [], 'data': []},
                           source_db_data={'labels': [], 'data': []}), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 Internal Server errors by showing info on the index page."""
    app.logger.error(f"500 Internal Server Error: {e}", exc_info=True) # Log exception info
    # Use original exception description if available, otherwise generic message
    error_message = getattr(e, 'original_exception', None) or getattr(e, 'description', "An internal server error occurred. Please try again later.")
    # Render index page with error message
    return render_template('index.html',
                           show_error=True,
                           error_code=500,
                           error_message=str(error_message), # Ensure it's a string
                           # Pass empty data for charts/categories
                           categories={},
                           antibiotic_class_data={'labels': [], 'data': []},
                           source_db_data={'labels': [], 'data': []}), 500

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


# --- Run the App ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001)) # Use 5001 to avoid potential conflict if 5000 is busy
    # Set debug=True for development to see errors and auto-reload
    # Set debug=False for production/deployment
    app.run(host='0.0.0.0', port=port, debug=True) 