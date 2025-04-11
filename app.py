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
            # If it's just a single node object at the top level? Less common for ontologies.
            if isinstance(data, dict) and "@id" in data:
                 app.logger.warning("JSON-LD data is a single top-level object, not a list or @graph. Processing as a single node.")
                 nodes = [data]
            else:
                raise ValueError("Unexpected JSON-LD structure. Expected a list of nodes or a {'@graph': [...]} object.")


        app.logger.info(f"Processing {len(nodes)} nodes from JSON-LD graph...")
        processed_count = 0 # Add counter
        skipped_nodes = 0 # Counter for skipped nodes
        unclassified_nodes = 0 # Counter for nodes not matching Class/Ind/Prop

        # --- Pass 1: Identify all entities and basic info ---
        for i, node in enumerate(nodes):
            if not isinstance(node, dict) or "@id" not in node:
                # Skip blank nodes or nodes without an identifier for now
                app.logger.debug(f"Node {i+1}/{len(nodes)}: Skipping node without '@id' or not a dict: {str(node)[:100]}...") # Log skipped node
                skipped_nodes += 1
                continue

            uri = node["@id"]
            node_types = node.get("@type", [])
            if not isinstance(node_types, list): # Ensure type is a list
                 node_types = [node_types]

            # --- Add detailed logging here ---
            app.logger.debug(f"Node {i+1}/{len(nodes)}: Processing URI='{uri}', Types='{node_types}'")
            # --- End detailed logging ---

            # Extract label and description (handle potential list format)
            label_val = _extract_jsonld_value(node.get(RDFS + "label")) or _local_name(uri)
            desc_val = _extract_jsonld_value(node.get(RDFS + "comment", "")) # Use rdfs:comment standardly

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

            # Identify Properties (Object, Datatype, Annotation)
            elif any(pt in node_types for pt in [OWL + "ObjectProperty", OWL + "DatatypeProperty", OWL + "AnnotationProperty", RDF + "Property"]): # Added rdf:Property
                 entity_type = "property"
                 # Only add to registry if not already added (e.g., if something is both Class and Property?)
                 if uri not in uri_registry:
                     uri_registry[uri] = {"label": label_val, "type": "property"}
                     # Don't increment processed_count here if we only count Classes/Individuals added to details
                     app.logger.debug(f"  -> Identified as Property: {label_val} (Added to registry only)") # Log identification
                 else:
                     app.logger.debug(f"  -> Identified as Property: {label_val} (Already in registry as {uri_registry[uri].get('type')})")
                 # Could potentially store domain/range here if needed later
            else:
                 # Log nodes that weren't classified
                 app.logger.debug(f"  -> Node not classified as Class, Individual, or Property based on types: {node_types}")
                 unclassified_nodes += 1


            # --- Process relationships and properties within this pass ---
            # (JSON-LD structure often allows processing in one pass)

            # If it's a Class, find superclasses
            if entity_type == "class":
                super_classes = node.get(RDFS + "subClassOf", [])
                if not isinstance(super_classes, list): super_classes = [super_classes]
                for parent_obj in super_classes:
                    parent_uri = _extract_jsonld_id(parent_obj)
                    # Check if parent_uri is a valid URI string before adding
                    if isinstance(parent_uri, str) and parent_uri != OWL + "Thing": # Ignore owl:Thing links for hierarchy
                        class_details[uri]["superClasses"].add(parent_uri)
                        app.logger.debug(f"      Added superclass link: {uri} -> {parent_uri}")
                    elif parent_uri: # Log if it's owl:Thing or other non-string ID
                        app.logger.debug(f"      Ignoring superclass link to {parent_uri}")


            # If it's an Individual, find its types and properties
            if entity_type == "individual":
                # Types
                types_raw = node.get(RDF + "type", []) # Use rdf:type standardly
                if not isinstance(types_raw, list): types_raw = [types_raw]
                for type_obj in types_raw:
                    class_uri = _extract_jsonld_id(type_obj)
                    # Link instance to class if class_uri is valid and not owl:NamedIndividual/owl:Thing
                    # Check if class_uri is a valid URI string
                    if isinstance(class_uri, str) and class_uri not in [OWL + "NamedIndividual", OWL + "Thing"]:
                        individual_details[uri]["types"].add(class_uri)
                        app.logger.debug(f"      Added type link: {uri} -> {class_uri}")
                    elif class_uri: # Log if it's NamedIndividual/Thing or other non-string ID
                         app.logger.debug(f"      Ignoring type link to {class_uri}")


                # Properties
                for prop_uri, values in node.items():
                    # Skip JSON-LD keywords and known handled properties
                    if prop_uri.startswith("@") or prop_uri in [RDF + "type", RDFS + "label", RDFS + "comment", RDFS + "subClassOf"]: # Added subClassOf here
                        continue

                    # Ensure values is a list for consistent processing
                    if not isinstance(values, list): values = [values]

                    app.logger.debug(f"      Processing property '{_local_name(prop_uri)}' ({prop_uri}) with {len(values)} value(s)")

                    for value_obj in values:
                        prop_entry = {}
                        target_id = _extract_jsonld_id(value_obj) # Check if it's a link {"@id": ...}

                        if target_id and isinstance(target_id, str): # Object Property Assertion (ensure target_id is string URI)
                            prop_entry["type"] = "uri"
                            prop_entry["value"] = target_id
                            app.logger.debug(f"        -> Object property value: {target_id}")
                        else: # Datatype or Annotation Property Assertion
                            literal_details = _extract_jsonld_literal_details(value_obj)
                            if literal_details["value"] is not None:
                                prop_entry["type"] = "literal"
                                prop_entry["value"] = literal_details["value"]
                                prop_entry["datatype"] = literal_details["datatype"]
                                # prop_entry["lang"] = literal_details["lang"] # Could store lang if needed
                                app.logger.debug(f"        -> Literal property value: '{literal_details['value']}' (Type: {literal_details['datatype']}, Lang: {literal_details['lang']})")

                            else:
                                # Skip if we couldn't extract a value
                                app.logger.debug(f"        -> Skipping property value, couldn't extract literal/id: {str(value_obj)[:100]}...")
                                continue

                        individual_details[uri]["properties"][prop_uri].append(prop_entry)


        # --- Pass 2: Build reverse relationships (subClasses, instances) ---
        app.logger.info("Building reverse relationships (subClasses, instances)...")
        all_subclasses = set()
        # Build subclass links
        for class_uri, details in class_details.items():
            for parent_uri in details["superClasses"]:
                if parent_uri in class_details: # Ensure parent exists in our map
                    class_details[parent_uri]["subClasses"].add(class_uri)
                    all_subclasses.add(class_uri) # Mark as a subclass
                    app.logger.debug(f"  Added subclass link: {parent_uri} <- {class_uri}")
                else:
                    app.logger.debug(f"  Parent class {parent_uri} for {class_uri} not found in class_details map.")

        # Build instance links
        for ind_uri, details in individual_details.items():
            for class_uri in details["types"]:
                if class_uri in class_details: # Ensure class exists
                    class_details[class_uri]["instances"].add(ind_uri)
                    app.logger.debug(f"  Added instance link: {class_uri} <- {ind_uri}")
                else:
                     app.logger.debug(f"  Class {class_uri} for instance {ind_uri} not found in class_details map.")


        # Identify top-level classes (defined classes not in all_subclasses)
        defined_classes = set(class_details.keys())
        top_level_uris = defined_classes - all_subclasses # Calculate here
        # Refinement: Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = OWL + "Thing"
        for uri, details in class_details.items():
            parents = details['superClasses']
            # If no parents OR only owl:Thing parent (which we ignore anyway), it's top-level
            if not parents or parents == {owl_thing_uri}:
                 if uri not in top_level_uris:
                      app.logger.debug(f"  Marking {uri} as top-level (no parents or only owl:Thing).")
                      top_level_uris.add(uri)


        # Add a summary log after processing
        app.logger.info(f"Finished processing {len(nodes)} nodes.")
        app.logger.info(f"  Skipped nodes (no @id or not dict): {skipped_nodes}")
        app.logger.info(f"  Processed nodes added to details (Class/Individual): {processed_count}")
        app.logger.info(f"  Unclassified nodes (based on type): {unclassified_nodes}")
        app.logger.info(f"Final Lookup structures: Registry size: {len(uri_registry)}, Classes: {len(class_details)}, Individuals: {len(individual_details)}")
        app.logger.info(f"Identified {len(top_level_uris)} top-level classes.")
        # Log top-level classes found for debugging
        if len(top_level_uris) < 20: # Log if the list isn't too long
             app.logger.debug(f"Top-level URIs found: {list(top_level_uris)}")
        elif len(top_level_uris) == 0:
             app.logger.warning("No top-level classes were identified.")


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
    except ValueError as e: # Catch specific errors like unexpected structure
        ontology_load_error = f"Error processing JSON-LD structure: {e}"
        app.logger.error(ontology_load_error, exc_info=True)
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
    # Check for explicit load error first
    if ontology_load_error:
        app.logger.error(f"Hierarchy requested, but ontology load failed: {ontology_load_error}")
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500

    # Check if data structures are empty *after* loading attempt finished without error
    if not uri_registry and not class_details and not individual_details:
         app.logger.error("Hierarchy requested, but data structures (uri_registry, class_details, individual_details) are empty after loading attempt completed without explicit error.")
         return jsonify({"error": "Ontology data structures are empty after processing. Check server logs (DEBUG level) for details on node processing and potential reasons (e.g., file content, type mismatches, logic issues)."}), 500

    try:
        # --- Determine Top Level Classes ---
        # Re-calculate here to ensure consistency, or trust the calculation in load_ontology
        # Let's recalculate to be safe, using the same logic as in load_ontology
        all_subclass_uris = set()
        for details in class_details.values():
            # Iterate through superClasses to find children (subClasses)
            for parent_uri in details.get("superClasses", set()):
                 if parent_uri in class_details:
                      # The child is the key 'details' belongs to
                      all_subclass_uris.add(details.get("id", list(class_details.keys())[list(class_details.values()).index(details)])) # Get the URI for the current details

        defined_classes = set(class_details.keys())
        top_level_uris_calculated = defined_classes - all_subclass_uris

        # Refinement: Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = OWL + "Thing"
        for uri, details in class_details.items():
            parents = details.get('superClasses', set())
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
                    # Check the actual calculated subclasses/instances for this specific URI
                    "hasSubClasses": bool(details.get("subClasses", set())),
                    "hasInstances": bool(details.get("instances", set()))
                })
            else:
                # This case should ideally not happen if top_level_uris_calculated is derived correctly
                app.logger.warning(f"Top-level URI {uri} identified but not found in class_details during hierarchy generation.")


        # Add a check if top_classes_data is empty even if registry wasn't
        if not top_classes_data and (uri_registry or class_details or individual_details):
             app.logger.warning("Hierarchy requested, data structures populated but no top-level classes were identified.")
             # Optionally return a specific message or just empty list
             # return jsonify({"error": "Ontology loaded but no top-level classes found. Check class definitions and subclass relationships."}), 500


        return jsonify({
            "topClasses": top_classes_data,
            "uriRegistry": uri_registry # Send the full registry for lookups
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
         print("\n" + "="*60)
         print("WARNING: Ontology loading process completed BUT data structures are empty.")
         print("         This means the JSON-LD was likely parsed, but no Classes or Individuals")
         print("         were successfully identified and stored based on the current logic.")
         print("         Please check the following:")
         print("           1. Flask DEBUG logs above for details on processed/skipped/unclassified nodes.")
         print("           2. The content of the JSON-LD file ('panres_v2.jsonld').")
         print("           3. The type URIs (e.g., owl:Class) used in the JSON-LD match the constants in app.py.")
         print("           4. The structure of nodes in the JSON-LD (@id, @type fields).")
         print("="*60 + "\n")
         print("Flask app will run, but API calls will return errors due to missing data.")
    else:
        print("Ontology data loaded and pre-processed successfully.")
        print(f"  -> Registry: {len(uri_registry)} items, Classes: {len(class_details)}, Individuals: {len(individual_details)}")


    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use threaded=True for development server to handle multiple requests better
    # Use debug=False for production or if reloading causes issues; set to True for development debugging features
    # Ensure Flask's debug mode is OFF for production, but keep our logging level high enough
    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 