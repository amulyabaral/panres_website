import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app, jsonify, request
import os
from urllib.parse import unquote, quote
from collections import defaultdict
import datetime
import json
import math # Add math import for ceil
import time # Add time for benchmarking population
import re # Import regex for sorting
import logging # Add logging import for logging

DATABASE = 'panres_ontology.db'
CITATION_TEXT = "Hannah-Marie Martiny, Nikiforos Pyrounakis, Thomas N Petersen, Oksana Lukjančenko, Frank M Aarestrup, Philip T L C Clausen, Patrick Munk, ARGprofiler—a pipeline for large-scale analysis of antimicrobial resistance genes and their flanking regions in metagenomic datasets, <i>Bioinformatics</i>, Volume 40, Issue 3, March 2024, btae086, <a href=\"https://doi.org/10.1093/bioinformatics/btae086\" target=\"_blank\" rel=\"noopener noreferrer\" class=\"text-dtu-red hover:underline\">https://doi.org/10.1093/bioinformatics/btae086</a>"
SITE_NAME = "PanRes 2.0 Database"

INDEX_CATEGORIES = {
    "PanRes Genes": {'query_type': 'type', 'value': 'PanGene', 'description': 'Unique gene sequences curated in PanRes.'},
    "Source Databases": {'query_type': 'predicate_object', 'value': 'is_from_database', 'description': 'Databases contributing genes to PanRes.', 'filter_subject_type': 'OriginalGene'},
    "Antibiotic Classes": {'query_type': 'predicate_object', 'value': 'has_resistance_class', 'description': 'Classes of antibiotics genes confer resistance to.'},
    "Predicted Phenotypes": {'query_type': 'predicate_object', 'value': 'has_predicted_phenotype', 'description': 'Specific antibiotic resistances predicted for genes.'},
}

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

# Define OWL namespace constant
OWL_NAMED_INDIVIDUAL = 'owl:NamedIndividual'

# Configure basic logging (adjust level and format as needed)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

def create_and_populate_fts(db_path):
    """
    Creates/Recreates an FTS5 table indexing terms associated with ALL distinct subjects.
    Indexes the subject's ID, label, and all associated predicates and objects (values or labels).
    """
    db = None
    start_time = time.time()
    print("Starting FTS table creation and population...")
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        # Use WAL mode for potentially better write performance during population
        db.execute("PRAGMA journal_mode=WAL;")
        cur = db.cursor()

        print(" -> Dropping existing FTS table (if any)...")
        cur.execute("DROP TABLE IF EXISTS item_search_fts;")

        print(" -> Creating new FTS table 'item_search_fts'...")
        # Schema: item_id (any subject), search_term (indexed content)
        cur.execute("""
            CREATE VIRTUAL TABLE item_search_fts USING fts5(
                item_id UNINDEXED,
                search_term,
                tokenize = 'unicode61 remove_diacritics 0'
            );
        """)
        db.commit()

        print(f" -> Finding ALL distinct subjects...")
        cur.execute(f"SELECT DISTINCT subject FROM triples")
        all_subject_rows = cur.fetchall()
        all_subject_ids = {row['subject'] for row in all_subject_rows if row['subject']}
        print(f"    Found {len(all_subject_ids)} distinct subjects.")

        if not all_subject_ids:
            print(" -> No subjects found. FTS table will be empty.")
            return

        print(" -> Preparing data for FTS insertion...")
        fts_data = []
        processed_count = 0
        batch_size = 10000 # Process subjects in batches
        subject_list = list(all_subject_ids)

        # Pre-fetch all labels to avoid repeated queries inside the loop
        print("    Pre-fetching labels...")
        all_ids_to_label = set(subject_list)
        # Also need labels for potential object URIs, fetch all potential links
        cur.execute("SELECT DISTINCT object FROM triples WHERE object_is_literal = 0")
        potential_object_uris_rows = cur.fetchall()
        potential_object_uris = {row['object'] for row in potential_object_uris_rows if row['object']}
        all_ids_to_label.update(potential_object_uris)

        labels = {}
        if all_ids_to_label:
            label_list = list(all_ids_to_label)
            label_batch_size = 900 # SQLite variable limit is often 999
            for i in range(0, len(label_list), label_batch_size):
                 batch_label_ids = label_list[i:i+label_batch_size]
                 placeholders = ','.join('?' * len(batch_label_ids))
                 label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
                 cur.execute(label_query, (RDFS_LABEL, *batch_label_ids))
                 label_results = cur.fetchall()
                 for row in label_results:
                     labels[row['subject']] = row['object']
            print(f"    Fetched {len(labels)} labels.")
        else:
            print("    No IDs found needing labels.")


        print(" -> Processing subjects and their triples for FTS...")
        for i in range(0, len(subject_list), batch_size):
            batch_ids = subject_list[i:i+batch_size]
            placeholders = ','.join('?' * len(batch_ids))

            # Fetch all triples for the current batch of subjects
            batch_triples_query = f"""
                SELECT subject, predicate, object, object_is_literal
                FROM triples
                WHERE subject IN ({placeholders})
            """
            cur.execute(batch_triples_query, batch_ids)
            batch_triples = cur.fetchall()

            # Organize triples by subject for easier processing
            triples_by_subject = defaultdict(list)
            for row in batch_triples:
                triples_by_subject[row['subject']].append(row)

            # Process each subject in the batch
            for subject_id in batch_ids:
                processed_count += 1
                subject_label = labels.get(subject_id)

                # Add the subject's ID and label to its searchable terms
                terms_for_subject = {subject_id}
                if subject_label:
                    terms_for_subject.add(subject_label)

                # Add terms from its triples
                for triple in triples_by_subject[subject_id]:
                    predicate = triple['predicate']
                    obj = triple['object']
                    is_literal = triple['object_is_literal']

                    terms_for_subject.add(predicate) # Add the predicate itself

                    if is_literal:
                        terms_for_subject.add(obj) # Add literal value
                    else:
                        # For URI objects, add the URI and its label (if found)
                        terms_for_subject.add(obj)
                        object_label = labels.get(obj)
                        if object_label:
                            terms_for_subject.add(object_label)

                # Add entries to fts_data: (item_id, term)
                for term in terms_for_subject:
                    if term: # Ensure term is not empty/None
                        fts_data.append((subject_id, str(term)))

                if processed_count % 5000 == 0: # Log less frequently for potentially larger runs
                     print(f"    Processed {processed_count}/{len(subject_list)} subjects...")

        print(f" -> Inserting {len(fts_data)} entries into FTS table...")
        # Use executemany for bulk insertion
        cur.executemany("INSERT INTO item_search_fts (item_id, search_term) VALUES (?, ?)", fts_data)
        db.commit()
        print(" -> FTS insertion complete.")

        # Optional: Optimize the FTS index
        print(" -> Optimizing FTS index...")
        cur.execute("INSERT INTO item_search_fts(item_search_fts) VALUES('optimize');")
        db.commit()
        print(" -> FTS index optimized.")

    except sqlite3.Error as e:
        print(f"!!! Database error during FTS population: {e}")
        if db: db.rollback()
        raise
    except Exception as e:
        print(f"!!! General error during FTS population: {e}")
        raise
    finally:
        if db:
            # Optional: Vacuum analyze might help performance after large changes
            # print(" -> Running VACUUM ANALYZE...")
            # db.execute("VACUUM;")
            # db.execute("ANALYZE;")
            db.close()
            print("Database connection closed.")
        end_time = time.time()
        print(f"FTS population finished in {end_time - start_time:.2f} seconds.")

