import os
import json
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD
from urllib.parse import urldefrag, quote, unquote
import gc # Import garbage collector

# --- Configuration ---
OWL_FILENAME = "panres_v2.owl"
OWL_FILE_PATH = os.path.join(os.path.dirname(__file__), OWL_FILENAME)
OUTPUT_JSON_FILENAME = "ontology_cache.json"
# Save the cache file in the same directory or a specific data directory
OUTPUT_JSON_PATH = os.path.join(os.path.dirname(__file__), OUTPUT_JSON_FILENAME)

# --- Helper Functions (Copied from app.py) ---
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

# --- Main Processing Logic (Adapted from app.py's load_and_process_ontology) ---
def create_ontology_cache():
    """Loads the OWL file, processes it, and saves the structured data to JSON."""
    print(f"Starting ontology processing from: {OWL_FILE_PATH}")
    if not os.path.exists(OWL_FILE_PATH):
        print(f"Error: Ontology file not found at {OWL_FILE_PATH}")
        return None

    g = Graph()
    try:
        # This is the memory-intensive step
        print("Parsing OWL file...")
        g.parse(OWL_FILE_PATH, format="xml") # Assuming RDF/XML format for .owl
        print(f"Ontology loaded successfully. Graph size: {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing ontology file: {e}")
        return None # Stop processing if parsing failed

    print("Processing graph into data structures...")
    data = {
        "classDetails": {},
        "individualDetails": {},
        "subClassMap": {}, # Map: parentId -> [childId]
        "classInstanceMap": {}, # Map: classId -> [instanceId]
        "uriRegistry": {}, # Map: uri -> { type: 'class'/'individual'/'property', label: '...' }
        "topClasses": [] # List of top-level class IDs
    }
    processed_classes = set()
    processed_individuals = set() # Make sure this set is defined

    # --- 1. Process Classes (Same logic as in app.py:66-129) ---
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
            "hasSubClasses": False, # Will be set later
            "hasInstances": False   # Will be set later
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

    # Determine top-level classes
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
    print(f"Processed {len(data['classDetails'])} classes.")

    # --- 2. Process Individuals (Same logic as in app.py:131-192) ---
    # Find all things explicitly declared as NamedIndividual or typed with a known class
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
                     data["classInstanceMap"][type_id].append(ind_id) # Add the individual ID

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
            else: continue # Skip blank nodes or other types if not handled

            ind_obj["properties"][prop_uri].append(prop_entry)
            # Register property URI if not seen before
            if prop_uri not in data["uriRegistry"]:
                 prop_label = next(g.objects(p, RDFS.label), None)
                 data["uriRegistry"][prop_uri] = {"type": "property", "label": str(prop_label) if prop_label else get_local_name(p)}

        data["individualDetails"][ind_id] = ind_obj
        data["uriRegistry"][ind_id] = {"type": "individual", "label": ind_obj["label"]}
        processed_individuals.add(ind_uri)
    print(f"Processed {len(data['individualDetails'])} individuals.")

    # --- 3. Post-process: Set flags for children (Same logic as in app.py:195-200) ---
    print("Setting child flags...")
    for class_id in data["classDetails"]:
        if class_id in data["subClassMap"] and data["subClassMap"][class_id]:
            data["classDetails"][class_id]["hasSubClasses"] = True
        if class_id in data["classInstanceMap"] and data["classInstanceMap"][class_id]:
            data["classDetails"][class_id]["hasInstances"] = True

    # --- Clear Graph from Memory ---
    print("Clearing RDF graph from memory...")
    del g
    gc.collect() # Explicitly request garbage collection

    # --- Save to JSON ---
    print(f"Saving processed data to {OUTPUT_JSON_PATH}...")
    try:
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            # Use indent=None for smaller file size in production
            json.dump(data, f, ensure_ascii=False, indent=None)
        print(f"Successfully saved processed data to {OUTPUT_JSON_PATH}")
    except Exception as e:
        print(f"Error saving data to JSON: {e}")
        return None

    print("Preprocessing finished.")
    return data # Return data just in case

if __name__ == "__main__":
    create_ontology_cache() 