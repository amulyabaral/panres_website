import rdflib
import sqlite3
import os
import sys
from rdflib import RDF, RDFS, OWL, XSD, URIRef, Literal, Namespace

# --- Configuration ---
# Define namespaces used in the OWL file
# Adjust the base namespace if yours is different
BASE = Namespace("http://myonto.com/PanResOntology.owl#")
# You might need others depending on the full ontology, but rdflib often handles common ones
# OWLR = Namespace("http://www.lesfleursdunormal.fr/static/_downloads/owlready_ontology.owl#")

# --- Helper Functions ---
def get_fragment(uri_ref):
    """Extracts the fragment part (after #) from a URIRef."""
    if isinstance(uri_ref, URIRef):
        return str(uri_ref).split('#')[-1]
    elif isinstance(uri_ref, Literal):
        return str(uri_ref) # Return literal value directly
    return str(uri_ref) # Fallback

def db_execute(cursor, sql, params=()):
    """Executes SQL and handles potential errors."""
    try:
        cursor.execute(sql, params)
    except sqlite3.Error as e:
        print(f"SQLite error executing:\nSQL: {sql}\nParams: {params}\nError: {e}", file=sys.stderr)
    except Exception as e:
         print(f"General error executing:\nSQL: {sql}\nParams: {params}\nError: {e}", file=sys.stderr)

def db_executemany(cursor, sql, params_list):
    """Executes SQL for many parameters and handles potential errors."""
    try:
        cursor.executemany(sql, params_list)
    except sqlite3.Error as e:
        print(f"SQLite error executing many:\nSQL: {sql}\nError: {e}", file=sys.stderr)
    except Exception as e:
         print(f"General error executing many:\nSQL: {sql}\nError: {e}", file=sys.stderr)