app = Flask(__name__)
app.config['DATABASE'] = DATABASE
app.config['SITE_NAME'] = SITE_NAME
app.config['CITATION_TEXT'] = CITATION_TEXT
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key_for_development')

if os.path.exists(DATABASE):
    try:
        # Check if FTS table is empty, repopulate if needed (or always repopulate on start)
        # For simplicity, let's always try to populate/update on startup.
        # A more robust check would verify schema or content version.
        print(f"Initializing FTS index for {DATABASE}...")
        create_and_populate_fts(DATABASE)
        print("FTS initialization complete.")
    except Exception as e:
        # Allow app to start but log error, search might fail
        print(f"!!! WARNING: Failed to initialize FTS index: {e}. Search may not work.")
        # raise RuntimeError(f"Failed to initialize FTS index: {e}") from e # Or raise to prevent startup
else:
    raise FileNotFoundError(f"Database file '{DATABASE}' not found. Cannot initialize FTS index.")

def get_db():
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(
                current_app.config['DATABASE'],
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            g.db.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            abort(500, description="Database connection failed.")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False, db_conn=None):
    conn_to_use = db_conn or g.get('db')

    if not conn_to_use:
        raise RuntimeError("Database connection not found in application context for query_db.")

    cur = None
    try:
        cur = conn_to_use.execute(query, args)
        rv = cur.fetchall()
        cur.close()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        if cur: cur.close()
        raise
    except Exception as e:
        if cur: cur.close()
        raise

def get_label(item_id, db_conn=None):
    result = query_db("SELECT object FROM triples WHERE subject = ? AND predicate = ?", (item_id, RDFS_LABEL), one=True, db_conn=db_conn)
    return result['object'] if result else item_id

