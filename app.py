import os
import json
from flask import Flask, jsonify, render_template, url_for, abort
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import urldefrag, quote, unquote

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Ontology Loading and Caching ---
ontology_data_cache = None
ontology_load_error = None # Store potential loading errors

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
    return str(uri) # Return as is if not a URIRef or cannot parse

def load_and_process_ontology():
    """Loads the OWL file, parses it, and structures the data. Stores error if fails."""
    global ontology_data_cache, ontology_load_error
    if ontology_data_cache or ontology_load_error:
        # Already loaded or failed loading, don't retry
        print("Using cached ontology data or previously recorded error.")
        return

    print(f"Loading ontology from: {OWL_FILE_PATH}")
    if not os.path.exists(OWL_FILE_PATH):
        print(f"Error: Ontology file not found at {OWL_FILE_PATH}")
        ontology_load_error = f"Ontology file '{OWL_FILENAME}' not found."
        return

    g = Graph()
    try:
        # This is the memory-intensive step
        g.parse(OWL_FILE_PATH, format="xml") # Assuming RDF/XML format for .owl
        print(f"Ontology loaded successfully. Graph size: {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing ontology file: {e}")
        ontology_load_error = f"Error parsing ontology file: {e}"
        return # Stop processing if parsing failed

    # --- Start Processing (similar to before, but structure for lookup) ---
    data = {
        "classDetails": {},
        "individualDetails": {},
        "subClassMap": {}, # Map: parentId -> [childId]
        "classInstanceMap": {}, # Map: classId -> [instanceId]
        "uriRegistry": {}, # Map: uri -> { type: 'class'/'individual'/'property', label: '...' }
        "topClasses": [] # List of top-level class IDs
    }

    # --- 1. Process Classes ---
    class_uris = set(g.subjects(RDF.type, OWL.Class))
    class_uris.update(g.subjects(RDFS.subClassOf, None))
    class_uris.update(g.objects(None, RDFS.subClassOf))
    processed_classes = set()

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
            # We will populate subClasses and instances on demand or via maps
            # "subClasses": [], # Removed for lazy loading
            # "instances": [], # Removed for lazy loading
            "hasSubClasses": False, # Flag for UI caret
            "hasInstances": False   # Flag for UI caret
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
                is_subclass = True # It has a parent other than Thing

        data["classDetails"][class_id] = class_obj
        data["uriRegistry"][class_id] = {"type": "class", "label": class_obj["label"]}
        processed_classes.add(class_uri)

    # Determine top-level classes (those whose parents are not in our processed list or OWL.Thing)
    all_known_class_ids = set(data["classDetails"].keys())
    for cid, cobj in data["classDetails"].items():
        is_top = True
        if not cobj["superClasses"]: # No parents listed
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

    # --- 2. Process Individuals ---
    individual_uris = set(g.subjects(RDF.type, OWL.NamedIndividual))
    for class_id in data["classDetails"]:
        individual_uris.update(g.subjects(RDF.type, URIRef(class_id)))
    processed_individuals = set()

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
            "properties": {} # { propUri: [{ type: 'uri'/'literal', value: '...', datatype: '...' }] }
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
                    data["classInstanceMap"][type_id].append(ind_id)

        # Get properties asserted on the individual
        for p, o in g.predicate_objects(ind_uri):
            prop_uri = str(p)
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
            else: continue

            ind_obj["properties"][prop_uri].append(prop_entry)
            # Register property URI if not seen before
            if prop_uri not in data["uriRegistry"]:
                 prop_label = next(g.objects(p, RDFS.label), None)
                 data["uriRegistry"][prop_uri] = {"type": "property", "label": str(prop_label) if prop_label else get_local_name(p)}

        data["individualDetails"][ind_id] = ind_obj
        data["uriRegistry"][ind_id] = {"type": "individual", "label": ind_obj["label"]}
        processed_individuals.add(ind_uri)

    # --- 3. Post-process: Set flags for children ---
    for class_id in data["classDetails"]:
        if class_id in data["subClassMap"] and data["subClassMap"][class_id]:
            data["classDetails"][class_id]["hasSubClasses"] = True
        if class_id in data["classInstanceMap"] and data["classInstanceMap"][class_id]:
            data["classDetails"][class_id]["hasInstances"] = True

    print(f"Processed {len(data['classDetails'])} classes and {len(data['individualDetails'])} individuals.")
    ontology_data_cache = data # Cache the processed data
    # Clear the graph object to free up memory after processing
    del g
    print("Graph object deleted from memory.")


# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Trigger loading if not already done
    if not ontology_data_cache and not ontology_load_error:
        load_and_process_ontology()
    # Pass potential load error to template
    return render_template('index.html', load_error=ontology_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry."""
    if ontology_load_error:
        return jsonify({"error": ontology_load_error}), 500
    if not ontology_data_cache:
        # Should have been loaded by index(), but handle race condition/direct access
        load_and_process_ontology()
        if ontology_load_error:
             return jsonify({"error": ontology_load_error}), 500
        if not ontology_data_cache: # Still nothing? Major issue.
             return jsonify({"error": "Ontology data could not be loaded or processed."}), 500

    # Return only necessary info for initial tree: top class IDs and registry
    top_class_details = [
        {
            "id": cid,
            "label": ontology_data_cache["classDetails"][cid]["label"],
            "hasSubClasses": ontology_data_cache["classDetails"][cid]["hasSubClasses"],
            "hasInstances": ontology_data_cache["classDetails"][cid]["hasInstances"]
        }
        for cid in ontology_data_cache["topClasses"]
        if cid in ontology_data_cache["classDetails"] # Ensure consistency
    ]
    # Sort top classes by label
    top_class_details.sort(key=lambda x: x["label"])

    return jsonify({
        "topClasses": top_class_details,
        "uriRegistry": ontology_data_cache["uriRegistry"]
    })

@app.route('/api/children/<path:node_uri>')
def get_children(node_uri):
    """Provides direct subclasses and instances for a given class URI."""
    # Decode the URI passed in the path
    class_id = unquote(node_uri)

    if ontology_load_error or not ontology_data_cache:
        return jsonify({"error": "Ontology data not available."}), 500
    if class_id not in ontology_data_cache["classDetails"]:
        abort(404, description="Class URI not found.")

    subclass_ids = ontology_data_cache["subClassMap"].get(class_id, [])
    instance_ids = ontology_data_cache["classInstanceMap"].get(class_id, [])

    subclass_details = [
        {
            "id": sid,
            "label": ontology_data_cache["classDetails"][sid]["label"],
            "hasSubClasses": ontology_data_cache["classDetails"][sid]["hasSubClasses"],
            "hasInstances": ontology_data_cache["classDetails"][sid]["hasInstances"]
        }
        for sid in subclass_ids if sid in ontology_data_cache["classDetails"]
    ]
    instance_details = [
        {
            "id": iid,
            "label": ontology_data_cache["individualDetails"][iid]["label"]
        }
        for iid in instance_ids if iid in ontology_data_cache["individualDetails"]
    ]

    # Sort results by label
    subclass_details.sort(key=lambda x: x["label"])
    instance_details.sort(key=lambda x: x["label"])

    return jsonify({
        "subClasses": subclass_details,
        "instances": instance_details
    })


@app.route('/api/details/<path:node_uri>')
def get_details(node_uri):
    """Provides full details for a specific class or individual URI."""
     # Decode the URI passed in the path
    item_id = unquote(node_uri)

    if ontology_load_error or not ontology_data_cache:
        return jsonify({"error": "Ontology data not available."}), 500

    details = None
    item_type = None

    if item_id in ontology_data_cache["classDetails"]:
        details = ontology_data_cache["classDetails"][item_id].copy() # Return a copy
        item_type = "class"
        # Add subclass/instance IDs for linking in details pane (if needed)
        details["subClasses"] = ontology_data_cache["subClassMap"].get(item_id, [])
        details["instances"] = ontology_data_cache["classInstanceMap"].get(item_id, [])

    elif item_id in ontology_data_cache["individualDetails"]:
        details = ontology_data_cache["individualDetails"][item_id].copy() # Return a copy
        item_type = "individual"
        # Properties are already part of the individualDetails structure

    if details:
        return jsonify({"type": item_type, "details": details})
    else:
        abort(404, description="Item URI not found.")


# Remove the old endpoint (optional, but good practice)
# @app.route('/api/ontology-data')
# def get_ontology_data_api():
#     """Provides the processed ontology data as JSON."""
#     data = load_and_process_ontology()
#     if "error" in data:
#         return jsonify(data), 500 # Internal Server Error
#     return jsonify(data)

# --- Main Execution ---
if __name__ == '__main__':
    # Load data on startup - crucial for performance with lazy loading API
    print("Initiating ontology load on startup...")
    load_and_process_ontology()
    if ontology_load_error:
        print(f"FATAL: Ontology loading failed: {ontology_load_error}")
        # Decide if the app should exit or run in a degraded state
        # For now, it will run but API calls will return errors.
    else:
        print("Ontology loaded successfully.")

    # Determine the port
    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}") # Added log

    # Use debug=False for production/deployment
    app.run(host='0.0.0.0', port=port_to_use, debug=False) 