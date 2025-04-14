document.addEventListener('DOMContentLoaded', () => {
    const ontologyTree = document.getElementById('ontology-tree');
    const detailsView = document.getElementById('details-view');
    // Define URIs used in rendering logic
    const IS_FROM_DATABASE_PROP_URI = "http://myonto.com/PanResOntology.owl#is_from_database";
    const CARD_LINK_PROP_URI = "http://myonto.com/PanResOntology.owl#card_link"; // Example, add others if needed
    const PUBMED_PROP_URI = "http://myonto.com/PanResOntology.owl#pubmed"; // Example

    function showLoading(element) {
        element.innerHTML = '<div class="loading">Loading...</div>';
    }

    function showError(element, message) {
        element.innerHTML = `<div class="error">Error: ${message}</div>`;
    }

    // --- Tree Management ---

    // Function to create a tree node (LI element)
    function createTreeNode(item, type) {
        const li = document.createElement('li');
        li.dataset.uri = item.uri;
        li.dataset.type = type;
        li.classList.add(`${type}-item`);

        // Add toggle only for classes
        if (type === 'class') {
            const toggle = document.createElement('span');
            toggle.classList.add('toggle');
            toggle.textContent = '+'; // Start collapsed
            li.appendChild(toggle);
        }

        const link = document.createElement('span');
        link.classList.add('item-link');
        link.textContent = item.label || item.uri.split('#').pop(); // Display label or URI fragment
        link.dataset.uri = item.uri; // Store URI on the clickable part too
        link.dataset.type = type;
        li.appendChild(link);

        // Add a placeholder for children (for classes)
        if (type === 'class') {
            const childrenUl = document.createElement('ul');
            childrenUl.style.display = 'none'; // Initially hidden
            li.appendChild(childrenUl);
        }
        return li;
    }

    // Function to fetch and display children for a class node
    async function fetchAndDisplayChildren(liElement, classUri) {
        const childrenUl = liElement.querySelector('ul');
        const toggle = liElement.querySelector('.toggle');
        if (!childrenUl || !toggle) return; // Should not happen

        // Avoid refetching if already loaded
        if (liElement.classList.contains('loaded')) {
            childrenUl.style.display = 'block';
            toggle.textContent = '-';
            liElement.classList.add('expanded');
            return;
        }

        // Show loading state on toggle? Maybe just change cursor
        toggle.style.cursor = 'wait';

        try {
            const response = await fetch(`/api/class-children/${encodeURIComponent(classUri)}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const children = await response.json();

            childrenUl.innerHTML = ''; // Clear any previous content (e.g., error)
            if (children.length > 0) {
                children.forEach(child => {
                    childrenUl.appendChild(createTreeNode(child, 'class'));
                });
            } else {
                // Optional: Indicate no children visually if desired, or just leave empty
                // childrenUl.innerHTML = '<li>(No subclasses)</li>';
                toggle.textContent = ''; // No toggle needed if no children
                toggle.classList.remove('toggle'); // Remove toggle functionality
            }

            childrenUl.style.display = 'block';
            toggle.textContent = '-';
            liElement.classList.add('loaded', 'expanded'); // Mark as loaded and expanded

        } catch (error) {
            console.error(`Error fetching children for ${classUri}:`, error);
            childrenUl.innerHTML = '<li class="error">Could not load children.</li>';
            childrenUl.style.display = 'block'; // Show the error
            toggle.textContent = '?'; // Indicate error state
        } finally {
             toggle.style.cursor = 'pointer';
        }
    }

    // Function to toggle visibility of children
    function toggleChildren(liElement) {
        const childrenUl = liElement.querySelector('ul');
        const toggle = liElement.querySelector('.toggle');
        if (!childrenUl || !toggle || !liElement.classList.contains('loaded')) return;

        if (childrenUl.style.display === 'none') {
            childrenUl.style.display = 'block';
            toggle.textContent = '-';
            liElement.classList.add('expanded');
        } else {
            childrenUl.style.display = 'none';
            toggle.textContent = '+';
            liElement.classList.remove('expanded');
        }
    }


    // --- Details Management ---

    // Helper function to check if a string is a valid URL
    function isValidHttpUrl(string) {
        let url;
        try {
            url = new URL(string);
        } catch (_) {
            return false;
        }
        return url.protocol === "http:" || url.protocol === "https:";
    }

    // Function to fetch and display details
    async function fetchAndDisplayDetails(type, uri) {
        if (!type || !uri || type === 'uri') {
             console.warn(`Invalid type or URI for fetching details: type=${type}, uri=${uri}`);
             showError(detailsView, `Cannot load details for ${uri || 'unknown item'} with type ${type}.`);
             return;
        }
        console.log(`Fetching details for ${type}: ${uri}`);
        showLoading(detailsView);
        try {
            const encodedUri = encodeURIComponent(uri);
            const response = await fetch(`/api/${type}/${encodedUri}`); // Use URI in path

            if (!response.ok) {
                let errorMsg = `HTTP error! status: ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMsg = errorData.description || errorData.error || errorMsg;
                } catch (e) { /* Ignore if response is not JSON */ }
                throw new Error(errorMsg);
            }
            const data = await response.json();
            renderDetails(type, data);
        } catch (error) {
            console.error(`Error fetching ${type} details for ${uri}:`, error);
            showError(detailsView, `Could not load details for ${uri}. ${error.message}`);
        }
    }


    // Function to render details in the main view
    function renderDetails(type, data) {
        let html = '';
        if (type === 'class' && data.class) {
            const item = data.class;
            html += `<h3>Class: ${item.label}</h3>`;
            // html += `<p><strong>URI:</strong> ${item.uri}</p>`; // Hide URI by default

            // Display Parent Classes
            if (data.parents && data.parents.length > 0) {
                 html += `<h4>Parent Classes (${data.parents.length})</h4><ul>`;
                 data.parents.forEach(parent => {
                     html += `<li><span class="class-item item-link" data-type="class" data-uri="${parent.uri}">${parent.label}</span></li>`;
                 });
                 html += `</ul>`;
            } else {
                 html += `<h4>Parent Classes</h4><p>None</p>`;
            }

            // Display Child Classes (Subclasses)
            if (data.children && data.children.length > 0) {
                html += `<h4>Subclasses (${data.children.length})</h4><ul>`;
                data.children.forEach(sub => {
                    html += `<li><span class="class-item item-link" data-type="class" data-uri="${sub.uri}">${sub.label}</span></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Subclasses</h4><p>None</p>`;
            }

            // Display Direct Individuals (Instances via rdf:type)
            if (data.direct_individuals && data.direct_individuals.length > 0) {
                html += `<h4>Direct Instances (${data.direct_individuals.length})</h4><ul>`;
                data.direct_individuals.forEach(ind => {
                    html += `<li><span class="individual-item item-link" data-type="individual" data-uri="${ind.uri}">${ind.label}</span></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Direct Instances</h4><p>None</p>`;
            }

            // Display Associated Individuals (e.g., Genes for Databases)
            if (data.associated_individuals && data.associated_individuals.length > 0) {
                // Customize heading based on class type? Maybe just generic.
                html += `<h4>Associated Genes/Individuals (${data.associated_individuals.length})</h4><ul>`;
                data.associated_individuals.forEach(ind => {
                    html += `<li><span class="individual-item item-link" data-type="individual" data-uri="${ind.uri}">${ind.label}</span></li>`;
                });
                html += `</ul>`;
            }
             // Optionally add a message if associated_individuals is empty but expected (e.g., for Databases)
             // else if (data.class.uri.includes("Database")) { // Simple check
             //    html += `<h4>Associated Genes/Individuals</h4><p>None found via 'is_from_database'.</p>`;
             // }


        } else if (type === 'individual' && data.individual) {
            const item = data.individual;
            html += `<h3>Individual: ${item.label}</h3>`;
            // html += `<p><strong>URI:</strong> ${item.uri}</p>`; // Hide URI

            // Display Class Types
            if (data.classes && data.classes.length > 0) {
                html += `<h4>Types (Classes) (${data.classes.length})</h4><ul>`;
                data.classes.forEach(cls => {
                    html += `<li><span class="class-item item-link" data-type="class" data-uri="${cls.uri}">${cls.label}</span></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Types (Classes)</h4><p>None specified</p>`;
            }

            // Display Datatype Properties
            if (data.datatype_properties && data.datatype_properties.length > 0) {
                html += `<h4>Properties (${data.datatype_properties.length})</h4><ul class="property-list">`;
                data.datatype_properties.forEach(prop => {
                    html += `<li><strong>${prop.property_label}:</strong> `;
                    // Check if value is a URL
                    if (isValidHttpUrl(prop.value)) {
                        html += `<a href="${prop.value}" target="_blank" rel="noopener noreferrer">${prop.value}</a>`;
                    }
                    // Check if it's a PubMed ID (assuming value is just the ID)
                    else if (prop.property_uri === PUBMED_PROP_URI && /^\d+$/.test(prop.value)) {
                         html += `<a href="https://pubmed.ncbi.nlm.nih.gov/${prop.value}/" target="_blank" rel="noopener noreferrer">${prop.value}</a>`;
                    }
                    else {
                        html += prop.value; // Display plain value
                    }
                    // Optionally show datatype
                    // if (prop.datatype) {
                    //     html += ` <em>(${prop.datatype.split('#').pop()})</em>`;
                    // }
                    html += `</li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Properties</h4><p>None</p>`;
            }

            // Display Object Properties (Relationships)
            if (data.object_properties && data.object_properties.length > 0) {
                html += `<h4>Relationships (${data.object_properties.length})</h4><ul class="relationship-list">`;
                data.object_properties.forEach(rel => {
                     html += `<li><strong>${rel.property_label}:</strong> `;
                     // Make the object clickable if it's a known class or individual
                     if ((rel.object_type === 'class' || rel.object_type === 'individual') && rel.object_uri) {
                         html += `<span class="item-link ${rel.object_type}-item" data-type="${rel.object_type}" data-uri="${rel.object_uri}">${rel.object_label}</span>`;
                     } else {
                         // Display as plain text/URI if not linkable
                         html += `${rel.object_label}`; // Display label only
                         // html += ` (URI: ${rel.object_uri})`; // Optionally show URI if needed
                     }
                     html += `</li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Relationships</h4><p>None</p>`;
            }
        } else {
             console.warn("RenderDetails called with unexpected data structure:", type, data);
             html = '<p>Details could not be displayed for this item.</p>';
        }

        detailsView.innerHTML = html;
    }


    // Function to load and display the initial tree structure (top-level classes)
    async function loadInitialTree() {
        showLoading(ontologyTree);
        try {
            const response = await fetch('/api/toplevel-classes');
            if (!response.ok) {
                 let errorMsg = `HTTP error! status: ${response.status}`;
                 try { const errorData = await response.json(); errorMsg = errorData.error || errorMsg; } catch (e) {}
                throw new Error(errorMsg);
            }
            const classes = await response.json();

            if (!Array.isArray(classes)) {
                 throw new Error("Invalid data format received from server.");
            }

            ontologyTree.innerHTML = ''; // Clear loading message
            const rootUl = document.createElement('ul');
            rootUl.id = 'root-ontology-list'; // Add ID for styling/selection

            if (classes.length === 0) {
                 rootUl.innerHTML = '<p>No top-level classes found.</p>';
                 detailsView.innerHTML = '<p>Ontology structure could not be loaded.</p>';
            } else {
                classes.forEach(cls => {
                    if (cls.uri) {
                        rootUl.appendChild(createTreeNode(cls, 'class'));
                    } else {
                        console.warn("Top-level class found without a URI:", cls);
                    }
                });
                 detailsView.innerHTML = '<p>Select an item from the left to see details.</p>';
            }
            ontologyTree.appendChild(rootUl);

        } catch (error) {
            console.error('Error fetching top-level classes:', error);
            showError(ontologyTree, `Could not load ontology structure. ${error.message}`);
            showError(detailsView, `Failed to load initial data. ${error.message}`);
        }
    }

    // --- Event Delegation ---

    // Click handler for the ontology tree (sidebar)
    ontologyTree.addEventListener('click', (event) => {
        const target = event.target;

        // Handle clicks on the toggle (+/-)
        if (target.classList.contains('toggle')) {
            const li = target.closest('li');
            if (li && li.dataset.type === 'class') {
                if (li.classList.contains('expanded')) {
                    toggleChildren(li); // Collapse
                } else if (li.classList.contains('loaded')) {
                     toggleChildren(li); // Expand already loaded
                } else {
                    fetchAndDisplayChildren(li, li.dataset.uri); // Fetch and expand
                }
            }
        }
        // Handle clicks on the item link itself (for details view)
        else if (target.classList.contains('item-link')) {
            const li = target.closest('li');
            const type = li.dataset.type;
            const uri = li.dataset.uri;
            if (type && uri) {
                fetchAndDisplayDetails(type, uri);
                // Optional: Highlight selected item in tree
                ontologyTree.querySelectorAll('.item-link.selected').forEach(el => el.classList.remove('selected'));
                target.classList.add('selected');
            }
        }
    });

    // Click handler for links within the details view
    detailsView.addEventListener('click', (event) => {
         if (event.target.classList.contains('item-link')) {
            const type = event.target.dataset.type;
            const uri = event.target.dataset.uri;
            if (type && uri) {
                fetchAndDisplayDetails(type, uri);
                 // Optional: Highlight selected item in details view (might be redundant)
                 // detailsView.querySelectorAll('.item-link.selected').forEach(el => el.classList.remove('selected'));
                 // event.target.classList.add('selected');

                 // Optional: Try to find and expand/highlight the item in the tree? (More complex)
            } else {
                 console.warn("Clicked item-link in details view without sufficient data:", event.target.dataset);
            }
        }
    });


    // --- Initial Load ---
    loadInitialTree();
}); 