# --- Database Schema ---
def create_schema(cursor):
    """Creates the database tables."""
    print("Creating database schema...")

    # Core Entities
    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS genes (
        gene_id TEXT PRIMARY KEY,
        gene_type TEXT, -- 'PanGene', 'OriginalGene'
        length INTEGER,
        accession TEXT,
        pubmed TEXT,
        card_link TEXT,
        original_fasta_header TEXT,
        gene_alt_name TEXT
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS proteins (
        protein_id TEXT PRIMARY KEY,
        protein_type TEXT, -- 'PanProtein', 'OriginalProtein'
        length INTEGER,
        accession TEXT,
        pubmed TEXT
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS clusters (
        cluster_id TEXT PRIMARY KEY,
        cluster_type TEXT -- 'PanGeneCluster', 'PanProteinCluster'
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS databases (
        database_id TEXT PRIMARY KEY
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS resistance_categories (
        category_id TEXT PRIMARY KEY,
        category_type TEXT, -- e.g., 'AntibioticResistanceClass', 'Metal', 'BiocideClass'
        label TEXT,
        metal_symbol TEXT,
        metal_comment TEXT,
        is_drug_combination INTEGER -- Boolean 0 or 1
    )""")

    # Linking Tables (Many-to-Many)
    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS gene_database_link (
        gene_id TEXT,
        database_id TEXT,
        FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
        FOREIGN KEY (database_id) REFERENCES databases(database_id),
        PRIMARY KEY (gene_id, database_id)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS gene_original_gene_link (
        pan_gene_id TEXT,
        original_gene_id TEXT,
        FOREIGN KEY (pan_gene_id) REFERENCES genes(gene_id),
        FOREIGN KEY (original_gene_id) REFERENCES genes(gene_id),
        PRIMARY KEY (pan_gene_id, original_gene_id)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS gene_cluster_link (
        gene_id TEXT,
        cluster_id TEXT,
        FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
        FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id),
        PRIMARY KEY (gene_id, cluster_id)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS gene_protein_link (
        gene_id TEXT,
        protein_id TEXT,
        FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
        FOREIGN KEY (protein_id) REFERENCES proteins(protein_id),
        PRIMARY KEY (gene_id, protein_id)
    )""") # Based on instance example, may need review

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS cluster_protein_link (
        cluster_id TEXT,
        protein_id TEXT,
        FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id),
        FOREIGN KEY (protein_id) REFERENCES proteins(protein_id),
        PRIMARY KEY (cluster_id, protein_id)
    )""") # Based on ontology property definition

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS gene_resistance_link (
        gene_id TEXT,
        category_id TEXT,
        link_type TEXT, -- 'phenotype' or 'class'
        FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
        FOREIGN KEY (category_id) REFERENCES resistance_categories(category_id),
        PRIMARY KEY (gene_id, category_id, link_type)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS protein_cluster_link (
        protein_id TEXT,
        cluster_id TEXT,
        FOREIGN KEY (protein_id) REFERENCES proteins(protein_id),
        FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id),
        PRIMARY KEY (protein_id, cluster_id)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS resistance_category_database_link (
        category_id TEXT,
        database_id TEXT,
        FOREIGN KEY (category_id) REFERENCES resistance_categories(category_id),
        FOREIGN KEY (database_id) REFERENCES databases(database_id),
        PRIMARY KEY (category_id, database_id)
    )""")

    db_execute(cursor, """
    CREATE TABLE IF NOT EXISTS resistance_category_hierarchy (
        child_category_id TEXT,
        parent_category_id TEXT,
        FOREIGN KEY (child_category_id) REFERENCES resistance_categories(category_id),
        FOREIGN KEY (parent_category_id) REFERENCES resistance_categories(category_id),
        PRIMARY KEY (child_category_id, parent_category_id)
    )""")

    print("Schema creation complete.")

# --- Data Extraction and Insertion ---
def process_ontology(graph, cursor):
    """Extracts data from the rdflib graph and inserts into SQLite."""
    print("Processing ontology graph...")

    # --- 1. Populate Databases ---
    print("Populating databases...")
    db_inserts = []
    # Find subclasses of BASE.Database and Database itself
    database_classes = set(graph.subjects(RDFS.subClassOf, BASE.Database))
    database_classes.add(BASE.Database)
    for db_class in database_classes:
        # Add the class itself if it's concrete
         if (db_class, RDF.type, OWL.Class) in graph and get_fragment(db_class) != 'Database':
              db_inserts.append((get_fragment(db_class),))
        # Find individuals explicitly declared as this type (might not exist if only used as classes)
        # for individual in graph.subjects(RDF.type, db_class):
        #      db_inserts.append((get_fragment(individual),))

    # Ensure uniqueness before inserting
    unique_db_inserts = list(set(db_inserts))
    if unique_db_inserts:
        db_executemany(cursor, "INSERT OR IGNORE INTO databases (database_id) VALUES (?)", unique_db_inserts)
    print(f"  Found and inserted {len(unique_db_inserts)} databases.")


    # --- 2. Populate Resistance Categories and Hierarchy ---
    print("Populating resistance categories and hierarchy...")
    category_inserts = []
    hierarchy_inserts = []
    # Find all classes under BASE.ResistanceType and direct instances of Resistance-related concepts
    resistance_types = set()
    q = """
        SELECT ?category WHERE {
            { ?category rdfs:subClassOf* ?super . }
            FILTER(?super IN (%s, %s, %s, %s, %s))
        }
    """ % (BASE.ResistanceType.n3(), BASE.AntibioticResistancePhenotype.n3(), BASE.Metal.n3(), BASE.Biocide.n3(), BASE.UnclassifiedResistance.n3())

    # Use SPARQL to find all relevant classes and individuals used as categories
    # This is complex, start by finding all owl:Class definitions and specific types
    category_uris = set()

    # Get all defined OWL Classes
    for s, p, o in graph.triples((None, RDF.type, OWL.Class)):
        if str(s).startswith(str(BASE)): # Filter for classes within our ontology
             category_uris.add(s)

    # Add specific instance types used as categories if needed (less common for classes)
    # Example: If a specific metal *instance* could be linked directly.

    for cat_uri in category_uris:
        cat_id = get_fragment(cat_uri)
        cat_type = get_fragment(graph.value(cat_uri, RDF.type, default=OWL.Class)) # Default type
        label = graph.value(cat_uri, RDFS.label, default=cat_id) # Use ID if no label
        metal_symbol = graph.value(cat_uri, BASE.metal_symbol)
        metal_comment = graph.value(cat_uri, BASE.metal_comment)
        is_drug_combination = graph.value(cat_uri, BASE.is_drug_combination)

        # Determine primary category type (subclass of ResistanceType branches)
        primary_type = cat_id # default to self if no simple parent found below ResistanceType
        for parent in graph.objects(cat_uri, RDFS.subClassOf):
             if str(parent).startswith(str(BASE)):
                 parent_id = get_fragment(parent)
                 # Capture direct parent links for hierarchy
                 hierarchy_inserts.append((cat_id, parent_id))
                 # Try to find a more specific parent type if needed (e.g. AntibioticResistanceClass)
                 # This logic might need refinement based on desired 'category_type' granularity
                 if parent_id in ["AntibioticResistanceClass", "AntibioticResistancePhenotype", "AntibioticResistanceMechanism",
                                  "BiocideClass", "Biocide", "MetalClass", "Metal",
                                  "UnclassifiedResistanceClass", "UnclassifiedResistance"]:
                     primary_type = parent_id


        category_inserts.append((
            cat_id,
            primary_type, # Store the most specific parent type found or self
            str(label),
            str(metal_symbol) if metal_symbol else None,
            str(metal_comment) if metal_comment else None,
            1 if isinstance(is_drug_combination, Literal) and is_drug_combination.datatype == XSD.boolean and bool(is_drug_combination) else 0
        ))

    # Insert categories (handle potential duplicates if URI appears multiple times)
    unique_category_inserts = {item[0]: item for item in category_inserts}.values()
    if unique_category_inserts:
        db_executemany(cursor, """
            INSERT OR IGNORE INTO resistance_categories
            (category_id, category_type, label, metal_symbol, metal_comment, is_drug_combination)
            VALUES (?, ?, ?, ?, ?, ?)""", list(unique_category_inserts))
    print(f"  Found and processed {len(unique_category_inserts)} potential categories.")

    # Insert hierarchy links
    unique_hierarchy_inserts = list(set(hierarchy_inserts))
    if unique_hierarchy_inserts:
        db_executemany(cursor, """
            INSERT OR IGNORE INTO resistance_category_hierarchy
            (child_category_id, parent_category_id) VALUES (?, ?)""", unique_hierarchy_inserts)
    print(f"  Inserted {len(unique_hierarchy_inserts)} hierarchy links.")

    # --- 3. Populate Clusters ---
    print("Populating clusters...")
    cluster_inserts = []
    cluster_uris = set(graph.subjects(RDF.type, BASE.PanGeneCluster))
    cluster_uris.update(graph.subjects(RDF.type, BASE.PanProteinCluster))

    for cluster_uri in cluster_uris:
        cluster_id = get_fragment(cluster_uri)
        cluster_type = None
        if (cluster_uri, RDF.type, BASE.PanGeneCluster) in graph:
            cluster_type = "PanGeneCluster"
        elif (cluster_uri, RDF.type, BASE.PanProteinCluster) in graph:
             cluster_type = "PanProteinCluster"
        cluster_inserts.append((cluster_id, cluster_type))

    unique_cluster_inserts = list(set(cluster_inserts))
    if unique_cluster_inserts:
        db_executemany(cursor, "INSERT OR IGNORE INTO clusters (cluster_id, cluster_type) VALUES (?, ?)", unique_cluster_inserts)
    print(f"  Found and inserted {len(unique_cluster_inserts)} clusters.")


    # --- 4. Populate Genes, Proteins and Links (Iterate through Individuals) ---
    print("Populating genes, proteins, and their links...")
    gene_inserts = {}
    protein_inserts = {}
    link_inserts = {
        "gene_db": [], "gene_orig": [], "gene_cluster": [], "gene_protein": [],
        "cluster_protein": [], "gene_resistance": [], "protein_cluster": [],
        "res_cat_db": []
    }

    # Iterate through all subjects in the graph (potential individuals)
    all_subjects = set(graph.subjects())
    print(f"  Processing {len(all_subjects)} potential individuals/subjects...")
    count = 0
    for subj_uri in all_subjects:
        if not isinstance(subj_uri, URIRef) or not str(subj_uri).startswith(str(BASE)):
            continue # Skip blank nodes or external URIs as primary subjects for now

        count += 1
        if count % 5000 == 0:
            print(f"    Processed {count}/{len(all_subjects)} subjects...")

        subj_id = get_fragment(subj_uri)
        subj_type_uris = list(graph.objects(subj_uri, RDF.type))

        # Check if it's a Gene type
        is_gene = False
        gene_type = None
        if BASE.PanGene in subj_type_uris:
            is_gene = True
            gene_type = "PanGene"
        elif BASE.OriginalGene in subj_type_uris:
             is_gene = True
             gene_type = "OriginalGene"
        # Add other gene types if necessary (e.g. AntimicrobialResistanceGene)
        elif BASE.AntimicrobialResistanceGene in subj_type_uris or \
             BASE.BiocideResistanceGene in subj_type_uris or \
             BASE.MetalResistanceGene in subj_type_uris:
             is_gene = True
             gene_type = gene_type or get_fragment(next((t for t in subj_type_uris if get_fragment(t).endswith("Gene")), BASE.PanGene))


        # Check if it's a Protein type
        is_protein = False
        protein_type = None
        if BASE.PanProtein in subj_type_uris:
            is_protein = True
            protein_type = "PanProtein"
        elif BASE.OriginalProtein in subj_type_uris:
            is_protein = True
            protein_type = "OriginalProtein" # Might be subclass, adjust if needed

        # Populate Gene Table
        if is_gene and subj_id not in gene_inserts:
             gene_inserts[subj_id] = {
                "gene_id": subj_id,
                "gene_type": gene_type,
                "length": graph.value(subj_uri, BASE.has_length),
                "accession": graph.value(subj_uri, BASE.accession),
                "pubmed": graph.value(subj_uri, BASE.pubmed),
                "card_link": graph.value(subj_uri, BASE.card_link),
                "original_fasta_header": graph.value(subj_uri, BASE.original_fasta_header),
                "gene_alt_name": graph.value(subj_uri, BASE.gene_alt_name)
            }

        # Populate Protein Table
        if is_protein and subj_id not in protein_inserts:
             protein_inserts[subj_id] = {
                 "protein_id": subj_id,
                 "protein_type": protein_type,
                 "length": graph.value(subj_uri, BASE.has_length),
                 "accession": graph.value(subj_uri, BASE.accession),
                 "pubmed": graph.value(subj_uri, BASE.pubmed)
             }

        # Populate Linking tables by iterating through properties of the subject
        for pred, obj in graph.predicate_objects(subj_uri):
            obj_id = get_fragment(obj)

            # Gene Links
            if is_gene:
                if pred == BASE.is_from_database:
                    link_inserts["gene_db"].append((subj_id, obj_id))
                elif pred == BASE.same_as:
                     link_inserts["gene_orig"].append((subj_id, obj_id))
                elif pred == BASE.member_of and (subj_uri, RDF.type, BASE.PanGene) in graph: # member_of PanGene -> PanGeneCluster
                     link_inserts["gene_cluster"].append((subj_id, obj_id))
                elif pred == BASE.translates_to: # Instance Example: PanGene translates_to Protein
                     link_inserts["gene_protein"].append((subj_id, obj_id))
                elif pred == BASE.has_resistance_class:
                     link_inserts["gene_resistance"].append((subj_id, obj_id, "class"))
                elif pred == BASE.has_predicted_phenotype:
                     link_inserts["gene_resistance"].append((subj_id, obj_id, "phenotype"))

            # Protein Links
            if is_protein:
                 if pred == BASE.member_of and (subj_uri, RDF.type, BASE.PanProtein) in graph: # member_of PanProtein -> PanProteinCluster
                     link_inserts["protein_cluster"].append((subj_id, obj_id))

            # Cluster Links (based on Ontology property definition)
            if pred == BASE.translates_to and (subj_uri, RDF.type, BASE.PanGeneCluster) in graph:
                 link_inserts["cluster_protein"].append((subj_id, obj_id))

            # Resistance Category Links (found_in applies to Classes)
            if pred == BASE.found_in and get_fragment(subj_uri) in unique_category_inserts: # Check if subject is a category
                 link_inserts["res_cat_db"].append((subj_id, obj_id))


    # --- 5. Insert collected data ---
    print("Inserting data into tables...")

    # Insert Genes
    gene_values = [
        (g['gene_id'], g['gene_type'], g['length'], g['accession'], g['pubmed'], g['card_link'], g['original_fasta_header'], g['gene_alt_name'])
        for g in gene_inserts.values()
    ]
    if gene_values:
        db_executemany(cursor, """
            INSERT OR IGNORE INTO genes (gene_id, gene_type, length, accession, pubmed, card_link, original_fasta_header, gene_alt_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", gene_values)
    print(f"  Inserted {len(gene_values)} genes.")

    # Insert Proteins
    protein_values = [
        (p['protein_id'], p['protein_type'], p['length'], p['accession'], p['pubmed'])
        for p in protein_inserts.values()
    ]
    if protein_values:
        db_executemany(cursor, """
            INSERT OR IGNORE INTO proteins (protein_id, protein_type, length, accession, pubmed)
            VALUES (?, ?, ?, ?, ?)""", protein_values)
    print(f"  Inserted {len(protein_values)} proteins.")


    # Insert Links (using executemany for efficiency)
    for link_type, data in link_inserts.items():
        unique_data = list(set(data)) # Remove duplicates before inserting
        if not unique_data:
            continue

        print(f"  Inserting {len(unique_data)} links for {link_type}...")
        if link_type == "gene_db":
            sql = "INSERT OR IGNORE INTO gene_database_link (gene_id, database_id) VALUES (?, ?)"
        elif link_type == "gene_orig":
            sql = "INSERT OR IGNORE INTO gene_original_gene_link (pan_gene_id, original_gene_id) VALUES (?, ?)"
        elif link_type == "gene_cluster":
             sql = "INSERT OR IGNORE INTO gene_cluster_link (gene_id, cluster_id) VALUES (?, ?)"
        elif link_type == "gene_protein":
             sql = "INSERT OR IGNORE INTO gene_protein_link (gene_id, protein_id) VALUES (?, ?)"
        elif link_type == "cluster_protein":
            sql = "INSERT OR IGNORE INTO cluster_protein_link (cluster_id, protein_id) VALUES (?, ?)"
        elif link_type == "gene_resistance":
            sql = "INSERT OR IGNORE INTO gene_resistance_link (gene_id, category_id, link_type) VALUES (?, ?, ?)"
        elif link_type == "protein_cluster":
            sql = "INSERT OR IGNORE INTO protein_cluster_link (protein_id, cluster_id) VALUES (?, ?)"
        elif link_type == "res_cat_db":
             sql = "INSERT OR IGNORE INTO resistance_category_database_link (category_id, database_id) VALUES (?, ?)"
        else:
            print(f"    Unknown link type: {link_type}", file=sys.stderr)
            continue

        db_executemany(cursor, sql, unique_data)

    # --- 6. Add Indexes (Optional but Recommended) ---
    print("Adding indexes...")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_type ON genes(gene_type)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_protein_type ON proteins(protein_type)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_cluster_type ON clusters(cluster_type)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_res_cat_type ON resistance_categories(category_type)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_db_gene ON gene_database_link(gene_id)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_db_db ON gene_database_link(database_id)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_orig_pan ON gene_original_gene_link(pan_gene_id)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_orig_orig ON gene_original_gene_link(original_gene_id)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_clust_gene ON gene_cluster_link(gene_id)")
    db_execute(cursor, "CREATE INDEX IF NOT EXISTS idx_gene_clust_clust ON gene_cluster_link(cluster_id)")
    # Add indexes for other linking tables similarly...
    print("Indexes added.")

    print("Ontology processing complete.")


# --- Main Execution ---
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <input_owl_file> <output_sqlite_db>")
        sys.exit(1)

    owl_file = sys.argv[1]
    db_file = sys.argv[2]

    if not os.path.exists(owl_file):
        print(f"Error: Input OWL file not found: {owl_file}", file=sys.stderr)
        sys.exit(1)

    # Remove existing DB file if it exists to start fresh
    if os.path.exists(db_file):
        print(f"Removing existing database file: {db_file}")
        os.remove(db_file)

    # Load OWL file
    print(f"Loading OWL file: {owl_file} ... (This may take time for large files)")
    g = rdflib.Graph()
    try:
        # Try guessing format first, explicitly use 'xml' (RDF/XML) if needed
        g.parse(owl_file, format=rdflib.util.guess_format(owl_file) or 'xml')
        print(f"Loaded {len(g)} triples.")
    except Exception as e:
        print(f"Error parsing OWL file: {e}", file=sys.stderr)
        print("Ensure the file is valid RDF/XML or another RDF format.", file=sys.stderr)
        sys.exit(1)


    # Connect to SQLite DB and get cursor
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Create Schema
        create_schema(cursor)

        # Process Data
        process_ontology(g, cursor)

        # Commit changes
        print("Committing changes to database...")
        conn.commit()
        print("Database commit successful.")

    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        if conn:
            conn.rollback() # Rollback changes on error
    except Exception as e:
         print(f"An unexpected error occurred: {e}", file=sys.stderr)
         if conn:
             conn.rollback()
    finally:
        if conn:
            print("Closing database connection.")
            conn.close()

    print(f"Conversion complete. SQLite database saved to: {db_file}")