def get_item_details(item_id):
    db = get_db()
    predicate_map = PREDICATE_MAP
    details = {
        'id': item_id,
        'label': get_label(item_id, db_conn=db),
        'properties': defaultdict(list),
        'raw_properties': defaultdict(list),
        'referencing_items': [],
        'grouped_referencing_items': None,
        'primary_type': None,
        'primary_type_display': None,
        'primary_type_category_key': None,
        'view_item_type': None,
        'grouping_basis': None,
        'is_pangen': False,
        'description': None
    }
    TECHNICAL_PROPS_DISPLAY = ["Type", "Subclass Of", "Domain", "Range", "Subproperty Of"]

    properties_cursor = None
    try:
        properties_cursor = db.execute("SELECT predicate, object FROM triples WHERE subject = ?", (item_id,))
        for row in properties_cursor:
            predicate, obj = row['predicate'], row['object']
            details['raw_properties'][predicate].append(obj)
            if predicate == RDF_TYPE:
                if not details['primary_type']:
                     details['primary_type'] = obj
                if obj == 'PanGene':
                    details['is_pangen'] = True
                    details['view_item_type'] = 'PanGene'
            elif predicate in DESCRIPTION_PREDICATES and not details['description']:
                 details['description'] = obj
    finally:
        if properties_cursor:
            properties_cursor.close()

    check_cur = None
    try:
        check_cur = db.cursor()
        for predicate, objects in details['raw_properties'].items():
            pred_display = predicate_map.get(predicate, predicate)
            processed_values = []
            for obj_val in objects:
                check_cur.execute("SELECT 1 FROM triples WHERE subject = ? LIMIT 1", (obj_val,))
                is_link = check_cur.fetchone() is not None
                display_val = get_label(obj_val, db_conn=db) if is_link else obj_val

                list_link_info = None
                if is_link and predicate in [HAS_RESISTANCE_CLASS, HAS_PREDICTED_PHENOTYPE, IS_FROM_DATABASE, RDF_TYPE]:
                     list_link_info = {
                         'predicate_key': predicate,
                         'predicate_display': pred_display,
                         'object_value_encoded': quote(obj_val)
                     }

                processed_values.append({
                    'value': obj_val,
                    'display': display_val,
                    'is_link': is_link,
                    'list_link_info': list_link_info
                })
            details['properties'][pred_display] = sorted(processed_values, key=lambda x: x['display'])
    finally:
        if check_cur:
            check_cur.close()

    del details['raw_properties']

    raw_referencing_items = []
    references_cursor = None
    try:
        references_cursor = db.execute("SELECT subject, predicate FROM triples WHERE object = ?", (item_id,))
        for row in references_cursor:
            raw_referencing_items.append({
                'ref_id': row['subject'],
                'predicate': row['predicate'],
                'ref_label': get_label(row['subject'], db_conn=db)
            })
    finally:
        if references_cursor:
            references_cursor.close()

    raw_referencing_items.sort(key=lambda x: x['ref_label'])

    if details['primary_type']:
        details['primary_type_display'] = details['primary_type']
        for cat_key, cat_info in INDEX_CATEGORIES.items():
            if cat_info['query_type'] == 'type' and cat_info['value'] == details['primary_type']:
                details['primary_type_display'] = cat_key
                details['primary_type_category_key'] = cat_key
                if details['primary_type'] == 'PanGene':
                     details['view_item_type'] = 'PanGene'
                break

        if not details['view_item_type']:
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

    if details['view_item_type'] in ['SourceDatabase', 'AntibioticClass', 'PredictedPhenotype']:
        details['properties'] = {k: v for k, v in details['properties'].items() if k not in TECHNICAL_PROPS_DISPLAY}
        if details['description']:
            details['properties']['Description'] = [{'value': details['description'], 'display': details['description'], 'is_link': False, 'list_link_info': None}]

        if details['view_item_type'] == 'SourceDatabase':
            details['grouping_basis'] = 'Antibiotic Class'
            grouped = defaultdict(list)
            original_gene_query = """
                SELECT T1.subject
                FROM triples T1
                JOIN triples T2 ON T1.subject = T2.subject
                WHERE T1.predicate = ? AND T1.object = ? AND T2.predicate = ? AND T2.object = 'OriginalGene'
            """
            gene_results = query_db(original_gene_query, (IS_FROM_DATABASE, item_id, RDF_TYPE), db_conn=db)
            gene_ids = [row['subject'] for row in gene_results] if gene_results else []

            if gene_ids:
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

                gene_info_map = {item['ref_id']: item for item in raw_referencing_items}
                for gene_id in gene_ids:
                    gene_item = gene_info_map.get(gene_id)
                    if not gene_item: continue

                    classes = gene_to_classes.get(gene_id)
                    if classes:
                        for class_name in classes:
                            class_label = get_label(class_name, db_conn=db)
                            grouped[class_label].append(gene_item)
                    else:
                        grouped['No Class Assigned'].append(gene_item)

            details['grouped_referencing_items'] = {k: sorted(v, key=lambda x: x['ref_label']) for k, v in sorted(grouped.items(), key=lambda item: (item[0].startswith("No "), item[0]))}
            details['referencing_items'] = None

        elif details['view_item_type'] in ['AntibioticClass', 'PredictedPhenotype']:
            details['referencing_items'] = raw_referencing_items
            details['grouped_referencing_items'] = None
            details['grouping_basis'] = None

    elif details['view_item_type'] == 'PanGene':
        details['referencing_items'] = raw_referencing_items
        details['grouped_referencing_items'] = None
        details['grouping_basis'] = None

    else:
        details['referencing_items'] = raw_referencing_items
        details['grouped_referencing_items'] = None
        details['grouping_basis'] = None

    if not details['properties'] and not details['referencing_items'] and not details['grouped_referencing_items']:
         return None

    return details

