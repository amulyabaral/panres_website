import os
# import gc # No longer needed for manual collection
from flask import Flask, jsonify, render_template, abort, url_for # Added url_for for potential future use
from urllib.parse import unquote, quote # Added quote for safe URI inclusion in queries
# import xml.etree.ElementTree as ET # No longer needed
# from collections import defaultdict # No longer needed
import logging

# --- rdflib Imports ---
from rdflib import Graph, ConjunctiveGraph, Literal, URIRef, Namespace
from rdflib.namespace import RDF, RDFS, OWL, XSD # Common namespaces
from rdflib.plugins.store.sqlalchemy import SQLAlchemy as SQLAlchemyStore # Store plugin

# --- Configuration ---
ONTOLOGY_FILENAME = "panres_v2.owl" # Source OWL file
ONTOLOGY_FILE_PATH = os.path.join(os.path.dirname(__file__), ONTOLOGY_FILENAME)

# --- Persistent Store Configuration ---
# IMPORTANT: Use Render's Persistent Disk mount path here in production
# For local dev, this will create the DB in the project directory
DB_DIR = os.environ.get('ONTOLOGY_DB_DIR', os.path.join(os.path.dirname(__file__), 'rdf_store'))
DB_FILENAME = "ontology_store.db"
DB_PATH = os.path.join(DB_DIR, DB_FILENAME)
STORE_URI = f"sqlite:///{DB_PATH}"
# Use a unique identifier for your graph within the store (optional but good practice)
GRAPH_ID = URIRef("http://myonto.com/PanResOntology") # Use your ontology URI if available

# --- Flask App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.logger.setLevel(logging.DEBUG) # Keep DEBUG for detailed logs during dev/troubleshooting

# --- Global Graph Object (using persistent store) ---
graph = None
ontology_load_error = None # Store potential loading/parsing errors

# --- Namespace Setup (Define your ontology's base namespace if needed) ---
# Attempt to find base namespace during parsing later, or define it here if known
BASE = Namespace("http://myonto.com/PanResOntology.owl#") # Replace if needed, or leave empty initially
# Add other namespaces used in your ontology if not standard (RDF, RDFS, OWL, XSD)
# e.g., MYNS = Namespace("http://example.org/my-schema#")

# --- Helper Functions ---

def _get_label(uri_ref, g):
    """Queries the graph for the rdfs:label of a URI."""
    if not isinstance(uri_ref, URIRef):
        uri_ref = URIRef(uri_ref) # Ensure it's a URIRef

    label = g.value(subject=uri_ref, predicate=RDFS.label)
    if label:
        return str(label)
    # Fallback to local name if no label found
    return _local_name(str(uri_ref))

def _local_name(uri):
    """Extracts the local name part of a URI (fragment or last path segment)."""
    if not uri: return ""
    try:
        if '#' in uri:
            return uri.split('#')[-1]
        return uri.split('/')[-1]
    except Exception as e:
        app.logger.warning(f"Could not extract local name from URI '{uri}': {e}")
        return str(uri)

def _format_uri_list(uri_list, g):
    """Sorts a list of URIs by label and formats them."""
    return sorted([str(uri) for uri in uri_list], key=lambda u: _get_label(u, g))

def _format_property_values(values, g):
    """Formats property values (literals or URIs) for JSON output."""
    results = []
    for v in values:
        if isinstance(v, Literal):
            results.append({
                "type": "literal",
                "value": str(v),
                "datatype": str(v.datatype) if v.datatype else None,
                # "lang": v.language # Add if needed
            })
        elif isinstance(v, URIRef):
            results.append({
                "type": "uri",
                "value": str(v)
            })
        else: # BNode or other? Skip for now or handle as needed
             app.logger.debug(f"Skipping non-Literal/URI property value: {v} (type: {type(v)})")
    return results


