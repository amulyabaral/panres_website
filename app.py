import os
import gc
from flask import Flask, jsonify, render_template, abort
# import json # No longer needed for primary loading
import xml.etree.ElementTree as ET # Import ElementTree for XML parsing
from urllib.parse import unquote
from collections import defaultdict
import logging # Use logging for errors

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl" # Back to OWL
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME) # Back to OWL path
# JSONLD_FILENAME = "panres_v2.jsonld" # Old: Use the JSON-LD file
# JSONLD_FILE_PATH = os.path.join(os.path.dirname(__file__), JSONLD_FILENAME) # Old path

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.logger.setLevel(logging.DEBUG) # Keep DEBUG for detailed logs

# --- Ontology Data Structures (Populated by load_ontology) ---
ontology_root = None # Store the root XML element after parsing
ontology_load_error = None # Store potential loading errors

# Pre-computed lookups for efficiency
uri_registry = {} # {uri: {"label": "...", "type": "class/individual/property"}}
class_details = defaultdict(lambda: {
    "label": "", "description": "", "superClasses": set(), "subClasses": set(), "instances": set()
}) # {class_uri: {details...}}
individual_details = defaultdict(lambda: {
    "label": "", "description": "", "types": set(), "properties": defaultdict(list)
}) # {individual_uri: {details...}}

# Define common XML namespaces used in the OWL file
# These might need adjustment based on your specific OWL file's declarations
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    # Add the base namespace if your URIs are relative (e.g., rdf:about="#ClassName")
    # Find this in the <rdf:RDF> tag, e.g., xml:base="..." or xmlns="..."
    "base": "http://myonto.com/PanResOntology.owl#" # Replace with your actual base URI if needed
}

# Helper function to resolve namespace prefixes
def _ns_tag(tag):
    prefix, local = tag.split(':')
    return f"{{{NS[prefix]}}}{local}"

# --- Helper Functions ---

def _get_uri_from_elem(elem, attrib_name="rdf:about"):
    """Extracts the URI from an element's attribute, resolving base if necessary."""
    uri_attrib = elem.get(_ns_tag(attrib_name))
    if uri_attrib:
        # If the URI starts with '#', prepend the base namespace
        if uri_attrib.startswith("#") and NS.get("base"):
            return NS["base"] + uri_attrib[1:]
        return uri_attrib
    # Handle rdf:resource as well for property values
    uri_resource = elem.get(_ns_tag("rdf:resource"))
    if uri_resource:
         if uri_resource.startswith("#") and NS.get("base"):
            return NS["base"] + uri_resource[1:]
         return uri_resource
    return None

def _get_text_from_elem(elem):
    """Extracts text content from an element, handling potential None."""
    return elem.text.strip() if elem is not None and elem.text else ""

# Remove or comment out JSON-LD specific helpers
# def _extract_jsonld_value(value_obj): ...
# def _extract_jsonld_id(value_obj): ...
# def _extract_jsonld_literal_details(value_obj): ...


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
        # Check if the URI starts with the base namespace and extract from there
        base_uri = NS.get("base")
        if base_uri and uri.startswith(base_uri):
            local = uri[len(base_uri):]
            if local: return local # Return if non-empty

        # Fallback to standard parsing
        if '#' in uri:
            return uri.split('#')[-1]
        return uri.split('/')[-1]
    except Exception as e:
        app.logger.warning(f"Could not extract local name from URI '{uri}': {e}")
        return str(uri)

