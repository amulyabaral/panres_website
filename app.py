import os
import json
from flask import Flask, jsonify, render_template, url_for
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import urldefrag

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Ontology Loading and Caching ---
ontology_data_cache = None

def get_local_name(uri):
    """Extracts the local name from a URI."""
    if isinstance(uri, URIRef):
        uri_str = str(uri)
        fragment = urldefrag(uri_str)[1]
        if fragment:
            return fragment
        else:
            return uri_str.rsplit('/', 1)[-1]
    return str(uri) # Return as is if not a URIRef or cannot parse

def load_and_process_ontology():
    """Loads the OWL file, parses it, and structures the data."""
    global ontology_data_cache
    if ontology_data_cache:
        print("Using cached ontology data.")
        return ontology_data_cache

    print(f"Loading ontology from: {OWL_FILE_PATH}")
    if not os.path.exists(OWL_FILE_PATH):
        print(f"Error: Ontology file not found at {OWL_FILE_PATH}")
        # Return a minimal structure or raise an error
        return {
            "allClasses": [], "topClasses": [], "subClassMap": {}, "classDetails": {},
            "allIndividuals": [], "individualDetails": {}, "classInstanceMap": {},
            "uriRegistry": {}, "error": f"Ontology file '{OWL_FILENAME}' not found."
        }

    g = Graph()
    try:
        g.parse(OWL_FILE_PATH, format="xml") # Assuming RDF/XML format for .owl
        print(f"Ontology loaded successfully. Graph size: {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing ontology file: {e}")
        # Return a minimal structure or raise an error
        return {
            "allClasses": [], "topClasses": [], "subClassMap": {}, "classDetails": {},
            "allIndividuals": [], "individualDetails": {}, "classInstanceMap": {},
            "uriRegistry": {}, "error": f"Error parsing ontology file: {e}"
        }

    # Define namespaces used in queries (adjust if needed based on your OWL)
    # You might need to bind namespaces explicitly if they aren't standard
    # g.bind("yourprefix", Namespace("http://your.namespace.com#"))

    data = {
        "allClasses": [], "topClasses": [], "subClassMap": {}, "classDetails": {},
        "allIndividuals": [], "individualDetails": {}, "classInstanceMap": {},
        "uriRegistry": {}
    }

    # --- 1. Process Classes ---
    class_uris = set(g.subjects(RDF.type, OWL.Class))
    # Include subjects that have rdfs:subClassOf triples, even if not explicitly typed owl:Class
    class_uris.update(g.subjects(RDFS.subClassOf, None))
    # Include objects that are subclassed, even if not explicitly typed owl:Class
    class_uris.update(g.objects(None, RDFS.subClassOf))

    processed_classes = set()

    for class_uri in class_uris:
        # Skip blank nodes and built-in OWL/RDF/RDFS/XSD classes
        if not isinstance(class_uri, URIRef) or str(class_uri).startswith(str(OWL)) \
           or str(class_uri).startswith(str(RDF)) or str(class_uri).startswith(str(RDFS)) \
           or str(class_uri).startswith(str(XSD)):
            continue

        if class_uri in processed_classes:
            continue

        class_id = str(class_uri)
        label = next(g.objects(class_uri, RDFS.label), None)
        comment = next(g.objects(class_uri, RDFS.comment), None)

        class_obj = {
            "id": class_id,
            "name": get_local_name(class_uri),
            "label": str(label) if label else get_local_name(class_uri),
            "description": str(comment) if comment else "",
            "superClasses": [],
            "subClasses": [],
            "instances": [] # Will be populated later or during individual processing
        }

        # Get superclasses (direct rdfs:subClassOf links)
        for super_class_uri in g.objects(class_uri, RDFS.subClassOf):
             # Exclude owl:Thing, blank nodes, and self-references
            if isinstance(super_class_uri, URIRef) and super_class_uri != OWL.Thing and super_class_uri != class_uri:
                super_id = str(super_class_uri)
                class_obj["superClasses"].append(super_id)
                # Build subClassMap
                if super_id not in data["subClassMap"]:
                    data["subClassMap"][super_id] = []
                if class_id not in data["subClassMap"][super_id]:
                     data["subClassMap"][super_id].append(class_id)

        data["allClasses"].append(class_obj)
        data["classDetails"][class_id] = class_obj
        data["uriRegistry"][class_id] = {"type": "class", "label": class_obj["label"]}
        processed_classes.add(class_uri)

    # Determine top-level classes (those not subclass of any other processed class)
    all_subclass_ids = set(sub_id for subs in data["subClassMap"].values() for sub_id in subs)
    data["topClasses"] = [
        cls["id"] for cls in data["allClasses"]
        if cls["id"] not in all_subclass_ids and not cls["superClasses"] # Double check no superclasses recorded
    ]
    # Fallback if above logic fails (e.g., only owl:Thing as parent)
    if not data["topClasses"]:
         data["topClasses"] = [
             cls["id"] for cls in data["allClasses"]
             if not any(sup in data["classDetails"] for sup in cls["superClasses"])
         ]


    # Add subclasses to each class object using the map
    for class_id, class_obj in data["classDetails"].items():
        class_obj["subClasses"] = data["subClassMap"].get(class_id, [])

    # --- 2. Process Individuals ---
    individual_uris = set(g.subjects(RDF.type, OWL.NamedIndividual))
    # Also find subjects that have a type which is an OWL Class we know about
    for class_id in data["classDetails"]:
        individual_uris.update(g.subjects(RDF.type, URIRef(class_id)))

    processed_individuals = set()

    for ind_uri in individual_uris:
        if not isinstance(ind_uri, URIRef) or ind_uri in processed_individuals:
            continue

        ind_id = str(ind_uri)
        label = next(g.objects(ind_uri, RDFS.label), None)
        comment = next(g.objects(ind_uri, RDFS.comment), None)

        ind_obj = {
            "id": ind_id,
            "name": get_local_name(ind_uri),
            "label": str(label) if label else get_local_name(ind_uri),
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
                # Add to class object directly
                if data["classDetails"][type_id].get("instances") is not None:
                     if ind_id not in data["classDetails"][type_id]["instances"]:
                        data["classDetails"][type_id]["instances"].append(ind_id)

        # Get properties asserted on the individual
        for p, o in g.predicate_objects(ind_uri):
            prop_uri = str(p)
            # Skip types, labels, comments handled above, and core RDF/OWL/RDFS props
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
            else: # Blank node or other? Skip for simplicity
                continue

            ind_obj["properties"][prop_uri].append(prop_entry)
            # Register property URI if not seen before
            if prop_uri not in data["uriRegistry"]:
                 prop_label = next(g.objects(p, RDFS.label), None)
                 data["uriRegistry"][prop_uri] = {"type": "property", "label": str(prop_label) if prop_label else get_local_name(p)}


        data["allIndividuals"].append(ind_obj)
        data["individualDetails"][ind_id] = ind_obj
        data["uriRegistry"][ind_id] = {"type": "individual", "label": ind_obj["label"]}
        processed_individuals.add(ind_uri)

    print(f"Processed {len(data['allClasses'])} classes and {len(data['allIndividuals'])} individuals.")
    ontology_data_cache = data # Cache the processed data
    return data

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/api/ontology-data')
def get_ontology_data_api():
    """Provides the processed ontology data as JSON."""
    data = load_and_process_ontology()
    if "error" in data:
        return jsonify(data), 500 # Internal Server Error
    return jsonify(data)

# --- Main Execution ---
if __name__ == '__main__':
    # Load data on startup (optional, can be lazy-loaded on first request)
    load_and_process_ontology()
    # Use debug=True only for development
    # For production (like Render), Gunicorn will run the app
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False) 