# --- Ontology Loading and Initialization ---
def initialize_ontology_graph():
    """Initializes the rdflib graph with the persistent store."""
    global graph, ontology_load_error, BASE
    ontology_load_error = None # Reset error state

    app.logger.info(f"Initializing ontology store. DB path: {STORE_URI}")

    store = SQLAlchemyStore(identifier=GRAPH_ID)
    g = ConjunctiveGraph(store=store, identifier=GRAPH_ID)

    try:
        # Ensure the directory for the SQLite file exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        app.logger.debug(f"Attempting to open store: {STORE_URI}")
        g.open(STORE_URI, create=True) # Create DB file if it doesn't exist
        app.logger.debug(f"Store opened successfully. Contains {len(g)} triples.")

        # Check if the graph is empty (e.g., first run or empty DB)
        # len(g) can be slow on large stores initially, use ASK query if needed
        # Or check if the source file is newer than the DB? More complex.
        if len(g) == 0:
            app.logger.info(f"Graph is empty. Parsing ontology file: {ONTOLOGY_FILE_PATH}")
            if not os.path.exists(ONTOLOGY_FILE_PATH):
                 ontology_load_error = f"Ontology source file not found: {ONTOLOGY_FILE_PATH}"
                 app.logger.error(ontology_load_error)
                 g.close()
                 return None # Return None to indicate failure

            try:
                # Determine format (common ones: 'xml', 'turtle', 'n3', 'json-ld')
                # Assuming OWL/XML based on original code
                file_format = "xml"
                g.parse(ONTOLOGY_FILE_PATH, format=file_format)
                g.commit() # Commit changes to the SQLite DB
                app.logger.info(f"Parsing complete. Graph now contains {len(g)} triples.")

                # --- Try to find base namespace after parsing ---
                # This is heuristic, might not always work perfectly
                ont_uri = g.value(predicate=RDF.type, object=OWL.Ontology)
                if ont_uri and isinstance(ont_uri, URIRef):
                    base_str = str(ont_uri)
                    if '#' in base_str:
                        BASE = Namespace(base_str.rsplit('#', 1)[0] + '#')
                    else:
                        BASE = Namespace(base_str.rsplit('/', 1)[0] + '/')
                    app.logger.info(f"Detected ontology URI, setting BASE namespace to: {BASE}")
                else:
                    app.logger.warning("Could not automatically detect ontology base namespace.")
                # --- End Base Namespace Detection ---


            except Exception as parse_error:
                ontology_load_error = f"Error parsing ontology file '{ONTOLOGY_FILE_PATH}': {parse_error}"
                app.logger.error(ontology_load_error, exc_info=True)
                g.close()
                return None # Return None on parsing error
        else:
            app.logger.info(f"Using existing ontology data from {DB_PATH}")
            # Optionally: Re-detect base namespace even if DB exists?
            ont_uri = g.value(predicate=RDF.type, object=OWL.Ontology)
            if ont_uri and isinstance(ont_uri, URIRef):
                 base_str = str(ont_uri)
                 if '#' in base_str: BASE = Namespace(base_str.rsplit('#', 1)[0] + '#')
                 else: BASE = Namespace(base_str.rsplit('/', 1)[0] + '/')
                 app.logger.info(f"Using BASE namespace from existing DB: {BASE}")


        # Bind common namespaces for cleaner queries
        g.bind("rdf", RDF)
        g.bind("rdfs", RDFS)
        g.bind("owl", OWL)
        g.bind("xsd", XSD)
        if BASE: g.bind("base", BASE) # Bind detected or predefined base

        return g # Return the initialized graph

    except Exception as e:
        ontology_load_error = f"An unexpected error occurred during graph initialization: {e}"
        app.logger.error(ontology_load_error, exc_info=True)
        if g: g.close() # Ensure store is closed on error
        return None # Return None on general error


# --- Initialize Graph on Startup ---
graph = initialize_ontology_graph()

# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    # Pass the potential load error to the template
    # Check if graph is None OR if ontology_load_error is set
    effective_load_error = ontology_load_error or ("Ontology graph failed to initialize." if graph is None else None)
    return render_template('index.html', load_error=effective_load_error)