# --- Ontology Loading and Pre-processing ---
def load_ontology():
    """Loads the ontology from the OWL/XML file and builds lookup structures."""
    global ontology_load_error, ontology_root
    global uri_registry, class_details, individual_details
    # Clear previous data
    uri_registry.clear()
    class_details.clear()
    individual_details.clear()
    ontology_root = None # Clear previous XML root
    ontology_load_error = None
    gc.collect()

    app.logger.info(f"Starting ontology loading from: {OWL_FILE_PATH}")

    if not os.path.exists(OWL_FILE_PATH):
        ontology_load_error = f"Error: Ontology OWL file not found at {OWL_FILE_PATH}"
        app.logger.error(ontology_load_error)
        return

    try:
        app.logger.info("Parsing OWL/XML file...")
        tree = ET.parse(OWL_FILE_PATH)
        ontology_root = tree.getroot()
        app.logger.info("OWL/XML file parsed successfully.")

        # --- Re-register namespaces dynamically from the root element ---
        # This makes it more robust if the file uses different prefixes
        global NS
        NS = dict([
            node for _, node in ET.iterparse(OWL_FILE_PATH, events=['start-ns'])
        ])
        # Add xml:base if present
        xml_base = ontology_root.get('{http://www.w3.org/XML/1998/namespace}base')
        if xml_base:
            NS['base'] = xml_base
            app.logger.info(f"Detected xml:base: {xml_base}")
        else:
            # Try to guess base from common prefixes if xml:base is missing
            common_prefixes = ['', 'owl', 'protege'] # Prefixes often used for the default namespace
            for prefix in common_prefixes:
                if prefix in NS and 'base' not in NS:
                     NS['base'] = NS[prefix]
                     app.logger.info(f"Using namespace for prefix '{prefix}' as base: {NS['base']}")
                     break
        if 'base' not in NS:
             app.logger.warning("Could not determine xml:base namespace. Relative URIs (like '#Class') might not resolve correctly.")
        app.logger.debug(f"Registered namespaces: {NS}")
        # --- End Namespace Registration ---


        app.logger.info("Processing ontology elements...")
        processed_count = 0
        entity_count = 0 # Count classes, individuals, properties found

        # --- Pass 1: Identify all entities and basic info ---
        app.logger.info("Pass 1: Identifying entities (Classes, Individuals, Properties)...")
        # Iterate over all direct children of the root (usually includes ontology metadata, classes, individuals, etc.)
        for elem in ontology_root:
            uri = _get_uri_from_elem(elem)
            if not uri:
                # app.logger.debug(f"Skipping element without URI: {elem.tag}")
                continue # Skip elements without rdf:about (like owl:Ontology)

            label = ""
            description = ""
            entity_type = None

            # Find label (rdfs:label)
            label_elem = elem.find(_ns_tag("rdfs:label"))
            if label_elem is not None:
                label = _get_text_from_elem(label_elem)

            # Find description (rdfs:comment) - common practice
            desc_elem = elem.find(_ns_tag("rdfs:comment"))
            if desc_elem is not None:
                description = _get_text_from_elem(desc_elem)

            # Determine type (Class, Individual, Property)
            if elem.tag == _ns_tag("owl:Class"):
                entity_type = "class"
                uri_registry[uri] = {"label": label or _local_name(uri), "type": "class"}
                class_details[uri]["label"] = label or _local_name(uri)
                class_details[uri]["description"] = description
                entity_count += 1
            elif elem.tag == _ns_tag("owl:NamedIndividual"):
                entity_type = "individual"
                uri_registry[uri] = {"label": label or _local_name(uri), "type": "individual"}
                individual_details[uri]["label"] = label or _local_name(uri)
                individual_details[uri]["description"] = description
                entity_count += 1
            elif elem.tag in [_ns_tag("owl:ObjectProperty"), _ns_tag("owl:DatatypeProperty"), _ns_tag("owl:AnnotationProperty")]:
                entity_type = "property"
                if uri not in uri_registry: # Only add if not already present (e.g., from class/individual pass)
                    uri_registry[uri] = {"label": label or _local_name(uri), "type": "property"}
                    entity_count += 1
            # else:
                # app.logger.debug(f"Element {uri} with tag {elem.tag} not processed as core entity type.")

            if entity_type:
                 app.logger.debug(f"  -> Found {entity_type}: {uri} (Label: '{label}')")
            processed_count += 1


        app.logger.info(f"Pass 1 finished. Processed {processed_count} elements with URIs. Found {entity_count} potential entities.")

        # --- Pass 2: Process relationships (subClassOf, type, properties) ---
        app.logger.info("Pass 2: Processing relationships...")
        processed_count = 0
        relationship_count = 0

        for elem in ontology_root:
            uri = _get_uri_from_elem(elem)
            if not uri: continue

            # Process Class relationships
            if uri in class_details:
                # Find superclasses (rdfs:subClassOf)
                for sub_class_of in elem.findall(_ns_tag("rdfs:subClassOf")):
                    parent_uri = _get_uri_from_elem(sub_class_of, "rdf:resource") # Superclass URI is often in rdf:resource
                    if parent_uri and parent_uri != _ns_tag("owl:Thing"): # Check parent_uri exists and ignore owl:Thing
                        class_details[uri]["superClasses"].add(parent_uri)
                        # Build reverse link (subClass)
                        if parent_uri in class_details:
                            class_details[parent_uri]["subClasses"].add(uri)
                            relationship_count += 1
                            app.logger.debug(f"      Subclass link: {parent_uri} <- {uri}")
                        else:
                             app.logger.debug(f"      Parent class {parent_uri} for {uri} not found in class_details map yet.")


            # Process Individual relationships
            elif uri in individual_details:
                # Find types (rdf:type)
                for type_elem in elem.findall(_ns_tag("rdf:type")):
                    class_uri = _get_uri_from_elem(type_elem, "rdf:resource")
                    if class_uri and class_uri not in [_ns_tag("owl:NamedIndividual"), _ns_tag("owl:Thing")]:
                        individual_details[uri]["types"].add(class_uri)
                        # Build reverse link (instance)
                        if class_uri in class_details:
                            class_details[class_uri]["instances"].add(uri)
                            relationship_count += 1
                            app.logger.debug(f"      Instance link: {class_uri} <- {uri}")
                        else:
                            app.logger.debug(f"      Class {class_uri} for instance {uri} not found in class_details map yet.")


                # Find properties (object and data properties asserted on the individual)
                for prop_elem in elem:
                    prop_tag = prop_elem.tag
                    # Skip known non-property tags within an individual definition
                    if prop_tag in [_ns_tag("rdf:type"), _ns_tag("rdfs:label"), _ns_tag("rdfs:comment")]:
                        continue

                    prop_uri = f"{{{prop_elem.tag.split('}')[0]}}}{prop_elem.tag.split('}')[1]}" # Reconstruct full URI for the property tag

                    # Check if it's a known property URI before processing
                    # This assumes properties are defined elsewhere and added to uri_registry in Pass 1
                    # if prop_uri not in uri_registry or uri_registry[prop_uri]['type'] != 'property':
                    #     app.logger.debug(f"      Skipping unknown or non-property tag {prop_tag} on individual {uri}")
                    #     continue

                    prop_entry = {}
                    target_uri = _get_uri_from_elem(prop_elem, "rdf:resource") # Check for linked resource first

                    if target_uri: # Object Property Assertion
                        prop_entry["type"] = "uri"
                        prop_entry["value"] = target_uri
                        app.logger.debug(f"      Individual {uri}: Property '{_local_name(prop_uri)}' -> URI {target_uri}")
                    else: # Datatype Property Assertion
                        literal_value = _get_text_from_elem(prop_elem)
                        if literal_value is not None: # Ensure there's text content
                            prop_entry["type"] = "literal"
                            prop_entry["value"] = literal_value
                            prop_entry["datatype"] = prop_elem.get(_ns_tag("rdf:datatype")) # Get datatype if present
                            # prop_entry["lang"] = prop_elem.get('{http://www.w3.org/XML/1998/namespace}lang') # Get xml:lang if present
                            app.logger.debug(f"      Individual {uri}: Property '{_local_name(prop_uri)}' -> Literal '{literal_value}' (Datatype: {prop_entry['datatype']})")

                        else:
                            # Skip if no resource and no text value
                            app.logger.debug(f"      Individual {uri}: Skipping property tag {prop_tag} - no rdf:resource or text value found.")
                            continue

                    individual_details[uri]["properties"][prop_uri].append(prop_entry)
                    relationship_count += 1

            processed_count += 1

        app.logger.info(f"Pass 2 finished. Processed {processed_count} elements. Found {relationship_count} relationships (subclass/instance/property links).")

        # --- Pass 3: Final cleanup/calculations (e.g., identify top-level classes) ---
        # This logic can remain similar, operating on the populated dictionaries
        app.logger.info("Pass 3: Identifying top-level classes...")
        all_subclasses = set()
        for class_uri, details in class_details.items():
            # The subClasses set was built during Pass 2's superclass processing
            all_subclasses.update(details["subClasses"])

        defined_classes = set(class_details.keys())
        top_level_uris = defined_classes - all_subclasses

        # Refinement: Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = _ns_tag("owl:Thing") # Use resolved owl:Thing URI
        for uri, details in class_details.items():
            parents = details.get('superClasses', set())
            if not parents or parents == {owl_thing_uri}:
                 if uri not in top_level_uris:
                      app.logger.debug(f"  Marking {uri} as top-level (no parents or only owl:Thing).")
                      top_level_uris.add(uri)

        app.logger.info(f"Identified {len(top_level_uris)} top-level classes.")
        if len(top_level_uris) < 20:
             app.logger.debug(f"Top-level URIs found: {list(top_level_uris)}")
        elif len(top_level_uris) == 0:
             app.logger.warning("No top-level classes were identified.")


        # Clear the XML tree from memory if no longer needed
        ontology_root = None
        gc.collect()
        app.logger.info("OWL data processed and XML tree cleared.")

    except FileNotFoundError:
        # Already handled by the initial check
        ontology_load_error = f"Error: Ontology OWL file not found at {OWL_FILE_PATH}"
        app.logger.error(ontology_load_error)
    except ET.ParseError as e:
        ontology_load_error = f"Error parsing ontology OWL/XML file: {e}"
        app.logger.error(ontology_load_error)
        ontology_root = None # Ensure root is None on error
    except Exception as e:
        ontology_load_error = f"An unexpected error occurred during ontology loading: {e}"
        app.logger.error(ontology_load_error, exc_info=True)
        # Clean up potentially partially filled structures
        uri_registry.clear()
        class_details.clear()
        individual_details.clear()
        ontology_root = None
        gc.collect()

