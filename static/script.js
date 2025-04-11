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

    // --- Fetch Initial Data ---
    async function fetchInitialData() {
        treeContainer.innerHTML = '<div class="loader"></div>';
        detailsContainer.innerHTML = '<div class="info-box">Loading ontology structure...</div>';
        detailsChartContainer.innerHTML = '';

        try {
            const response = await fetch('/api/hierarchy');
            if (!response.ok) {
                let errorMsg = `Error fetching hierarchy: ${response.statusText}`;
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

            // Render the top-level tree
            renderTreeRoot(data.topClasses);
            setupSearch();
            detailsContainer.innerHTML = '<div class="info-box">Click on a class or individual to view details.</div>';

        } catch (error) {
            console.error("Error loading initial data:", error);
            loadError = error.message;
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
            throw new Error(`Error ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        parentUl.innerHTML = ''; // Clear the loading indicator
        
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
        // Use registry first if available
        if (uriRegistry && uriRegistry[uri]) {
            return uriRegistry[uri].label;
        }
        
        // Fallback parsing
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

    function renderUriLink(uri) {
        const info = uriRegistry ? uriRegistry[uri] : null;
        const label = info ? info.label : getLocalName(uri);
        const title = uri;

        if (info && (info.type === 'class' || info.type === 'individual')) {
            return `<a href="#" title="${title}" onclick="event.preventDefault(); window.handleInternalLinkClick('${uri}', '${info.type}');">${label}</a>`;
        } else if (info && info.type === 'property') {
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        } else {
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        }
    }

    function renderPropertyValue(val) {
        if (val.type === 'uri') {
            return renderUriLink(val.value);
        } else { // Literal
            const escapedValue = val.value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            let literalHtml = escapedValue;
            if (val.datatype) {
                const dtLabel = getLocalName(val.datatype);
                const dtLink = uriRegistry[val.datatype]
                    ? renderUriLink(val.datatype)
                    : `<span title="${val.datatype}">${dtLabel}</span>`;
                literalHtml += ` <small>(${dtLink})</small>`;
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
                    <h4>Parent Classes</h4>
                    <ul>`;
                const sortedSuperClasses = classObj.superClasses
                    .map(id => ({ id: id, label: getLocalName(id) }))
                    .sort((a, b) => a.label.localeCompare(b.label));
                sortedSuperClasses.forEach(superInfo => {
                    html += `<li>${renderUriLink(superInfo.id)}</li>`;
                });
                html += `</ul></div>`;
            }

            // Sub classes
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

            // Instances
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
        detailsChartContainer.innerHTML = '';

        try {
            const encodedUri = encodeURIComponent(individualId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'individual' || !result.details) {
                throw new Error("Invalid details data received.");
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
                const sortedTypes = indObj.types
                    .map(id => ({ id: id, label: getLocalName(id) }))
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

                const sortedPropUris = Object.keys(properties).sort((a, b) => {
                    const labelA = getLocalName(a);
                    const labelB = getLocalName(b);
                    return labelA.localeCompare(labelB);
                });

                sortedPropUris.forEach(propUri => {
                    const values = properties[propUri];
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
                const label = item.querySelector('.tree-label').textContent.toLowerCase();
                if (label.includes(searchTerm)) {
                    item.classList.add('highlight');
                    matchCount++;
                    
                    // Ensure the item is visible by expanding parent nodes
                    let parent = item.parentElement;
                    while (parent && !parent.classList.contains('tree-container')) {
                        if (parent.tagName === 'UL' && parent.style.display === 'none') {
                            parent.style.display = 'block';
                            const parentItem = parent.parentElement.querySelector('.tree-item');
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
        
        // First try to find the node in the tree and expand to it
        const selector = `.tree-item[data-uri="${CSS.escape(uri)}"]`;
        const node = document.querySelector(selector);
        
        if (node) {
            // Item exists in the tree, select it
            selectNode(uri, type);
            
            // Scroll to the node
            node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else {
            // Item doesn't exist in currently expanded tree,
            // just show its details directly
            if (type === 'class') {
                showClassDetails(uri);
            } else if (type === 'individual') {
                showIndividualDetails(uri);
            }
        }
    };

    // Initialize the application
    fetchInitialData();
}); 