import os
import json
from flask import Flask, jsonify, render_template, url_for, abort
# rdflib imports are no longer needed at runtime
# from rdflib import Graph, Namespace, Literal, URIRef
# from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import unquote # Keep unquote

# --- Configuration ---
# Point to the pre-processed cache file
CACHE_JSON_FILENAME = "ontology_cache.json"
CACHE_JSON_PATH = os.path.join(os.path.dirname(__file__), CACHE_JSON_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Ontology Data Cache ---
ontology_data_cache = None
ontology_load_error = None # Store potential loading errors

# Remove get_local_name if it's not used elsewhere after removing rdflib processing
# def get_local_name(uri): ...

def load_ontology_from_cache():
    """Loads the pre-processed ontology data from the JSON cache file."""
    global ontology_data_cache, ontology_load_error
    # Prevent reloading if already loaded or failed
    if ontology_data_cache or ontology_load_error:
        print("Using existing ontology data or previously recorded error.")
        return

    print(f"Loading ontology data from cache file: {CACHE_JSON_PATH}")
    if not os.path.exists(CACHE_JSON_PATH):
        error_msg = f"Ontology cache file '{CACHE_JSON_FILENAME}' not found. Please run the preprocessing script first."
        print(f"Error: {error_msg}")
        ontology_load_error = error_msg
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

# Remove the old load_and_process_ontology function entirely
# def load_and_process_ontology(): ...


# --- Flask Routes (Modify to use cache) ---
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
    # Load data from cache on startup
    print("Initiating ontology load from cache on startup...")
    load_ontology_from_cache() # Call the new loading function
    if ontology_load_error:
        print(f"WARNING: Ontology cache loading failed: {ontology_load_error}")
        # App will still run but API calls might return errors.
    else:
        print("Ontology data loaded successfully from cache.")

    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use debug=False for production/deployment
    app.run(host='0.0.0.0', port=port_to_use, debug=False) 