import os
import gc
from flask import Flask, jsonify, render_template, abort
import xml.etree.ElementTree as ET # Use ElementTree for XML parsing
from urllib.parse import unquote
from collections import defaultdict
import logging # Use logging for errors

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.logger.setLevel(logging.INFO) # Set logging level

# --- Ontology Data Structures (Populated by load_ontology) ---
ontology_root = None # Stores the parsed XML root element
ontology_load_error = None # Store potential loading errors

# Pre-computed lookups for efficiency
uri_registry = {} # {uri: {"label": "...", "type": "class/individual/property"}}
class_details = defaultdict(lambda: {
    "label": "", "description": "", "superClasses": set(), "subClasses": set(), "instances": set()
}) # {class_uri: {details...}}
individual_details = defaultdict(lambda: {
    "label": "", "description": "", "types": set(), "properties": defaultdict(list)
}) # {individual_uri: {details...}}

# Define common namespaces used in OWL/RDF XML
NS = {
    'rdf': "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    'rdfs': "http://www.w3.org/2000/01/rdf-schema#",
    'owl': "http://www.w3.org/2002/07/owl#",
    'xsd': "http://www.w3.org/2001/XMLSchema#",
    # Add the default namespace if your OWL file uses one (check the rdf:RDF tag's xmlns attribute)
    # Example: '': "http://myonto.com/PanResOntology.owl#"
    # If the default namespace is defined, prefix tags like 'Class' with it in findall, e.g., '{http://myonto.com/PanResOntology.owl#}Class'
    # Or handle it by checking tag names directly if ET doesn't support default NS well in findall.
    # For now, assuming prefixes are used explicitly or no default namespace relevant here.
}

# --- Helper Functions ---

def _get_uri_from_elem(elem):
    """Gets the rdf:about or rdf:resource URI from an element."""
    if elem is None:
        return None
    uri = elem.get(f"{{{NS['rdf']}}}about")
    if uri is None:
        uri = elem.get(f"{{{NS['rdf']}}}resource")
    return uri

def _get_text_from_elem(elem, child_tag):
    """Safely gets text content from a specific child element."""
    if elem is None: return ""
    child = elem.find(child_tag, NS)
    return child.text.strip() if child is not None and child.text else ""

def get_label(uri):
    """Gets the label from the pre-built registry, falling back to local name."""
    if uri in uri_registry:
        return uri_registry[uri].get("label", "") or _local_name(uri) # Use label if present, else local name
    return _local_name(uri) # Fallback if URI not in registry

def _local_name(uri):
    """Extracts the local name part of a URI."""
    if not uri: return ""
    try:
        if '#' in uri:
            return uri.split('#')[-1]
        return uri.split('/')[-1]
    except:
        return str(uri) # Failsafe

