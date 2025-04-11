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
    # Add the default namespace from your OWL file (xml:base or xmlns)
    '': "http://myonto.com/PanResOntology.owl#",
    # Add other namespaces found in your file
    'owlr': "http://www.lesfleursdunormal.fr/static/_downloads/owlready_ontology.owl#"
}

# --- Helper Functions ---

def _get_uri_from_elem(elem):
    """Gets the rdf:about or rdf:resource URI from an element."""
    if elem is None:
        return None
    # Check for rdf:about first
    uri = elem.get(f"{{{NS['rdf']}}}about")
    if uri is None:
        # Then check for rdf:resource
        uri = elem.get(f"{{{NS['rdf']}}}resource")
    # Resolve relative URIs using xml:base if necessary (ElementTree doesn't do this automatically)
    # For simplicity here, assuming absolute URIs or simple fragment identifiers (#...)
    # If your base is 'http://myonto.com/PanResOntology.owl' and uri is '#Ant3-IIc',
    # you might want to return 'http://myonto.com/PanResOntology.owl#Ant3-IIc'
    # This basic version assumes the URI is usable as is or the fragment is the key part.
    # Handle potential '#' prefix if base URI context is needed elsewhere
    # base_uri = ontology_root.get(f"{{{NS['xml']}}}base") # Need 'xml': 'http://www.w3.org/XML/1998/namespace' in NS
    # if uri and uri.startswith('#') and base_uri:
    #     return base_uri + uri
    return uri


def _get_text_from_elem(elem, child_tag_with_ns):
    """Safely gets text content from a specific child element using full namespace tag."""
    if elem is None: return ""
    # ElementTree find needs the full {namespace}tag format
    child = elem.find(child_tag_with_ns, NS) # NS argument might not be needed if tag is already qualified
    return child.text.strip() if child is not None and child.text else ""

def get_label(uri):
    """Gets the label from the pre-built registry, falling back to local name."""
    # Handle cases where the URI might not have been fully resolved if using fragments
    full_uri = uri # Assume uri is the full identifier for now
    # Example potential resolution (if needed and base_uri is available):
    # base_uri = "http://myonto.com/PanResOntology.owl" # Or get dynamically
    # if uri and uri.startswith('#'):
    #     full_uri = base_uri + uri

    if full_uri in uri_registry:
        return uri_registry[full_uri].get("label", "") or _local_name(full_uri) # Use label if present, else local name
    return _local_name(uri) # Fallback if URI not in registry (use original uri for local name)

def _local_name(uri):
    """Extracts the local name part of a URI (fragment or last path segment)."""
    if not uri: return ""
    try:
        if '#' in uri:
            return uri.split('#')[-1]
        # Basic path splitting, might need refinement for complex URLs
        return uri.split('/')[-1]
    except Exception as e:
        app.logger.warning(f"Could not extract local name from URI '{uri}': {e}")
        return str(uri) # Failsafe

