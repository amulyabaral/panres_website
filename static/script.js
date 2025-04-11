document.addEventListener('DOMContentLoaded', async () => {
    const hierarchyContainer = document.getElementById('hierarchy-container');
    const detailsContainer = document.getElementById('details-container');
    const searchInput = document.getElementById('search');

    // Store only the URI registry globally now
    let uriRegistry = {};
    // Store load error status
    let loadError = null;

    // --- Fetch Initial Hierarchy Data ---
    async function fetchInitialHierarchy() {
        hierarchyContainer.innerHTML = '<div class="loader"></div>'; // Show loader
        detailsContainer.innerHTML = '<div class="info-box">Loading ontology hierarchy...</div>';
        try {
            const response = await fetch('/api/hierarchy');
            if (!response.ok) {
                let errorMsg = `Error fetching hierarchy: ${response.statusText}`;
                try {
                    const errorData = await response.json();
                    errorMsg += errorData.error ? ` - ${errorData.error}` : '';
                } catch (e) { /* Ignore if response is not JSON */ }
                throw new Error(errorMsg);
            }
            const data = await response.json();

            if (data.error) {
                 throw new Error(`Server error: ${data.error}`);
            }

            console.log("Received Initial Hierarchy:", data);

            // Check if data seems valid
            if (!data.topClasses || !data.uriRegistry) {
                 throw new Error("Received incomplete or invalid hierarchy data structure.");
            }

            uriRegistry = data.uriRegistry; // Store the registry

            // Render the top level and setup search
            renderInitialHierarchy(data.topClasses);
            setupSearch(); // Search will be limited initially
            detailsContainer.innerHTML = '<div class="info-box">Select an item from the explorer to view its details.</div>';

        } catch (error) {
            console.error("Error loading initial hierarchy:", error);
            loadError = error.message; // Store error message
            hierarchyContainer.innerHTML = `
                <div class="error-box">
                    <p>Could not load ontology hierarchy:</p>
                    <p>${error.message}</p>
                </div>`;
            detailsContainer.innerHTML = ''; // Clear details on error
        }
    }

    // --- Helper Functions ---
    function getLocalName(uri) {
        // Use registry first if available
        if (uriRegistry && uriRegistry[uri]) {
            return uriRegistry[uri].label;
        }
        // Fallback parsing (same as before)
        if (!uri) return '';
        try {
            const url = new URL(uri);
            if (url.hash) return url.hash.substring(1);
            const pathParts = url.pathname.split('/');
            return pathParts[pathParts.length - 1] || uri;
        } catch (e) {
            const hashIndex = uri.lastIndexOf('#');
            const slashIndex = uri.lastIndexOf('/');
            const index = Math.max(hashIndex, slashIndex);
            return index !== -1 ? uri.substring(index + 1) : uri;
        }
    }

    // --- Rendering Functions ---

    function renderInitialHierarchy(topClasses) {
        hierarchyContainer.innerHTML = ''; // Clear loader/error
        const treeUl = document.createElement('ul');
        treeUl.className = 'tree';

        if (!topClasses || topClasses.length === 0) {
             hierarchyContainer.innerHTML = '<div class="info-box">No top-level classes found.</div>';
             return;
        }

        // topClasses are already sorted by label from the server
        topClasses.forEach(classInfo => {
            // classInfo contains { id, label, hasSubClasses, hasInstances }
            const classItem = createTreeItem(classInfo, 'class');
            treeUl.appendChild(classItem);
        });

        hierarchyContainer.appendChild(treeUl);
        // Toggles are added dynamically when children are loaded
    }

    // Generic function to create a tree list item (li)
    function createTreeItem(itemInfo, itemType) {
        // itemInfo expected structure:
        // For class: { id, label, hasSubClasses, hasInstances }
        // For instance: { id, label }
        const li = document.createElement('li');
        li.dataset.itemId = itemInfo.id; // Store ID on the LI for easy access
        li.dataset.itemType = itemType;
        li.dataset.childrenLoaded = 'false'; // Track if children have been fetched

        const hasChildren = itemType === 'class' && (itemInfo.hasSubClasses || itemInfo.hasInstances);

        // Add caret if it has children
        if (hasChildren) {
            const caret = document.createElement('span');
            caret.className = 'caret';
            caret.onclick = handleCaretClick; // Assign click handler
            li.appendChild(caret);
        }

        // Create the item name span
        const itemSpan = document.createElement('span');
        itemSpan.textContent = itemInfo.label; // Already fetched
        itemSpan.dataset.itemId = itemInfo.id; // Redundant but useful for querySelector
        itemSpan.dataset.itemType = itemType;
        if (itemType === 'individual') {
            itemSpan.classList.add('individual-node');
        }
        itemSpan.onclick = (event) => {
            event.stopPropagation();
            if (itemType === 'class') {
                showClassDetails(itemInfo.id);
            } else {
                showIndividualDetails(itemInfo.id);
            }
        };
        li.appendChild(itemSpan);

        // Placeholder for nested list (will be populated on demand)
        if (hasChildren) {
            const nestedUl = document.createElement('ul');
            nestedUl.className = 'nested';
            // Add a temporary loading indicator inside
            nestedUl.innerHTML = '<li><span class="loading-placeholder">Loading...</span></li>';
            li.appendChild(nestedUl);
        }

        return li;
    }

    // --- Event Handlers ---

    async function handleCaretClick(event) {
        event.stopPropagation();
        const caret = event.target;
        const li = caret.closest('li');
        const nestedUl = li.querySelector(':scope > .nested');
        const itemId = li.dataset.itemId;
        const childrenLoaded = li.dataset.childrenLoaded === 'true';

        if (!nestedUl) return; // Should not happen if caret exists

        // Toggle display
        caret.classList.toggle('caret-down');
        nestedUl.classList.toggle('active');

        // Fetch children only if expanding for the first time
        if (nestedUl.classList.contains('active') && !childrenLoaded) {
            li.dataset.childrenLoaded = 'true'; // Mark as loading/loaded
            nestedUl.innerHTML = '<li><span class="loading-placeholder">Loading...</span></li>'; // Show loading indicator

            try {
                // Encode URI component for safe transfer in URL path
                const encodedUri = encodeURIComponent(itemId);
                const response = await fetch(`/api/children/${encodedUri}`);

                if (!response.ok) {
                    throw new Error(`Error fetching children: ${response.statusText}`);
                }
                const childrenData = await response.json();

                // Clear loading indicator
                nestedUl.innerHTML = '';

                // Add subclasses
                if (childrenData.subClasses && childrenData.subClasses.length > 0) {
                    childrenData.subClasses.forEach(subClassInfo => {
                        const subClassItem = createTreeItem(subClassInfo, 'class');
                        nestedUl.appendChild(subClassItem);
                    });
                }

                // Add instances
                if (childrenData.instances && childrenData.instances.length > 0) {
                    childrenData.instances.forEach(instanceInfo => {
                        const instanceItem = createTreeItem(instanceInfo, 'individual');
                        nestedUl.appendChild(instanceItem);
                    });
                }

                if (nestedUl.innerHTML === '') {
                     nestedUl.innerHTML = '<li><span class="info-box-small">No subclasses or instances found.</span></li>';
                }

            } catch (error) {
                console.error(`Error loading children for ${itemId}:`, error);
                nestedUl.innerHTML = `<li><span class="error-box-small">Error loading children.</span></li>`;
            }
        }
    }


    // --- Detail Display Functions ---

    function clearSelection() {
        const allSpans = hierarchyContainer.querySelectorAll('ul.tree li span[data-item-id]');
        allSpans.forEach(span => span.classList.remove('selected'));
    }

    function highlightSelectedItem(itemId) {
        clearSelection();
        const selectedSpan = hierarchyContainer.querySelector(`span[data-item-id="${CSS.escape(itemId)}"]`);
        if (selectedSpan) {
            selectedSpan.classList.add('selected');
            // Ensure the selected item is visible (expand parents)
            let currentLi = selectedSpan.closest('li');
            while (currentLi) {
                const parentUl = currentLi.parentElement;
                if (parentUl && parentUl.classList.contains('nested') && !parentUl.classList.contains('active')) {
                     parentUl.classList.add('active');
                     const parentLi = parentUl.closest('li');
                     if (parentLi) {
                         const parentCaret = parentLi.querySelector(':scope > .caret');
                         if (parentCaret) parentCaret.classList.add('caret-down');
                     }
                }
                // Move up only if parent is not the root tree
                 if (parentUl && parentUl.classList.contains('tree')) break;
                 currentLi = parentUl ? parentUl.closest('li') : null;

            }
             // Scroll into view (optional)
             selectedSpan.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    function renderUriLink(uri) {
        // Use the globally stored registry
        const info = uriRegistry ? uriRegistry[uri] : null;
        const label = info ? info.label : getLocalName(uri); // Use registry label or parse
        const title = uri; // Always show full URI on hover

        if (info && (info.type === 'class' || info.type === 'individual')) {
            // Known class or individual - make clickable to show details
            // Use CSS.escape on the itemId for the selector
            // We need to find the item in the tree and simulate a click on the *span*
            return `<a href="#" title="${title}" onclick="event.preventDefault(); document.querySelector('span[data-item-id=\\'${CSS.escape(uri)}\\']')?.click();">${label}</a>`;
        } else if (info && info.type === 'property') {
             // Known property - maybe just display label, link opens in new tab?
             return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        }
        else {
            // Other URI (external link, datatype, etc.) - make it a link opening in new tab
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        }
    }

    async function showClassDetails(classId) {
        highlightSelectedItem(classId);
        detailsContainer.innerHTML = '<div class="loader"></div>'; // Show loader

        try {
            const encodedUri = encodeURIComponent(classId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'class' || !result.details) {
                 throw new Error("Invalid details data received.");
            }
            const classObj = result.details;

            let html = `
                <h3>Class: ${classObj.label}</h3>
                <p class="class-uri"><strong>URI:</strong> ${classObj.id}</p>
            `;

            if (classObj.description) {
                html += `<div class="property-group">
                    <h4>Description</h4>
                    <p>${classObj.description}</p>
                </div>`;
            }

            // Super classes (IDs are in classObj.superClasses)
            if (classObj.superClasses && classObj.superClasses.length > 0) {
                html += `<div class="property-group">
                    <h4>Parent Classes</h4>
                    <ul>`;
                const sortedSuperClasses = classObj.superClasses
                    .map(id => ({ id: id, label: getLocalName(id) })) // Use getLocalName (uses registry)
                    .sort((a, b) => a.label.localeCompare(b.label));
                sortedSuperClasses.forEach(superInfo => {
                    html += `<li>${renderUriLink(superInfo.id)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Sub classes (IDs are in classObj.subClasses)
            if (classObj.subClasses && classObj.subClasses.length > 0) {
                html += `<div class="property-group">
                    <h4>Subclasses (${classObj.subClasses.length})</h4>
                    <ul>`;
                const sortedSubClasses = classObj.subClasses
                     .map(id => ({ id: id, label: getLocalName(id) }))
                     .sort((a, b) => a.label.localeCompare(b.label));
                sortedSubClasses.forEach(subInfo => {
                     html += `<li>${renderUriLink(subInfo.id)}</li>`;
                });
                html += `</ul></div>`;
            }

             // Instances (IDs are in classObj.instances)
            if (classObj.instances && classObj.instances.length > 0) {
                html += `<div class="property-group">
                    <h4>Instances (${classObj.instances.length})</h4>
                    <ul>`;
                const sortedInstances = classObj.instances
                    .map(id => ({ id: id, label: getLocalName(id) }))
                    .sort((a, b) => a.label.localeCompare(b.label));
                sortedInstances.forEach(indInfo => {
                     html += `<li>${renderUriLink(indInfo.id)}</li>`;
                });
                html += `</ul></div>`;
            }

            detailsContainer.innerHTML = html;

        } catch (error) {
             console.error(`Error fetching details for ${classId}:`, error);
             detailsContainer.innerHTML = `<div class="error-box">Could not load details for ${getLocalName(classId)}. ${error.message}</div>`;
        }
    }

    async function showIndividualDetails(individualId) {
        highlightSelectedItem(individualId);
        detailsContainer.innerHTML = '<div class="loader"></div>'; // Show loader

         try {
            const encodedUri = encodeURIComponent(individualId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'individual' || !result.details) {
                 throw new Error("Invalid details data received.");
            }
            const indObj = result.details;


            let html = `
                <h3>Individual: ${indObj.label}</h3>
                <p class="class-uri"><strong>URI:</strong> ${indObj.id}</p>
            `;

             if (indObj.description) {
                html += `<div class="property-group">
                    <h4>Description</h4>
                    <p>${indObj.description}</p>
                </div>`;
            }

            // Types (Classes - IDs are in indObj.types)
             if (indObj.types && indObj.types.length > 0) {
                html += `<div class="property-group">
                    <h4>Types (Classes)</h4>
                    <ul>`;
                 const sortedTypes = indObj.types
                    .map(id => ({ id: id, label: getLocalName(id) }))
                    .sort((a, b) => a.label.localeCompare(b.label));
                sortedTypes.forEach(typeInfo => {
                    html += `<li>${renderUriLink(typeInfo.id)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Properties (already structured in indObj.properties)
            const properties = indObj.properties;
            if (properties && Object.keys(properties).length > 0) {
                 html += `<div class="property-group"><h4>Properties</h4>`;

                 const sortedPropUris = Object.keys(properties).sort((a, b) => {
                     const labelA = getLocalName(a); // Uses registry via getLocalName
                     const labelB = getLocalName(b);
                     return labelA.localeCompare(labelB);
                 });

                 sortedPropUris.forEach(propUri => {
                     const values = properties[propUri]; // Array of {type, value, datatype}
                     html += `<div style="margin-bottom: 8px;"><strong>${renderUriLink(propUri)}:</strong>`;
                     if (values.length > 1) {
                         html += `<ul class="property-value">`;
                         values.forEach(val => html += `<li>${renderPropertyValue(val)}</li>`);
                         html += `</ul>`;
                     } else if (values.length === 1) {
                         html += `<div class="property-value">${renderPropertyValue(values[0])}</div>`;
                     }
                     html += `</div>`;
                 });
                 html += `</div>`;
            } else {
                 html += `<div class="info-box">No properties defined for this individual.</div>`;
            }

            detailsContainer.innerHTML = html;

        } catch (error) {
             console.error(`Error fetching details for ${individualId}:`, error);
             detailsContainer.innerHTML = `<div class="error-box">Could not load details for ${getLocalName(individualId)}. ${error.message}</div>`;
        }
    }

    // Helper to render a single property value (URI or Literal) - Unchanged mostly
    function renderPropertyValue(val) {
        if (val.type === 'uri') {
            return renderUriLink(val.value); // Uses global registry via renderUriLink
        } else { // Literal
            const escapedValue = val.value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            let literalHtml = escapedValue;
            if (val.datatype) {
                 const dtLabel = getLocalName(val.datatype); // Use registry/parsing for datatype label
                 // Link datatype only if it's likely an ontology element (heuristic: check registry)
                 const dtLink = uriRegistry[val.datatype]
                              ? renderUriLink(val.datatype)
                              : `<span title="${val.datatype}">${dtLabel}</span>`;
                 literalHtml += ` <small>(${dtLink})</small>`;
            }
            return literalHtml;
        }
    }


    function setupSearch() {
        // --- Basic Search (Client-side, only searches loaded nodes) ---
        // NOTE: This search is now limited as not all data is loaded initially.
        // A server-side search endpoint would be much more effective.
        searchInput.addEventListener('input', debounce(function() {
            const searchTerm = this.value.toLowerCase().trim();
            const allVisibleSpans = hierarchyContainer.querySelectorAll('ul.tree li span[data-item-id]'); // Only search visible items

            // Reset highlights if search term is short
            if (searchTerm.length < 2) {
                 allVisibleSpans.forEach(span => span.style.backgroundColor = ''); // Clear background highlight
                 // Maybe add a message indicating search is limited?
                return;
            }

            // Simple highlight matching visible items
            allVisibleSpans.forEach(span => {
                const label = span.textContent.toLowerCase();
                const name = getLocalName(span.dataset.itemId).toLowerCase(); // Get name for matching

                if (label.includes(searchTerm) || name.includes(searchTerm)) {
                    span.style.backgroundColor = 'yellow'; // Simple highlight
                } else {
                    span.style.backgroundColor = ''; // Clear highlight
                }
            });
             // NOTE: This doesn't hide/show or expand nodes. It only highlights.
             // A full client-side search with lazy loading is complex.
             console.warn("Search is currently limited to highlighting already loaded/visible items.");


        }, 300));
    }

    // Utility function for debouncing (Unchanged)
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func.apply(this, args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // --- Initial Load ---
    fetchInitialHierarchy(); // Start by fetching only the top level

}); // End DOMContentLoaded 