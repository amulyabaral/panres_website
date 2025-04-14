import rdflib
import sqlite3
import os
from rdflib import RDF, RDFS, OWL, URIRef, Literal, BNode # Import BNode

# --- Configuration ---
OWL_FILE = 'panres_v2.owl' # <--- CHANGE THIS TO YOUR OWL FILE PATH
DB_FILE = 'panres_ontology.db'      # <--- Name for the output SQLite database
# Define common URIs
RDFS_LABEL = RDFS.label # Use the constant for clarity

# --- Helper Function ---
def get_uri_or_bnode_str(node):
    """Returns the URI as a string, or a BNode identifier."""
    if isinstance(node, URIRef):
        return str(node)
    elif isinstance(node, BNode):
        # Return the internal identifier for the blank node
        return str(node.n3())
    return None # Or raise an error if only URIs/BNodes are expected

# --- Database Setup ---
def create_schema(cursor):
    """Creates the database tables."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Classes (
            class_uri TEXT PRIMARY KEY,
            label TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ClassHierarchy (
            parent_uri TEXT,
            child_uri TEXT,
            PRIMARY KEY (parent_uri, child_uri),
            FOREIGN KEY (parent_uri) REFERENCES Classes(class_uri) ON DELETE CASCADE,
            FOREIGN KEY (child_uri) REFERENCES Classes(class_uri) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Properties (
            property_uri TEXT PRIMARY KEY,
            label TEXT,
            property_type TEXT CHECK(property_type IN ('ObjectProperty', 'DatatypeProperty', 'AnnotationProperty')),
            domain_info TEXT,
            range_info TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Individuals (
            individual_uri TEXT PRIMARY KEY,
            label TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS IndividualTypes (
            individual_uri TEXT,
            class_uri TEXT,
            PRIMARY KEY (individual_uri, class_uri),
            FOREIGN KEY (individual_uri) REFERENCES Individuals(individual_uri) ON DELETE CASCADE,
            FOREIGN KEY (class_uri) REFERENCES Classes(class_uri) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ObjectPropertyAssertions (
            subject_uri TEXT,
            property_uri TEXT,
            object_uri TEXT,
            PRIMARY KEY (subject_uri, property_uri, object_uri),
            FOREIGN KEY (subject_uri) REFERENCES Individuals(individual_uri) ON DELETE CASCADE,
            FOREIGN KEY (property_uri) REFERENCES Properties(property_uri) ON DELETE CASCADE,
            FOREIGN KEY (object_uri) REFERENCES Individuals(individual_uri) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS DatatypePropertyAssertions (
            individual_uri TEXT,
            property_uri TEXT,
            value TEXT,
            datatype_uri TEXT,
            PRIMARY KEY (individual_uri, property_uri, value),
            FOREIGN KEY (individual_uri) REFERENCES Individuals(individual_uri) ON DELETE CASCADE,
            FOREIGN KEY (property_uri) REFERENCES Properties(property_uri) ON DELETE CASCADE
        )
    ''')
    print("Database schema created (if not exists).")

# --- Data Extraction and Population ---
def parse_and_populate(graph, cursor):
    """Extracts data from the RDF graph and populates the database."""

    # Store URIs of known properties to differentiate assertions later
    object_properties = set()
    datatype_properties = set() # Includes Annotation properties

    # 1. Populate Properties Table
    print("Populating Properties table...")
    prop_types = {
        OWL.ObjectProperty: 'ObjectProperty',
        OWL.DatatypeProperty: 'DatatypeProperty',
        OWL.AnnotationProperty: 'AnnotationProperty'
    }
    for prop_type_class, prop_type_str in prop_types.items():
        for prop_uri in graph.subjects(RDF.type, prop_type_class):
            if isinstance(prop_uri, URIRef):
                # Basic extraction of domain/range (can be complex, store as string)
                domain_info = "; ".join([get_uri_or_bnode_str(d) for d in graph.objects(prop_uri, RDFS.domain) if get_uri_or_bnode_str(d)])
                range_info = "; ".join([get_uri_or_bnode_str(r) for r in graph.objects(prop_uri, RDFS.range) if get_uri_or_bnode_str(r)])
                # Get label
                label = graph.value(subject=prop_uri, predicate=RDFS_LABEL)

                cursor.execute(
                    "INSERT OR IGNORE INTO Properties (property_uri, label, property_type, domain_info, range_info) VALUES (?, ?, ?, ?, ?)",
                    (str(prop_uri), str(label) if label else None, prop_type_str, domain_info, range_info)
                )
                if prop_type_str == 'ObjectProperty':
                    object_properties.add(prop_uri)
                else:
                    datatype_properties.add(prop_uri) # Includes AnnotationProperty
                    # Special case: if the property is rdfs:label itself
                    if prop_uri == RDFS_LABEL:
                         # Ensure rdfs:label is in the table even if not explicitly typed
                         cursor.execute(
                             "INSERT OR IGNORE INTO Properties (property_uri, label, property_type) VALUES (?, ?, ?)",
                             (str(RDFS_LABEL), "Label", 'AnnotationProperty') # Assign a default label and type
                         )
                         datatype_properties.add(RDFS_LABEL) # Make sure it's tracked

    print(f"Found {len(object_properties)} Object Properties, {len(datatype_properties)} Datatype/Annotation Properties.")

    # 2. Populate Classes and ClassHierarchy Tables
    print("Populating Classes and ClassHierarchy tables...")
    all_classes = set()
    hierarchy_pairs = set()

    for class_uri in graph.subjects(RDF.type, OWL.Class):
         # Ensure it's a URI and not a blank node representing a complex class expression
        if isinstance(class_uri, URIRef):
            all_classes.add(class_uri)
            label = graph.value(subject=class_uri, predicate=RDFS_LABEL)
            cursor.execute(
                "INSERT OR IGNORE INTO Classes (class_uri, label) VALUES (?, ?)",
                (str(class_uri), str(label) if label else None)
            )
            # Find direct parents (subClassOf) that are also named classes
            for parent_uri in graph.objects(subject=class_uri, predicate=RDFS.subClassOf):
                if isinstance(parent_uri, URIRef): # Only capture direct named superclasses
                     # Add parent to classes table too, in case it wasn't found via rdf:type owl:Class
                    parent_label = graph.value(subject=parent_uri, predicate=RDFS_LABEL)
                    cursor.execute(
                        "INSERT OR IGNORE INTO Classes (class_uri, label) VALUES (?, ?)",
                        (str(parent_uri), str(parent_label) if parent_label else None)
                    )
                    all_classes.add(parent_uri)
                    hierarchy_pairs.add((str(parent_uri), str(class_uri)))

    for parent_str, child_str in hierarchy_pairs:
         cursor.execute(
             "INSERT OR IGNORE INTO ClassHierarchy (parent_uri, child_uri) VALUES (?, ?)",
             (parent_str, child_str)
         )
    print(f"Processed {len(all_classes)} Classes and {len(hierarchy_pairs)} hierarchy links.")


    # 3. Populate Individuals and Assertions
    print("Populating Individuals, Types, and Property Assertions tables...")
    individuals = set()
    individual_labels = {} # Store labels temporarily {uri_str: label_str}
    individual_types = set()
    object_assertions = set()
    datatype_assertions = set() # Using a set to avoid duplicates before insertion

    # First pass: Find all individuals and their labels
    # Iterate through all triples to find potential individuals
    potential_individuals = set()
    for s, p, o in graph:
        # Consider subjects and objects of object properties as potential individuals
        if p in object_properties:
            if isinstance(s, URIRef) and s not in all_classes and s not in object_properties and s not in datatype_properties:
                potential_individuals.add(s)
            if isinstance(o, URIRef) and o not in all_classes and o not in object_properties and o not in datatype_properties:
                potential_individuals.add(o)
        # Consider subjects of datatype properties
        elif p in datatype_properties:
             if isinstance(s, URIRef) and s not in all_classes and s not in object_properties and s not in datatype_properties:
                potential_individuals.add(s)
        # Consider subjects of rdf:type assertions where object is a class
        elif p == RDF.type and isinstance(o, URIRef) and o in all_classes:
             if isinstance(s, URIRef) and s not in all_classes and s not in object_properties and s not in datatype_properties:
                potential_individuals.add(s)

    # Get labels for potential individuals
    for ind_uri in potential_individuals:
         label = graph.value(subject=ind_uri, predicate=RDFS_LABEL)
         individuals.add(ind_uri) # Add to the final set of individuals
         if label:
             individual_labels[str(ind_uri)] = str(label)

    # Insert individuals with labels
    print(f"Found {len(individuals)} unique individuals.")
    for ind_uri in individuals:
        label_str = individual_labels.get(str(ind_uri))
        cursor.execute("INSERT OR IGNORE INTO Individuals (individual_uri, label) VALUES (?, ?)", (str(ind_uri), label_str))


    # Second pass: Populate types and assertions using the identified individuals
    print("Processing types and assertions...")
    for s, p, o in graph:
        s_str = str(s)
        # Check if subject is a known individual
        if s in individuals:
            # Check rdf:type assertions
            if p == RDF.type and isinstance(o, URIRef) and o in all_classes:
                individual_types.add((s_str, str(o)))

            # Check Object Property assertions
            elif p in object_properties and isinstance(o, URIRef) and o in individuals: # Ensure object is also an individual
                object_assertions.add((s_str, str(p), str(o)))

            # Check Datatype/Annotation Property assertions
            elif p in datatype_properties and isinstance(o, Literal):
                datatype_str = str(o.datatype) if o.datatype else None
                # Use tuple for the set, None for datatype if not present
                datatype_assertions.add((s_str, str(p), str(o), datatype_str))


    print(f"Found {len(individual_types)} type assertions.")
    for ind_uri_str, class_uri_str in individual_types:
        cursor.execute("INSERT OR IGNORE INTO IndividualTypes (individual_uri, class_uri) VALUES (?, ?)", (ind_uri_str, class_uri_str))

    print(f"Found {len(object_assertions)} object property assertions.")
    for s_str, p_str, o_str in object_assertions:
        cursor.execute("INSERT OR IGNORE INTO ObjectPropertyAssertions (subject_uri, property_uri, object_uri) VALUES (?, ?, ?)", (s_str, p_str, o_str))

    print(f"Found {len(datatype_assertions)} datatype property assertions.")
    for s_str, p_str, val_str, dt_str in datatype_assertions:
         cursor.execute(
             "INSERT OR IGNORE INTO DatatypePropertyAssertions (individual_uri, property_uri, value, datatype_uri) VALUES (?, ?, ?, ?)",
             (s_str, p_str, val_str, dt_str)
         )

    print("Population complete.")


# --- Main Execution ---
if __name__ == "__main__":
    if not os.path.exists(OWL_FILE):
        print(f"Error: OWL file not found at '{OWL_FILE}'")
        exit(1)

    # Optional: Remove existing DB for a clean run
    if os.path.exists(DB_FILE):
        print(f"Removing existing database file: {DB_FILE}")
        os.remove(DB_FILE)

    conn = None # Initialize conn
    try:
        # Connect to SQLite database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Enable foreign key support
        cursor.execute("PRAGMA foreign_keys = ON")

        # Create the database schema
        create_schema(cursor)

        # Load the OWL/RDF file
        print(f"Loading OWL file: {OWL_FILE}...")
        g = rdflib.Graph()
        try:
            # Explicitly try formats if guess fails, common ones first
            try:
                 g.parse(OWL_FILE, format=rdflib.util.guess_format(OWL_FILE))
            except Exception as guess_err:
                 print(f"Guess format failed ({guess_err}), trying XML...")
                 try:
                     g.parse(OWL_FILE, format='xml')
                 except Exception as xml_err:
                     print(f"XML format failed ({xml_err}), trying Turtle...")
                     try:
                         g.parse(OWL_FILE, format='turtle')
                     except Exception as ttl_err:
                         print(f"Turtle format failed ({ttl_err}). All parsing attempts failed.")
                         raise ttl_err # Re-raise the last error
            print("OWL file parsed successfully.")
        except Exception as e:
            print(f"Error parsing OWL file: {e}")
            if conn: conn.close()
            exit(1)


        # Parse the graph and populate the database
        parse_and_populate(g, cursor)

        # Commit changes and close the connection
        conn.commit()
        print(f"Database '{DB_FILE}' created and populated successfully.")

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")