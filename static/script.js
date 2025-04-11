document.addEventListener('DOMContentLoaded', async () => {
    const hierarchyContainer = document.getElementById('hierarchy-container');
    const detailsContainer = document.getElementById('details-container');
    const searchInput = document.getElementById('search');

    // Store ontology data fetched from the server
    let ontologyData = {
        allClasses: [],
        topClasses: [],
        subClassMap: {},
        classDetails: {},
        allIndividuals: [],
        individualDetails: {},
        classInstanceMap: {},
        uriRegistry: {} // URI registry now comes from server
    };

    // --- Fetch Ontology Data ---
    async function fetchOntologyData() {
        hierarchyContainer.innerHTML = '<div class="loader"></div>'; // Show loader
        detailsContainer.innerHTML = '<div class="info-box">Loading ontology data...</div>';
        try {
            const response = await fetch('/api/ontology-data');
            if (!response.ok) {
                let errorMsg = `Error fetching ontology data: ${response.statusText}`;
                try {
                    const errorData = await response.json();
                    errorMsg += errorData.error ? ` - ${errorData.error}` : '';
                } catch (e) { /* Ignore if response is not JSON */ }
                throw new Error(errorMsg);
            }
            ontologyData = await response.json();

            if (ontologyData.error) {
                 throw new Error(`Server error: ${ontologyData.error}`);
            }

            console.log("Received Ontology Data:", ontologyData);

            // Check if data seems valid (basic check)
            if (!ontologyData.allClasses || !ontologyData.classDetails || !ontologyData.uriRegistry) {
                 throw new Error("Received incomplete or invalid ontology data structure.");
            }

            // Render the hierarchy and setup search
            renderClassHierarchy();
            setupSearch();
            detailsContainer.innerHTML = '<div class="info-box">Select an item from the explorer to view its details.</div>'; // Reset details view

        } catch (error) {
            console.error("Error loading or processing ontology data:", error);
            hierarchyContainer.innerHTML = `
                <div class="error-box">
                    <p>Could not load ontology data:</p>
                    <p>${error.message}</p>
                </div>`;
            detailsContainer.innerHTML = ''; // Clear details on error
        }
    }

    // --- Helper Functions (Mostly Unchanged) ---
    function getLocalName(uri) {
        if (!uri) return '';
        try {
            // Use URL constructor for robust parsing if possible
            const url = new URL(uri);
            if (url.hash) {
                return url.hash.substring(1);
            }
            const pathParts = url.pathname.split('/');
            return pathParts[pathParts.length - 1] || uri; // Fallback to full URI if path is just '/'
        } catch (e) {
            // Fallback for non-standard URIs or simple names
            const hashIndex = uri.lastIndexOf('#');
            const slashIndex = uri.lastIndexOf('/');
            const index = Math.max(hashIndex, slashIndex);
            return index !== -1 ? uri.substring(index + 1) : uri;
        }
    }

    // --- Rendering Functions (Mostly Unchanged, use fetched ontologyData) ---

    function renderClassHierarchy() {
        hierarchyContainer.innerHTML = ''; // Clear loader/error
        const treeUl = document.createElement('ul');
        treeUl.className = 'tree';

        // Ensure topClasses and classDetails exist
        if (!ontologyData.topClasses || !ontologyData.classDetails) {
             hierarchyContainer.innerHTML = '<div class="error-box">Error: Missing class data for rendering.</div>';
             return;
        }


        const sortedTopClasses = ontologyData.topClasses
            .map(id => ontologyData.classDetails[id])
            .filter(Boolean) // Filter out potential undefined if ID wasn't found
            .sort((a, b) => a.label.localeCompare(b.label))
            .map(cls => cls.id);

        if (sortedTopClasses.length === 0) {
             hierarchyContainer.innerHTML = '<div class="info-box">No top-level classes found in the ontology.</div>';
             return;
        }

        sortedTopClasses.forEach(classId => {
            const classObj = ontologyData.classDetails[classId];
            if (classObj) { // Ensure class object exists
               const classItem = createClassTreeItem(classObj);
               treeUl.appendChild(classItem);
            } else {
                console.warn(`Top class ID ${classId} not found in classDetails.`);
            }
        });

        hierarchyContainer.appendChild(treeUl);
        setupTreeToggles();
    }

    function createClassTreeItem(classObj) {
        const li = document.createElement('li');

        // Check for subclasses and instances using the fetched data
        const hasSubClasses = classObj.subClasses && classObj.subClasses.length > 0;
        const hasInstances = classObj.instances && classObj.instances.length > 0;

        // Add caret if it has children (subclasses or instances)
        if (hasSubClasses || hasInstances) {
            const caret = document.createElement('span');
            caret.className = 'caret';
            li.appendChild(caret);
        }

        // Create the class name element
        const classSpan = document.createElement('span');
        classSpan.textContent = classObj.label || classObj.name; // Use label, fallback to name
        classSpan.dataset.itemId = classObj.id;
        classSpan.dataset.itemType = 'class';
        classSpan.onclick = (event) => {
            event.stopPropagation(); // Prevent li click if needed
            showClassDetails(classObj.id);
        };
        li.appendChild(classSpan);


        // Create nested list if there are subclasses or instances
        if (hasSubClasses || hasInstances) {
            const nestedUl = document.createElement('ul');
            nestedUl.className = 'nested';

            // Add subclasses first, sorted
            if (hasSubClasses) {
                const sortedSubClasses = classObj.subClasses
                    .map(id => ontologyData.classDetails[id])
                    .filter(Boolean) // Filter out undefined
                    .sort((a, b) => (a.label || a.name).localeCompare(b.label || b.name))
                    .map(cls => cls.id);

                sortedSubClasses.forEach(subClassId => {
                    const subClassObj = ontologyData.classDetails[subClassId];
                     if (subClassObj) { // Ensure subclass object exists
                        const subClassItem = createClassTreeItem(subClassObj); // Recursive call
                        nestedUl.appendChild(subClassItem);
                     } else {
                         console.warn(`Subclass ID ${subClassId} not found in classDetails for parent ${classObj.id}`);
                     }
                });
            }

            // Add instances next, sorted
            if (hasInstances) {
                 const sortedInstances = classObj.instances
                    .map(id => ontologyData.individualDetails[id])
                    .filter(Boolean) // Filter out undefined
                    .sort((a, b) => (a.label || a.name).localeCompare(b.label || b.name));

                 sortedInstances.forEach(indObj => {
                     const instanceLi = document.createElement('li');
                     const instanceSpan = document.createElement('span');
                     instanceSpan.textContent = indObj.label || indObj.name;
                     instanceSpan.className = 'individual-node'; // Style individuals
                     instanceSpan.dataset.itemId = indObj.id;
                     instanceSpan.dataset.itemType = 'individual';
                     instanceSpan.onclick = (event) => {
                         event.stopPropagation();
                         showIndividualDetails(indObj.id);
                     };
                     instanceLi.appendChild(instanceSpan);
                     nestedUl.appendChild(instanceLi);
                 });
            }

            li.appendChild(nestedUl);
        }

        return li;
    }

    function setupTreeToggles() {
        // Set up toggling for tree carets
        const carets = hierarchyContainer.querySelectorAll(".caret"); // Scope query to container
        carets.forEach(caret => {
            // Remove existing listener to prevent duplicates if re-rendering
            caret.replaceWith(caret.cloneNode(true));
        });
        // Add new listeners
        hierarchyContainer.querySelectorAll(".caret").forEach(caret => {
             caret.addEventListener("click", function(event) {
                event.stopPropagation(); // Prevent li click
                this.classList.toggle("caret-down");
                const nestedList = this.parentElement.querySelector(":scope > .nested"); // Direct child selector
                if (nestedList) {
                    nestedList.classList.toggle("active");
                }
            });
        });
    }

    function clearSelection() {
         // Remove existing selected class/individual highlight
        const allSpans = hierarchyContainer.querySelectorAll('ul.tree li span[data-item-id]');
        allSpans.forEach(span => span.classList.remove('selected'));
    }

    function highlightSelectedItem(itemId) {
        clearSelection();
         // Add selected highlight
        const selectedSpan = hierarchyContainer.querySelector(`span[data-item-id="${CSS.escape(itemId)}"]`); // Use CSS.escape for potentially complex URIs
        if (selectedSpan) {
            selectedSpan.classList.add('selected');
        }
    }

    function renderUriLink(uri) {
        // Use the registry from the server
        const info = ontologyData.uriRegistry ? ontologyData.uriRegistry[uri] : null;
        const label = info ? info.label : getLocalName(uri);
        const title = uri; // Always show full URI on hover

        if (info && (info.type === 'class' || info.type === 'individual')) {
            // Known class or individual - make clickable to show details
            // Use CSS.escape on the itemId for the selector
            return `<a href="#" title="${title}" onclick="event.preventDefault(); document.querySelector('span[data-item-id=\\'${CSS.escape(uri)}\\']')?.click();">${label}</a>`;
        } else {
            // Other URI (property, external link, datatype, etc.) - make it a link opening in new tab
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        }
    }

    function showClassDetails(classId) {
        const classObj = ontologyData.classDetails[classId];
        if (!classObj) {
             detailsContainer.innerHTML = `<div class="error-box">Class details not found for ID: ${classId}</div>`;
             return;
        }

        highlightSelectedItem(classId);

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

        // Super classes
        if (classObj.superClasses && classObj.superClasses.length > 0) {
            html += `<div class="property-group">
                <h4>Parent Classes</h4>
                <ul>`;
            // Sort superclasses by label for consistency
            const sortedSuperClasses = classObj.superClasses
                .map(id => ontologyData.uriRegistry[id] ? { id: id, label: ontologyData.uriRegistry[id].label } : { id: id, label: getLocalName(id) })
                .sort((a, b) => a.label.localeCompare(b.label));
            sortedSuperClasses.forEach(superInfo => {
                html += `<li>${renderUriLink(superInfo.id)}</li>`;
            });
            html += `</ul></div>`;
        }

        // Sub classes
        if (classObj.subClasses && classObj.subClasses.length > 0) {
            html += `<div class="property-group">
                <h4>Subclasses</h4>
                <ul>`;
            const sortedSubClasses = classObj.subClasses
                .map(id => ontologyData.classDetails[id])
                .filter(Boolean)
                .sort((a, b) => (a.label || a.name).localeCompare(b.label || b.name));
            sortedSubClasses.forEach(subClass => {
                 html += `<li>${renderUriLink(subClass.id)}</li>`;
            });
            html += `</ul></div>`;
        }

         // Instances
        if (classObj.instances && classObj.instances.length > 0) {
            html += `<div class="property-group">
                <h4>Instances (${classObj.instances.length})</h4>
                <ul>`;
            const sortedInstances = classObj.instances
                .map(id => ontologyData.individualDetails[id])
                .filter(Boolean)
                .sort((a, b) => (a.label || a.name).localeCompare(b.label || b.name));
            sortedInstances.forEach(ind => {
                 html += `<li>${renderUriLink(ind.id)}</li>`;
            });
            html += `</ul></div>`;
        }


        detailsContainer.innerHTML = html;
    }

    function showIndividualDetails(individualId) {
        const indObj = ontologyData.individualDetails[individualId];
         if (!indObj) {
             detailsContainer.innerHTML = `<div class="error-box">Individual details not found for ID: ${individualId}</div>`;
             return;
         }

        highlightSelectedItem(individualId);

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

        // Types (Classes)
         if (indObj.types && indObj.types.length > 0) {
            html += `<div class="property-group">
                <h4>Types (Classes)</h4>
                <ul>`;
            // Sort types by label
             const sortedTypes = indObj.types
                .map(id => ontologyData.uriRegistry[id] ? { id: id, label: ontologyData.uriRegistry[id].label } : { id: id, label: getLocalName(id) })
                .sort((a, b) => a.label.localeCompare(b.label));
            sortedTypes.forEach(typeInfo => {
                html += `<li>${renderUriLink(typeInfo.id)}</li>`;
            });
            html += `</ul></div>`;
        }

        // Properties
        const properties = indObj.properties;
        if (properties && Object.keys(properties).length > 0) {
             html += `<div class="property-group"><h4>Properties</h4>`;

             // Sort properties by label using uriRegistry if available
             const sortedPropUris = Object.keys(properties).sort((a, b) => {
                 const labelA = ontologyData.uriRegistry[a]?.label || getLocalName(a);
                 const labelB = ontologyData.uriRegistry[b]?.label || getLocalName(b);
                 return labelA.localeCompare(labelB);
             });

             sortedPropUris.forEach(propUri => {
                 const values = properties[propUri]; // Array of {type, value, datatype}

                 html += `<div style="margin-bottom: 8px;"><strong>${renderUriLink(propUri)}:</strong>`; // Render property name as link

                 if (values.length > 1) { // Multiple values for this property
                     html += `<ul class="property-value">`;
                     values.forEach(val => {
                         html += `<li>${renderPropertyValue(val)}</li>`;
                     });
                     html += `</ul>`;
                 } else if (values.length === 1) { // Single value
                     const val = values[0];
                     html += `<div class="property-value">${renderPropertyValue(val)}</div>`;
                 }
                 html += `</div>`; // Close property div
             });

             html += `</div>`; // Close property-group
        } else {
             html += `<div class="info-box">No properties defined for this individual.</div>`;
        }


        detailsContainer.innerHTML = html;
    }

    // Helper to render a single property value (URI or Literal)
    function renderPropertyValue(val) {
        if (val.type === 'uri') {
            return renderUriLink(val.value);
        } else { // Literal
            // Basic HTML escaping for safety
            const escapedValue = val.value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            let literalHtml = escapedValue;
            if (val.datatype) {
                // Render datatype as a link if known, otherwise just text
                 const dtInfo = ontologyData.uriRegistry ? ontologyData.uriRegistry[val.datatype] : null;
                 const dtLabel = dtInfo ? dtInfo.label : getLocalName(val.datatype);
                 const dtLink = dtInfo ? renderUriLink(val.datatype) : `<span title="${val.datatype}">${dtLabel}</span>`;
                 literalHtml += ` <small>(${dtLink})</small>`;
            }
            return literalHtml;
        }
    }


    function setupSearch() {
        searchInput.addEventListener('input', debounce(function() {
            const searchTerm = this.value.toLowerCase().trim();
            const allListItems = hierarchyContainer.querySelectorAll('ul.tree li');
            const allSpans = hierarchyContainer.querySelectorAll('ul.tree li span[data-item-id]');

            // Reset view if search term is short
            if (searchTerm.length < 2) {
                allListItems.forEach(li => {
                    li.style.display = ''; // Show item
                    const nestedList = li.querySelector(':scope > .nested');
                    const caret = li.querySelector(':scope > .caret');
                    if (nestedList) {
                        // Collapse all nested lists initially
                        nestedList.classList.remove('active');
                        if(caret) caret.classList.remove('caret-down');
                    }
                });
                 // Optional: Re-expand top-level items if desired
                 hierarchyContainer.querySelectorAll('ul.tree > li > .caret').forEach(caret => {
                     // caret.click(); // Simulate click might be too much, just set classes
                     // caret.classList.add('caret-down');
                     // const nested = caret.parentElement.querySelector(':scope > .nested');
                     // if (nested) nested.classList.add('active');
                 });
                return;
            }

            // --- Search Logic ---
            // Hide all items initially
            allListItems.forEach(li => {
                li.style.display = 'none';
            });

            let matchFound = false;
            // Find and show matching items and their ancestors
            allSpans.forEach(span => {
                const itemId = span.dataset.itemId;
                const itemType = span.dataset.itemType;
                let itemData;

                // Get data from the central store
                if (itemType === 'class' && ontologyData.classDetails[itemId]) {
                    itemData = ontologyData.classDetails[itemId];
                } else if (itemType === 'individual' && ontologyData.individualDetails[itemId]) {
                    itemData = ontologyData.individualDetails[itemId];
                }

                if (itemData) {
                    const label = (itemData.label || '').toLowerCase();
                    const name = (itemData.name || '').toLowerCase(); // Local name/ID
                    const desc = (itemData.description || '').toLowerCase();

                    if (label.includes(searchTerm) || name.includes(searchTerm) || desc.includes(searchTerm)) {
                        matchFound = true;
                        // Show this item and its path to the root
                        let currentLi = span.closest('li');
                        while (currentLi && currentLi.matches('ul.tree li')) { // Ensure we stay within the tree
                            currentLi.style.display = ''; // Show the list item

                            // Expand parent lists and turn down carets
                            const parentUl = currentLi.parentElement;
                            if (parentUl && parentUl.classList.contains('nested')) {
                                parentUl.classList.add('active'); // Expand the list
                                const parentLi = parentUl.closest('li');
                                if (parentLi) {
                                    const parentCaret = parentLi.querySelector(':scope > .caret');
                                    if (parentCaret) {
                                        parentCaret.classList.add('caret-down'); // Show caret as expanded
                                    }
                                }
                            }
                            // Move up to the parent list item
                            // Check if parentElement is the main tree UL before getting closest('li') again
                            if (parentUl && parentUl.classList.contains('tree')) {
                                break; // Stop if we reached the top-level ul.tree
                            }
                            currentLi = parentUl ? parentUl.closest('li') : null;
                        }
                    }
                }
            });

            // Optional: Show a message if no results found
            // You might need a dedicated element for this message
            const noResultsMsg = hierarchyContainer.querySelector('.no-search-results');
            if (!matchFound && searchTerm.length >= 2) {
                if (!noResultsMsg) {
                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'info-box no-search-results';
                    msgDiv.textContent = 'No matching classes or individuals found.';
                    hierarchyContainer.appendChild(msgDiv);
                } else {
                    noResultsMsg.style.display = 'block';
                }
            } else if (noResultsMsg) {
                noResultsMsg.style.display = 'none';
            }

        }, 300)); // 300ms debounce
    }

    // Utility function for debouncing
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
    fetchOntologyData();

}); // End DOMContentLoaded 