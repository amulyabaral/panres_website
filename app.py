import sqlite3
from flask import Flask, render_template, g, abort, url_for, current_app, jsonify, request
import os
from urllib.parse import unquote, quote
from collections import defaultdict
import datetime
import json

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

def create_and_populate_fts(db_path):
    db = None
    try:
        db = sqlite3.connect(db_path)
        cur = db.cursor()
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS item_search_fts USING fts5(
                item_id UNINDEXED,
                search_term,
                tokenize = 'unicode61 remove_diacritics 0'
            );
        """)

        cur.execute("SELECT COUNT(*) FROM item_search_fts")
        count = cur.fetchone()[0]
        if count > 0:
            return

        cur.execute(f"""
            SELECT DISTINCT
                t1.subject,
                COALESCE(t2.object, t1.subject) AS search_text
            FROM triples t1
            LEFT JOIN triples t2 ON t1.subject = t2.subject AND t2.predicate = ?
            WHERE t1.subject IS NOT NULL AND search_text IS NOT NULL
        """, (RDFS_LABEL,))
        items = cur.fetchall()

        if items:
            fts_data = [(row[0], row[1]) for row in items]
            cur.executemany("INSERT INTO item_search_fts (item_id, search_term) VALUES (?, ?)", fts_data)
            db.commit()

    except sqlite3.Error as e:
        if db: db.rollback()
        raise
    except Exception as e:
        raise
    finally:
        if db:
            db.close()

app = Flask(__name__)
app.config['DATABASE'] = DATABASE
app.config['SITE_NAME'] = SITE_NAME
app.config['CITATION_TEXT'] = CITATION_TEXT
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key_for_development')

if os.path.exists(DATABASE):
    try:
        create_and_populate_fts(DATABASE)
    except Exception as e:
        raise RuntimeError(f"Failed to initialize FTS index: {e}") from e
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

@app.route('/')
def index():
    category_counts = get_category_counts()
    return render_template('index.html',
                           index_categories=INDEX_CATEGORIES,
                           category_counts=category_counts,
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
    suggestions = get_autocomplete_suggestions_fts(search_term)
    return jsonify(suggestions)

def get_autocomplete_suggestions_fts(term, limit=15):
    if not term or len(term) < 2:
        return []

    db = get_db()
    fts_query_term = f'"{term}"*'

    try:
        query_fts = """
            SELECT item_id
            FROM item_search_fts
            WHERE search_term MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        initial_results = query_db(query_fts, (fts_query_term, limit * 2), db_conn=db)

        if not initial_results:
            return []

        item_ids = list({row['item_id'] for row in initial_results})
        if not item_ids: return []

        placeholders = ','.join('?' * len(item_ids))

        labels = {}
        label_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
        label_results = query_db(label_query, (RDFS_LABEL, *item_ids), db_conn=db)
        if label_results: labels = {row['subject']: row['object'] for row in label_results}

        types = {}
        type_query = f"SELECT subject, object FROM triples WHERE predicate = ? AND subject IN ({placeholders})"
        type_results = query_db(type_query, (RDF_TYPE, *item_ids), db_conn=db)
        if type_results:
            for row in type_results:
                if row['subject'] not in types:
                    types[row['subject']] = row['object']

        class_subjects = set()
        pheno_subjects = set()
        db_subjects = set()

        class_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
        class_res = query_db(class_q, (HAS_RESISTANCE_CLASS, *item_ids), db_conn=db)
        if class_res: class_subjects = {row['object'] for row in class_res}

        pheno_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
        pheno_res = query_db(pheno_q, (HAS_PREDICTED_PHENOTYPE, *item_ids), db_conn=db)
        if pheno_res: pheno_subjects = {row['object'] for row in pheno_res}

        db_q = f"SELECT DISTINCT object FROM triples WHERE predicate = ? AND object IN ({placeholders})"
        db_res = query_db(db_q, (IS_FROM_DATABASE, *item_ids), db_conn=db)
        if db_res: db_subjects = {row['object'] for row in db_res}

        final_suggestions = []
        seen_subjects = set()
        for row in initial_results:
            subject_id = row['item_id']

            if subject_id in seen_subjects or len(final_suggestions) >= limit:
                continue

            rdf_type = types.get(subject_id)
            display_name = labels.get(subject_id, subject_id)
            indicator = "Other"

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

            final_suggestions.append({
                'id': subject_id,
                'display_name': display_name,
                'link': url_for('details', item_id=quote(subject_id)),
                'type_indicator': indicator
            })
            seen_subjects.add(subject_id)

        return final_suggestions

    except sqlite3.Error as e:
        return []
    except Exception as e:
        return []

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    is_development = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEBUG') == '1'
    app.run(host='0.0.0.0', port=port, debug=is_development) 