import os
import gc
from flask import Flask, jsonify, render_template, abort
import json # Import the json library
from urllib.parse import unquote
from collections import defaultdict
import logging # Use logging for errors

# --- Configuration ---
# OWL_FILENAME = "panres_v2.owl" # Old config
# OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME) # Old config
JSONLD_FILENAME = "panres_v2.jsonld" # New: Use the JSON-LD file
JSONLD_FILE_PATH = os.path.join(os.path.dirname(__file__), JSONLD_FILENAME) # New path

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
# app.logger.setLevel(logging.INFO) # Set logging level
app.logger.setLevel(logging.DEBUG) # Change to DEBUG to see detailed parsing logs

# --- Ontology Data Structures (Populated by load_ontology) ---
# ontology_root = None # No longer needed (was for XML tree)
ontology_load_error = None # Store potential loading errors

# Pre-computed lookups for efficiency
uri_registry = {} # {uri: {"label": "...", "type": "class/individual/property"}}
class_details = defaultdict(lambda: {
    "label": "", "description": "", "superClasses": set(), "subClasses": set(), "instances": set()
}) # {class_uri: {details...}}
individual_details = defaultdict(lambda: {
    "label": "", "description": "", "types": set(), "properties": defaultdict(list)
}) # {individual_uri: {details...}}

# Define common namespaces used for resolving keys in JSON-LD if needed
# These might differ slightly from XML namespaces but represent the same URIs
# We'll primarily use full URIs found in the JSON-LD keys/values
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"
# Base namespace might be implicit in the JSON-LD context or used in full URIs
BASE_NS = "http://myonto.com/PanResOntology.owl#"

# --- Helper Functions ---

# _get_uri_from_elem is no longer needed as we parse JSON

# _get_text_from_elem is no longer needed

def _extract_jsonld_value(value_obj):
    """Extracts the primary value from a JSON-LD value object or list.
       Handles {"@value": ...}, {"@id": ...}, or lists of these.
       Returns the first value found, prioritizing @value.
    """
    if isinstance(value_obj, list):
        if not value_obj: return None
        # Prioritize the first item in the list
        item = value_obj[0]
    elif isinstance(value_obj, dict):
        item = value_obj
    else: # Handle direct string literals or simple values if they occur
        return str(value_obj)

    if isinstance(item, dict):
        if "@value" in item:
            return str(item["@value"])
        if "@id" in item:
            return item["@id"] # Return URI for links
    return None # Or raise error?

def _extract_jsonld_id(value_obj):
    """Extracts the @id URI from a JSON-LD value object or list."""
    if isinstance(value_obj, list):
        if not value_obj: return None
        item = value_obj[0] # Get first item
    elif isinstance(value_obj, dict):
        item = value_obj
    else:
        return None # Not a link object

    if isinstance(item, dict) and "@id" in item:
        return item["@id"]
    return None

def _extract_jsonld_literal_details(value_obj):
    """Extracts details (value, type, lang) from a JSON-LD literal object."""
    details = {"value": None, "datatype": None, "lang": None}
    if isinstance(value_obj, list):
        if not value_obj: return details
        item = value_obj[0] # Get first item
    elif isinstance(value_obj, dict):
        item = value_obj
    else: # Simple literal string
        details["value"] = str(value_obj)
        return details

    if isinstance(item, dict):
        if "@value" in item:
            details["value"] = str(item["@value"])
        if "@type" in item:
            details["datatype"] = item["@type"]
        if "@language" in item:
            details["lang"] = item["@language"]

    return details


def get_label(uri):
    """Gets the label from the pre-built registry, falling back to local name."""
    # This function remains largely the same, relying on uri_registry
    if uri in uri_registry:
        return uri_registry[uri].get("label", "") or _local_name(uri)
    return _local_name(uri)

def _local_name(uri):
    """Extracts the local name part of a URI (fragment or last path segment)."""
    # This function remains the same
    if not uri: return ""
    try:
        if '#' in uri:
            return uri.split('#')[-1]
        return uri.split('/')[-1]
    except Exception as e:
        app.logger.warning(f"Could not extract local name from URI '{uri}': {e}")
        return str(uri)

