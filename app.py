import os
import json
import gc  # Import garbage collector
from flask import Flask, jsonify, render_template, url_for, abort
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import urldefrag, quote, unquote

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)
CACHE_JSON_FILENAME = "ontology_cache.json"
CACHE_JSON_PATH = os.path.join(os.path.dirname(__file__), CACHE_JSON_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Ontology Data Cache ---
ontology_data_cache = None
ontology_load_error = None  # Store potential loading errors

# --- Helper Functions ---
def get_local_name(uri):
    """Extracts the local name from a URI."""
    if isinstance(uri, URIRef):
        uri_str = str(uri)
        fragment = urldefrag(uri_str)[1]
        if fragment:
            return fragment
        else:
            # Handle potential double encoding if URIs have slashes/hashes in local names
            safe_uri_str = unquote(uri_str)
            return safe_uri_str.rsplit('/', 1)[-1].rsplit('#', 1)[-1]
    return str(uri)  # Return as is if not a URIRef or cannot parse

# --- Preprocessing Function ---
def create_ontology_cache():
    """Loads the OWL file, processes it, and saves the structured data to JSON."""
    print(f"Starting ontology processing from: {OWL_FILE_PATH}")
    if not os.path.exists(OWL_FILE_PATH):
        error_msg = f"Error: Ontology file not found at {OWL_FILE_PATH}"
        print(error_msg)
        return None, error_msg

    g = Graph()
    try:
        # This is the memory-intensive step
        print("Parsing OWL file...")
        g.parse(OWL_FILE_PATH, format="xml")  # Assuming RDF/XML format for .owl
        print(f"Ontology loaded successfully. Graph size: {len(g)} triples.")
    except Exception as e:
        error_msg = f"Error parsing ontology file: {e}"
        print(error_msg)
        return None, error_msg  # Stop processing if parsing failed

    print("Processing graph into data structures...")
    data = {
        "classDetails": {},
        "individualDetails": {},
        "subClassMap": {},  # Map: parentId -> [childId]
        "classInstanceMap": {},  # Map: classId -> [instanceId]
        "uriRegistry": {},  # Map: uri -> { type: 'class'/'individual'/'property', label: '...' }
        "topClasses": []  # List of top-level class IDs
    }
    processed_classes = set()
    processed_individuals = set()

    # --- 1. Process Classes ---
    class_uris = set(g.subjects(RDF.type, OWL.Class))
    class_uris.update(g.subjects(RDFS.subClassOf, None))
    class_uris.update(g.objects(None, RDFS.subClassOf))

    for class_uri in class_uris:
        if not isinstance(class_uri, URIRef) or str(class_uri).startswith(str(OWL)) \
           or str(class_uri).startswith(str(RDF)) or str(class_uri).startswith(str(RDFS)) \
           or str(class_uri).startswith(str(XSD)) or class_uri == OWL.Thing:
            continue
        if class_uri in processed_classes: continue

        class_id = str(class_uri)
        label = next(g.objects(class_uri, RDFS.label), None)
        comment = next(g.objects(class_uri, RDFS.comment), None)
        local_name = get_local_name(class_uri)

        class_obj = {
            "id": class_id,
            "name": local_name,
            "label": str(label) if label else local_name,
            "description": str(comment) if comment else "",
            "superClasses": [],
            "hasSubClasses": False,  # Will be set later
            "hasInstances": False    # Will be set later
        }

        is_subclass = False
        for super_class_uri in g.objects(class_uri, RDFS.subClassOf):
            if isinstance(super_class_uri, URIRef) and super_class_uri != OWL.Thing and super_class_uri != class_uri:
                super_id = str(super_class_uri)
                class_obj["superClasses"].append(super_id)
                # Build subClassMap
                if super_id not in data["subClassMap"]:
                    data["subClassMap"][super_id] = []
                if class_id not in data["subClassMap"][super_id]:
                     data["subClassMap"][super_id].append(class_id)
                is_subclass = True  # It has a parent other than Thing

        data["classDetails"][class_id] = class_obj
        data["uriRegistry"][class_id] = {"type": "class", "label": class_obj["label"]}
        processed_classes.add(class_uri)

    # Determine top-level classes
    all_known_class_ids = set(data["classDetails"].keys())
    for cid, cobj in data["classDetails"].items():
        is_top = True
        if not cobj["superClasses"]:  # No parents listed
             is_top = True
        else:
            # Check if any parent is a known class we processed
            if any(sup_id in all_known_class_ids for sup_id in cobj["superClasses"]):
                is_top = False
            # If all parents are outside our set (e.g., only OWL.Thing), consider it top
            else:
                 is_top = True

        if is_top:
            data["topClasses"].append(cid)
    print(f"Processed {len(data['classDetails'])} classes.")

    # --- 2. Process Individuals ---
    individual_uris = set(g.subjects(RDF.type, OWL.NamedIndividual))
    for class_id in data["classDetails"]:
        individual_uris.update(g.subjects(RDF.type, URIRef(class_id)))

    for ind_uri in individual_uris:
        if not isinstance(ind_uri, URIRef) or ind_uri in processed_individuals: continue

        ind_id = str(ind_uri)
        label = next(g.objects(ind_uri, RDFS.label), None)
        comment = next(g.objects(ind_uri, RDFS.comment), None)
        local_name = get_local_name(ind_uri)

        ind_obj = {
            "id": ind_id,
            "name": local_name,
            "label": str(label) if label else local_name,
            "description": str(comment) if comment else "",
            "types": [],
            "properties": {}  # { propUri: [{ type: 'uri'/'literal', value: '...', datatype: '...' }] }
        }

        # Get types (classes)
        for type_uri in g.objects(ind_uri, RDF.type):
            if isinstance(type_uri, URIRef) and str(type_uri) in data["classDetails"]:
                type_id = str(type_uri)
                ind_obj["types"].append(type_id)
                # Link instance to class
                if type_id not in data["classInstanceMap"]:
                    data["classInstanceMap"][type_id] = []
                if ind_id not in data["classInstanceMap"][type_id]:
                     data["classInstanceMap"][type_id].append(ind_id)  # Add the individual ID

        # Get properties asserted on the individual
        for p, o in g.predicate_objects(ind_uri):
            prop_uri = str(p)
            # Skip RDF/RDFS/OWL schema properties unless needed
            if p == RDF.type or p == RDFS.label or p == RDFS.comment or \
               str(p).startswith(str(RDF)) or str(p).startswith(str(RDFS)) or str(p).startswith(str(OWL)):
                continue

            if prop_uri not in ind_obj["properties"]:
                ind_obj["properties"][prop_uri] = []

            prop_entry = {}
            if isinstance(o, URIRef):
                prop_entry["type"] = "uri"
                prop_entry["value"] = str(o)
            elif isinstance(o, Literal):
                prop_entry["type"] = "literal"
                prop_entry["value"] = str(o)
                prop_entry["datatype"] = str(o.datatype) if o.datatype else None
            else: continue  # Skip blank nodes or other types if not handled

            ind_obj["properties"][prop_uri].append(prop_entry)
            # Register property URI if not seen before
            if prop_uri not in data["uriRegistry"]:
                 prop_label = next(g.objects(p, RDFS.label), None)
                 data["uriRegistry"][prop_uri] = {"type": "property", "label": str(prop_label) if prop_label else get_local_name(p)}

        data["individualDetails"][ind_id] = ind_obj
        data["uriRegistry"][ind_id] = {"type": "individual", "label": ind_obj["label"]}
        processed_individuals.add(ind_uri)
    print(f"Processed {len(data['individualDetails'])} individuals.")

    # --- 3. Post-process: Set flags for children ---
    print("Setting child flags...")
    for class_id in data["classDetails"]:
        if class_id in data["subClassMap"] and data["subClassMap"][class_id]:
            data["classDetails"][class_id]["hasSubClasses"] = True
        if class_id in data["classInstanceMap"] and data["classInstanceMap"][class_id]:
            data["classDetails"][class_id]["hasInstances"] = True

    # --- Clear Graph from Memory ---
    print("Clearing RDF graph from memory...")
    del g
    gc.collect()  # Explicitly request garbage collection

    # --- Save to JSON ---
    print(f"Saving processed data to {CACHE_JSON_PATH}...")
    try:
        with open(CACHE_JSON_PATH, 'w', encoding='utf-8') as f:
            # Use indent=None for smaller file size in production
            json.dump(data, f, ensure_ascii=False, indent=None)
        print(f"Successfully saved processed data to {CACHE_JSON_PATH}")
    except Exception as e:
        error_msg = f"Error saving data to JSON: {e}"
        print(error_msg)
        return None, error_msg

    print("Preprocessing finished.")
    return data, None  # Return data and no error

def load_ontology_from_cache():
    """Loads the pre-processed ontology data from the JSON cache file."""
    global ontology_data_cache, ontology_load_error
    # Prevent reloading if already loaded or failed
    if ontology_data_cache or ontology_load_error:
        print("Using existing ontology data or previously recorded error.")
        return

    print(f"Loading ontology data from cache file: {CACHE_JSON_PATH}")
    if not os.path.exists(CACHE_JSON_PATH):
        print("Cache file not found. Creating it now...")
        # Try to create the cache
        data, error = create_ontology_cache()
        if error:
            ontology_load_error = error
            return
        ontology_data_cache = data
        return

    try:
        with open(CACHE_JSON_PATH, 'r', encoding='utf-8') as f:
            ontology_data_cache = json.load(f)
        # Basic validation
        if not isinstance(ontology_data_cache, dict) or "classDetails" not in ontology_data_cache:
             raise ValueError("Cache file does not contain expected structure.")
        print(f"Successfully loaded ontology data from cache. {len(ontology_data_cache.get('classDetails', {}))} classes, {len(ontology_data_cache.get('individualDetails', {}))} individuals.")
    except json.JSONDecodeError as e:
        error_msg = f"Error reading cache file '{CACHE_JSON_FILENAME}': Invalid JSON. {e}"
        print(f"Error: {error_msg}")
        ontology_load_error = error_msg
    except Exception as e:
        error_msg = f"An unexpected error occurred while loading '{CACHE_JSON_FILENAME}': {e}"
        print(f"Error: {error_msg}")
        ontology_load_error = error_msg

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Ensure data is loaded from cache on first request
    if not ontology_data_cache and not ontology_load_error:
         load_ontology_from_cache()
    # Pass potential load error to template
    return render_template('index.html', load_error=ontology_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry from cache."""
    if ontology_load_error:
        return jsonify({"error": ontology_load_error}), 500
    if not ontology_data_cache:
        # Attempt to load if accessed directly before index()
        load_ontology_from_cache()
        if ontology_load_error:
             return jsonify({"error": ontology_load_error}), 500
        if not ontology_data_cache: # Still nothing? Critical error.
             return jsonify({"error": "Ontology data cache could not be loaded."}), 500

    # Access data directly from the cache, using .get for safety
    top_classes_data = ontology_data_cache.get("topClasses", [])
    class_details_data = ontology_data_cache.get("classDetails", {})
    uri_registry_data = ontology_data_cache.get("uriRegistry", {})

    top_class_details = [
        {
            "id": cid,
            "label": class_details_data[cid]["label"],
            "hasSubClasses": class_details_data[cid]["hasSubClasses"],
            "hasInstances": class_details_data[cid]["hasInstances"]
        }
        for cid in top_classes_data
        if cid in class_details_data # Ensure consistency
    ]
    top_class_details.sort(key=lambda x: x["label"])

    return jsonify({
        "topClasses": top_class_details,
        "uriRegistry": uri_registry_data
    })


@app.route('/api/children/<path:node_uri>')
def get_children(node_uri):
    """Provides direct subclasses and instances for a given class URI from cache."""
    class_id = unquote(node_uri)

    if ontology_load_error or not ontology_data_cache:
        return jsonify({"error": "Ontology data not available."}), 500

    class_details_data = ontology_data_cache.get("classDetails", {})
    subclass_map_data = ontology_data_cache.get("subClassMap", {})
    instance_map_data = ontology_data_cache.get("classInstanceMap", {})
    individual_details_data = ontology_data_cache.get("individualDetails", {})

    if class_id not in class_details_data:
        abort(404, description="Class URI not found in cache.")

    subclass_ids = subclass_map_data.get(class_id, [])
    instance_ids = instance_map_data.get(class_id, [])

    subclass_details = [
        {
            "id": sid,
            "label": class_details_data[sid]["label"],
            "hasSubClasses": class_details_data[sid]["hasSubClasses"],
            "hasInstances": class_details_data[sid]["hasInstances"]
        }
        for sid in subclass_ids if sid in class_details_data
    ]
    instance_details = [
        {
            "id": iid,
            "label": individual_details_data[iid]["label"]
        }
        for iid in instance_ids if iid in individual_details_data
    ]

    subclass_details.sort(key=lambda x: x["label"])
    instance_details.sort(key=lambda x: x["label"])

    return jsonify({
        "subClasses": subclass_details,
        "instances": instance_details
    })


@app.route('/api/details/<path:node_uri>')
def get_details(node_uri):
    """Provides full details for a specific class or individual URI from cache."""
    item_id = unquote(node_uri)

    if ontology_load_error or not ontology_data_cache:
        return jsonify({"error": "Ontology data not available."}), 500

    details = None
    item_type = None
    class_details_data = ontology_data_cache.get("classDetails", {})
    individual_details_data = ontology_data_cache.get("individualDetails", {})
    subclass_map_data = ontology_data_cache.get("subClassMap", {})
    instance_map_data = ontology_data_cache.get("classInstanceMap", {})


    if item_id in class_details_data:
        details = class_details_data[item_id].copy() # Return a copy
        item_type = "class"
        # Add subclass/instance IDs from cache maps
        details["subClasses"] = subclass_map_data.get(item_id, [])
        details["instances"] = instance_map_data.get(item_id, [])

    elif item_id in individual_details_data:
        details = individual_details_data[item_id].copy() # Return a copy
        item_type = "individual"
        # Properties are already part of the individualDetails structure

    if details:
        return jsonify({"type": item_type, "details": details})
    else:
         abort(404, description="Item URI not found in cache.")


# --- Main Execution ---
if __name__ == '__main__':
    # Load data from cache on startup or create it if needed
    print("Checking ontology cache on startup...")
    load_ontology_from_cache()
    if ontology_load_error:
        print(f"WARNING: Ontology cache loading failed: {ontology_load_error}")
        # App will still run but API calls might return errors.
    else:
        print("Ontology data loaded successfully.")

    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use debug=False for production/deployment
    app.run(host='0.0.0.0', port=port_to_use, debug=False) 