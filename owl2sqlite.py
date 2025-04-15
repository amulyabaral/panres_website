import sqlite3
import rdflib
from rdflib import URIRef, Literal, Namespace
import os

# --- Configuration ---
# Make sure this path points correctly to your OWL file
owl_file_path = 'panres_v2.owl'
# This will be the name of the SQLite database file created
db_file_path = 'panres_ontology.db'
# Base IRI of your ontology (used for stripping)
base_iri = "http://myonto.com/PanResOntology.owl#"
# Other namespaces to handle for cleaner output
xsd_iri = "http://www.w3.org/2001/XMLSchema#"
rdf_syntax_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
rdf_schema_ns = "http://www.w3.org/2000/01/rdf-schema#"
owl_ns = "http://www.w3.org/2002/07/owl#"

# Dictionary defining how to shorten common URIs/Namespaces
namespaces_to_strip_prefix = {
    base_iri: '', # Use only the fragment part for your ontology terms
    xsd_iri: 'xsd:', # Prefix XSD types for clarity (e.g., xsd:string)
    rdf_syntax_ns: 'rdf:', # Prefix RDF terms (e.g., rdf:type)
    rdf_schema_ns: 'rdfs:', # Prefix RDFS terms (e.g., rdfs:subClassOf)
    owl_ns: 'owl:' # Prefix OWL terms (e.g., owl:Class)
}

# --- Helper Function to Clean Identifiers ---
def clean_identifier(term):
    """
    Cleans URI references using the namespaces_to_strip_prefix map.
    Returns the string representation of literals.
    Returns None for unhandled types like Blank Nodes.
    """
    if isinstance(term, URIRef):
        term_str = str(term)
        for ns, prefix in namespaces_to_strip_prefix.items():
            if term_str.startswith(ns):
                # Get the part after # or /
                fragment = term_str.split('#')[-1] if '#' in term_str else term_str.split('/')[-1]
                # Special case: if it's the base IRI and prefix is empty, just return the fragment
                if ns == base_iri and prefix == '':
                    return fragment
                # Otherwise, return the defined prefix + fragment
                return prefix + fragment
        # If no known namespace matches, return the full URI
        return term_str
    elif isinstance(term, Literal):
        # For literals, return the value as a string. Datatype is handled separately.
        return str(term.value)
    else:
        # Currently skipping Blank Nodes (BNode)
        return None

def get_literal_datatype(term):
    """Gets the cleaned datatype URI string for a literal, if available."""
    if isinstance(term, Literal) and term.datatype:
        dt_str = str(term.datatype)
        # Apply the same cleaning logic as for identifiers
        for ns, prefix in namespaces_to_strip_prefix.items():
            if dt_str.startswith(ns):
                 fragment = dt_str.split('#')[-1] if '#' in dt_str else dt_str.split('/')[-1]
                 if ns == base_iri and prefix == '': # Unlikely for datatypes, but consistent
                     return fragment
                 return prefix + fragment
        # Return full datatype URI if not in the mapping
        return dt_str
    return None # No datatype specified for the literal

# --- Main Script ---
def convert_owl_to_sqlite(owl_file, db_file):
    """
    Parses an OWL file and stores its triples in an SQLite database's 'Triples' table.
    """
    if not os.path.exists(owl_file):
        print(f"Error: Input OWL file not found at '{owl_file}'")
        return

    print(f"Starting conversion for: {owl_file}")
    print("Step 1: Parsing OWL file...")
    graph = rdflib.Graph()
    try:
        # rdflib automatically detects RDF/XML format from the .owl extension or content
        graph.parse(owl_file)
        print(f" -> Successfully parsed {len(graph)} RDF triples.")
    except Exception as e:
        print(f"Error parsing OWL file: {e}")
        return

    print(f"Step 2: Connecting to SQLite database: {db_file}")
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        print("Step 3: Setting up database table 'Triples' (dropping if exists)...")
        cursor.execute("DROP TABLE IF EXISTS Triples")
        cursor.execute("""
            CREATE TABLE Triples (
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                object_is_literal INTEGER NOT NULL CHECK(object_is_literal IN (0, 1)), -- 1 for Literal, 0 for Resource/URI
                object_datatype TEXT -- Stores cleaned datatype (e.g., 'xsd:string') or NULL
            )
        """)
        conn.commit() # Commit table creation

        print("Step 4: Inserting triples into the database...")
        count = 0
        inserted_count = 0
        skipped_count = 0
        commit_interval = 5000 # Commit after every N inserts

        # Use a set to track processed triples (s, p, o cleaned strings) to avoid duplicates in DB
        # This is important if the source OWL has redundant statements
        inserted_signatures = set()

        for s, p, o in graph:
            count += 1

            # Clean the components of the triple
            subject_id = clean_identifier(s)
            predicate_id = clean_identifier(p)
            object_val = clean_identifier(o) # Value for literal, cleaned URI for resource
            is_literal = isinstance(o, Literal)
            datatype = get_literal_datatype(o) # Cleaned datatype or None

            # Skip if any part couldn't be processed (e.g., Blank Nodes)
            if subject_id is None or predicate_id is None or object_val is None:
                skipped_count += 1
                continue

            # Create a signature for duplicate checking
            triple_signature = (subject_id, predicate_id, object_val, is_literal, datatype)
            if triple_signature in inserted_signatures:
                skipped_count +=1 # Count as skipped due to duplication
                continue
            inserted_signatures.add(triple_signature)

            # Insert the cleaned triple
            try:
                cursor.execute("""
                    INSERT INTO Triples (subject, predicate, object, object_is_literal, object_datatype)
                    VALUES (?, ?, ?, ?, ?)
                """, (subject_id, predicate_id, object_val, 1 if is_literal else 0, datatype))
                inserted_count += 1
            except sqlite3.Error as insert_e:
                 print(f"Error inserting triple signature: {triple_signature}. Error: {insert_e}")
                 skipped_count += 1 # Count errors as skipped

            # Commit periodically to save progress
            if inserted_count % commit_interval == 0:
                conn.commit()
                print(f" -> Committed {inserted_count} inserted triples...") # Optional progress update

        # Final commit for any remaining inserts
        conn.commit()

        print("Step 5: Creating database indices for faster queries...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subject ON Triples (subject)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_predicate ON Triples (predicate)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_object ON Triples (object, object_is_literal)") # Useful for finding specific values/links

        conn.commit() # Commit index creation

        print("\n--- Conversion Summary ---")
        print(f"Total RDF triples read from OWL: {len(graph)}")
        print(f"Unique triples inserted into DB: {inserted_count}")
        print(f"Triples skipped (duplicates, errors, BNodes): {skipped_count}")
        print(f"Database saved successfully to: {db_file}")

    except sqlite3.Error as e:
        print(f"Database error occurred: {e}")
        if conn:
            conn.rollback() # Rollback changes on error
    except Exception as e:
        print(f"An unexpected error occurred during conversion: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

# --- Run the Conversion ---
if __name__ == "__main__":
    convert_owl_to_sqlite(owl_file_path, db_file_path)