# --- Ontology Loading and Pre-processing ---
def load_ontology():
    """Loads the OWL file using ElementTree and builds lookup structures."""
    global ontology_root, ontology_load_error
    global uri_registry, class_details, individual_details
    # Clear previous data
    uri_registry.clear()
    class_details.clear()
    individual_details.clear()
    ontology_root = None
    ontology_load_error = None
    gc.collect()

    app.logger.info(f"Starting ontology loading from: {OWL_FILE_PATH}")

    if not os.path.exists(OWL_FILE_PATH):
        ontology_load_error = f"Error: Ontology file not found at {OWL_FILE_PATH}"
        app.logger.error(ontology_load_error)
        return

    try:
        app.logger.info("Parsing OWL file with ElementTree...")
        # Iterate over the XML file to find the root rdf:RDF element
        # This helps handle potential processing instructions or comments before the root
        context = ET.iterparse(OWL_FILE_PATH, events=('start', 'end'))
        _, root_elem = next(context) # Get the root element event
        ontology_root = root_elem # Store the root <rdf:RDF> element
        app.logger.info(f"Ontology XML parsed successfully. Root tag: {ontology_root.tag}")

        # Extract base URI if needed later (requires xml namespace)
        # NS['xml'] = "http://www.w3.org/XML/1998/namespace"
        # base_uri = ontology_root.get(f"{{{NS['xml']}}}base")
        # app.logger.info(f"XML Base URI: {base_uri}")


        app.logger.info("Building lookup structures...")
        # --- Pass 1: Identify all entities and basic info ---
        # Iterate through all direct children of the root <rdf:RDF> element
        for elem in ontology_root:
            uri = _get_uri_from_elem(elem)
            if not uri:
                # Skip elements without rdf:about, like owl:Ontology or potentially complex anonymous nodes
                # Log skipped elements for debugging if necessary
                # app.logger.debug(f"Skipping element with tag {elem.tag} - no rdf:about URI found.")
                continue # Skip elements without a direct URI identifier

            # Use qualified tags for finding label/comment
            label = _get_text_from_elem(elem, f"{{{NS['rdfs']}}}label") or _local_name(uri)
            description = _get_text_from_elem(elem, f"{{{NS['rdfs']}}}comment")

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
                for prop_elem in elem: # Iterate through children of the NamedIndividual element
                    prop_uri_tag = prop_elem.tag # Full tag e.g. "{http://myonto.com/PanResOntology.owl#}is_from_database"

                    # Skip rdf:type, rdfs:label, rdfs:comment as they are handled elsewhere or globally
                    if prop_uri_tag in [f"{{{NS['rdf']}}}type", f"{{{NS['rdfs']}}}label", f"{{{NS['rdfs']}}}comment"]:
                         continue

                    # Process the property assertion
                    prop_entry = {}
                    obj_uri = _get_uri_from_elem(prop_elem) # Checks rdf:resource

                    if obj_uri: # Object Property Assertion (linked via rdf:resource)
                        prop_entry["type"] = "uri"
                        prop_entry["value"] = obj_uri
                    elif prop_elem.text is not None: # Datatype or Annotation Property Assertion (has text content)
                        prop_entry["type"] = "literal"
                        prop_entry["value"] = prop_elem.text.strip()
                        prop_entry["datatype"] = prop_elem.get(f"{{{NS['rdf']}}}datatype") # Get datatype attribute
                    else:
                        # Skip elements that are neither resource links nor have text content
                        # Could be empty tags or complex inline structures not parsed here.
                        # app.logger.debug(f"Skipping property element {prop_uri_tag} for individual {uri}: No resource or text content.")
                        continue

                    # Store the property using the full tag URI as the key
                    individual_details[uri]["properties"][prop_uri_tag].append(prop_entry)


            # Identify Properties (Object, Datatype, Annotation)
            elif elem.tag in [f"{{{NS['owl']}}}ObjectProperty", f"{{{NS['owl']}}}DatatypeProperty", f"{{{NS['owl']}}}AnnotationProperty"]:
                 # Only add to registry if not already added (e.g., as a class/individual by mistake)
                 if uri not in uri_registry:
                     uri_registry[uri] = {"label": label, "type": "property"}
                     # Could potentially parse domain/range here if needed for validation later
                 else:
                     # If URI exists, update type if it wasn't property before? Or log warning?
                     if uri_registry[uri]['type'] != 'property':
                         app.logger.warning(f"URI {uri} previously registered as {uri_registry[uri]['type']}, now also identified as property. Keeping previous type.")


        # --- Pass 2: Process relationships ---
        all_subclasses = set() # Keep track of URIs that are subclasses of something specific
        # Iterate again now that all entities are potentially in the registry
        for elem in ontology_root:
            subj_uri = _get_uri_from_elem(elem)
            if not subj_uri: continue

            # Process SubClassOf (only relevant for owl:Class elements)
            if elem.tag == f"{{{NS['owl']}}}Class":
                for sub_class_elem in elem.findall(f"{{{NS['rdfs']}}}subClassOf", NS):
                    parent_uri = _get_uri_from_elem(sub_class_elem)
                    # Handle simple rdf:resource links to parent classes
                    if parent_uri and subj_uri != parent_uri and parent_uri != f"{{{NS['owl']}}}Thing":
                        # Ensure both are known classes before adding relationship
                        # Check registry type for safety, though class_details check is primary
                        if subj_uri in class_details and parent_uri in class_details:
                            class_details[subj_uri]["superClasses"].add(parent_uri)
                            class_details[parent_uri]["subClasses"].add(subj_uri)
                            all_subclasses.add(subj_uri) # Mark this as having a parent
                        # else:
                            # Log if a subClassOf points to a non-class or unknown URI?
                            # app.logger.debug(f"SubClassOf skipped: {subj_uri} -> {parent_uri}. Not both known classes.")
                    # TODO: Add parsing for complex subClassOf (e.g., Restrictions) if needed

            # Process rdf:type (Instance Of - typically found on NamedIndividual elements)
            if elem.tag == f"{{{NS['owl']}}}NamedIndividual":
                for type_elem in elem.findall(f"{{{NS['rdf']}}}type", NS):
                    class_uri = _get_uri_from_elem(type_elem)
                    # Link instance to class if both are known and class_uri isn't owl:NamedIndividual/owl:Thing
                    if class_uri and subj_uri in individual_details and class_uri in class_details \
                       and class_uri not in [f"{{{NS['owl']}}}NamedIndividual", f"{{{NS['owl']}}}Thing"]:
                        individual_details[subj_uri]["types"].add(class_uri)
                        class_details[class_uri]["instances"].add(subj_uri)
                    # else:
                        # Log if rdf:type points to non-class, unknown URI, or ignored type?
                        # app.logger.debug(f"rdf:type skipped for individual {subj_uri}: Class URI {class_uri} not found or invalid.")


        # Identify top-level classes (those defined but not subclasses of other defined classes)
        defined_classes = set(class_details.keys())
        # A class is top-level if it's defined and not found in the set of all subclasses
        top_level_uris = defined_classes - all_subclasses
        # Refinement: Also consider classes whose only parent is owl:Thing (if owl:Thing is not in class_details)
        owl_thing_uri = f"{{{NS['owl']}}}Thing"
        for uri in list(defined_classes): # Iterate over a copy if modifying inside loop
             details = class_details[uri]
             parents = details['superClasses']
             # If it has parents BUT those parents are ONLY owl:Thing, consider it top-level too
             if parents and parents.issubset({owl_thing_uri}):
                  top_level_uris.add(uri)
             # If it has NO parents at all, it's already included via (defined_classes - all_subclasses)


        app.logger.info(f"Lookup structures built. Registry size: {len(uri_registry)}, Classes: {len(class_details)}, Individuals: {len(individual_details)}")
        app.logger.info(f"Identified {len(top_level_uris)} top-level classes.")

        # Clear the XML tree from memory after processing
        ontology_root = None
        root_elem = None
        context = None
        gc.collect()
        app.logger.info("XML tree cleared from memory.")

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
    # Check if structures were populated, even if load error didn't occur (e.g., empty file)
    if not uri_registry and not class_details and not individual_details:
         return jsonify({"error": "Ontology data structures are empty. Check file content or parsing."}), 500

    try:
        # --- Determine Top Level Classes ---
        # This logic is now performed during load_ontology, we just need to retrieve them.
        all_subclass_uris = set()
        for details in class_details.values():
            all_subclass_uris.update(details["subClasses"])

        defined_classes = set(class_details.keys())
        top_level_uris_calculated = defined_classes - all_subclass_uris

        # Add classes whose only parent is owl:Thing (if owl:Thing isn't explicitly defined as a class)
        owl_thing_uri = f"{{{NS['owl']}}}Thing"
        for uri, details in class_details.items():
            parents = details['superClasses']
            if not parents or parents == {owl_thing_uri}:
                 top_level_uris_calculated.add(uri)
        # --- End Determine Top Level ---


        top_classes_data = []
        # Use the calculated top_level_uris
        for uri in sorted(list(top_level_uris_calculated), key=get_label):
            # Ensure the URI still exists in class_details (safety check)
            if uri in class_details:
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
                # Sort lists using the get_label helper for consistency
                "superClasses": sorted([uri for uri in details["superClasses"]], key=get_label),
                "subClasses": sorted([uri for uri in details["subClasses"]], key=get_label),
                "instances": sorted([uri for uri in details["instances"]], key=get_label)
            }
        elif item_uri in individual_details:
            item_type = "individual"
            details = individual_details[item_uri]

            # Format properties for display
            formatted_properties = {}
            # Sort properties by their label for consistent display
            # The keys in details["properties"] are the full tag URIs
            sorted_prop_tag_uris = sorted(
                details["properties"].keys(),
                key=lambda tag_uri: get_label(tag_uri) # Use get_label on the property tag URI
            )

            for prop_tag_uri in sorted_prop_tag_uris:
                values = details["properties"][prop_tag_uri]
                # The key in the output JSON should also be the full tag URI
                # The frontend will use get_label(prop_tag_uri) for display
                formatted_properties[prop_tag_uri] = values # values is already a list of {"type": ..., "value": ..., "datatype": ...}

            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details["description"],
                "types": sorted([uri for uri in details["types"]], key=get_label),
                "properties": formatted_properties # Use the formatted properties
            }

        if details_data:
            return jsonify({"type": item_type, "details": details_data})
        else:
            # Check registry for other types (like properties) or if URI is completely unknown
            if item_uri in uri_registry:
                 registry_info = uri_registry[item_uri]
                 # Provide minimal info if it's a known property or other registered URI
                 return jsonify({
                     "type": registry_info.get("type", "unknown"),
                     "details": {
                         "id": item_uri,
                         "label": get_label(item_uri),
                         "description": registry_info.get("description", ""), # Add description if available
                         # Indicate that full details are not applicable for this type
                         "message": f"Details view primarily for Classes and Individuals. This is a {registry_info.get('type', 'registered URI')}."
                     }
                 })
            else:
                 # Use Flask's abort to trigger a standard 404 response
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