def get_category_counts():
    db = get_db()
    counts = {}
    for key, info in INDEX_CATEGORIES.items():
        count = 0
        if info['query_type'] == 'type':
            query = "SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ? AND object = ?"
            result = query_db(query, (RDF_TYPE, info['value']), one=True, db_conn=db)
            count = result['count'] if result else 0
        elif info['query_type'] == 'predicate_object':
            if 'filter_subject_type' in info:
                query = """
                    SELECT COUNT(DISTINCT T1.object) as count
                    FROM triples T1
                    JOIN triples T2 ON T1.subject = T2.subject
                    WHERE T1.predicate = ? AND T2.predicate = ? AND T2.object = ?
                """
                result = query_db(query, (info['value'], RDF_TYPE, info['filter_subject_type']), one=True, db_conn=db)
            else:
                query = "SELECT COUNT(DISTINCT object) as count FROM triples WHERE predicate = ?"
                result = query_db(query, (info['value'],), one=True, db_conn=db)
            count = result['count'] if result else 0
        elif info['query_type'] == 'predicate_subject':
            query = "SELECT COUNT(DISTINCT subject) as count FROM triples WHERE predicate = ?"
            result = query_db(query, (info['value'],), one=True, db_conn=db)
            count = result['count'] if result else 0

        counts[key] = count
    return counts

def get_items_for_category(category_key):
    db = get_db()
    items = []
    total_count = 0
    category_info = INDEX_CATEGORIES.get(category_key)

    if not category_info:
        return items, total_count

    if category_info['query_type'] == 'type':
        query = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject"
        results = query_db(query, (RDF_TYPE, category_info['value']), db_conn=db)
        if results:
            items = [{'id': row['subject']} for row in results]
            total_count = len(items)

    elif category_info['query_type'] == 'predicate_object':
        if 'filter_subject_type' in category_info:
            query = """
                SELECT DISTINCT T1.object
                FROM triples T1
                JOIN triples T2 ON T1.subject = T2.subject
                WHERE T1.predicate = ? AND T2.predicate = ? AND T2.object = ?
                ORDER BY T1.object
            """
            results = query_db(query, (category_info['value'], RDF_TYPE, category_info['filter_subject_type']), db_conn=db)
        else:
            query = "SELECT DISTINCT object FROM triples WHERE predicate = ? ORDER BY object"
            results = query_db(query, (category_info['value'],), db_conn=db)

        if results:
            items = [{'id': row['object']} for row in results]
            total_count = len(items)

    elif category_info['query_type'] == 'predicate_subject':
        query = "SELECT DISTINCT subject FROM triples WHERE predicate = ? ORDER BY subject"
        results = query_db(query, (category_info['value'],), db_conn=db)
        if results:
            items = [{'id': row['subject']} for row in results]
            total_count = len(items)

    return items, total_count

def get_grouped_pangen_data():
    db = get_db()
    grouped_by_class = defaultdict(list)
    grouped_by_phenotype = defaultdict(list)
    all_pangen_ids = set()
    gene_labels = {}

    pangen_query = f"""
        SELECT T1.subject, T3.object as label
        FROM triples T1
        JOIN triples T2 ON T1.subject = T2.subject
        LEFT JOIN triples T3 ON T1.subject = T3.subject AND T3.predicate = ?
        WHERE T2.predicate = ? AND T2.object = 'PanGene'
    """
    pangen_cursor = None
    try:
        pangen_cursor = db.execute(pangen_query, (RDFS_LABEL, RDF_TYPE))
        for row in pangen_cursor:
            gene_id = row['subject']
            all_pangen_ids.add(gene_id)
            gene_labels[gene_id] = row['label'] if row['label'] else gene_id
    finally:
        if pangen_cursor:
            pangen_cursor.close()

    total_count = len(all_pangen_ids)

    if not all_pangen_ids:
        return {}, {}, 0

    placeholders = ','.join('?' * len(all_pangen_ids))
    pangen_list = list(all_pangen_ids)

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

    for gene_id in all_pangen_ids:
        gene_display_name = gene_labels[gene_id]
        gene_entry = (gene_id, gene_display_name)

        classes = pangen_to_class.get(gene_id)
        if classes:
            for class_id in classes:
                class_label = get_label(class_id, db_conn=db)
                grouped_by_class[class_label].append(gene_entry)
        else:
            grouped_by_class['No Class Assigned'].append(gene_entry)

        phenotypes = pangen_to_phenotype.get(gene_id)
        if phenotypes:
            for phenotype_id in phenotypes:
                phenotype_label = get_label(phenotype_id, db_conn=db)
                grouped_by_phenotype[phenotype_label].append(gene_entry)
        else:
            grouped_by_phenotype['No Phenotype Assigned'].append(gene_entry)

    def sort_grouped_data(grouped_dict):
        sorted_dict = {}
        sorted_keys = sorted(grouped_dict.keys(), key=lambda k: (k.startswith("No "), k))
        for key in sorted_keys:
            sorted_genes = sorted(grouped_dict[key], key=lambda g: g[1])
            sorted_dict[key] = sorted_genes
        return sorted_dict

    return sort_grouped_data(grouped_by_class), sort_grouped_data(grouped_by_phenotype), total_count

