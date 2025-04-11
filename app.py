import os
import gc
from flask import Flask, jsonify, render_template, abort
from rdflib import Graph, Namespace, Literal, URIRef, BNode
from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import unquote
from collections import defaultdict

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Ontology Graph ---
ontology_graph = None
ontology_load_error = None # Store potential loading errors

# Define common namespaces
ns_rdf = RDF
ns_rdfs = RDFS
ns_owl = OWL
ns_xsd = XSD
# Add your ontology's base namespace if known, otherwise rdflib handles prefixes
# Example: NS_BASE = Namespace("http://myonto.com/PanResOntology.owl#")

# --- Helper Functions ---
def get_label(uri, g):
    """Gets the RDFS label for a URI, falling back to local name."""
    label = g.value(uri, RDFS.label)
    if label:
        return str(label)
    # Fallback to local name extraction
    try:
        uri_str = str(uri)
        if '#' in uri_str:
            return uri_str.split('#')[-1]
        return uri_str.split('/')[-1]
    except:
        return str(uri) # Failsafe

def get_comment(uri, g):
    """Gets the RDFS comment for a URI."""
    comment = g.value(uri, RDFS.comment)
    return str(comment) if comment else ""

def has_children(uri, g, child_type='subclass'):
    """Checks if a class URI has direct subclasses or instances."""
    if child_type == 'subclass':
        # Check if anything is a subclass of this URI (excluding Thing and self)
        query = g.query(
            "ASK { ?subclass rdfs:subClassOf ?class . FILTER(?subclass != ?class && ?subclass != owl:Thing) }",
            initBindings={'class': uri, 'rdfs': RDFS, 'owl': OWL}
        )
        return bool(query)
    elif child_type == 'instance':
        # Check if anything has this URI as its rdf:type
        query = g.query(
            "ASK { ?instance rdf:type ?class . }",
            initBindings={'class': uri, 'rdf': RDF}
        )
        return bool(query)
    return False

def build_uri_registry(g):
    """Builds a basic registry of known URIs and their types/labels."""
    registry = {}
    # Classes
    for class_uri in g.subjects(RDF.type, OWL.Class):
        if isinstance(class_uri, URIRef):
             # Basic filtering of built-in OWL/RDF/RDFS/XSD classes
            uri_str = str(class_uri)
            if not any(uri_str.startswith(str(ns)) for ns in [OWL, RDF, RDFS, XSD]):
                registry[uri_str] = {"type": "class", "label": get_label(class_uri, g)}
    # Individuals
    for ind_uri in g.subjects(RDF.type, OWL.NamedIndividual):
         if isinstance(ind_uri, URIRef):
            registry[str(ind_uri)] = {"type": "individual", "label": get_label(ind_uri, g)}
    # Also add individuals typed by specific classes if not caught by NamedIndividual
    for s, o in g.subject_objects(RDF.type):
        if isinstance(s, URIRef) and isinstance(o, URIRef) and str(o) in registry and registry[str(o)]['type'] == 'class':
             if str(s) not in registry:
                 registry[str(s)] = {"type": "individual", "label": get_label(s, g)}
    # Properties (Object, Datatype, Annotation)
    for prop_type in [OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty]:
        for prop_uri in g.subjects(RDF.type, prop_type):
            if isinstance(prop_uri, URIRef):
                registry[str(prop_uri)] = {"type": "property", "label": get_label(prop_uri, g)}
    return registry


