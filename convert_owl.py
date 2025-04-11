import rdflib
import json
import sys # Used for command-line arguments

def convert_owl_to_jsonld(owl_file_path, json_output_path):
    """
    Converts an OWL file (in RDF/XML or other RDF formats) to JSON-LD.

    Args:
        owl_file_path (str): The path to the input OWL file.
        json_output_path (str): The path where the output JSON-LD file will be saved.
    """
    print(f"Loading graph from: {owl_file_path}...")
    g = rdflib.Graph()
    try:
        # Attempt to parse the file, guessing the format (handles RDF/XML, Turtle, etc.)
        g.parse(owl_file_path, format=rdflib.util.guess_format(owl_file_path))
        print(f"Graph loaded successfully with {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing the OWL file: {e}")
        return

    print(f"Serializing graph to JSON-LD: {json_output_path}...")
    try:
        # Serialize the graph to JSON-LD format
        # Use compact_keys=True and a basic context for potentially smaller output,
        # but standard serialization without context is safer for full preservation.
        # Set indent=None for smallest file size, or indent=2 for readability.
        json_ld_data = g.serialize(format='json-ld', indent=2) # Use indent=None for minimum size

        # Write the JSON-LD output to the specified file
        with open(json_output_path, 'w', encoding='utf-8') as f:
            f.write(json_ld_data)
        print("Conversion successful.")

    except Exception as e:
        print(f"Error serializing graph to JSON-LD: {e}")

# --- Script Execution ---
if __name__ == "__main__":
    # Check if command-line arguments are provided
    if len(sys.argv) != 3:
        print("Usage: python convert_owl.py <input_owl_file.owl> <output_file.jsonld>")
        # Example usage if no arguments provided:
        print("\nExample: Using default filenames 'input.owl' and 'output.jsonld'")
        input_file = "input.owl" # Replace with your actual input filename
        output_file = "output.jsonld" # Replace with your desired output filename
        # Make sure 'input.owl' exists or change the filename here before running.
        # You might need to create a dummy input.owl for testing if you run without args.
        try:
            # Create a dummy input file for demonstration if it doesn't exist
            import os
            if not os.path.exists(input_file):
                 with open(input_file, "w") as f:
                     # Add minimal valid RDF content if creating dummy file
                     f.write("""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                                xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
                                <rdfs:Class rdf:about="http://example.org/MyClass"/>
                             </rdf:RDF>""")
                 print(f"Created dummy '{input_file}' for demonstration.")
            convert_owl_to_jsonld(input_file, output_file)
        except Exception as e:
            print(f"Could not run example: {e}")

    else:
        input_file = sys.argv[1]
        output_file = sys.argv[2]
        convert_owl_to_jsonld(input_file, output_file)