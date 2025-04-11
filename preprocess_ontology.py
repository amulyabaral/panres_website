import rdflib
import sqlite3
import os
import sys # Import sys for exiting
from rdflib import RDF, RDFS, OWL, URIRef, BNode, Literal
from urllib.parse import urldefrag

# --- Configuration ---
OWL_FILE = 'panres_v2.owl'
# Use Render's persistent disk mount point
DB_DIR = os.environ.get('RENDER_DISK_PATH', '/db/ontology')
DB_FILE = os.path.join(DB_DIR, 'ontology.db')
# Make sure this matches your OWL file's base IRI - extract if possible or keep hardcoded
# BASE_IRI = "http://myonto.com/PanResOntology.owl#"

# --- Helper Functions ---
def get_local_name(uri):
    """Extracts the local name from a URI, handling potential fragments."""
    if isinstance(uri, BNode): # Handle Blank Nodes
        return f"_:{uri}"
    try:
        uri_str = str(uri)
        _, fragment = urldefrag(uri_str)
        if fragment:
            return fragment
        # Fallback if no fragment
        return uri_str.split('/')[-1].split('#')[-1]
    except Exception as e:
        print(f"Warning: Could not parse URI '{uri}' for local name: {e}")
        return str(uri) # Return full URI if parsing fails

def get_property_value(graph, subject, predicate):
    """Gets a single object for a subject-predicate pair, preferring URIRefs/Literals over BNodes."""
    objects = list(graph.objects(subject, predicate))
    if not objects:
        return None
    # Prefer non-blank nodes if available
    for obj in objects:
        if not isinstance(obj, BNode):
            return obj
    return objects[0] # Fallback to the first one (might be BNode)

def get_property_values(graph, subject, predicate):
    """Gets all objects for a subject-predicate pair."""
    return list(graph.objects(subject, predicate))