# --- Flask Routes ---
# No changes needed in the Flask routes themselves, as they rely on the
# pre-computed dictionaries (uri_registry, class_details, individual_details)
# which are now populated by the updated load_ontology function.

@app.route('/')
def index():
    """Serves the main HTML page."""
    # Pass the potential load error to the template
    return render_template('index.html', load_error=ontology_load_error)

# ... (get_hierarchy, get_children, get_details routes remain the same) ...
# Make sure the recalculation logic in get_hierarchy uses the correct owl:Thing URI
@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes and the URI registry using pre-computed data."""
    # Check for explicit load error first
    if ontology_load_error:
        app.logger.error(f"Hierarchy requested, but ontology load failed: {ontology_load_error}")
        return jsonify({"error": f"Ontology load failed: {ontology_load_error}"}), 500

    # Check if data structures are empty *after* loading attempt finished without error
    # Use uri_registry as the primary check, as it should contain classes, individuals, and properties
    if not uri_registry:
         app.logger.error("Hierarchy requested, but uri_registry is empty after loading attempt completed without explicit error.")
         # Provide a slightly different error message for XML loading
         return jsonify({"error": "Ontology data structures are empty after processing the OWL file. Check server logs (DEBUG level) for details on element processing, namespace issues, or potential reasons (e.g., file content, structure mismatches, logic issues)."}), 500

    try:
        # --- Determine Top Level Classes ---
        # Recalculate using the same logic as in load_ontology Pass 3
        all_subclass_uris = set()
        for details in class_details.values():
            all_subclass_uris.update(details.get("subClasses", set())) # Use pre-computed subclasses

        defined_classes = set(class_details.keys())
        top_level_uris_calculated = defined_classes - all_subclass_uris

        # Refinement: Add classes whose only parent is owl:Thing or have no parents
        owl_thing_uri = _ns_tag("owl:Thing") # Use resolved owl:Thing URI
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
                    "hasSubClasses": bool(details.get("subClasses", set())),
                    "hasInstances": bool(details.get("instances", set()))
                })
            else:
                app.logger.warning(f"Top-level URI {uri} identified but not found in class_details during hierarchy generation.")


        if not top_classes_data and uri_registry: # Check registry has *something*
             app.logger.warning("Hierarchy requested, data structures populated but no top-level classes were identified.")


        return jsonify({
            "topClasses": top_classes_data,
            "uriRegistry": uri_registry
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/hierarchy: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing hierarchy: {e}"}), 500

# ... (get_children and get_details routes are likely okay, but double-check URI handling if issues arise) ...
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
                   # Use 404 directly
                   abort(404, description=f"Class URI <{class_uri}> not found in ontology data.")


    try:
        details = class_details[class_uri]
        subclass_data = []
        instance_data = []

        # Get subclasses
        for sub_uri in sorted(list(details.get("subClasses", set())), key=get_label):
            if sub_uri in class_details:
                 sub_details = class_details[sub_uri]
                 subclass_data.append({
                     "id": sub_uri,
                     "label": get_label(sub_uri),
                     "hasSubClasses": bool(sub_details.get("subClasses", set())),
                     "hasInstances": bool(sub_details.get("instances", set()))
                 })

        # Get instances
        for inst_uri in sorted(list(details.get("instances", set())), key=get_label):
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
                "description": details.get("description", ""),
                "superClasses": sorted([uri for uri in details.get("superClasses", set())], key=get_label),
                "subClasses": sorted([uri for uri in details.get("subClasses", set())], key=get_label),
                "instances": sorted([uri for uri in details.get("instances", set())], key=get_label)
            }
        elif item_uri in individual_details:
            item_type = "individual"
            details = individual_details[item_uri]
            formatted_properties = {}
            # Sort properties by their label (using get_label on the property URI)
            sorted_prop_uris = sorted(
                details.get("properties", {}).keys(),
                key=lambda prop_uri: get_label(prop_uri)
            )

            for prop_uri in sorted_prop_uris:
                # The values list already contains dicts like {"type": "uri/literal", "value": ..., "datatype": ...}
                # Sort values within a property? Maybe by value itself? For now, keep backend order.
                formatted_properties[prop_uri] = details["properties"][prop_uri]

            details_data = {
                "id": item_uri,
                "label": get_label(item_uri),
                "description": details.get("description", ""),
                "types": sorted([uri for uri in details.get("types", set())], key=get_label),
                "properties": formatted_properties
            }

        if details_data:
            return jsonify({"type": item_type, "details": details_data})
        else:
            # Check the registry for properties or other URIs
            if item_uri in uri_registry:
                 registry_info = uri_registry[item_uri]
                 # Provide minimal info for non-class/individual entities
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
    print("Loading ontology data from OWL/XML on startup...") # Update message
    load_ontology() # Load from OWL file

    if ontology_load_error:
        print(f"WARNING: Ontology loading failed: {ontology_load_error}")
        print("Flask app will run, but API calls will likely return errors.")
    # Update the warning message for OWL/XML context
    elif not uri_registry and not class_details and not individual_details:
         print("\n" + "="*60)
         print("WARNING: Ontology loading process completed BUT data structures are empty.")
         print("         This means the OWL/XML was likely parsed, but no Classes or Individuals")
         print("         were successfully identified and stored based on the current logic.")
         print("         Please check the following:")
         print("           1. Flask DEBUG logs above for details on processed elements and relationships.")
         print("           2. The content and structure of the OWL file ('panres_v2.owl').")
         print("           3. The XML Namespaces (NS dictionary) defined in app.py match the OWL file.")
         print("           4. The XML tags used for classes, individuals, properties, labels, etc.")
         print("              (e.g., owl:Class, owl:NamedIndividual, rdfs:label, rdfs:subClassOf)")
         print("              match the expectations in the load_ontology function.")
         print("="*60 + "\n")
         print("Flask app will run, but API calls will return errors due to missing data.")
    else:
        print("Ontology data loaded and pre-processed successfully.")
        print(f"  -> Registry: {len(uri_registry)} items, Classes: {len(class_details)}, Individuals: {len(individual_details)}")


    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 