@app.route('/api/hierarchy')
def get_hierarchy():
    """Provides top-level classes using SPARQL queries."""
    if ontology_load_error or graph is None:
        error_msg = ontology_load_error or "Ontology graph not available."
        app.logger.error(f"Hierarchy requested, but ontology load/init failed: {error_msg}")
        return jsonify({"error": f"Ontology access error: {error_msg}"}), 500

    try:
        # Query for classes that are not subclasses of any other class (excluding owl:Thing)
        # This might include classes directly under owl:Thing or with no superclass declared
        query = """
            SELECT DISTINCT ?class (SAMPLE(?label) AS ?labelSample)
            WHERE {
              ?class a owl:Class .
              OPTIONAL { ?class rdfs:label ?label . }

              # Filter out classes that have a superclass which is also a class
              # (and not owl:Thing itself)
              FILTER NOT EXISTS {
                ?class rdfs:subClassOf ?parent .
                ?parent a owl:Class .
                FILTER (?parent != owl:Thing && ?parent != ?class) # Exclude self-subclassing and owl:Thing
              }

              # Exclude owl:Thing itself from the results
              FILTER (?class != owl:Thing)
            }
            GROUP BY ?class
            ORDER BY (LCASE(STR(?labelSample))) # Order by label case-insensitively
        """

        app.logger.debug("Executing hierarchy query...")
        results = graph.query(query)
        app.logger.debug(f"Hierarchy query returned {len(results)} potential top classes.")

        top_classes_data = []
        uri_registry_subset = {} # Build a registry subset for the frontend

        for row in results:
            class_uri = str(row.class)
            label = str(row.labelSample) if row.labelSample else _local_name(class_uri)

            # Check if it has subclasses (more efficient than getting all subclasses)
            has_subclasses_query = f"""
                ASK {{ ?sub rdfs:subClassOf <{class_uri}> . ?sub a owl:Class . FILTER(?sub != <{class_uri}>) }}
            """
            has_subclasses = graph.query(has_subclasses_query).askAnswer

            # Check if it has instances
            has_instances_query = f"""
                ASK {{ ?inst rdf:type <{class_uri}> . ?inst a owl:NamedIndividual . }}
            """
            has_instances = graph.query(has_instances_query).askAnswer

            class_info = {
                "id": class_uri,
                "label": label,
                "hasSubClasses": has_subclasses,
                "hasInstances": has_instances
            }
            top_classes_data.append(class_info)
            uri_registry_subset[class_uri] = {"label": label, "type": "class"}

        app.logger.info(f"Identified {len(top_classes_data)} top-level classes.")
        if not top_classes_data:
             app.logger.warning("Hierarchy query returned no top-level classes.")

        # Note: Sending the *full* URI registry might be too large with rdflib.
        # We send only the top-level ones initially. The frontend can fetch details
        # as needed, and we can augment the registry client-side or make another endpoint.
        # For simplicity now, just send the top-level registry info.
        return jsonify({
            "topClasses": top_classes_data,
            "uriRegistry": uri_registry_subset # Send subset for initial view
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/hierarchy: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing hierarchy: {e}"}), 500


@app.route('/api/children/<path:node_uri_encoded>')
def get_children(node_uri_encoded):
    """Provides direct subclasses and instances using SPARQL queries."""
    if ontology_load_error or graph is None:
        # ... (error handling as above) ...
        error_msg = ontology_load_error or "Ontology graph not available."
        return jsonify({"error": f"Ontology access error: {error_msg}"}), 500

    class_uri_str = unquote(node_uri_encoded)
    class_uri = URIRef(class_uri_str) # Convert to URIRef for queries

    # Verify it's actually a class first
    is_class_query = f"ASK {{ <{class_uri_str}> a owl:Class . }}"
    if not graph.query(is_class_query).askAnswer:
         # Check if it's an individual (no children) or something else
         is_individual_query = f"ASK {{ <{class_uri_str}> a owl:NamedIndividual . }}"
         if graph.query(is_individual_query).askAnswer:
              return jsonify({"subClasses": [], "instances": []}) # Individuals have no children
         else:
              # Could be a property or non-existent URI
              # Check existence minimally
              exists_query = f"ASK {{ <{class_uri_str}> ?p ?o . }}"
              if graph.query(exists_query).askAnswer:
                   return jsonify({"subClasses": [], "instances": []}) # Other known URIs have no hierarchy children
              else:
                   abort(404, description=f"URI <{class_uri_str}> not found or not a Class.")


    try:
        subclass_data = []
        instance_data = []
        children_registry = {} # Registry info for direct children

        # --- Query for Subclasses ---
        subclass_query = """
            SELECT DISTINCT ?sub (SAMPLE(?label) AS ?labelSample)
            WHERE {{
              ?sub rdfs:subClassOf <{parent_uri}> .
              ?sub a owl:Class .
              FILTER (?sub != <{parent_uri}> && ?sub != owl:Thing) # Exclude self and owl:Thing
              OPTIONAL {{ ?sub rdfs:label ?label . }}
            }}
            GROUP BY ?sub
            ORDER BY (LCASE(STR(?labelSample)))
        """.format(parent_uri=class_uri_str) # Use formatted string for URI

        app.logger.debug(f"Executing subclass query for {class_uri_str}...")
        subclass_results = graph.query(subclass_query)
        app.logger.debug(f"Found {len(subclass_results)} subclasses.")

        for row in subclass_results:
            sub_uri = str(row.sub)
            label = str(row.labelSample) if row.labelSample else _local_name(sub_uri)

            # Check if the subclass itself has subclasses
            has_subclasses_query = f"ASK {{ ?grandchild rdfs:subClassOf <{sub_uri}> . ?grandchild a owl:Class . FILTER(?grandchild != <{sub_uri}>) }}"
            has_subclasses = graph.query(has_subclasses_query).askAnswer

            # Check if the subclass has instances
            has_instances_query = f"ASK {{ ?inst rdf:type <{sub_uri}> . ?inst a owl:NamedIndividual . }}"
            has_instances = graph.query(has_instances_query).askAnswer

            sub_info = {
                "id": sub_uri,
                "label": label,
                "hasSubClasses": has_subclasses,
                "hasInstances": has_instances
            }
            subclass_data.append(sub_info)
            children_registry[sub_uri] = {"label": label, "type": "class"}

        # --- Query for Instances ---
        instance_query = """
            SELECT DISTINCT ?inst (SAMPLE(?label) AS ?labelSample)
            WHERE {{
              ?inst rdf:type <{parent_uri}> .
              ?inst a owl:NamedIndividual . # Ensure it's explicitly a NamedIndividual
              OPTIONAL {{ ?inst rdfs:label ?label . }}
            }}
            GROUP BY ?inst
            ORDER BY (LCASE(STR(?labelSample)))
        """.format(parent_uri=class_uri_str)

        app.logger.debug(f"Executing instance query for {class_uri_str}...")
        instance_results = graph.query(instance_query)
        app.logger.debug(f"Found {len(instance_results)} instances.")

        for row in instance_results:
            inst_uri = str(row.inst)
            label = str(row.labelSample) if row.labelSample else _local_name(inst_uri)
            inst_info = {
                "id": inst_uri,
                "label": label
                # Individuals don't have hasSubClasses/hasInstances in this context
            }
            instance_data.append(inst_info)
            children_registry[inst_uri] = {"label": label, "type": "individual"}

        return jsonify({
            "subClasses": subclass_data,
            "instances": instance_data,
            "uriRegistryUpdate": children_registry # Send registry info for the children
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/children for {class_uri_str}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing children for {class_uri_str}: {e}"}), 500


@app.route('/api/details/<path:node_uri_encoded>')
def get_details(node_uri_encoded):
    """Provides full details for a specific URI using SPARQL queries."""
    if ontology_load_error or graph is None:
        # ... (error handling as above) ...
        error_msg = ontology_load_error or "Ontology graph not available."
        return jsonify({"error": f"Ontology access error: {error_msg}"}), 500

    item_uri_str = unquote(node_uri_encoded)
    item_uri = URIRef(item_uri_str)

    try:
        # --- Determine Type (Class, Individual, Property, Other?) ---
        item_type = None
        if graph.query(f"ASK {{ <{item_uri_str}> a owl:Class . }}").askAnswer:
            item_type = "class"
        elif graph.query(f"ASK {{ <{item_uri_str}> a owl:NamedIndividual . }}").askAnswer:
            item_type = "individual"
        elif graph.query(f"ASK {{ <{item_uri_str}> a rdf:Property . }}").askAnswer or \
             graph.query(f"ASK {{ <{item_uri_str}> a owl:ObjectProperty . }}").askAnswer or \
             graph.query(f"ASK {{ <{item_uri_str}> a owl:DatatypeProperty . }}").askAnswer or \
             graph.query(f"ASK {{ <{item_uri_str}> a owl:AnnotationProperty . }}").askAnswer:
            item_type = "property"
        else:
             # Check if the URI exists at all
             if not graph.query(f"ASK {{ <{item_uri_str}> ?p ?o . }}").askAnswer and \
                not graph.query(f"ASK {{ ?s ?p <{item_uri_str}> . }}").askAnswer:
                  abort(404, description=f"URI <{item_uri_str}> not found in the ontology data.")
             else:
                  item_type = "other" # URI exists but isn't a Class, Individual, or known Property type

        app.logger.debug(f"Determined type for <{item_uri_str}>: {item_type}")

        # --- Fetch Details based on Type ---
        details_data = {"id": item_uri_str}
        label = _get_label(item_uri, graph)
        details_data["label"] = label

        # Get description (rdfs:comment is common)
        description = graph.value(subject=item_uri, predicate=RDFS.comment)
        details_data["description"] = str(description) if description else ""

        registry_update = {item_uri_str: {"label": label, "type": item_type}}

        if item_type == "class":
            # Superclasses (direct)
            superclasses = graph.objects(subject=item_uri, predicate=RDFS.subClassOf)
            # Filter out blank nodes and owl:Thing if desired
            details_data["superClasses"] = _format_uri_list(
                [s for s in superclasses if isinstance(s, URIRef) and s != OWL.Thing], graph
            )

            # Subclasses (direct)
            subclasses = graph.subjects(predicate=RDFS.subClassOf, object=item_uri)
            details_data["subClasses"] = _format_uri_list(
                 [s for s in subclasses if isinstance(s, URIRef) and graph.value(s, RDF.type) == OWL.Class], graph # Ensure they are classes
            )

            # Instances (direct)
            instances = graph.subjects(predicate=RDF.type, object=item_uri)
            details_data["instances"] = _format_uri_list(
                 [i for i in instances if isinstance(i, URIRef) and graph.value(i, RDF.type) == OWL.NamedIndividual], graph # Ensure they are named individuals
            )

            # Add related items to registry update
            for uri_list in [details_data["superClasses"], details_data["subClasses"], details_data["instances"]]:
                for uri in uri_list:
                    if uri not in registry_update:
                         # Determine type quickly (might need more robust check)
                         rel_type = "class" if uri in details_data["superClasses"] or uri in details_data["subClasses"] else "individual"
                         registry_update[uri] = {"label": _get_label(uri, graph), "type": rel_type}


        elif item_type == "individual":
            # Types (direct classes)
            types = graph.objects(subject=item_uri, predicate=RDF.type)
            details_data["types"] = _format_uri_list(
                [t for t in types if isinstance(t, URIRef) and t != OWL.NamedIndividual and graph.value(t, RDF.type) == OWL.Class], graph # Filter out non-classes
            )

            # Properties and Values
            properties = {}
            # Query all outgoing triples from the individual
            for p, o in graph.predicate_objects(subject=item_uri):
                # Exclude non-property predicates like rdf:type, rdfs:label, rdfs:comment if handled separately
                if p in [RDF.type, RDFS.label, RDFS.comment]:
                    continue

                p_str = str(p)
                if p_str not in properties:
                    properties[p_str] = []

                # Format the object (Literal or URI)
                if isinstance(o, Literal):
                    properties[p_str].append({
                        "type": "literal",
                        "value": str(o),
                        "datatype": str(o.datatype) if o.datatype else None,
                        # "lang": o.language
                    })
                elif isinstance(o, URIRef):
                     prop_val_uri = str(o)
                     properties[p_str].append({
                        "type": "uri",
                        "value": prop_val_uri
                     })
                     # Add property value URI to registry update
                     if prop_val_uri not in registry_update:
                          # Determine type of the linked URI (could be slow if many properties)
                          linked_type = "other"
                          if graph.query(f"ASK {{ <{prop_val_uri}> a owl:Class . }}").askAnswer: linked_type = "class"
                          elif graph.query(f"ASK {{ <{prop_val_uri}> a owl:NamedIndividual . }}").askAnswer: linked_type = "individual"
                          registry_update[prop_val_uri] = {"label": _get_label(prop_val_uri, graph), "type": linked_type}

                # else: Handle BNodes if necessary

            # Sort properties by label for consistent display
            sorted_prop_uris = sorted(properties.keys(), key=lambda p_uri: _get_label(p_uri, graph))
            details_data["properties"] = {p_uri: properties[p_uri] for p_uri in sorted_prop_uris}

            # Add property URIs and type URIs to registry update
            for uri in details_data["types"]:
                 if uri not in registry_update: registry_update[uri] = {"label": _get_label(uri, graph), "type": "class"}
            for uri in details_data["properties"].keys():
                 if uri not in registry_update: registry_update[uri] = {"label": _get_label(uri, graph), "type": "property"}


        elif item_type == "property":
            # Add property-specific details if needed (e.g., domain, range)
            domains = graph.objects(subject=item_uri, predicate=RDFS.domain)
            ranges = graph.objects(subject=item_uri, predicate=RDFS.range)
            details_data["domains"] = _format_uri_list([d for d in domains if isinstance(d, URIRef)], graph)
            details_data["ranges"] = _format_uri_list([r for r in ranges if isinstance(r, URIRef)], graph)
            # Add domain/range URIs to registry update
            for uri_list in [details_data["domains"], details_data["ranges"]]:
                for uri in uri_list:
                    if uri not in registry_update: registry_update[uri] = {"label": _get_label(uri, graph), "type": "class"} # Assume classes

        elif item_type == "other":
            details_data["message"] = f"URI exists but is not identified as a standard Class, Individual, or Property."


        return jsonify({
            "type": item_type,
            "details": details_data,
            "uriRegistryUpdate": registry_update # Send registry info for related items
        })

    except Exception as e:
        app.logger.error(f"Error processing /api/details for {item_uri_str}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error processing details for {item_uri_str}: {e}"}), 500


# --- Cleanup ---
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Closes the rdflib graph store connection."""
    if graph is not None:
        try:
            graph.close()
            app.logger.info("rdflib graph store closed.")
        except Exception as e:
            app.logger.error(f"Error closing rdflib graph store: {e}", exc_info=True)

# --- Main Execution ---
if __name__ == '__main__':
    # Initialization happens above when 'graph = initialize_ontology_graph()' is called
    print("--- Ontology Browser Backend ---")
    if ontology_load_error:
        print(f"WARNING: Ontology graph initialization failed: {ontology_load_error}")
        print("Flask app will run, but API calls will likely return errors.")
    elif graph is None:
        print(f"WARNING: Ontology graph is None after initialization attempt.")
        print("Flask app will run, but API calls will likely return errors.")
    else:
        print(f"Ontology graph initialized successfully from store: {STORE_URI}")
        print(f"Graph contains approx {len(graph)} triples.") # len() might be slow on cold start

    port_to_use = int(os.environ.get('PORT', 8080))
    print(f"Attempting to run Flask app on host 0.0.0.0 and port {port_to_use}")

    # Use threaded=True for handling multiple requests during development
    # Use gunicorn in production (debug=False)
    app.run(host='0.0.0.0', port=port_to_use, debug=False, threaded=True) 