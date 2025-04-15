import os
from flask import Flask, render_template, abort
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, OWL
from urllib.parse import urldefrag
import json

# --- Configuration ---
# OWL_FILE = "panres_v2.owl" # Previous config
JSONLD_FILE = "panres_minimal.jsonld" # New config: Point to the JSON-LD file
# Define common namespaces (add more if needed from your ontology)
# These might still be useful as fallbacks or for specific lookups,
# but rdflib will also discover namespaces during parsing.
NAMESPACES = {
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": OWL,
    "panres": Namespace("http://www.semanticweb.org/gibson/ontologies/2024/1/panres#"), # Example - adjust if needed
    # Add other namespaces used in your ontology here
}
# --- End Configuration ---


app = Flask(__name__)

def get_ontology_data(filepath):
    """Parses the JSON-LD file and extracts basic information."""
    if not os.path.exists(filepath):
        return None, f"Error: JSON-LD file not found at '{filepath}'"

    g = Graph()
    try:
        g.parse(filepath, format="json-ld")
    except Exception as e:
        return None, f"Error parsing JSON-LD file '{filepath}': {e}"

    discovered_namespaces = {prefix: str(ns) for prefix, ns in g.namespaces()}
    all_namespaces = {**NAMESPACES, **discovered_namespaces}

    data = {
        "classes": [],
        "object_properties": [],
        "datatype_properties": [],
        "individuals": [],
        "namespaces": {k: str(v) for k, v in all_namespaces.items()},
        "error": None
    }

    def get_label(uri):
        if not isinstance(uri, URIRef):
            return str(uri)

        label = g.value(uri, RDFS.label)
        if label:
            return str(label)
        try:
            prefix, namespace, name = g.compute_qname(uri, generate=True)
            return f"{prefix}:{name}"
        except Exception:
            fragment = urldefrag(str(uri))[1]
            return fragment if fragment else str(uri)


    for class_uri in g.subjects(predicate=RDF.type, object=OWL.Class):
        if isinstance(class_uri, URIRef):
             data["classes"].append({"uri": str(class_uri), "label": get_label(class_uri)})

    for prop_uri in g.subjects(predicate=RDF.type, object=OWL.ObjectProperty):
         if isinstance(prop_uri, URIRef):
             data["object_properties"].append({"uri": str(prop_uri), "label": get_label(prop_uri)})

    for prop_uri in g.subjects(predicate=RDF.type, object=OWL.DatatypeProperty):
         if isinstance(prop_uri, URIRef):
             data["datatype_properties"].append({"uri": str(prop_uri), "label": get_label(prop_uri)})

    data["classes"].sort(key=lambda x: x["label"])
    data["object_properties"].sort(key=lambda x: x["label"])
    data["datatype_properties"].sort(key=lambda x: x["label"])


    return data, None


@app.route('/')
def index():
    """Main route to display the ontology browser."""
    ontology_data, error = get_ontology_data(JSONLD_FILE)

    if error:
        return render_template('error.html', error_message=error, filename=JSONLD_FILE)

    if not ontology_data:
         return render_template('error.html', error_message=f"Failed to load ontology data from {JSONLD_FILE}.", filename=JSONLD_FILE)


    return render_template('index.html', data=ontology_data, filename=JSONLD_FILE)

if __name__ == '__main__':
    app.run(debug=True)