# --- Main Pre-processing Logic ---
def preprocess_ontology():
    print(f"Ontology file path: {os.path.abspath(OWL_FILE)}")
    if not os.path.exists(OWL_FILE):
        print(f"Error: Ontology file not found at '{OWL_FILE}'")
        sys.exit(1) # Exit with error code

    print(f"Loading OWL file: {OWL_FILE}...")
    g = rdflib.Graph()
    try:
        # Try parsing common formats if XML fails
        try:
            g.parse(OWL_FILE, format='xml')
        except Exception as xml_e:
            print(f"Info: Parsing as RDF/XML failed ({xml_e}), trying Turtle...")
            try:
                 g.parse(OWL_FILE, format='turtle')
            except Exception as ttl_e:
                 print(f"Info: Parsing as Turtle failed ({ttl_e}), trying N3...")
                 try:
                     g.parse(OWL_FILE, format='n3')
                 except Exception as n3_e:
                     print(f"Error: Failed to parse OWL file with multiple formats.")
                     print(f"  RDF/XML error: {xml_e}")
                     print(f"  Turtle error: {ttl_e}")
                     print(f"  N3 error: {n3_e}")
                     sys.exit(1)

        print(f"Successfully parsed {len(g)} triples.")
    except Exception as e:
        print(f"Critical Error parsing OWL file: {e}")
        sys.exit(1)

    # Ensure the database directory exists
    print(f"Ensuring database directory exists: {DB_DIR}")
    if not os.path.exists(DB_DIR):
        try:
            os.makedirs(DB_DIR, exist_ok=True) # exist_ok=True is safer
            print(f"Created database directory: {DB_DIR}")
        except OSError as e:
            print(f"Error creating directory {DB_DIR}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Unexpected error creating directory {DB_DIR}: {e}")
            sys.exit(1)


    # Remove existing DB if it exists
    if os.path.exists(DB_FILE):
        print(f"Removing existing database: {DB_FILE}...")
        try:
            os.remove(DB_FILE)
            print(f"Removed existing database: {DB_FILE}")
        except OSError as e:
            print(f"Warning: Error removing existing database {DB_FILE}: {e}. Attempting to continue.")
            # Decide if you want to stop or continue if removal fails
            # sys.exit(1) # Uncomment to make removal failure critical
        except Exception as e:
            print(f"Unexpected error removing existing database {DB_FILE}: {e}")
            sys.exit(1)


    print(f"Connecting to SQLite database: {DB_FILE}...")
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Enable foreign key support
        cursor.execute("PRAGMA foreign_keys = ON;")
    except sqlite3.Error as e:
        print(f"Error connecting to or setting up SQLite database: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error connecting to database: {e}")
        sys.exit(1)


    print("Creating database schema...")
    try:
        # --- Database Schema ---
        # Added ON DELETE SET NULL for parent_uri FK
        cursor.execute('''
            CREATE TABLE classes (
                uri TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                label TEXT,
                description TEXT,
                parent_uri TEXT,
                FOREIGN KEY (parent_uri) REFERENCES classes(uri) ON DELETE SET NULL
            )
        ''')

        # Added ON DELETE CASCADE for class_uri FK (if class deleted, delete its individuals)
        cursor.execute('''
            CREATE TABLE individuals (
                uri TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                class_uri TEXT NOT NULL,
                label TEXT,
                description TEXT,
                FOREIGN KEY (class_uri) REFERENCES classes(uri) ON DELETE CASCADE
            )
        ''')

        # Added FK constraints (optional, but good practice if possible)
        # Note: These assume subjects are always individuals. If classes can have properties/relationships,
        # you might need separate tables or remove these FKs. Let's keep them for now.
        cursor.execute('''
            CREATE TABLE properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_uri TEXT NOT NULL,
                predicate_uri TEXT NOT NULL,
                predicate_name TEXT,
                value_literal TEXT,
                value_type TEXT, -- e.g., string, integer, link
                FOREIGN KEY (subject_uri) REFERENCES individuals(uri) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_uri TEXT NOT NULL,
                predicate_uri TEXT NOT NULL,
                predicate_name TEXT,
                object_uri TEXT NOT NULL,
                FOREIGN KEY (subject_uri) REFERENCES individuals(uri) ON DELETE CASCADE
                -- Cannot easily add FK for object_uri as it could be class or individual
            )
        ''')
        print("Schema created.")
    except sqlite3.Error as e:
        print(f"Error creating database schema: {e}")
        conn.close()
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error creating schema: {e}")
        conn.close()
        sys.exit(1)


    # --- Populate Classes ---
    # Need to insert classes in an order that respects parent dependencies for FKs
    # Or insert all first, then update parents. Let's try inserting with NULL parent first.
    print("Processing classes (Phase 1: Initial Insert)...")
    all_potential_classes = set()
    # 1. Explicit owl:Class definitions
    for s in g.subjects(RDF.type, OWL.Class):
        if isinstance(s, URIRef):
            all_potential_classes.add(s)
    # 2. Subjects/Objects of rdfs:subClassOf
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef): all_potential_classes.add(s)
        if isinstance(o, URIRef): all_potential_classes.add(o) # Parent could be a class too
    # 3. Ranges/Domains of properties if they are URIRefs
    for p in g.subjects(RDF.type, OWL.ObjectProperty):
         for domain in g.objects(p, RDFS.domain):
             if isinstance(domain, URIRef): all_potential_classes.add(domain)
         for range_ in g.objects(p, RDFS.range):
             if isinstance(range_, URIRef): all_potential_classes.add(range_)
    # 4. Types of individuals
    for o in g.objects(predicate=RDF.type):
        # Check if the type 'o' looks like a class (heuristic: has subclasses or instances)
        # or is explicitly owl:Class (already covered)
        # This helps catch classes not explicitly typed but used as types.
        if isinstance(o, URIRef):
             if list(g.subjects(RDFS.subClassOf, o)) or list(g.subjects(RDF.type, o)):
                 all_potential_classes.add(o)


    inserted_classes = set()
    class_data_to_insert = []
    for class_uri in all_potential_classes:
        # Skip OWL primitives and RDFS classes if they sneak in, unless desired
        if str(class_uri).startswith(str(OWL)) or str(class_uri).startswith(str(RDFS)) or str(class_uri).startswith(str(RDF)):
             continue

        name = get_local_name(class_uri)
        if not name: # Skip if local name couldn't be determined
            print(f"Warning: Skipping class with unparsable URI: {class_uri}")
            continue

        label_obj = get_property_value(g, class_uri, RDFS.label)
        label = str(label_obj) if label_obj else name

        desc_obj = get_property_value(g, class_uri, RDFS.comment) # Using rdfs:comment as description
        description = str(desc_obj) if desc_obj else None

        # Store data for insertion (parent_uri initially NULL)
        class_data_to_insert.append((str(class_uri), name, label, description))

    # Insert classes with NULL parent first
    try:
        cursor.executemany('''
            INSERT OR IGNORE INTO classes (uri, name, label, description, parent_uri)
            VALUES (?, ?, ?, ?, NULL)
        ''', class_data_to_insert)
        # Record which ones were actually inserted (or ignored)
        for data in class_data_to_insert:
            cursor.execute("SELECT 1 FROM classes WHERE uri = ?", (data[0],))
            if cursor.fetchone():
                inserted_classes.add(data[0])
        print(f"Phase 1: Inserted/Found {len(inserted_classes)} potential classes.")
    except sqlite3.IntegrityError as e:
         print(f"Error during initial class insert batch: {e}")
         # Potentially problematic, might need individual inserts with try-except
    except sqlite3.Error as e:
        print(f"SQLite error during initial class insert: {e}")
        conn.rollback() # Rollback partial changes
        conn.close()
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error during initial class insert: {e}")
        conn.rollback()
        conn.close()
        sys.exit(1)


    # --- Update Parent URIs ---
    print("Processing classes (Phase 2: Updating Parents)...")
    update_count = 0
    parent_updates = []
    for class_uri_str in inserted_classes:
        class_uri = URIRef(class_uri_str)
        parent_uri_obj = get_property_value(g, class_uri, RDFS.subClassOf)

        # Ensure parent is a valid, inserted class URI and not the class itself
        if parent_uri_obj and isinstance(parent_uri_obj, URIRef) and str(parent_uri_obj) in inserted_classes and parent_uri_obj != class_uri:
            parent_uri_str = str(parent_uri_obj)
            parent_updates.append((parent_uri_str, class_uri_str))

    try:
        cursor.executemany('''
            UPDATE classes SET parent_uri = ? WHERE uri = ? AND parent_uri IS NULL
        ''', parent_updates)
        update_count = cursor.rowcount
        print(f"Phase 2: Updated parent links for {update_count} classes.")
    except sqlite3.Error as e:
        print(f"SQLite error during parent update: {e}")
        # Non-critical usually, but log it
    except Exception as e:
        print(f"Unexpected error during parent update: {e}")
        # Non-critical usually, but log it

    # --- Populate Individuals and their Properties/Relationships ---
    print("Processing individuals...")
    individual_uris = set()
    # Find subjects typed as a known class (now in our DB)
    for class_uri_str in inserted_classes:
        class_uri = URIRef(class_uri_str)
        for s in g.subjects(RDF.type, class_uri):
            if isinstance(s, URIRef):
                individual_uris.add(s)

    # Also find explicitly declared owl:NamedIndividual
    for s in g.subjects(RDF.type, OWL.NamedIndividual):
         if isinstance(s, URIRef):
             individual_uris.add(s)

    individual_count = 0
    property_count = 0
    relationship_count = 0

    for ind_uri in individual_uris:
        ind_uri_str = str(ind_uri)
        name = get_local_name(ind_uri)
        if not name:
            print(f"Warning: Skipping individual with unparsable URI: {ind_uri}")
            continue

        label_obj = get_property_value(g, ind_uri, RDFS.label)
        label = str(label_obj) if label_obj else name

        desc_obj = get_property_value(g, ind_uri, RDFS.comment)
        description = str(desc_obj) if desc_obj else None

        # Find the most specific class type *that exists in our classes table*
        # This requires checking against `inserted_classes` set
        individual_class_uri_str = None
        possible_types = get_property_values(g, ind_uri, RDF.type)
        valid_types_in_db = [str(t) for t in possible_types if isinstance(t, URIRef) and str(t) in inserted_classes]

        if not valid_types_in_db:
             # Maybe it's only typed as owl:NamedIndividual, check if it has properties linking it implicitly
             # Or maybe it's an orphan. Skip for now if no known class type.
             print(f"Warning: Could not determine a known class type in DB for individual {name} ({ind_uri_str}). Skipping.")
             continue

        # Simple approach: take the first valid type found.
        # TODO: Implement finding the *most specific* type if needed (requires hierarchy traversal)
        individual_class_uri_str = valid_types_in_db[0]

        # Insert individual
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO individuals (uri, name, class_uri, label, description)
                VALUES (?, ?, ?, ?, ?)
            ''', (ind_uri_str, name, individual_class_uri_str, label, description))

            if cursor.rowcount > 0:
                 individual_count += 1
            else:
                 # If ignored (duplicate name/uri), still proceed to add properties/relationships
                 # unless the existing record should be updated.
                 print(f"Info: Individual '{name}' ({ind_uri_str}) already exists or caused an integrity issue (ignored).")
                 # Check if it exists to be sure we can add properties
                 cursor.execute("SELECT 1 FROM individuals WHERE uri = ?", (ind_uri_str,))
                 if not cursor.fetchone():
                     print(f"Error: Could not insert or find individual {name} ({ind_uri_str}). Skipping properties.")
                     continue # Skip properties if individual doesn't exist

        except sqlite3.IntegrityError as e:
            print(f"Skipping individual due to IntegrityError for {name} ({ind_uri_str}): {e}")
            continue # Skip properties if individual insertion failed critically
        except sqlite3.Error as e:
            print(f"Error inserting individual {name} ({ind_uri_str}): {e}")
            continue # Skip properties
        except Exception as e:
            print(f"Unexpected error inserting individual {name} ({ind_uri_str}): {e}")
            continue

        # Process properties and relationships for this individual
        props_to_insert = []
        rels_to_insert = []
        for p, o in g.predicate_objects(ind_uri):
            if p == RDF.type or p == RDFS.label or p == RDFS.comment:
                continue # Already handled

            pred_uri_str = str(p)
            pred_name = get_local_name(p)

            if isinstance(o, Literal):
                value_type = get_local_name(o.datatype) if o.datatype else 'string'
                props_to_insert.append((ind_uri_str, pred_uri_str, pred_name, str(o), value_type))
            elif isinstance(o, URIRef):
                # Could be a relationship to another individual OR a class
                obj_uri_str = str(o)
                rels_to_insert.append((ind_uri_str, pred_uri_str, pred_name, obj_uri_str))
            # Else: Could be a blank node object, skip for simplicity

        # Batch insert properties and relationships for the individual
        try:
            if props_to_insert:
                cursor.executemany('''
                    INSERT INTO properties (subject_uri, predicate_uri, predicate_name, value_literal, value_type)
                    VALUES (?, ?, ?, ?, ?)
                ''', props_to_insert)
                property_count += len(props_to_insert)
            if rels_to_insert:
                 cursor.executemany('''
                     INSERT INTO relationships (subject_uri, predicate_uri, predicate_name, object_uri)
                     VALUES (?, ?, ?, ?)
                 ''', rels_to_insert)
                 relationship_count += len(rels_to_insert)
        except sqlite3.Error as e:
            print(f"Error inserting properties/relationships for {name} ({ind_uri_str}): {e}")
            # Consider rolling back this individual's props/rels or just logging
        except Exception as e:
            print(f"Unexpected error inserting props/rels for {name} ({ind_uri_str}): {e}")


    print(f"Processed {individual_count} new individuals.")
    print(f"Inserted {property_count} properties.")
    print(f"Inserted {relationship_count} relationships.")

    # --- Create Indexes ---
    print("Creating indexes...")
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_classes_name ON classes (name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_classes_parent ON classes (parent_uri);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_individuals_name ON individuals (name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_individuals_class ON individuals (class_uri);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_properties_subject ON properties (subject_uri);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_properties_predicate ON properties (predicate_name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_subject ON relationships (subject_uri);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_object ON relationships (object_uri);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_predicate ON relationships (predicate_name);")
        print("Indexes created or already exist.")
    except sqlite3.Error as e:
        print(f"Error creating indexes: {e}")
    except Exception as e:
        print(f"Unexpected error creating indexes: {e}")


    # --- Commit and Close ---
    try:
        conn.commit()
        print("Database changes committed.")
    except sqlite3.Error as e:
        print(f"Error committing changes to database: {e}")
    except Exception as e:
        print(f"Unexpected error committing changes: {e}")
    finally:
        conn.close()
        print("Database connection closed. Pre-processing complete.")

if __name__ == "__main__":
    preprocess_ontology() 