def get_related_subjects(predicate, object_value):
    db = get_db()
    items = []
    total_count = 0
    query = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = ? ORDER BY subject"
    results = query_db(query, (predicate, object_value), db_conn=db)

    if results:
        subject_ids = [row['subject'] for row in results]
        labels = {}
        if subject_ids:
            placeholders = ','.join('?' * len(subject_ids))
            label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
            label_results = query_db(label_query, (RDFS_LABEL, *subject_ids), db_conn=db)
            if label_results:
                labels = {row['subject']: row['object'] for row in label_results}

        items = [{'id': row['subject'],
                  'display_name': labels.get(row['subject'], row['subject']),
                  'link': url_for('details', item_id=quote(row['subject']))}
                 for row in results]
        total_count = len(items)
        items.sort(key=lambda x: x['display_name'])

    return items, total_count

@app.context_processor
def inject_global_vars():
    return {
        'site_name': SITE_NAME,
        'current_year': datetime.datetime.now().year,
        'citation_text': CITATION_TEXT
    }

def get_pangen_distribution_data(limit=8):
    """
    Calculates the distribution of PanGenes by Antibiotic Class,
    Predicted Phenotype, and Source Database. Returns top N + Others.
    """
    db = get_db()
    distributions = {
        'class': {'labels': [], 'counts': []},
        'phenotype': {'labels': [], 'counts': []},
        'database': {'labels': [], 'counts': []}
    }

    # --- Get all PanGene IDs ---
    pangen_ids_result = query_db(
        "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object = 'PanGene'",
        (RDF_TYPE,), db_conn=db
    )
    if not pangen_ids_result:
        return distributions # Return empty if no PanGenes found

    pangen_ids = [row['subject'] for row in pangen_ids_result]
    placeholders = ','.join('?' * len(pangen_ids))

    # --- Distribution by Antibiotic Class ---
    class_counts = defaultdict(int)
    class_query = f"""
        SELECT object FROM triples
        WHERE predicate = ? AND subject IN ({placeholders})
    """
    class_results = query_db(class_query, (HAS_RESISTANCE_CLASS, *pangen_ids), db_conn=db)
    if class_results:
        for row in class_results:
            class_counts[row['object']] += 1
    distributions['class'] = process_distribution_counts(db, class_counts, limit)

    # --- Distribution by Predicted Phenotype ---
    phenotype_counts = defaultdict(int)
    phenotype_query = f"""
        SELECT object FROM triples
        WHERE predicate = ? AND subject IN ({placeholders})
    """
    phenotype_results = query_db(phenotype_query, (HAS_PREDICTED_PHENOTYPE, *pangen_ids), db_conn=db)
    if phenotype_results:
        for row in phenotype_results:
            phenotype_counts[row['object']] += 1
    distributions['phenotype'] = process_distribution_counts(db, phenotype_counts, limit)

    # --- Distribution by Source Database (via OriginalGene and same_as) ---
    # 1. Find OriginalGenes linked to our PanGenes
    original_gene_map = defaultdict(list) # {original_gene_id: [pangen_id, ...]}
    same_as_query = f"""
        SELECT subject, object FROM triples
        WHERE predicate = 'same_as' AND subject IN ({placeholders})
    """
    same_as_results = query_db(same_as_query, (*pangen_ids,), db_conn=db)
    original_gene_ids = set()
    pangen_to_original = defaultdict(set) # {pangen_id: {original_gene_id, ...}}
    if same_as_results:
        for row in same_as_results:
            pangen_id = row['subject']
            original_gene_id = row['object']
            original_gene_ids.add(original_gene_id)
            pangen_to_original[pangen_id].add(original_gene_id)

    # 2. Find Databases for these OriginalGenes
    db_counts = defaultdict(int)
    if original_gene_ids:
        og_placeholders = ','.join('?' * len(original_gene_ids))
        db_query = f"""
            SELECT T1.subject as original_gene, T1.object as database_id
            FROM triples T1
            JOIN triples T2 ON T1.subject = T2.subject
            WHERE T1.predicate = ?
              AND T1.subject IN ({og_placeholders})
              AND T2.predicate = ? AND T2.object = 'OriginalGene'
        """
        db_results = query_db(db_query, (IS_FROM_DATABASE, *list(original_gene_ids), RDF_TYPE), db_conn=db)

        # 3. Count unique PanGenes per Database
        database_pangenes = defaultdict(set) # {database_id: {pangen_id, ...}}
        if db_results:
            original_to_db = {row['original_gene']: row['database_id'] for row in db_results}
            for pangen_id, linked_original_ids in pangen_to_original.items():
                for og_id in linked_original_ids:
                    database_id = original_to_db.get(og_id)
                    if database_id:
                        database_pangenes[database_id].add(pangen_id)

        for db_id, unique_pangenes in database_pangenes.items():
            db_counts[db_id] = len(unique_pangenes)

    distributions['database'] = process_distribution_counts(db, db_counts, limit)

    return distributions


