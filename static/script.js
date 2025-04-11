document.addEventListener('DOMContentLoaded', async () => {
    // DOM Elements
    const treeContainer = document.getElementById('tree-container');
    const detailsContainer = document.getElementById('details-container');
    const detailsChartContainer = document.getElementById('details-chart-container');
    const searchInput = document.getElementById('search');

    // Global state
    let uriRegistry = {};
    let selectedNodeId = null;
    let selectedNodeType = null;
    let loadError = null;

    // --- Helper: Update Global URI Registry ---
    function updateUriRegistry(registryUpdate) {
        if (registryUpdate && typeof registryUpdate === 'object') {
            uriRegistry = { ...uriRegistry, ...registryUpdate };
            // console.log("URI Registry updated:", Object.keys(uriRegistry).length, "entries");
        }
    }

    // --- Fetch Initial Data ---
    async function fetchInitialData() {
        treeContainer.innerHTML = '<div class="loader"></div>';
        detailsContainer.innerHTML = '<div class="info-box">Loading ontology structure...</div>';
        detailsChartContainer.innerHTML = '';

        try {
            const response = await fetch('/api/hierarchy');
            if (!response.ok) {
                let errorMsg = `Error fetching hierarchy (${response.status}): ${response.statusText}`;
                try {
                    // Try to get more specific error from JSON response body
                    const errorData = await response.json();
                    if (errorData && errorData.error) {
                        errorMsg = `Error fetching hierarchy: ${errorData.error}`;
                    }
                } catch (jsonError) {
                    // Ignore if response is not JSON or empty
                    console.warn("Could not parse error response as JSON:", jsonError);
                }
                throw new Error(errorMsg); // Throw the potentially more specific error
            }
            const data = await response.json();

            // Check for error property even in successful responses ( belt-and-suspenders )
            if (data.error) {
                throw new Error(`Server error: ${data.error}`);
            }

            console.log("Received Initial Hierarchy:", data);

            // Check if data seems valid
            if (!data.topClasses) {
                throw new Error("Received incomplete or invalid hierarchy data structure (missing topClasses).");
            }

            // Update registry with initial data (might just be top-level items)
            updateUriRegistry(data.uriRegistry);

            // Render the top-level tree
            renderTreeRoot(data.topClasses);
            setupSearch();
            detailsContainer.innerHTML = '<div class="info-box">Click on a class or individual to view details.</div>';

        } catch (error) {
            console.error("Error loading initial data:", error);
            loadError = error.message;
            // Display the potentially more detailed error message
            treeContainer.innerHTML = `<div class="error-box"><p>Could not load ontology structure:</p><p>${error.message}</p></div>`;
            detailsContainer.innerHTML = '';
            detailsChartContainer.innerHTML = '';
        }
    }

    // --- Tree Rendering ---
    function renderTreeRoot(topClasses) {
        treeContainer.innerHTML = '';
        
        if (topClasses.length === 0) {
            treeContainer.innerHTML = '<div class="info-box">No top-level classes found.</div>';
            return;
        }

        const rootUl = document.createElement('ul');
        rootUl.className = 'tree';
        
        // Sort top classes by label
        topClasses.sort((a, b) => a.label.localeCompare(b.label));
        
        topClasses.forEach(classInfo => {
            const li = createClassNode(classInfo);
            rootUl.appendChild(li);
        });
        
        treeContainer.appendChild(rootUl);
    }

    function createClassNode(classInfo) {
        const li = document.createElement('li');
        
        const itemDiv = document.createElement('div');
        itemDiv.className = 'tree-item';
        itemDiv.dataset.uri = classInfo.id;
        itemDiv.dataset.type = 'class';
        
        // Toggle button for expandable nodes
        const toggleSpan = document.createElement('span');
        toggleSpan.className = 'tree-toggle';
        
        if (classInfo.hasSubClasses || classInfo.hasInstances) {
            toggleSpan.textContent = '+';
            toggleSpan.addEventListener('click', handleToggleClick);
        } else {
            toggleSpan.textContent = ' ';
        }
        
        // Class icon
        const iconSpan = document.createElement('span');
        iconSpan.className = 'tree-icon class-icon';
        
        // Label
        const labelSpan = document.createElement('span');
        labelSpan.className = 'tree-label';
        labelSpan.textContent = classInfo.label;
        
        // Assemble the item
        itemDiv.appendChild(toggleSpan);
        itemDiv.appendChild(iconSpan);
        itemDiv.appendChild(labelSpan);
        
        // Add click handler to the whole item (except toggle)
        labelSpan.addEventListener('click', () => {
            selectNode(classInfo.id, 'class');
        });
        
        li.appendChild(itemDiv);
        
        // Create placeholder for children that will be loaded on expansion
        const childrenUl = document.createElement('ul');
        childrenUl.style.display = 'none';
        li.appendChild(childrenUl);
        
        return li;
    }

    function createIndividualNode(indInfo) {
        const li = document.createElement('li');
        
        const itemDiv = document.createElement('div');
        itemDiv.className = 'tree-item';
        itemDiv.dataset.uri = indInfo.id;
        itemDiv.dataset.type = 'individual';
        
        // Spacer instead of toggle (individuals don't expand)
        const spacerSpan = document.createElement('span');
        spacerSpan.className = 'tree-toggle';
        spacerSpan.textContent = ' ';
        
        // Individual icon
        const iconSpan = document.createElement('span');
        iconSpan.className = 'tree-icon individual-icon';
        
        // Label
        const labelSpan = document.createElement('span');
        labelSpan.className = 'tree-label';
        labelSpan.textContent = indInfo.label;
        
        // Assemble the item
        itemDiv.appendChild(spacerSpan);
        itemDiv.appendChild(iconSpan);
        itemDiv.appendChild(labelSpan);
        
        // Add click handler
        itemDiv.addEventListener('click', () => {
            selectNode(indInfo.id, 'individual');
        });
        
        li.appendChild(itemDiv);
        return li;
    }

    // --- Event Handlers ---
    async function handleToggleClick(event) {
        event.stopPropagation();
        
        const toggleElement = event.target;
        const itemElement = toggleElement.parentElement;
        const listElement = itemElement.parentElement;
        const childrenUl = listElement.querySelector('ul');
        
        if (toggleElement.textContent === '+') {
            toggleElement.textContent = '-';
            
            // Check if children need to be loaded
            if (childrenUl.children.length === 0) {
                childrenUl.innerHTML = '<li><div class="loader" style="width:20px;height:20px;margin:10px;"></div></li>';
                
                try {
                    await loadChildren(itemElement.dataset.uri, childrenUl);
                } catch (error) {
                    childrenUl.innerHTML = `<li><div class="error-box">Error loading children: ${error.message}</div></li>`;
                }
            }
            
            childrenUl.style.display = 'block';
        } else {
            toggleElement.textContent = '+';
            childrenUl.style.display = 'none';
        }
    }

    async function loadChildren(classUri, parentUl) {
        const encodedUri = encodeURIComponent(classUri);
        const response = await fetch(`/api/children/${encodedUri}`);
        
        if (!response.ok) {
            // Try to get error message from JSON body
            let errorMsg = `Error ${response.status}: ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData && errorData.error) errorMsg = errorData.error;
            } catch(e) {}
            throw new Error(errorMsg);
        }
        
        const data = await response.json();
        parentUl.innerHTML = ''; // Clear the loading indicator
        
        // Update global registry with children info
        updateUriRegistry(data.uriRegistryUpdate);

        // Process subclasses
        if (data.subClasses && data.subClasses.length > 0) {
            // Sort by label
            data.subClasses.sort((a, b) => a.label.localeCompare(b.label));
            
            data.subClasses.forEach(subClass => {
                const li = createClassNode(subClass);
                parentUl.appendChild(li);
            });
        }
        
        // Process instances
        if (data.instances && data.instances.length > 0) {
            // Sort by label
            data.instances.sort((a, b) => a.label.localeCompare(b.label));
            
            data.instances.forEach(instance => {
                const li = createIndividualNode(instance);
                parentUl.appendChild(li);
            });
        }
        
        if (parentUl.children.length === 0) {
            parentUl.innerHTML = '<li><div class="info-box" style="margin:5px;">No children found.</div></li>';
        }
    }

    function selectNode(uri, type) {
        // Deselect previously selected node
        const previouslySelected = document.querySelector('.tree-item.selected');
        if (previouslySelected) {
            previouslySelected.classList.remove('selected');
        }
        
        // Find and select the current node
        const selector = `.tree-item[data-uri="${CSS.escape(uri)}"]`;
        const currentNode = document.querySelector(selector);
        if (currentNode) {
            currentNode.classList.add('selected');
        }
        
        // Update state
        selectedNodeId = uri;
        selectedNodeType = type;
        
        // Show details
        if (type === 'class') {
            showClassDetails(uri);
        } else if (type === 'individual') {
            showIndividualDetails(uri);
        }
    }

    // --- Helper Functions ---
    function getLocalName(uri) {
        // Use registry first if available and has a label
        if (uriRegistry && uriRegistry[uri] && uriRegistry[uri].label) {
            return uriRegistry[uri].label;
        }

        // Fallback parsing: handle # and /
        if (!uri) return '';
        try {
            let localName = uri;
            if (uri.includes('#')) {
                localName = uri.substring(uri.lastIndexOf('#') + 1);
            } else if (uri.includes('/')) {
                localName = uri.substring(uri.lastIndexOf('/') + 1);
            }
            // Handle potential empty string after split if URI ends with # or /
            return localName || uri;
        } catch (e) {
            console.warn("Error parsing local name for URI:", uri, e);
            return uri; // Failsafe
        }
    }

    function renderUriLink(uri) {
        // Use getLocalName which prioritizes registry label
        const label = getLocalName(uri);
        const title = uri; // Show full URI on hover

        // Check registry for type to decide link behavior
        const info = uriRegistry ? uriRegistry[uri] : null;

        let linkType = 'external'; // Default to external
        if (info) {
            if (info.type === 'class' || info.type === 'individual') {
                linkType = 'internal';
            } else if (info.type === 'property') {
                linkType = 'external'; // Or 'none' if properties shouldn't be links
            }
        } else {
            // Guess type based on common patterns if not in registry (less reliable)
            // This is optional, could just default all unknown to external
            // if (uri.toLowerCase().includes('class')) linkType = 'internal'; // Very rough guess
        }

        if (linkType === 'internal') {
            const escapedUri = uri.replace(/'/g, "\\'");
            // Use the type from the registry if available, otherwise guess based on context (less ideal)
            const nodeType = info ? info.type : (label === label.toUpperCase() ? 'individual' : 'class'); // Very rough guess if no info
            return `<a href="#" title="${title}" onclick="event.preventDefault(); window.handleInternalLinkClick('${escapedUri}', '${nodeType}');">${label}</a>`;
        } else if (linkType === 'external') {
             return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        } else { // 'none' or other cases
             return `<span title="${title}">${label}</span>`;
        }
    }

    function renderPropertyValue(val) {
        if (!val) return ''; // Handle null/undefined values

        if (val.type === 'uri') {
            return renderUriLink(val.value);
        } else { // Literal
            // Basic escaping for HTML display
            const escapedValue = val.value
                                    .replace(/&/g, '&amp;')
                                    .replace(/</g, '&lt;')
                                    .replace(/>/g, '&gt;')
                                    .replace(/"/g, '&quot;')
                                    .replace(/'/g, '&#039;');
            let literalHtml = escapedValue;
            if (val.datatype) {
                // Try to render the datatype URI as a link or label
                const dtLabel = getLocalName(val.datatype); // Get label/local name for datatype
                const dtLink = `<a href="${val.datatype}" target="_blank" title="${val.datatype}">${dtLabel}</a>`;
                // Alternative: just show label: const dtLink = `<span title="${val.datatype}">${dtLabel}</span>`;
                literalHtml += ` <small>(type: ${dtLink})</small>`;
            }
            return literalHtml;
        }
    }

    // --- Detail Display Functions ---
    async function showClassDetails(classId) {
        detailsContainer.innerHTML = '<div class="loader"></div>';
        detailsChartContainer.innerHTML = '';

        try {
            const encodedUri = encodeURIComponent(classId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'class' || !result.details) {
                throw new Error("Invalid details data received.");
            }
            
            const classObj = result.details;

            // Update registry with related items from details
            updateUriRegistry(result.uriRegistryUpdate);

            // Render Text Details
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
                    <h4>Parent Classes (${classObj.superClasses.length})</h4>
                    <ul>`;
                // Sort by label using getLocalName which checks registry
                const sortedSuperClasses = classObj.superClasses
                    .sort((a, b) => getLocalName(a).localeCompare(getLocalName(b)));
                sortedSuperClasses.forEach(superUri => {
                    html += `<li>${renderUriLink(superUri)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Sub classes
            if (classObj.subClasses && classObj.subClasses.length > 0) {
                html += `<div class="property-group">
                    <h4>Subclasses (${classObj.subClasses.length})</h4>
                    <ul>`;
                // Sort by label
                const sortedSubClasses = classObj.subClasses
                    .sort((a, b) => getLocalName(a).localeCompare(getLocalName(b)));
                sortedSubClasses.forEach(subUri => {
                    html += `<li>${renderUriLink(subUri)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Instances
            if (classObj.instances && classObj.instances.length > 0) {
                html += `<div class="property-group">
                    <h4>Instances (${classObj.instances.length})</h4>
                    <ul>`;
                 // Sort by label
                const sortedInstances = classObj.instances
                    .sort((a, b) => getLocalName(a).localeCompare(getLocalName(b)));
                sortedInstances.forEach(indUri => {
                    html += `<li>${renderUriLink(indUri)}</li>`;
                });
                html += `</ul></div>`;
            }

            detailsContainer.innerHTML = html;

            // Render Bar Chart
            renderDetailsBarChart(classObj.subClasses.length, classObj.instances.length);

        } catch (error) {
            console.error(`Error fetching details for ${classId}:`, error);
            detailsContainer.innerHTML = `<div class="error-box">Could not load details for ${getLocalName(classId)}. ${error.message}</div>`;
            detailsChartContainer.innerHTML = '';
        }
    }

    async function showIndividualDetails(individualId) {
        detailsContainer.innerHTML = '<div class="loader"></div>';
        detailsChartContainer.innerHTML = ''; // Clear chart for individuals

        try {
            const encodedUri = encodeURIComponent(individualId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) {
                 let errorMsg = `Details fetch failed (${response.status}): ${response.statusText}`;
                 try {
                     const errorData = await response.json();
                     if (errorData && errorData.error) errorMsg = errorData.error;
                     else if (errorData && errorData.description) errorMsg = errorData.description; // Handle 404 description
                 } catch(e) {}
                 throw new Error(errorMsg);
            }
            const result = await response.json();

            // Update registry with related items from details
            updateUriRegistry(result.uriRegistryUpdate);

            if (result.type !== 'individual' || !result.details) {
                // Handle cases where the URI is found but not an individual (e.g., a property, class)
                if (result.details && (result.details.message || result.type === 'class' || result.type === 'property')) {
                    // Show basic info for non-individuals
                    let nonIndHtml = `<div class="info-box">Details for non-individual URI (${result.type || 'other'})</div>
                                      <p><strong>URI:</strong> ${renderUriLink(result.details.id)}</p>
                                      <p><strong>Label:</strong> ${result.details.label}</p>`;
                     if (result.details.description) nonIndHtml += `<p><strong>Description:</strong> ${result.details.description}</p>`;
                     // Add specific fields if property/class if needed (e.g., domain/range for property)
                     if (result.type === 'property' && result.details.domains) { /* ... */ }
                     if (result.type === 'class' && result.details.superClasses) { /* ... */ }

                    detailsContainer.innerHTML = nonIndHtml;
                    return;
                }
                throw new Error("Invalid details data received for individual.");
            }

            const indObj = result.details;

            // Render Text Details
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
                    .sort((a, b) => getLocalName(a).localeCompare(getLocalName(b)));
                sortedTypes.forEach(typeUri => {
                    html += `<li>${renderUriLink(typeUri)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Properties
            const properties = indObj.properties; // This is an object { prop_tag_uri: [values] }
            if (properties && Object.keys(properties).length > 0) {
                html += `<div class="property-group"><h4>Properties</h4>`;

                // Get the property tag URIs (keys) and sort them by label
                const sortedPropTagUris = Object.keys(properties).sort((a, b) => {
                    const labelA = getLocalName(a); // Use getLocalName for the property URI itself
                    const labelB = getLocalName(b);
                    return labelA.localeCompare(labelB);
                });

                sortedPropTagUris.forEach(propTagUri => {
                    const values = properties[propTagUri]; // List of value objects
                    // Render the property name using its URI (will show label via renderUriLink)
                    html += `<div class="property-assertion">
                                <strong class="property-name">${renderUriLink(propTagUri)}:</strong>`;
                    if (values.length > 1) {
                        html += `<ul class="property-value-list">`;
                        // Sort multiple values? Maybe not necessary unless they have inherent order.
                        values.forEach(val => html += `<li>${renderPropertyValue(val)}</li>`);
                        html += `</ul>`;
                    } else if (values.length === 1) {
                        // Display single value directly
                        html += `<span class="property-value">${renderPropertyValue(values[0])}</span>`;
                    } else {
                        html += `<span class="property-value"><em>(No value specified)</em></span>`; // Should not happen based on backend logic
                    }
                    html += `</div>`; // Close property-assertion
                });
                html += `</div>`; // Close property-group
            } else {
                html += `<div class="info-box">No specific properties asserted for this individual.</div>`;
            }

            detailsContainer.innerHTML = html;

        } catch (error) {
            console.error(`Error fetching details for ${individualId}:`, error);
            detailsContainer.innerHTML = `<div class="error-box">Could not load details for ${getLocalName(individualId)}. ${error.message}</div>`;
        }
    }

    // Render bar chart showing subclass and instance counts
    function renderDetailsBarChart(subClassCount, instanceCount) {
        if (subClassCount === 0 && instanceCount === 0) {
            detailsChartContainer.innerHTML = '<div class="info-box">No direct subclasses or instances.</div>';
            return;
        }

        const chartContainer = detailsChartContainer;
        chartContainer.innerHTML = '';
        
        // Create simple bar chart with CSS
        const chartHtml = `
            <div class="chart-title">Class Contents</div>
            <div class="simple-bar-chart">
                <div class="chart-row">
                    <div class="chart-label">Direct Subclasses</div>
                    <div class="chart-bar subclass-bar" style="width: ${Math.min(100, subClassCount * 5)}%">
                        <span class="chart-value">${subClassCount}</span>
                    </div>
                </div>
                <div class="chart-row">
                    <div class="chart-label">Direct Instances</div>
                    <div class="chart-bar instance-bar" style="width: ${Math.min(100, instanceCount * 5)}%">
                        <span class="chart-value">${instanceCount}</span>
                    </div>
                </div>
            </div>
        `;
        
        chartContainer.innerHTML = chartHtml;

        // Add chart styles
        const style = document.createElement('style');
        style.textContent = `
            .chart-title {
                font-weight: bold;
                margin-bottom: 10px;
                color: var(--primary-color);
            }
            .simple-bar-chart {
                font-family: sans-serif;
                font-size: 14px;
            }
            .chart-row {
                display: flex;
                align-items: center;
                margin-bottom: 10px;
            }
            .chart-label {
                width: 150px;
                flex-shrink: 0;
            }
            .chart-bar {
                height: 24px;
                background-color: var(--secondary-color);
                border-radius: 4px;
                display: flex;
                align-items: center;
                justify-content: flex-end;
                padding-right: 10px;
                color: white;
                font-weight: bold;
                transition: width 0.3s ease;
                min-width: 40px;
            }
            .subclass-bar {
                background-color: var(--secondary-color);
            }
            .instance-bar {
                background-color: #FFA15A;
            }
            .chart-value {
                white-space: nowrap;
            }
        `;
        document.head.appendChild(style);
    }

    // --- Search Functionality ---
    function setupSearch() {
        searchInput.addEventListener('input', debounce(function() {
            const searchTerm = this.value.toLowerCase().trim();
            
            if (searchTerm.length < 2) {
                // Reset search highlights
                const highlightedItems = document.querySelectorAll('.tree-item.highlight');
                highlightedItems.forEach(item => item.classList.remove('highlight'));
                return;
            }
            
            // Simple client-side search through visible tree items
            const treeItems = document.querySelectorAll('.tree-item');
            let matchCount = 0;
            
            treeItems.forEach(item => {
                // Use getLocalName on the item's URI to search using the best available label
                const uri = item.dataset.uri;
                const label = getLocalName(uri).toLowerCase(); // Search using registry label if possible

                if (label.includes(searchTerm)) {
                    item.classList.add('highlight');
                    matchCount++;
                    
                    // Ensure the item is visible by expanding parent nodes
                    let parent = item.parentElement;
                    while (parent && !parent.classList.contains('tree-container')) {
                        if (parent.tagName === 'UL' && parent.style.display === 'none') {
                            parent.style.display = 'block';
                            const parentItem = parent.parentElement.querySelector(':scope > .tree-item');
                            if (parentItem) {
                                const toggle = parentItem.querySelector('.tree-toggle');
                                if (toggle) toggle.textContent = '-';
                            }
                        }
                        parent = parent.parentElement;
                    }
                } else {
                    item.classList.remove('highlight');
                }
            });
            
            console.log(`Found ${matchCount} matches for "${searchTerm}"`);
            
        }, 300));
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

    // Make this function available globally for internal links
    window.handleInternalLinkClick = (uri, type) => {
        console.log("Internal link clicked:", uri, type);

        // Find the node in the currently rendered tree
        const selector = `.tree-item[data-uri="${CSS.escape(uri)}"]`;
        const node = document.querySelector(selector);

        if (node) {
            // Item exists in the tree, ensure it's visible and select it
            // Expand parents if necessary
            let parent = node.parentElement;
            while (parent && !parent.classList.contains('tree-container')) {
                if (parent.tagName === 'UL' && parent.style.display === 'none') {
                    parent.style.display = 'block';
                    const parentItemDiv = parent.parentElement.querySelector(':scope > .tree-item');
                    if (parentItemDiv) {
                        const toggle = parentItemDiv.querySelector('.tree-toggle');
                        if (toggle && toggle.textContent === '+') {
                             toggle.textContent = '-';
                             // Note: This still doesn't lazy-load if parents weren't expanded before.
                             // Clicking the toggle manually is needed if children weren't loaded.
                        }
                    }
                }
                parent = parent.parentElement;
            }

            selectNode(uri, type); // Select the node (fetches details)

            // Scroll to the node after potential expansions
            setTimeout(() => {
                 node.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 100); // Small delay to allow layout changes

        } else {
            // Item doesn't exist in currently expanded tree,
            // just show its details directly without changing tree selection/scroll
            console.log(`Node ${uri} not found in current tree view. Fetching details directly.`);
            // Use the provided type hint, but the details endpoint will confirm the type anyway
            if (type === 'class') {
                showClassDetails(uri);
            } else if (type === 'individual') {
                showIndividualDetails(uri);
            } else {
                 // Fallback for unknown type from link - let details endpoint figure it out
                 // We could try showClassDetails or showIndividualDetails and let them handle
                 // the response if it's not the expected type. Let's try showClassDetails first.
                 console.warn(`Unknown type '${type}' for internal link, attempting class details fetch.`);
                 showClassDetails(uri);
            }
            // Clear tree selection when showing details for an item not in the tree
            const previouslySelected = document.querySelector('.tree-item.selected');
            if (previouslySelected) {
                previouslySelected.classList.remove('selected');
            }
            selectedNodeId = null;
            selectedNodeType = null;
        }
    };

    // Initialize the application
    fetchInitialData();
}); 