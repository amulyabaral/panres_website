document.addEventListener('DOMContentLoaded', () => {
    const ontologyTree = document.getElementById('ontology-tree');
    const detailsView = document.getElementById('details-view');

    function showLoading(element) {
        element.innerHTML = '<div class="loading">Loading...</div>';
    }

    function showError(element, message) {
        element.innerHTML = `<div class="error">Error: ${message}</div>`;
    }

    // Function to fetch and display details
    async function fetchAndDisplayDetails(type, name) {
        // Basic validation
        if (!type || !name || type === 'uri' || type === 'auto') {
             console.warn(`Invalid type or name provided for fetching details: type=${type}, name=${name}`);
             showError(detailsView, `Cannot load details for ${name || 'unknown item'} with type ${type}.`);
             return;
        }
        console.log(`Fetching details for ${type}: ${name}`); // Log clicks
        showLoading(detailsView);
        try {
            // Ensure name is properly encoded for URLs
            const encodedName = encodeURIComponent(name);
            const response = await fetch(`/api/${type}/${encodedName}`);

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
            console.error(`Error fetching ${type} details for ${name}:`, error);
            showError(detailsView, `Could not load details for ${name}. ${error.message}`);
        }
    }


    // Function to render details in the main view
    function renderDetails(type, data) {
        let html = '';
        if (type === 'class' && data.class) {
            const item = data.class;
            html += `<h3>Class: ${item.label || item.name}</h3>`;
            html += `<p><strong>URI:</strong> ${item.uri}</p>`;
            if (item.description) {
                html += `<p><strong>Description:</strong> ${item.description}</p>`;
            }
            // Display Parent Class Info (using data.parent from API)
            if (data.parent && data.parent.uri) {
                 html += `<p><strong>Parent Class:</strong> <span class="class-item item-link" data-type="class" data-name="${data.parent.name}">${data.parent.label || data.parent.name}</span> (URI: ${data.parent.uri})</p>`;
            } else if (item.parent_uri) {
                 // Fallback if parent info wasn't joined correctly but URI exists
                 html += `<p><strong>Parent Class URI:</strong> ${item.parent_uri}</p>`;
            } else {
                 html += `<p><strong>Parent Class:</strong> None</p>`;
            }


            if (data.subclasses && data.subclasses.length > 0) {
                html += `<h4>Subclasses (${data.subclasses.length})</h4><ul>`;
                data.subclasses.forEach(sub => {
                    html += `<li><span class="class-item item-link" data-type="class" data-name="${sub.name}">${sub.label || sub.name}</span></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Subclasses</h4><p>None</p>`;
            }

            if (data.individuals && data.individuals.length > 0) {
                html += `<h4>Individuals (${data.individuals.length})</h4><ul>`;
                data.individuals.forEach(ind => {
                    html += `<li><span class="individual-item item-link" data-type="individual" data-name="${ind.name}">${ind.label || ind.name}</span></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Individuals</h4><p>None</p>`;
            }

             // Display properties/relationships of the class itself if any (might be empty)
            if (data.properties && data.properties.length > 0) {
                html += `<h4>Class Properties</h4><ul class="property-list">`;
                data.properties.forEach(prop => {
                    html += `<li><strong>${prop.predicate_name}:</strong> ${prop.value_literal} <em>(${prop.value_type})</em></li>`;
                });
                html += `</ul>`;
            }
            if (data.relationships && data.relationships.length > 0) {
                html += `<h4>Class Relationships</h4><ul class="relationship-list">`;
                data.relationships.forEach(rel => {
                     // Use object_type and object_name provided by the API
                     const linkType = rel.object_type || 'uri'; // Default to 'uri' if type unknown
                     const linkName = rel.object_name || rel.object_uri;
                     // Only make it a link if we have a valid type and name
                     if ((linkType === 'class' || linkType === 'individual') && rel.object_name) {
                         html += `<li><strong>${rel.predicate_name}:</strong> <span class="item-link ${linkType}-item" data-type="${linkType}" data-name="${rel.object_name}">${linkName}</span></li>`;
                     } else {
                         html += `<li><strong>${rel.predicate_name}:</strong> ${linkName} (URI: ${rel.object_uri})</li>`; // Display as plain text/URI if not linkable
                     }
                });
                html += `</ul>`;
            }


        } else if (type === 'individual' && data.individual) {
            const item = data.individual;
            html += `<h3>Individual: ${item.label || item.name}</h3>`;
            html += `<p><strong>URI:</strong> ${item.uri}</p>`;
             if (data.class) {
                 html += `<p><strong>Type (Class):</strong> <span class="class-item item-link" data-type="class" data-name="${data.class.name}">${data.class.label || data.class.name}</span></p>`;
             } else if (item.class_uri) {
                 html += `<p><strong>Type (Class URI):</strong> ${item.class_uri}</p>`; // Fallback
             }
            if (item.description) {
                html += `<p><strong>Description:</strong> ${item.description}</p>`;
            }

            if (data.properties && data.properties.length > 0) {
                html += `<h4>Properties (${data.properties.length})</h4><ul class="property-list">`;
                data.properties.forEach(prop => {
                    html += `<li><strong>${prop.predicate_name || prop.predicate_uri}:</strong> ${prop.value_literal} <em>(${prop.value_type})</em></li>`;
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Properties</h4><p>None</p>`;
            }


            if (data.relationships && data.relationships.length > 0) {
                html += `<h4>Relationships (${data.relationships.length})</h4><ul class="relationship-list">`;
                data.relationships.forEach(rel => {
                    // Use object_type and object_name provided by the API
                    const linkType = rel.object_type || 'uri'; // Default to 'uri' if type unknown
                    const linkName = rel.object_name || rel.object_uri;
                    const predicateName = rel.predicate_name || rel.predicate_uri;
                    // Only make it a link if we have a valid type and name
                    if ((linkType === 'class' || linkType === 'individual') && rel.object_name) {
                         html += `<li><strong>${predicateName}:</strong> <span class="item-link ${linkType}-item" data-type="${linkType}" data-name="${rel.object_name}">${linkName}</span></li>`;
                    } else {
                         html += `<li><strong>${predicateName}:</strong> ${linkName} (URI: ${rel.object_uri})</li>`; // Display as plain text/URI if not linkable
                    }
                });
                html += `</ul>`;
            } else {
                 html += `<h4>Relationships</h4><p>None</p>`;
            }
        } else {
             // Handle cases where data might be missing after a successful fetch (shouldn't happen often)
             console.warn("RenderDetails called with unexpected data structure:", type, data);
             html = '<p>Details could not be displayed for this item.</p>';
        }

        detailsView.innerHTML = html;
    }


    // Function to load and display the initial tree structure
    async function loadInitialTree() {
        showLoading(ontologyTree);
        try {
            const response = await fetch('/api/toplevel-classes');
            if (!response.ok) {
                 let errorMsg = `HTTP error! status: ${response.status}`;
                 try {
                     const errorData = await response.json();
                     errorMsg = errorData.description || errorData.error || errorMsg;
                 } catch (e) { /* Ignore */ }
                throw new Error(errorMsg);
            }
            const classes = await response.json();

            if (!Array.isArray(classes)) {
                 console.error("API response for top-level classes is not an array:", classes);
                 throw new Error("Invalid data format received from server.");
            }

            if (classes.length === 0) {
                 ontologyTree.innerHTML = '<p>No top-level classes found in the database.</p>';
                 // Optionally show a message in details view too
                 detailsView.innerHTML = '<p>Select an item from the left (if any appear) to see details.</p>';
                 return;
            }

            let treeHtml = '<ul>';
            classes.forEach(cls => {
                // Ensure cls.name exists before creating the item
                if (cls.name) {
                    treeHtml += `<li><span class="class-item item-link" data-type="class" data-name="${cls.name}">${cls.label || cls.name}</span></li>`;
                } else {
                    console.warn("Top-level class found without a name:", cls);
                }
            });
            treeHtml += '</ul>';
            ontologyTree.innerHTML = treeHtml;
             // Clear details view on initial load
             detailsView.innerHTML = '<p>Select an item from the left to see details.</p>';

        } catch (error) {
            console.error('Error fetching top-level classes:', error);
            showError(ontologyTree, `Could not load ontology structure. ${error.message}`);
            // Also show error in main view
             showError(detailsView, `Failed to load initial data. ${error.message}`);
        }
    }

    // Event delegation for clicking on items in the tree or details view
    document.body.addEventListener('click', (event) => {
        if (event.target.classList.contains('item-link')) {
            const type = event.target.dataset.type;
            const name = event.target.dataset.name;
            // const uri = event.target.dataset.uri; // URI not needed directly for fetching if name+type is used

            // Remove the old 'auto' logic, rely on specific type/name from API data
            if (type && name && type !== 'uri' && type !== 'auto') {
                fetchAndDisplayDetails(type, name);
                 // Optional: Highlight selected item
                 document.querySelectorAll('.item-link.selected').forEach(el => el.classList.remove('selected'));
                 event.target.classList.add('selected');
            } else {
                 console.warn("Clicked item-link without sufficient data:", event.target.dataset);
                 // Optionally show a message if a link is somehow unclickable
                 // showError(detailsView, "Cannot navigate from this link.");
            }
        }
    });

    // Initial load
    loadInitialTree();
}); 