# --- Ontology Loading and Pre-processing ---
def load_ontology():
    """Loads the OWL file using ElementTree and builds lookup structures."""
    global ontology_root, ontology_load_error
    global uri_registry, class_details, individual_details
    app.logger.info(f"Starting ontology loading from: {OWL_FILE_PATH}")

    if not os.path.exists(OWL_FILE_PATH):
        ontology_load_error = f"Error: Ontology file not found at {OWL_FILE_PATH}"
        app.logger.error(ontology_load_error)
        return

    try:
        app.logger.info("Parsing OWL file with ElementTree...")
        tree = ET.parse(OWL_FILE_PATH)
        ontology_root = tree.getroot()
        app.logger.info(f"Ontology XML parsed successfully.")

        app.logger.info("Building lookup structures...")
        # --- Pass 1: Identify all entities and basic info ---
        for elem in ontology_root.iter():
            uri = _get_uri_from_elem(elem)
            if not uri: continue # Skip elements without a direct URI identifier

            label = _get_text_from_elem(elem, 'rdfs:label') or _local_name(uri)
            description = _get_text_from_elem(elem, 'rdfs:comment')

            # Identify Classes
            if elem.tag == f"{{{NS['owl']}}}Class":
                uri_registry[uri] = {"label": label, "type": "class"}
                class_details[uri]["label"] = label
                class_details[uri]["description"] = description

            # Identify Individuals
            elif elem.tag == f"{{{NS['owl']}}}NamedIndividual":
                uri_registry[uri] = {"label": label, "type": "individual"}
                individual_details[uri]["label"] = label
                individual_details[uri]["description"] = description
                # Extract properties asserted directly on the individual
                for prop_elem in elem:
                    prop_uri = prop_elem.tag # Full URI like {http://namespace#}propertyName
                    if prop_uri.startswith(f"{{{NS['rdf']}}}") or prop_uri.startswith(f"{{{NS['rdfs']}}}") or prop_uri.startswith(f"{{{NS['owl']}}}"):
                         # Skip rdf:type, rdfs:label, rdfs:comment handled elsewhere
                         if prop_uri not in [f"{{{NS['rdf']}}}type", f"{{{NS['rdfs']}}}label", f"{{{NS['rdfs']}}}comment"]:
                             continue # Skip other schema properties if needed

                    prop_entry = {}
                    obj_uri = _get_uri_from_elem(prop_elem)
                    if obj_uri: # Object Property
                        prop_entry["type"] = "uri"
                        prop_entry["value"] = obj_uri
                    elif prop_elem.text: # Datatype or Annotation Property
                        prop_entry["type"] = "literal"
                        prop_entry["value"] = prop_elem.text.strip()
                        prop_entry["datatype"] = prop_elem.get(f"{{{NS['rdf']}}}datatype")
                    else:
                        continue # Skip if no value found

                    individual_details[uri]["properties"][prop_uri].append(prop_entry)


            # Identify Properties (Object, Datatype, Annotation)
            elif elem.tag in [f"{{{NS['owl']}}}ObjectProperty", f"{{{NS['owl']}}}DatatypeProperty", f"{{{NS['owl']}}}AnnotationProperty"]:
                 # Only add to registry if not already added (e.g., as a class/individual by mistake)
                 if uri not in uri_registry:
                     uri_registry[uri] = {"label": label, "type": "property"}


        # --- Pass 2: Process relationships ---
        all_subclasses = set() # Keep track of URIs that are subclasses of something specific
        for elem in ontology_root.iter():
            subj_uri = _get_uri_from_elem(elem)
            if not subj_uri: continue

            # Process SubClassOf
            for sub_class_elem in elem.findall('rdfs:subClassOf', NS):
                parent_uri = _get_uri_from_elem(sub_class_elem)
                if parent_uri and subj_uri != parent_uri and parent_uri != f"{{{NS['owl']}}}Thing":
                    # Ensure both are known classes before adding relationship
                    if subj_uri in class_details and parent_uri in class_details:
                        class_details[subj_uri]["superClasses"].add(parent_uri)
                        class_details[parent_uri]["subClasses"].add(subj_uri)
                        all_subclasses.add(subj_uri) # Mark this as having a parent

            # Process rdf:type (Instance Of)
            for type_elem in elem.findall('rdf:type', NS):
                class_uri = _get_uri_from_elem(type_elem)
                # Link instance to class if both are known and class_uri isn't owl:NamedIndividual
                if class_uri and subj_uri in individual_details and class_uri in class_details \
                   and class_uri != f"{{{NS['owl']}}}NamedIndividual":
                    individual_details[subj_uri]["types"].add(class_uri)
                    class_details[class_uri]["instances"].add(subj_uri)

        # Identify top-level classes (those defined but not subclasses of other defined classes)
        # This logic might need refinement based on exact OWL structure (e.g., handling owl:Thing explicitly)
        defined_classes = set(class_details.keys())
        top_level_uris = defined_classes - all_subclasses
        # Add classes whose only parent is owl:Thing? Check class_details[uri]['superClasses'] if needed.

        # Store top-level URIs for the hierarchy endpoint (can be done here or in the endpoint)
        # For simplicity, we'll filter in the endpoint using the computed details.

        app.logger.info(f"Lookup structures built. Registry size: {len(uri_registry)}, Classes: {len(class_details)}, Individuals: {len(individual_details)}")
        # Clear the XML tree from memory if possible (depends on if needed later)
        # ontology_root = None
        # gc.collect()

    except ET.ParseError as e:
        ontology_load_error = f"Error parsing ontology XML: {e}"
        app.logger.error(ontology_load_error)
        ontology_root = None
    except Exception as e:
        ontology_load_error = f"An unexpected error occurred during ontology loading: {e}"
        app.logger.error(ontology_load_error, exc_info=True)
        ontology_root = None
        # Clean up potentially partially filled structures
        uri_registry.clear()
        class_details.clear()
        individual_details.clear()
        gc.collect()


# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html', load_error=ontology_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry using pre-computed data."""
    if ontology_load_error:
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500
    if not class_details and not individual_details: # Check if structures are empty
         return jsonify({"error": "Ontology data structures not loaded or empty."}), 500

    try:
        top_classes_data = []
        all_subclass_uris = set()
        for details in class_details.values():
            all_subclass_uris.update(details["subClasses"])

        # Determine top-level: defined classes that are not subclasses of *other defined classes*
        # (This excludes being a subclass of owl:Thing if owl:Thing isn't in class_details)
        top_level_uris = set(class_details.keys()) - all_subclass_uris

        # Alternative: A class is top-level if its only superclass (if any) is owl:Thing
        # top_level_uris = set()
        # owl_thing_uri = f"{{{NS['owl']}}}Thing"
        # for uri, details in class_details.items():
        #     parents = details['superClasses']
        #     if not parents or parents == {owl_thing_uri}:
        #          top_level_uris.add(uri)


        for uri in sorted(list(top_level_uris), key=get_label):
            details = class_details[uri]
            top_classes_data.append({
                "id": uri,
                "label": get_label(uri), # Use helper to get label
                "hasSubClasses": bool(details["subClasses"]),
                "hasInstances": bool(details["instances"])
            })

        # Return the pre-built registry along with top classes
        return jsonify({
            "topClasses": top_classes_data,
            "uriRegistry": uri_registry # Send the whole registry
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/hierarchy: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing hierarchy: {e}"}), 500


@app.route('/api/children/<path:node_uri>')
def get_children(node_uri_encoded):
    """Provides direct subclasses and instances using pre-computed data."""
    class_uri = unquote(node_uri_encoded)

    if ontology_load_error:
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500
    if class_uri not in class_details:
         # Check if it's an individual or unknown
         if class_uri in individual_details:
              return jsonify({"subClasses": [], "instances": []}) # Individuals have no ontology children
         else:
              return jsonify({"error": f"Class URI <{class_uri}> not found."}), 404

    try:
        details = class_details[class_uri]
        subclass_data = []
        instance_data = []

        # Get subclasses
        for sub_uri in sorted(list(details["subClasses"]), key=get_label):
            if sub_uri in class_details: # Ensure subclass exists in details
                 sub_details = class_details[sub_uri]
                 subclass_data.append({
                     "id": sub_uri,
                     "label": get_label(sub_uri),
                     "hasSubClasses": bool(sub_details["subClasses"]),
                     "hasInstances": bool(sub_details["instances"])
                 })

        # Get instances
        for inst_uri in sorted(list(details["instances"]), key=get_label):
             if inst_uri in individual_details: # Ensure instance exists
                 instance_data.append({
                     "id": inst_uri,
                     "label": get_label(inst_uri)
                 })

        return jsonify({
            "subClasses": subclass_data,
            "instances": instance_data
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/children for {class_uri}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing children for {class_uri}: {e}"}), 500


@app.route('/api/details/<path:node_uri>')
def get_details(node_uri_encoded):
    """Provides full details for a specific URI using pre-computed data."""
    item_uri = unquote(node_uri_encoded)

    if ontology_load_error:
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500

    try:
        details_data = None
        item_type = None

        if item_uri in class_details:
            item_type = "class"
            details = class_details[item_uri]
            # Convert sets to sorted lists for JSON serialization
            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details["description"],
                "superClasses": sorted(list(details["superClasses"]), key=get_label),
                "subClasses": sorted(list(details["subClasses"]), key=get_label),
                "instances": sorted(list(details["instances"]), key=get_label)
            }
        elif item_uri in individual_details:
            item_type = "individual"
            details = individual_details[item_uri]
            # Convert sets/defaultdicts for JSON
            # Sort properties by label for consistent display
            sorted_properties = {
                prop_uri: values
                for prop_uri, values in sorted(
                    details["properties"].items(), key=lambda item: get_label(item[0])
                )
            }
            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details["description"],
                "types": sorted(list(details["types"]), key=get_label),
                "properties": sorted_properties # Already sorted dict
            }

        if details_data:
            return jsonify({"type": item_type, "details": details_data})
        else:
            # Check registry for other types or if URI is completely unknown
            if item_uri in uri_registry:
                 return jsonify({"error": f"Item <{item_uri}> found but type (Class/Individual) could not be determined or is not supported for details view."}), 404
            else:
                 abort(404, description=f"Item URI <{item_uri}> not found in the ontology data.")

    except Exception as e:
        app.logger.error(f"Error processing /api/details for {item_uri}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing details for {item_uri}: {e}"}), 500


# --- Main Execution ---
if __name__ == '__main__':
    print("Loading ontology data on startup...")
    load_ontology() # This populates the lookup structures or ontology_load_error

    if ontology_load_error:
        print(f"WARNING: Ontology loading failed: {ontology_load_error}")
        print("Flask app will run, but API calls will likely return errors.")
    elif not uri_registry:
         print("WARNING: Ontology loaded but registry is empty. Check OWL file content and parsing logic.")
    else:
        print("Ontology data loaded and pre-processed successfully.")


    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use debug=False for production/deployment
    # threaded=True is generally safe for Flask apps not sharing complex state across requests
    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 