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
        showLoading(detailsView);
        try {
            const response = await fetch(`/api/${type}/${encodeURIComponent(name)}`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.description || `HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            renderDetails(type, data);
        } catch (error) {
            console.error(`Error fetching ${type} details:`, error);
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
            if (item.parent_uri) {
                 // Fetch parent name separately if needed, or make API return it
                 html += `<p><strong>Parent Class URI:</strong> ${item.parent_uri}</p>`;
            }

            if (data.subclasses && data.subclasses.length > 0) {
                html += `<h4>Subclasses</h4><ul>`;
                data.subclasses.forEach(sub => {
                    html += `<li><span class="class-item item-link" data-type="class" data-name="${sub.name}">${sub.label || sub.name}</span></li>`;
                });
                html += `</ul>`;
            }

            if (data.individuals && data.individuals.length > 0) {
                html += `<h4>Individuals</h4><ul>`;
                data.individuals.forEach(ind => {
                    html += `<li><span class="individual-item item-link" data-type="individual" data-name="${ind.name}">${ind.label || ind.name}</span></li>`;
                });
                html += `</ul>`;
            }
             // Display properties/relationships of the class itself if any
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
                     html += `<li><strong>${rel.predicate_name}:</strong> <span class="item-link" data-type="auto" data-uri="${rel.object_uri}" data-name="${rel.object_name}">${rel.object_name || rel.object_uri}</span></li>`;
                });
                html += `</ul>`;
            }


        } else if (type === 'individual' && data.individual) {
            const item = data.individual;
            html += `<h3>Individual: ${item.label || item.name}</h3>`;
            html += `<p><strong>URI:</strong> ${item.uri}</p>`;
             if (data.class) {
                 html += `<p><strong>Type:</strong> <span class="class-item item-link" data-type="class" data-name="${data.class.name}">${data.class.label || data.class.name}</span></p>`;
             }
            if (item.description) {
                html += `<p><strong>Description:</strong> ${item.description}</p>`;
            }

            if (data.properties && data.properties.length > 0) {
                html += `<h4>Properties</h4><ul class="property-list">`;
                data.properties.forEach(prop => {
                    html += `<li><strong>${prop.predicate_name}:</strong> ${prop.value_literal} <em>(${prop.value_type})</em></li>`;
                });
                html += `</ul>`;
            }

            if (data.relationships && data.relationships.length > 0) {
                html += `<h4>Relationships</h4><ul class="relationship-list">`;
                data.relationships.forEach(rel => {
                    // Link to the related item (class or individual)
                    html += `<li><strong>${rel.predicate_name}:</strong> <span class="item-link" data-type="${rel.object_type}" data-name="${rel.object_name}" data-uri="${rel.object_uri}">${rel.object_name || rel.object_uri}</span></li>`;
                });
                html += `</ul>`;
            }
        } else {
             html = '<p>Details could not be loaded.</p>';
        }

        detailsView.innerHTML = html;
    }


    // Function to load and display the initial tree structure
    async function loadInitialTree() {
        showLoading(ontologyTree);
        try {
            const response = await fetch('/api/toplevel-classes');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const classes = await response.json();

            if (classes.length === 0) {
                 ontologyTree.innerHTML = '<p>No top-level classes found.</p>';
                 return;
            }

            let treeHtml = '<ul>';
            classes.forEach(cls => {
                treeHtml += `<li><span class="class-item item-link" data-type="class" data-name="${cls.name}">${cls.label || cls.name}</span></li>`;
            });
            treeHtml += '</ul>';
            ontologyTree.innerHTML = treeHtml;

        } catch (error) {
            console.error('Error fetching top-level classes:', error);
            showError(ontologyTree, 'Could not load ontology structure.');
        }
    }

    // Event delegation for clicking on items in the tree or details view
    document.body.addEventListener('click', (event) => {
        if (event.target.classList.contains('item-link')) {
            const type = event.target.dataset.type;
            const name = event.target.dataset.name;
            const uri = event.target.dataset.uri; // URI might be present for relationships

            if (type === 'auto' && uri) {
                 // Attempt to guess type based on common patterns or fetch URI info
                 // Simple guess: if name starts with lowercase maybe individual, uppercase maybe class? (Highly unreliable!)
                 // A better approach would be an API endpoint /api/uri-info/<uri> that returns type and name
                 console.warn("Auto-detecting type from URI is not fully implemented. Trying based on name:", name);
                 // Very basic guess:
                 const guessedType = name && name[0] === name[0].toUpperCase() ? 'class' : 'individual';
                 fetchAndDisplayDetails(guessedType, name); // Use name if available
            } else if (type && name) {
                fetchAndDisplayDetails(type, name);
                 // Optional: Highlight selected item
                 document.querySelectorAll('.item-link.selected').forEach(el => el.classList.remove('selected'));
                 event.target.classList.add('selected');
            }
        }
    });

    // Initial load
    loadInitialTree();
}); 