def process_distribution_counts(db, counts_dict, limit):
    """Helper to sort, limit, group 'Others', and get labels."""
    if not counts_dict:
        return {'labels': [], 'counts': []}

    sorted_items = sorted(counts_dict.items(), key=lambda item: item[1], reverse=True)

    top_items = sorted_items[:limit]
    other_items = sorted_items[limit:]

    final_labels = []
    final_counts = []

    # Get labels for top items
    top_ids = [item[0] for item in top_items]
    labels = {}
    if top_ids:
        placeholders = ','.join('?' * len(top_ids))
        label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
        label_results = query_db(label_query, (RDFS_LABEL, *top_ids), db_conn=db)
        if label_results:
            labels = {row['subject']: row['object'] for row in label_results}

    for item_id, count in top_items:
        final_labels.append(labels.get(item_id, item_id))
        final_counts.append(count)

    # Add 'Others' if necessary
    if other_items:
        other_count = sum(item[1] for item in other_items)
        if other_count > 0:
            final_labels.append("Others")
            final_counts.append(other_count)

    return {'labels': final_labels, 'counts': final_counts}

@app.route('/')
def index():
    category_counts = get_category_counts()
    # Fetch distribution data for charts
    distribution_data = get_pangen_distribution_data()
    return render_template('index.html',
                           index_categories=INDEX_CATEGORIES,
                           category_counts=category_counts,
                           distribution_data=distribution_data, # Pass data to template
                           show_error=False)

@app.route('/list/<category_key>')
@app.route('/list/related/<predicate>/<path:object_value>')
def list_items(category_key=None, predicate=None, object_value=None):
    db = get_db()
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
        decoded_object_value = unquote(object_value)
        items, total_item_count = get_related_subjects(predicate, decoded_object_value)
        predicate_display = predicate_map.get(predicate, predicate)
        object_label = get_label(decoded_object_value, db_conn=db)

        page_title = f"Items where {predicate_display} is {object_label}"
        item_type = "Related Item"
        grouping_predicate_display = predicate_display
        grouping_value_display = object_label

        details_for_object = get_item_details(decoded_object_value)
        if details_for_object and details_for_object.get('primary_type_category_key'):
            parent_category_key = details_for_object['primary_type_category_key']

    elif category_key and category_key in INDEX_CATEGORIES:
        category_info = INDEX_CATEGORIES[category_key]
        page_title = category_key
        item_type = category_info.get('value', category_key)

        if category_key == "PanRes Genes":
            grouped_by_class, _, total_item_count = get_grouped_pangen_data()
            grouped_items = grouped_by_class
            grouping_predicate_display = predicate_map.get(HAS_RESISTANCE_CLASS)
            item_type = "PanGene"
            page_title = "PanRes Genes grouped by Antibiotic Class"
        else:
            items, total_item_count = get_items_for_category(category_key)
            for item in items:
                if 'display_name' not in item:
                    item['display_name'] = get_label(item['id'], db_conn=db)
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
    decoded_item_id = unquote(item_id)
    item_details = get_item_details(decoded_item_id)

    if not item_details:
        abort(404, description=f"Item '{decoded_item_id}' not found in the PanRes data.")

    return render_template(
        'details.html',
        item_id=decoded_item_id,
        details=item_details,
    )

@app.errorhandler(404)
def handle_not_found(e):
    path = request.path if request else "Unknown path"
    error_message = e.description or f"The requested page '{path}' could not be found."
    return render_template('index.html',
                           show_error=True,
                           error_code=404,
                           error_message=error_message,
                           index_categories=INDEX_CATEGORIES,
                           category_counts={},
                           ), 404