# --- Ontology Loading and Pre-processing ---
def load_ontology():
    """Loads the ontology from the JSON-LD file and builds lookup structures."""
    global ontology_load_error
    global uri_registry, class_details, individual_details
    # Clear previous data
    uri_registry.clear()
    class_details.clear()
    individual_details.clear()
    ontology_load_error = None
    gc.collect()

    app.logger.info(f"Starting ontology loading from: {JSONLD_FILE_PATH}")

    if not os.path.exists(JSONLD_FILE_PATH):
        ontology_load_error = f"Error: Ontology JSON-LD file not found at {JSONLD_FILE_PATH}"
        app.logger.error(ontology_load_error)
        return

    try:
        app.logger.info("Parsing JSON-LD file...")
        with open(JSONLD_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        app.logger.info("JSON-LD file parsed successfully.")

        # The JSON-LD might be a list of nodes or a dict with "@graph"
        nodes = []
        if isinstance(data, list):
            nodes = data
        elif isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
            nodes = data["@graph"]
        else:
            raise ValueError("Unexpected JSON-LD structure. Expected a list of nodes or a {'@graph': [...]} object.")

        app.logger.info(f"Processing {len(nodes)} nodes from JSON-LD graph...")
        processed_count = 0 # Add counter
        top_level_uris = set() # Initialize here

        # --- Pass 1: Identify all entities and basic info ---
        for node in nodes:
            if "@id" not in node:
                # Skip blank nodes or nodes without an identifier for now
                # app.logger.debug(f"Skipping node without '@id': {node.get('@type', 'No type')}")
                continue

            uri = node["@id"]
            node_types = node.get("@type", [])
            if not isinstance(node_types, list): # Ensure type is a list
                 node_types = [node_types]

            # --- Add detailed logging here ---
            app.logger.debug(f"Processing node: URI='{uri}', Types='{node_types}'")
            # --- End detailed logging ---

            # Extract label and description (handle potential list format)
            label_val = _extract_jsonld_value(node.get(RDFS + "label")) or _local_name(uri)
            desc_val = _extract_jsonld_value(node.get(RDFS + "comment", ""))

            entity_type = None # Determine primary type (class, individual, property)

            # Identify Classes
            if OWL + "Class" in node_types:
                entity_type = "class"
                uri_registry[uri] = {"label": label_val, "type": "class"}
                class_details[uri]["label"] = label_val
                class_details[uri]["description"] = desc_val
                processed_count += 1 # Increment counter
                app.logger.debug(f"  -> Identified as Class: {label_val}") # Log identification

            # Identify Individuals (NamedIndividual)
            elif OWL + "NamedIndividual" in node_types:
                entity_type = "individual"
                uri_registry[uri] = {"label": label_val, "type": "individual"}
                individual_details[uri]["label"] = label_val
                individual_details[uri]["description"] = desc_val
                processed_count += 1 # Increment counter
                app.logger.debug(f"  -> Identified as Individual: {label_val}") # Log identification

            # Identify Properties
            elif any(pt in node_types for pt in [OWL + "ObjectProperty", OWL + "DatatypeProperty", OWL + "AnnotationProperty"]):
                 entity_type = "property"
                 # Only add to registry if not already added (e.g., if something is both Class and Property?)
                 if uri not in uri_registry:
                     uri_registry[uri] = {"label": label_val, "type": "property"}
                     processed_count += 1 # Increment counter - count properties added to registry
                     app.logger.debug(f"  -> Identified as Property: {label_val}") # Log identification
                 # Could potentially store domain/range here if needed later
            else:
                 # Log nodes that weren't classified
                 app.logger.debug(f"  -> Node not classified as Class, Individual, or Property.")


            # --- Process relationships and properties within this pass ---
            # (JSON-LD structure often allows processing in one pass)

            # If it's a Class, find superclasses
            if entity_type == "class":
                super_classes = node.get(RDFS + "subClassOf", [])
                if not isinstance(super_classes, list): super_classes = [super_classes]
                for parent_obj in super_classes:
                    parent_uri = _extract_jsonld_id(parent_obj)
                    if parent_uri and parent_uri != OWL + "Thing": # Ignore owl:Thing links for hierarchy
                        class_details[uri]["superClasses"].add(parent_uri)
                        # We'll build subClasses later or in a second pass

            # If it's an Individual, find its types and properties
            if entity_type == "individual":
                # Types
                types = node.get(RDF + "type", [])
                if not isinstance(types, list): types = [types]
                for type_obj in types:
                    class_uri = _extract_jsonld_id(type_obj)
                    # Link instance to class if class_uri is valid and not owl:NamedIndividual/owl:Thing
                    if class_uri and class_uri not in [OWL + "NamedIndividual", OWL + "Thing"]:
                        individual_details[uri]["types"].add(class_uri)
                        # We'll add to class_details[class_uri]["instances"] later

                # Properties
                for prop_uri, values in node.items():
                    # Skip JSON-LD keywords and known handled properties
                    if prop_uri.startswith("@") or prop_uri in [RDF + "type", RDFS + "label", RDFS + "comment"]:
                        continue

                    # Ensure values is a list for consistent processing
                    if not isinstance(values, list): values = [values]

                    for value_obj in values:
                        prop_entry = {}
                        target_id = _extract_jsonld_id(value_obj) # Check if it's a link {"@id": ...}

                        if target_id: # Object Property Assertion
                            prop_entry["type"] = "uri"
                            prop_entry["value"] = target_id
                        else: # Datatype or Annotation Property Assertion
                            literal_details = _extract_jsonld_literal_details(value_obj)
                            if literal_details["value"] is not None:
                                prop_entry["type"] = "literal"
                                prop_entry["value"] = literal_details["value"]
                                prop_entry["datatype"] = literal_details["datatype"]
                                # prop_entry["lang"] = literal_details["lang"] # Could store lang if needed
                            else:
                                # Skip if we couldn't extract a value
                                continue

                        individual_details[uri]["properties"][prop_uri].append(prop_entry)


        # --- Pass 2: Build reverse relationships (subClasses, instances) ---
        app.logger.info("Building reverse relationships (subClasses, instances)...")
        all_subclasses = set()
        for class_uri, details in class_details.items():
            for parent_uri in details["superClasses"]:
                if parent_uri in class_details: # Ensure parent exists in our map
                    class_details[parent_uri]["subClasses"].add(class_uri)
                    all_subclasses.add(class_uri) # Mark as a subclass

        for ind_uri, details in individual_details.items():
            for class_uri in details["types"]:
                if class_uri in class_details: # Ensure class exists
                    class_details[class_uri]["instances"].add(ind_uri)

        # Identify top-level classes (defined classes not in all_subclasses)
        defined_classes = set(class_details.keys())
        top_level_uris = defined_classes - all_subclasses # Calculate here
        # Refinement: Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = OWL + "Thing"
        for uri, details in class_details.items():
            parents = details['superClasses']
            # If no parents OR only owl:Thing parent (which we ignore anyway), it's top-level
            if not parents or parents == {owl_thing_uri}:
                 top_level_uris.add(uri)


        # Add a summary log after processing
        app.logger.info(f"Finished processing nodes. Identified {processed_count} entities (Classes/Individuals/Properties added to registry).")
        app.logger.info(f"Final Lookup structures: Registry size: {len(uri_registry)}, Classes: {len(class_details)}, Individuals: {len(individual_details)}")
        app.logger.info(f"Identified {len(top_level_uris)} top-level classes.")

        # No XML tree to clear, Python's GC will handle the loaded JSON data when done.
        gc.collect()
        app.logger.info("JSON-LD data processed.")

    except FileNotFoundError:
        # Already handled by the initial check, but keep for safety
        ontology_load_error = f"Error: Ontology JSON-LD file not found at {JSONLD_FILE_PATH}"
        app.logger.error(ontology_load_error)
    except json.JSONDecodeError as e:
        ontology_load_error = f"Error parsing ontology JSON-LD file: {e}"
        app.logger.error(ontology_load_error)
    except Exception as e:
        ontology_load_error = f"An unexpected error occurred during ontology loading: {e}"
        app.logger.error(ontology_load_error, exc_info=True)
        # Clean up potentially partially filled structures
        uri_registry.clear()
        class_details.clear()
        individual_details.clear()
        gc.collect()


# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Pass the potential load error to the template
    return render_template('index.html', load_error=ontology_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry using pre-computed data."""
    if ontology_load_error:
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500
    # Check AFTER load attempt, if registry/details are still empty, something went wrong in parsing
    if not uri_registry and not class_details and not individual_details:
         app.logger.error("Hierarchy requested, but ontology data structures are empty after loading attempt.") # Add specific log here
         return jsonify({"error": "Ontology data structures are empty. Check JSON-LD file content or parsing."}), 500

    try:
        # --- Determine Top Level Classes (Retrieve from load_ontology calculation) ---
        # Re-calculate here to be safe, or trust the calculation in load_ontology
        all_subclass_uris = set()
        for details in class_details.values():
            all_subclass_uris.update(details["subClasses"]) # Use pre-computed subclasses

        defined_classes = set(class_details.keys())
        top_level_uris_calculated = defined_classes - all_subclass_uris

        # Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = OWL + "Thing"
        for uri, details in class_details.items():
            parents = details['superClasses']
            # If no parents OR only owl:Thing parent (which we ignore anyway), it's top-level
            if not parents or parents == {owl_thing_uri}:
                 top_level_uris_calculated.add(uri)
        # --- End Determine Top Level ---


        top_classes_data = []
        # Use the calculated top_level_uris
        for uri in sorted(list(top_level_uris_calculated), key=get_label):
            if uri in class_details:
                details = class_details[uri]
                top_classes_data.append({
                    "id": uri,
                    "label": get_label(uri),
                    "hasSubClasses": bool(details["subClasses"]),
                    "hasInstances": bool(details["instances"])
                })

        return jsonify({
            "topClasses": top_classes_data,
            "uriRegistry": uri_registry
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/hierarchy: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing hierarchy: {e}"}), 500


@app.route('/api/children/<path:node_uri_encoded>')
def get_children(node_uri_encoded):
    """Provides direct subclasses and instances using pre-computed data."""
    class_uri = unquote(node_uri_encoded)

    if ontology_load_error:
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500
    if class_uri not in class_details:
         if class_uri in individual_details:
              return jsonify({"subClasses": [], "instances": []}) # Individuals have no ontology children
         else:
              # Check registry before declaring not found
              if class_uri in uri_registry:
                   # It's something else (e.g., property), return empty children
                   return jsonify({"subClasses": [], "instances": []})
              else:
                   return jsonify({"error": f"Class URI <{class_uri}> not found."}), 404

    try:
        details = class_details[class_uri]
        subclass_data = []
        instance_data = []

        # Get subclasses
        for sub_uri in sorted(list(details["subClasses"]), key=get_label):
            if sub_uri in class_details:
                 sub_details = class_details[sub_uri]
                 subclass_data.append({
                     "id": sub_uri,
                     "label": get_label(sub_uri),
                     "hasSubClasses": bool(sub_details["subClasses"]),
                     "hasInstances": bool(sub_details["instances"])
                 })

        # Get instances
        for inst_uri in sorted(list(details["instances"]), key=get_label):
             if inst_uri in individual_details:
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


@app.route('/api/details/<path:node_uri_encoded>') # Changed variable name for clarity
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
            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details["description"],
                "superClasses": sorted([uri for uri in details["superClasses"]], key=get_label),
                "subClasses": sorted([uri for uri in details["subClasses"]], key=get_label),
                "instances": sorted([uri for uri in details["instances"]], key=get_label)
            }
        elif item_uri in individual_details:
            item_type = "individual"
            details = individual_details[item_uri]
            formatted_properties = {}
            # Sort properties by their label (using get_label on the property URI)
            sorted_prop_uris = sorted(
                details["properties"].keys(),
                key=lambda prop_uri: get_label(prop_uri)
            )

            for prop_uri in sorted_prop_uris:
                # The values list already contains dicts like {"type": "uri/literal", "value": ..., "datatype": ...}
                # Sort values within a property? Maybe by value itself? For now, keep backend order.
                formatted_properties[prop_uri] = details["properties"][prop_uri]

            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details["description"],
                "types": sorted([uri for uri in details["types"]], key=get_label),
                "properties": formatted_properties
            }

        if details_data:
            return jsonify({"type": item_type, "details": details_data})
        else:
            if item_uri in uri_registry:
                 registry_info = uri_registry[item_uri]
                 return jsonify({
                     "type": registry_info.get("type", "unknown"),
                     "details": {
                         "id": item_uri,
                         "label": get_label(item_uri),
                         "description": "", # Properties might not have descriptions stored yet
                         "message": f"Details view primarily for Classes and Individuals. This is a {registry_info.get('type', 'registered URI')}."
                     }
                 })
            else:
                 # Use abort for standard 404 response
                 abort(404, description=f"Item URI <{item_uri}> not found in the ontology data.")


    except Exception as e:
        app.logger.error(f"Error processing /api/details for {item_uri}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing details for {item_uri}: {e}"}), 500


# --- Main Execution ---
if __name__ == '__main__':
    print("Loading ontology data from JSON-LD on startup...")
    load_ontology() # Load from JSON-LD file

    if ontology_load_error:
        print(f"WARNING: Ontology loading failed: {ontology_load_error}")
        print("Flask app will run, but API calls will likely return errors.")
    # Check if registry is empty *after* loading attempt, even if no error was raised
    elif not uri_registry and not class_details and not individual_details:
         print("WARNING: Ontology loaded but data structures are empty. Check JSON-LD file content and parsing logic in load_ontology().")
         print("         >> Check console DEBUG logs above for details on processed nodes. <<")
    else:
        print("Ontology data loaded and pre-processed successfully.")

    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use threaded=True for development server to handle multiple requests better
    # Use debug=False for production or if reloading causes issues; set to True for development debugging features
    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 