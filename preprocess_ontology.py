import rdflib
import sqlite3
import os
from rdflib import RDF, RDFS, OWL, URIRef
from urllib.parse import urldefrag

# --- Configuration ---
OWL_FILE = 'panres_v2.owl'
DB_FILE = 'ontology.db'
BASE_IRI = "http://myonto.com/PanResOntology.owl#" # Make sure this matches your OWL file's base IRI

# --- Helper Functions ---
def get_local_name(uri):
    """Extracts the local name from a URI, handling potential fragments."""
    try:
        _, fragment = urldefrag(str(uri))
        if fragment:
            return fragment
        # Fallback if no fragment
        return str(uri).split('/')[-1].split('#')[-1]
    except Exception:
        return str(uri) # Return full URI if parsing fails

def get_property_value(graph, subject, predicate):
    """Gets a single object for a subject-predicate pair."""
    objects = list(graph.objects(subject, predicate))
    return objects[0] if objects else None

def get_property_values(graph, subject, predicate):
    """Gets all objects for a subject-predicate pair."""
    return list(graph.objects(subject, predicate))

# --- Main Pre-processing Logic ---
def preprocess_ontology():
    print(f"Loading OWL file: {OWL_FILE}...")
    g = rdflib.Graph()
    try:
        g.parse(OWL_FILE, format='xml') # Assuming RDF/XML format based on your example
        print(f"Successfully parsed {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing OWL file: {e}")
        return

    # Remove existing DB if it exists
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
        print(f"Removed existing database: {DB_FILE}")

    print(f"Connecting to SQLite database: {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    print("Creating database schema...")
    # --- Database Schema ---
    cursor.execute('''
        CREATE TABLE classes (
            uri TEXT PRIMARY KEY,
            name TEXT UNIQUE,
            label TEXT,
            description TEXT,
            parent_uri TEXT,
            FOREIGN KEY (parent_uri) REFERENCES classes(uri)
        )
    ''')

    cursor.execute('''
        CREATE TABLE individuals (
            uri TEXT PRIMARY KEY,
            name TEXT UNIQUE,
            class_uri TEXT,
            label TEXT,
            description TEXT,
            FOREIGN KEY (class_uri) REFERENCES classes(uri)
        )
    ''')

    cursor.execute('''
        CREATE TABLE properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_uri TEXT, -- Can be individual or class URI
            predicate_uri TEXT,
            predicate_name TEXT,
            value_literal TEXT,
            value_type TEXT -- e.g., string, integer, link
            -- No foreign keys here to allow properties on classes too if needed
        )
    ''')

    cursor.execute('''
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_uri TEXT, -- Typically an individual URI
            predicate_uri TEXT,
            predicate_name TEXT,
            object_uri TEXT -- Typically another individual or class URI
            -- No foreign keys here for flexibility, could reference individuals or classes
        )
    ''')
    print("Schema created.")

    # --- Populate Classes ---
    print("Processing classes...")
    processed_classes = set()
    # Prioritize classes explicitly defined with owl:Class
    class_nodes = list(g.subjects(RDF.type, OWL.Class))
    # Also consider subjects/objects used in rdfs:subClassOf that might not be explicitly typed
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and s not in class_nodes: class_nodes.append(s)
        if isinstance(o, URIRef) and o not in class_nodes: class_nodes.append(o)

    for class_uri in class_nodes:
        if not isinstance(class_uri, URIRef) or str(class_uri) in processed_classes:
            continue # Skip blank nodes or already processed

        name = get_local_name(class_uri)
        label = get_property_value(g, class_uri, RDFS.label) or name
        description = get_property_value(g, class_uri, RDFS.comment) # Using rdfs:comment as description
        parent_uri = get_property_value(g, class_uri, RDFS.subClassOf)

        # Handle cases where parent might be a complex class expression (e.g., unionOf) - skip for simplicity here
        if parent_uri and not isinstance(parent_uri, URIRef):
            parent_uri = None # Simplification: only direct named parent classes

        parent_uri_str = str(parent_uri) if parent_uri else None

        try:
            cursor.execute('''
                INSERT OR IGNORE INTO classes (uri, name, label, description, parent_uri)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(class_uri), name, str(label) if label else name, str(description) if description else None, parent_uri_str))
            processed_classes.add(str(class_uri))
        except sqlite3.IntegrityError as e:
             print(f"Skipping duplicate class name or URI error for {name} ({class_uri}): {e}")
        except Exception as e:
            print(f"Error inserting class {name} ({class_uri}): {e}")

    print(f"Processed {len(processed_classes)} classes.")


    # --- Populate Individuals and their Properties/Relationships ---
    print("Processing individuals...")
    individual_count = 0
    # Find individuals by finding subjects that have an rdf:type which is an owl:Class
    potential_individuals = set()
    for s, o in g.subject_objects(RDF.type):
         # Check if the type 'o' is a class we know about (or owl:NamedIndividual)
         is_known_class = list(g.objects(o, RDF.type, OWL.Class)) or o == OWL.NamedIndividual
         # More robust check: is 'o' a subclass of something we added to classes table?
         # This requires querying the DB or keeping class URIs in memory. Let's use the DB.
         cursor.execute("SELECT 1 FROM classes WHERE uri = ?", (str(o),))
         is_in_db_class = cursor.fetchone()

         if isinstance(s, URIRef) and (is_known_class or is_in_db_class):
             potential_individuals.add(s)

    # Also explicitly add owl:NamedIndividual types
    for ind_uri in g.subjects(RDF.type, OWL.NamedIndividual):
         if isinstance(ind_uri, URIRef):
             potential_individuals.add(ind_uri)


    for ind_uri in potential_individuals:
        name = get_local_name(ind_uri)
        label = get_property_value(g, ind_uri, RDFS.label) or name
        description = get_property_value(g, ind_uri, RDFS.comment)

        # Find the most specific class type defined in our classes table
        class_uri = None
        types = get_property_values(g, ind_uri, RDF.type)
        for t in types:
            if isinstance(t, URIRef):
                 cursor.execute("SELECT uri FROM classes WHERE uri = ?", (str(t),))
                 result = cursor.fetchone()
                 if result:
                     class_uri = result[0]
                     # Potentially add logic here to find the *most specific* class if multiple apply
                     break # Take the first valid one found for now

        if not class_uri:
            print(f"Warning: Could not determine a known class for individual {name} ({ind_uri}). Skipping individual record.")
            continue # Skip if we can't link it to a known class

        try:
            cursor.execute('''
                INSERT OR IGNORE INTO individuals (uri, name, class_uri, label, description)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(ind_uri), name, class_uri, str(label) if label else name, str(description) if description else None))
            individual_count += 1
        except sqlite3.IntegrityError as e:
            print(f"Skipping duplicate individual name or URI error for {name} ({ind_uri}): {e}")
            continue # Skip properties if individual insertion failed
        except Exception as e:
            print(f"Error inserting individual {name} ({ind_uri}): {e}")
            continue # Skip properties if individual insertion failed


        # Process properties and relationships for this individual
        for p, o in g.predicate_objects(ind_uri):
            pred_name = get_local_name(p)
            if isinstance(o, rdflib.Literal):
                # Store as Property
                value_type = get_local_name(o.datatype) if o.datatype else 'string'
                if p == RDFS.label or p == RDFS.comment or p == RDF.type: continue # Already handled
                cursor.execute('''
                    INSERT INTO properties (subject_uri, predicate_uri, predicate_name, value_literal, value_type)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(ind_uri), str(p), pred_name, str(o), value_type))
            elif isinstance(o, rdflib.URIRef):
                 # Store as Relationship (unless it's rdf:type)
                 if p == RDF.type: continue # Handled above
                 cursor.execute('''
                     INSERT INTO relationships (subject_uri, predicate_uri, predicate_name, object_uri)
                     VALUES (?, ?, ?, ?)
                 ''', (str(ind_uri), str(p), pred_name, str(o)))
            # Else: Could be a blank node, skip for simplicity

    print(f"Processed {individual_count} individuals and their properties/relationships.")

    # --- Create Indexes ---
    print("Creating indexes...")
    cursor.execute("CREATE INDEX idx_classes_parent ON classes (parent_uri);")
    cursor.execute("CREATE INDEX idx_individuals_class ON individuals (class_uri);")
    cursor.execute("CREATE INDEX idx_properties_subject ON properties (subject_uri);")
    cursor.execute("CREATE INDEX idx_properties_predicate ON properties (predicate_name);")
    cursor.execute("CREATE INDEX idx_relationships_subject ON relationships (subject_uri);")
    cursor.execute("CREATE INDEX idx_relationships_object ON relationships (object_uri);")
    cursor.execute("CREATE INDEX idx_relationships_predicate ON relationships (predicate_name);")
    print("Indexes created.")

    # --- Commit and Close ---
    conn.commit()
    conn.close()
    print("Database closed. Pre-processing complete.")

if __name__ == "__main__":
    if not os.path.exists(OWL_FILE):
        print(f"Error: Ontology file not found at '{OWL_FILE}'")
    else:
        preprocess_ontology() 