@app.errorhandler(500)
def internal_server_error(e):
    error_message = getattr(e, 'original_exception', None) or getattr(e, 'description', "An internal server error occurred. Please try again later.")
    return render_template('index.html',
                           show_error=True,
                           error_code=500,
                           error_message=str(error_message),
                           index_categories=INDEX_CATEGORIES,
                           category_counts={},
                           ), 500

@app.route('/testdb')
def test_db_connection():
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
        return f"An error occurred: {e}", 500

@app.route('/autocomplete')
def autocomplete():
    search_term = request.args.get('q', '').strip()
    # Call the direct query function with updated logic
    suggestions = get_autocomplete_suggestions_direct(search_term)
    return jsonify(suggestions)

# Autocomplete function using direct queries with explicit case-sensitive grouping and robust type checking
def get_autocomplete_suggestions_direct(term, limit=500): # Keep the higher limit for now
    """
    Performs autocomplete search directly on triples table.
    Prioritizes case-sensitive prefix matches (GLOB) over case-insensitive ones (LIKE).
    Standardizes gene type display based on all item types. Includes logging.
    """
    logging.info(f"Autocomplete search for term: '{term}'") # Use INFO level for general flow
    if not term or len(term) < 1:
        return []

    db = get_db()
    glob_pattern = f"{term}*"
    like_pattern = f"{term}%"
    # Fetch limit per query type
    candidate_limit_per_query = limit * 2 # Fetch a reasonable amount for each query type

    try:
        # --- 1. Find Case-Sensitive Matches (GLOB) ---
        query_glob_id = "SELECT DISTINCT subject FROM triples WHERE subject GLOB ? LIMIT ?"
        glob_id_res = query_db(query_glob_id, (glob_pattern, candidate_limit_per_query), db_conn=db)
        glob_matched_ids = {row['subject'] for row in glob_id_res} if glob_id_res else set()
        logging.debug(f"GLOB ID matches: {len(glob_matched_ids)}") # DEBUG for details

        query_glob_label = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object GLOB ? LIMIT ?"
        glob_label_res = query_db(query_glob_label, (RDFS_LABEL, glob_pattern, candidate_limit_per_query), db_conn=db)
        if glob_label_res:
            glob_matched_ids.update(row['subject'] for row in glob_label_res)
        logging.debug(f"Total GLOB matches: {len(glob_matched_ids)}")
        # Log if pan_1 is found here (example)
        if 'pan_1' in glob_matched_ids: logging.info("'pan_1' found in GLOB matches.")


        # --- 2. Find Case-Insensitive Matches (LIKE) ---
        query_like_id = "SELECT DISTINCT subject FROM triples WHERE subject LIKE ? LIMIT ?"
        like_id_res = query_db(query_like_id, (like_pattern, candidate_limit_per_query), db_conn=db)
        like_matched_ids = {row['subject'] for row in like_id_res} if like_id_res else set()
        logging.debug(f"LIKE ID matches: {len(like_matched_ids)}")

        query_like_label = "SELECT DISTINCT subject FROM triples WHERE predicate = ? AND object LIKE ? LIMIT ?"
        like_label_res = query_db(query_like_label, (RDFS_LABEL, like_pattern, candidate_limit_per_query), db_conn=db)
        if like_label_res:
            like_matched_ids.update(row['subject'] for row in like_label_res)
        logging.debug(f"Total LIKE matches: {len(like_matched_ids)}")
        if 'pan_1' in like_matched_ids and 'pan_1' not in glob_matched_ids: logging.info("'pan_1' found in LIKE matches (but not GLOB).")


        # --- 3. Separate Purely Case-Insensitive Matches ---
        purely_insensitive_ids = like_matched_ids - glob_matched_ids
        logging.debug(f"Purely Insensitive matches: {len(purely_insensitive_ids)}")
        if 'pan_1' in purely_insensitive_ids: logging.info("'pan_1' is purely insensitive match.")


        # --- 4. Combine IDs, prioritizing GLOB matches ---
        # Sort alphabetically within each group before combining for consistent ordering
        glob_list_sorted = sorted(list(glob_matched_ids))
        insensitive_list_sorted = sorted(list(purely_insensitive_ids))
        ordered_ids = glob_list_sorted + insensitive_list_sorted
        logging.info(f"Combined ordered IDs: {len(ordered_ids)}")
        if 'pan_1' in ordered_ids: logging.info(f"'pan_1' is in ordered_ids at index {ordered_ids.index('pan_1')}")


        if not ordered_ids:
            logging.info("No matching IDs found.")
            return []

        # Limit total IDs *before* fetching details
        # Use a limit slightly larger than the final desired limit to allow sorting later if needed
        detail_fetch_limit = limit + 50 # Fetch details for slightly more than needed
        if len(ordered_ids) > detail_fetch_limit:
             logging.info(f"Truncating ordered_ids from {len(ordered_ids)} to {detail_fetch_limit}")
             ordered_ids = ordered_ids[:detail_fetch_limit]

        item_ids = ordered_ids # Use this potentially truncated ordered list
        actual_ids_count = len(item_ids)
        if actual_ids_count == 0: return []

        placeholders = ','.join('?' * actual_ids_count)

        # --- 5. Fetch details (Labels, ALL Types) ---
        logging.info(f"Fetching details for {actual_ids_count} IDs...")
        labels = {}
        label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
        label_results = query_db(label_query, (RDFS_LABEL, *item_ids), db_conn=db)
        if label_results: labels = {row['subject']: row['object'] for row in label_results}

        # Fetch *all* types for each subject
        all_types_map = defaultdict(list)
        type_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
        type_results = query_db(type_query, (RDF_TYPE, *item_ids), db_conn=db)
        if type_results:
            for row in type_results: all_types_map[row['subject']].append(row['object'])
        logging.info(f"Fetched types for {len(all_types_map)} items.")
        if 'pan_1' in all_types_map: logging.info(f"Types for 'pan_1': {all_types_map['pan_1']}")


        # --- 6. Check for Class/Phenotype/Database roles ---
        class_ids, phenotype_ids, database_ids = set(), set(), set()
        check_query = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
        # Run these checks only if the corresponding predicates exist in your PREDICATE_MAP or constants
        if HAS_RESISTANCE_CLASS:
            class_res = query_db(check_query, (HAS_RESISTANCE_CLASS, *item_ids), db_conn=db)
            if class_res: class_ids = {row['object'] for row in class_res}
        if HAS_PREDICTED_PHENOTYPE:
            pheno_res = query_db(check_query, (HAS_PREDICTED_PHENOTYPE, *item_ids), db_conn=db)
            if pheno_res: phenotype_ids = {row['object'] for row in pheno_res}
        if IS_FROM_DATABASE:
            db_res = query_db(check_query, (IS_FROM_DATABASE, *item_ids), db_conn=db)
            if db_res: database_ids = {row['object'] for row in db_res}
        logging.debug(f"Checked roles: Classes({len(class_ids)}), Phenotypes({len(phenotype_ids)}), Databases({len(database_ids)})")


        # --- 7. Build suggestion list, respecting the order from step 4 ---
        PANGENE_TYPES = {'PanGene', 'AntimicrobialResistanceGene', 'BiocideResistanceGene', 'MetalResistanceGene'}
        final_suggestions = []
        processed_ids = set() # Ensure no duplicates

        logging.info(f"Building final suggestions from {len(item_ids)} ordered IDs...")
        for item_id in item_ids:
            if item_id in processed_ids: continue

            display_name = labels.get(item_id, item_id)
            item_all_types = all_types_map.get(item_id, []) # Get all types

            # Determine standardized type indicator based on all types
            type_indicator = "Other" # Default
            # Check specific roles first
            if item_id in class_ids: type_indicator = "Resistance Class"
            elif item_id in phenotype_ids: type_indicator = "Predicted Phenotype"
            elif item_id in database_ids: type_indicator = "Source Database"
            # Then check gene types using *all* types associated with the item
            elif any(t in PANGENE_TYPES for t in item_all_types):
                 type_indicator = "PanGene" # Standardize to PanGene if any relevant type exists
            elif 'OriginalGene' in item_all_types:
                 type_indicator = "OriginalGene"
            else:
                 # Fallback: find a preferred type label (non-OWL, non-NamedIndividual)
                 preferred_type = next((t for t in item_all_types if t != OWL_NAMED_INDIVIDUAL and not t.startswith('owl:')), None)
                 if preferred_type:
                     type_indicator = get_label(preferred_type, db_conn=db)
                 elif item_all_types: # If only OWL/NamedIndividual types, use the first one's label
                     type_indicator = get_label(item_all_types[0], db_conn=db)

            final_suggestions.append({
                'id': item_id,
                'display_name': display_name,
                'link': url_for('details', item_id=quote(item_id)),
                'type_indicator': type_indicator
            })
            processed_ids.add(item_id)

            # Stop if we have enough for the final limit
            if len(final_suggestions) >= limit:
                logging.info(f"Reached limit ({limit}) during final list construction.")
                break

        logging.info(f"Returning {len(final_suggestions)} suggestions.")
        # The list is already ordered (GLOB first, then LIKE, sorted alphabetically within groups)
        # and truncated to the limit.
        return final_suggestions

    except sqlite3.Error as e:
        logging.error(f"Autocomplete DB Error: {e}", exc_info=True) # Log traceback
        return []
    except Exception as e:
        logging.error(f"Autocomplete General Error: {e}", exc_info=True) # Log traceback
        return []

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    is_development = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEBUG') == '1'
    app.run(host='0.0.0.0', port=port, debug=is_development) 