import os
from flask import Flask, render_template, abort
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, RDFS, OWL
from urllib.parse import urldefrag

# --- Configuration ---
OWL_FILE = "panres_v2.owl"
# Define common namespaces (add more if needed from your ontology)
NAMESPACES = {
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": OWL,
    "panres": Namespace("http://www.semanticweb.org/gibson/ontologies/2024/1/panres#"), # Example - adjust if your ontology has a different base URI
    # Add other namespaces used in your ontology here
}
# --- End Configuration ---


app = Flask(__name__)

def get_ontology_data(filepath):
    """Parses the OWL file and extracts basic information."""
    if not os.path.exists(filepath):
        return None, f"Error: Ontology file not found at '{filepath}'"

    g = Graph()
    try:
        g.parse(filepath, format="xml") # Adjust format if needed (e.g., "turtle")
    except Exception as e:
        return None, f"Error parsing ontology file: {e}"

    data = {
        "classes": [],
        "object_properties": [],
        "datatype_properties": [],
        "individuals": [],
        "namespaces": {k: str(v) for k, v in NAMESPACES.items()}, # Pass namespaces to template
        "error": None
    }

    # Helper to get a display label (label or local name)
    def get_label(uri):
        label = g.value(uri, RDFS.label)
        if label:
            return str(label)
        # Try to create a prefixed name or use the fragment
        try:
            prefix, namespace, name = g.compute_qname(uri, generate=False)
            return f"{prefix}:{name}"
        except:
            # Fallback to fragment identifier
            return urldefrag(str(uri))[1] or str(uri)


    # Query for Classes
    for class_uri in g.subjects(predicate=RDF.type, object=OWL.Class):
        if isinstance(class_uri, rdflib.term.URIRef): # Ignore blank nodes
             data["classes"].append({"uri": str(class_uri), "label": get_label(class_uri)})

    # Query for Object Properties
    for prop_uri in g.subjects(predicate=RDF.type, object=OWL.ObjectProperty):
         if isinstance(prop_uri, rdflib.term.URIRef):
             data["object_properties"].append({"uri": str(prop_uri), "label": get_label(prop_uri)})

    # Query for Datatype Properties
    for prop_uri in g.subjects(predicate=RDF.type, object=OWL.DatatypeProperty):
         if isinstance(prop_uri, rdflib.term.URIRef):
             data["datatype_properties"].append({"uri": str(prop_uri), "label": get_label(prop_uri)})

    # Query for Named Individuals (optional, can be numerous)
    # for ind_uri in g.subjects(predicate=RDF.type, object=OWL.NamedIndividual):
    #      if isinstance(ind_uri, rdflib.term.URIRef):
    #          # Also get the class type(s) of the individual
    #          types = [get_label(t) for t in g.objects(ind_uri, RDF.type) if t != OWL.NamedIndividual]
    #          data["individuals"].append({
    #              "uri": str(ind_uri),
    #              "label": get_label(ind_uri),
    #              "types": types
    #          })

    # Sort results by label for better readability
    data["classes"].sort(key=lambda x: x["label"])
    data["object_properties"].sort(key=lambda x: x["label"])
    data["datatype_properties"].sort(key=lambda x: x["label"])
    # data["individuals"].sort(key=lambda x: x["label"])


    return data, None


@app.route('/')
def index():
    """Main route to display the ontology browser."""
    ontology_data, error = get_ontology_data(OWL_FILE)

    if error:
        # Display error message if file loading/parsing failed
        return render_template('error.html', error_message=error)

    if not ontology_data:
         # Handle case where data is None but no specific error message was set
         return render_template('error.html', error_message="Failed to load ontology data.")


    return render_template('index.html', data=ontology_data)

if __name__ == '__main__':
    app.run(debug=True) # debug=True for development, remove for production
