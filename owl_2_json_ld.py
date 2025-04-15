# Install rdflib if you haven't already: pip install rdflib
import rdflib
import json
import sys
import os

def convert_owl_to_jsonld_minimal_context(input_owl_file, output_jsonld_file):
    """
    Converts an OWL file (RDF/XML format) to JSON-LD format,
    preserving all information using a minimal context (no explicit mappings).

    Args:
        input_owl_file (str): Path to the input OWL file (e.g., 'panres_v2.owl').
        output_jsonld_file (str): Path to save the output JSON-LD file.
    """
    if not os.path.exists(input_owl_file):
        print(f"Error: Input file not found at '{input_owl_file}'", file=sys.stderr)
        return

    try:
        # Create an RDF graph
        g = rdflib.Graph()

        # Parse the input OWL file (assuming RDF/XML format)
        print(f"Parsing '{input_owl_file}'...")
        g.parse(input_owl_file, format='xml')
        print(f"Parsing complete. Found {len(g)} triples.")

        # Define the *minimal* JSON-LD context
        # Use @vocab for the default ontology namespace.
        # Define standard prefixes used in OWL/RDF.
        # NO explicit mappings for properties or types beyond these basics.
        context = {
            "@vocab": "http://myonto.com/PanResOntology.owl#",
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "owl": "http://www.w3.org/2002/07/owl#"
            # No 'xsd' prefix defined here - datatypes will likely appear as full URIs
            # No explicit mappings for 'has_length', 'is_from_database', etc.
        }

        # Serialize the graph to JSON-LD
        print(f"Serializing graph to JSON-LD: '{output_jsonld_file}'...")
        # Using indent for readability.
        # compact_arrays=True makes single values not appear in arrays ([value])
        jsonld_data_bytes = g.serialize(
            format='json-ld',
            context=context,
            indent=2,
            encoding='utf-8',
            compact_arrays=True # Usually preferred for JSON-LD readability
        )

        # Write the output file
        with open(output_jsonld_file, 'wb') as f: # Write as bytes
            f.write(jsonld_data_bytes)

        print("Conversion successful.")
        print(f"Output written to '{output_jsonld_file}'")

    except Exception as e:
        print(f"An error occurred during conversion: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Get filenames from command line arguments if provided
    if len(sys.argv) == 3:
        input_file = sys.argv[1]
        output_file = sys.argv[2]
    elif len(sys.argv) == 1:
        # Use default filenames if no arguments are provided
        input_file = 'panres_v2.owl'
        output_file = 'panres_minimal.jsonld' # Changed default output name
        print("Usage: python convert_script_minimal.py <input_owl_file> <output_jsonld_file>")
        print(f"Using default filenames: '{input_file}' -> '{output_file}'")
    else:
        print("Usage: python convert_script_minimal.py <input_owl_file> <output_jsonld_file>")
        sys.exit(1)

    # Run the conversion
    convert_owl_to_jsonld_minimal_context(input_file, output_file)