# --- Ontology Loading Function ---
def load_ontology_graph():
    """Loads the OWL file into the global graph object."""
    global ontology_graph, ontology_load_error
    print(f"Starting ontology loading from: {OWL_FILE_PATH}")
    if not os.path.exists(OWL_FILE_PATH):
        ontology_load_error = f"Error: Ontology file not found at {OWL_FILE_PATH}"
        print(ontology_load_error)
        return

    g = Graph()
    try:
        print("Parsing OWL file...")
        g.parse(OWL_FILE_PATH) # rdflib detects format based on extension or content
        print(f"Ontology loaded successfully. Graph size: {len(g)} triples.")
        ontology_graph = g
        # Clear graph from memory after parsing if rdflib keeps internal copies (depends on store)
        # del g
        # gc.collect()
    except Exception as e:
        ontology_load_error = f"Error parsing ontology file: {e}"
        print(ontology_load_error)
        ontology_graph = None # Ensure graph is None on error
        # del g # Clean up graph object if parsing failed midway
        # gc.collect()

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Pass potential load error to template
    return render_template('index.html', load_error=ontology_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry by querying the graph."""
    if ontology_load_error:
        # This handles errors during the initial load
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500
    if not ontology_graph:
        return jsonify({"error": "Ontology graph not loaded or unavailable."}), 500

    try: # Add try block here
        g = ontology_graph
        top_classes = []
        processed_classes = set() # Keep track of classes with known parents

        # Find all classes that are subclasses of something
        for s, o in g.subject_objects(RDFS.subClassOf):
            if isinstance(s, URIRef) and isinstance(o, URIRef) and o != OWL.Thing:
                 # Basic filtering of built-in OWL/RDF/RDFS/XSD classes
                uri_str = str(s)
                if not any(uri_str.startswith(str(ns)) for ns in [OWL, RDF, RDFS, XSD]):
                    processed_classes.add(s)

        # Find all defined classes
        all_classes = set()
        for class_uri in g.subjects(RDF.type, OWL.Class):
             if isinstance(class_uri, URIRef):
                uri_str = str(class_uri)
                # Exclude built-ins and Thing
                if not any(uri_str.startswith(str(ns)) for ns in [OWL, RDF, RDFS, XSD]) and class_uri != OWL.Thing:
                     all_classes.add(class_uri)

        # Top classes are those defined but not found as subclasses of others (excluding Thing)
        top_class_uris = all_classes - processed_classes

        # Also consider classes that only subclass owl:Thing as top-level
        for s, o in g.subject_objects(RDFS.subClassOf):
             if isinstance(s, URIRef) and o == OWL.Thing and s not in processed_classes:
                 uri_str = str(s)
                 if not any(uri_str.startswith(str(ns)) for ns in [OWL, RDF, RDFS, XSD]):
                     top_class_uris.add(s)


        for class_uri in top_class_uris:
            class_id = str(class_uri)
            top_classes.append({
                "id": class_id,
                "label": get_label(class_uri, g),
                "hasSubClasses": has_children(class_uri, g, 'subclass'),
                "hasInstances": has_children(class_uri, g, 'instance')
            })

        top_classes.sort(key=lambda x: x["label"])

        # Build registry on the fly (can be slow for large ontologies)
        # Consider caching this registry if performance is an issue
        uri_registry = build_uri_registry(g)

        return jsonify({
            "topClasses": top_classes,
            "uriRegistry": uri_registry
        })

    except Exception as e: # Catch any exception during processing
        # Log the error server-side for debugging
        app.logger.error(f"Error processing /api/hierarchy: {e}", exc_info=True)
        # Return a JSON error to the client
        return jsonify({"error": f"Internal server error processing hierarchy: {e}"}), 500


@app.route('/api/children/<path:node_uri>')
def get_children(node_uri):
    """Provides direct subclasses and instances for a given class URI by querying the graph."""
    class_uri = URIRef(unquote(node_uri)) # Assume node_uri is a full URI

    if ontology_load_error:
        return jsonify({"error": ontology_load_error}), 500
    if not ontology_graph:
        return jsonify({"error": "Ontology graph not loaded."}), 500

    g = ontology_graph
    subclass_details = []
    instance_details = []

    # Find direct subclasses
    for sub_uri in g.subjects(RDFS.subClassOf, class_uri):
        # Ensure it's a URI, not a blank node, and not the class itself or Thing
        if isinstance(sub_uri, URIRef) and sub_uri != class_uri and sub_uri != OWL.Thing:
            # Avoid adding subclasses that are only defined via complex restrictions unless explicitly typed as owl:Class
            # This check might be too strict depending on the ontology style
            # if (sub_uri, RDF.type, OWL.Class) in g:
            subclass_details.append({
                "id": str(sub_uri),
                "label": get_label(sub_uri, g),
                "hasSubClasses": has_children(sub_uri, g, 'subclass'),
                "hasInstances": has_children(sub_uri, g, 'instance')
            })

    # Find direct instances
    for inst_uri in g.subjects(RDF.type, class_uri):
        # Ensure it's a named individual (URIRef)
        if isinstance(inst_uri, URIRef):
             # Check if it's explicitly an OWL NamedIndividual or just typed
             # if (inst_uri, RDF.type, OWL.NamedIndividual) in g: # Be stricter if needed
             instance_details.append({
                 "id": str(inst_uri),
                 "label": get_label(inst_uri, g)
             })

    subclass_details.sort(key=lambda x: x["label"])
    instance_details.sort(key=lambda x: x["label"])

    return jsonify({
        "subClasses": subclass_details,
        "instances": instance_details
    })


@app.route('/api/details/<path:node_uri>')
def get_details(node_uri):
    """Provides full details for a specific class or individual URI by querying the graph."""
    item_uri = URIRef(unquote(node_uri))

    if ontology_load_error:
        return jsonify({"error": ontology_load_error}), 500
    if not ontology_graph:
        return jsonify({"error": "Ontology graph not loaded."}), 500

    g = ontology_graph
    details = None
    item_type = None

    # Check if it's a class
    if (item_uri, RDF.type, OWL.Class) in g:
        item_type = "class"
        details = {
            "id": str(item_uri),
            "label": get_label(item_uri, g),
            "description": get_comment(item_uri, g),
            "superClasses": [],
            "subClasses": [],
            "instances": []
        }
        # Get superclasses (direct parents)
        for super_uri in g.objects(item_uri, RDFS.subClassOf):
            if isinstance(super_uri, URIRef) and super_uri != OWL.Thing:
                details["superClasses"].append(str(super_uri))
        # Get subclasses (direct children)
        for sub_uri in g.subjects(RDFS.subClassOf, item_uri):
             if isinstance(sub_uri, URIRef) and sub_uri != item_uri and sub_uri != OWL.Thing:
                 details["subClasses"].append(str(sub_uri))
        # Get instances (direct members)
        for inst_uri in g.subjects(RDF.type, item_uri):
             if isinstance(inst_uri, URIRef):
                 details["instances"].append(str(inst_uri))

    # Check if it's an individual (NamedIndividual or just having a class type)
    elif (item_uri, RDF.type, OWL.NamedIndividual) in g or \
         any(isinstance(t, URIRef) and (t, RDF.type, OWL.Class) in g for t in g.objects(item_uri, RDF.type)):
        item_type = "individual"
        details = {
            "id": str(item_uri),
            "label": get_label(item_uri, g),
            "description": get_comment(item_uri, g),
            "types": [],
            "properties": defaultdict(list) # { propUri: [{ type: 'uri'/'literal', value: '...', datatype: '...' }] }
        }
        # Get types (classes)
        for type_uri in g.objects(item_uri, RDF.type):
            if isinstance(type_uri, URIRef) and type_uri != OWL.NamedIndividual:
                 # Optionally check if the type_uri is actually an owl:Class
                 # if (type_uri, RDF.type, OWL.Class) in g:
                 details["types"].append(str(type_uri))

        # Get properties asserted on the individual
        for p, o in g.predicate_objects(item_uri):
            prop_uri = str(p)
            # Skip basic schema properties already handled
            if p in [RDF.type, RDFS.label, RDFS.comment]:
                continue
            # Skip other RDF/RDFS/OWL schema properties if desired
            # if str(p).startswith(str(RDF)) or str(p).startswith(str(RDFS)) or str(p).startswith(str(OWL)):
            #    continue

            prop_entry = {}
            if isinstance(o, URIRef):
                prop_entry["type"] = "uri"
                prop_entry["value"] = str(o)
            elif isinstance(o, Literal):
                prop_entry["type"] = "literal"
                prop_entry["value"] = str(o)
                prop_entry["datatype"] = str(o.datatype) if o.datatype else None
            else: # Skip blank nodes or other types
                continue

            details["properties"][prop_uri].append(prop_entry)

    if details:
        return jsonify({"type": item_type, "details": details})
    else:
         # Check if the URI exists at all in the graph before 404'ing
         if (item_uri, None, None) in g or (None, None, item_uri) in g or (None, item_uri, None) in g:
             # URI exists but isn't recognized as a class or individual we handle
             return jsonify({"error": f"Item <{node_uri}> found but type (Class/Individual) could not be determined or is not supported."}), 404
         else:
             abort(404, description=f"Item URI <{node_uri}> not found in the ontology graph.")


# --- Main Execution ---
if __name__ == '__main__':
    # Load graph on startup
    print("Loading ontology graph on startup...")
    load_ontology_graph() # This populates ontology_graph or ontology_load_error

    if ontology_load_error:
        print(f"WARNING: Ontology loading failed: {ontology_load_error}")
        print("Flask app will run, but API calls will likely return errors.")
    elif ontology_graph:
        print("Ontology graph loaded successfully.")
    else:
         print("WARNING: Ontology graph variable is unexpectedly None after loading attempt.")


    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use debug=False for production/deployment
    # Use threaded=True if rdflib store is thread-safe (default Memory store usually is)
    # Use processes > 1 if store supports multiprocessing (less common for in-memory)
    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 