document.addEventListener('DOMContentLoaded', async () => {
    // Containers
    const plotContainer = document.getElementById('plot-container');
    const detailsContainer = document.getElementById('details-container');
    const detailsChartContainer = document.getElementById('details-chart-container'); // New chart container
    const searchInput = document.getElementById('search');

    // Global state
    let uriRegistry = {};
    let plotData = { // Structure for Plotly sunburst
        ids: [],
        labels: [],
        parents: [],
        // values: [], // Optional: Can be used for sizing segments
        meta: {}, // Store original URI and type { uri: '...', type: 'class'/'individual' }
        loadedChildren: new Set() // Track which class nodes have had children loaded
    };
    let loadError = null;

    // --- Fetch Initial Plot Data ---
    async function fetchInitialPlotData() {
        plotContainer.innerHTML = '<div class="loader"></div>'; // Show loader
        detailsContainer.innerHTML = '<div class="info-box">Loading ontology structure...</div>';
        detailsChartContainer.innerHTML = ''; // Clear charts

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

            // --- Prepare Initial Plotly Data ---
            plotData = { ids: [], labels: [], parents: [], meta: {}, loadedChildren: new Set() }; // Reset
            const rootId = 'ontology_root'; // Artificial root for Plotly
            plotData.ids.push(rootId);
            plotData.labels.push('Ontology Root');
            plotData.parents.push(''); // Root has no parent
            plotData.meta[rootId] = { uri: null, type: 'root' };

            if (data.topClasses.length === 0) {
                 plotContainer.innerHTML = '<div class="info-box">No top-level classes found.</div>';
                 detailsContainer.innerHTML = '<div class="info-box">Select an item to view details.</div>';
                 return;
            }

            data.topClasses.forEach(classInfo => {
                const nodeId = `class_${getLocalName(classInfo.id)}`; // Create a unique ID for Plotly
                plotData.ids.push(nodeId);
                plotData.labels.push(classInfo.label);
                plotData.parents.push(rootId); // Parent is the artificial root
                plotData.meta[nodeId] = {
                    uri: classInfo.id,
                    type: 'class',
                    hasSubClasses: classInfo.hasSubClasses,
                    hasInstances: classInfo.hasInstances
                };
                // Mark if children need loading (only if it has potential children)
                if (classInfo.hasSubClasses || classInfo.hasInstances) {
                    // Don't add to loadedChildren yet
                } else {
                    plotData.loadedChildren.add(classInfo.id); // Mark as having no children to load
                }
            });

            renderSunburstPlot();
            setupSearch(); // Search will be very basic now
            detailsContainer.innerHTML = '<div class="info-box">Click on a segment in the explorer to view details.</div>';

        } catch (error) {
            console.error("Error loading initial plot data:", error);
            loadError = error.message;
            plotContainer.innerHTML = `<div class="error-box"><p>Could not load ontology structure:</p><p>${error.message}</p></div>`;
            detailsContainer.innerHTML = '';
            detailsChartContainer.innerHTML = '';
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

    function renderSunburstPlot() {
        plotContainer.innerHTML = ''; // Clear loader/error

        if (plotData.ids.length <= 1) { // Only root node
             plotContainer.innerHTML = '<div class="info-box">No data to display in plot.</div>';
             return;
        }

        const trace = {
            type: "sunburst",
            ids: plotData.ids,
            labels: plotData.labels,
            parents: plotData.parents,
            // values: plotData.values, // Optional: size segments
            // branchvalues: "total", // How to calculate size if values are used
            outsidetextfont: { size: 16, color: "#377eb8" },
            leaf: { opacity: 0.6 },
            marker: { line: { width: 1.5 } },
            // textinfo: 'label', // Show labels on segments
            hoverinfo: 'label', // Show label on hover
            maxdepth: 3 // Initially show only top levels + 1? Adjust as needed
        };

        const layout = {
            margin: { l: 10, r: 10, b: 10, t: 10 }, // Reduced margins
            sunburstcolorway: ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52"], // Color scheme
            // height: plotContainer.clientHeight, // Try to fit container height
            // width: plotContainer.clientWidth
        };

        Plotly.newPlot(plotContainer, [trace], layout, { responsive: true });

        // --- Add Click Handler ---
        plotContainer.on('plotly_click', handlePlotClick);
    }

    function updateSunburstPlot(newIds, newLabels, newParents, newMeta) {
         // Append new data
         plotData.ids.push(...newIds);
         plotData.labels.push(...newLabels);
         plotData.parents.push(...newParents);
         Object.assign(plotData.meta, newMeta); // Merge new meta info

         // Use Plotly.react for efficient updates
         const traceUpdate = {
             ids: [plotData.ids],
             labels: [plotData.labels],
             parents: [plotData.parents]
             // values: [plotData.values] // Update values if used
         };

         Plotly.react(plotContainer, traceUpdate); // React redraws efficiently
         console.log("Plot updated with new data.");
    }


    // --- Event Handlers ---

    async function handlePlotClick(data) {
        if (!data.points || data.points.length === 0) return;

        const clickedPoint = data.points[0];
        const nodeId = clickedPoint.id; // The unique ID we created (e.g., 'class_MyClassName')
        const nodeMeta = plotData.meta[nodeId];

        if (!nodeMeta || !nodeMeta.uri) {
            console.log("Clicked on root or unknown node:", nodeId);
            // Optionally zoom out or reset view if root is clicked
            // Plotly.restyle(plotContainer, { 'marker.root.color': 'red' }); // Example interaction
            return;
        }

        const itemUri = nodeMeta.uri;
        const itemType = nodeMeta.type;

        console.log(`Clicked plot segment: ID=${nodeId}, Type=${itemType}, URI=${itemUri}`);

        // Show details for the clicked item
        if (itemType === 'class') {
            showClassDetails(itemUri); // Show details pane content
            // Check if children need to be loaded for the plot
            if (!plotData.loadedChildren.has(itemUri) && (nodeMeta.hasSubClasses || nodeMeta.hasInstances)) {
                await loadChildrenForPlot(itemUri, nodeId); // Fetch and update plot
            } else {
                 console.log("Children already loaded or node has no children.");
                 // Optional: Zoom into the clicked segment using Plotly layout updates
                 // Plotly.relayout(plotContainer, {'sunburst.level': nodeId}); // Example, might need adjustment
            }
        } else if (itemType === 'individual') {
            showIndividualDetails(itemUri);
        }
    }

    async function loadChildrenForPlot(parentUri, parentNodeId) {
        console.log(`Fetching children for plot node: ${parentNodeId} (URI: ${parentUri})`);
        // Optional: Add a visual loading indicator near the clicked segment? (Complex)

        try {
            const encodedUri = encodeURIComponent(parentUri);
            const response = await fetch(`/api/children/${encodedUri}`);
            if (!response.ok) throw new Error(`Error fetching children: ${response.statusText}`);
            const childrenData = await response.json();

            const newIds = [];
            const newLabels = [];
            const newParents = [];
            const newMeta = {};

            // Add subclasses
            if (childrenData.subClasses && childrenData.subClasses.length > 0) {
                childrenData.subClasses.forEach(subClassInfo => {
                    const childNodeId = `class_${getLocalName(subClassInfo.id)}`;
                    if (!plotData.ids.includes(childNodeId)) { // Avoid duplicates if loaded via another path
                        newIds.push(childNodeId);
                        newLabels.push(subClassInfo.label);
                        newParents.push(parentNodeId); // Link to the clicked plot node ID
                        newMeta[childNodeId] = {
                            uri: subClassInfo.id,
                            type: 'class',
                            hasSubClasses: subClassInfo.hasSubClasses,
                            hasInstances: subClassInfo.hasInstances
                        };
                        // Mark if children need loading
                        if (!subClassInfo.hasSubClasses && !subClassInfo.hasInstances) {
                            plotData.loadedChildren.add(subClassInfo.id);
                        }
                    }
                });
            }

            // Add instances
            if (childrenData.instances && childrenData.instances.length > 0) {
                childrenData.instances.forEach(instanceInfo => {
                    const childNodeId = `indiv_${getLocalName(instanceInfo.id)}`;
                     if (!plotData.ids.includes(childNodeId)) {
                        newIds.push(childNodeId);
                        newLabels.push(instanceInfo.label);
                        newParents.push(parentNodeId);
                        newMeta[childNodeId] = {
                            uri: instanceInfo.id,
                            type: 'individual'
                            // Individuals don't have further children in this hierarchy model
                        };
                        plotData.loadedChildren.add(instanceInfo.id); // Mark instances as leaves
                    }
                });
            }

            if (newIds.length > 0) {
                updateSunburstPlot(newIds, newLabels, newParents, newMeta);
            } else {
                 console.log("No new children found to add to the plot.");
            }

            // Mark the parent as loaded regardless of whether children were found
            plotData.loadedChildren.add(parentUri);

        } catch (error) {
            console.error(`Error loading children for plot node ${parentNodeId}:`, error);
            // Optional: Display error message to user (e.g., in details pane)
        }
    }


    // --- Detail Display Functions ---

    function clearSelection() {
        // Plotly doesn't have a direct 'selected' state like the tree.
        // We might visually indicate selection by changing color or outline in the plot,
        // or simply rely on the details pane showing the current selection.
        // For now, just clear the details pane styling if any was applied.
        detailsContainer.classList.remove('selected-item-details'); // Example class
    }

    function highlightSelectedItem(itemId) {
        clearSelection();
        // Instead of highlighting in a tree, we now rely on the details pane
        // being updated. Optionally, interact with the plot here.
        console.log("Highlight requested for:", itemId);
        detailsContainer.classList.add('selected-item-details'); // Example

        // --- Optional: Plot Interaction ---
        // Find the corresponding plot node ID
        const targetNodeId = Object.keys(plotData.meta).find(nodeId => plotData.meta[nodeId].uri === itemId);
        if (targetNodeId) {
            console.log("Found corresponding plot node:", targetNodeId);
            // Example: Slightly change marker line color (subtle)
            // This requires more complex Plotly update logic, potentially storing original styles.
            // Plotly.restyle(plotContainer, { 'marker.line.color': ['red'] }, [plotData.ids.indexOf(targetNodeId)]);
            // Or zoom/focus on the node (might be disruptive)
            // Plotly.relayout(plotContainer, {'sunburst.level': targetNodeId});
        }
    }

    function renderUriLink(uri) {
        const info = uriRegistry ? uriRegistry[uri] : null;
        const label = info ? info.label : getLocalName(uri);
        const title = uri;

        if (info && (info.type === 'class' || info.type === 'individual')) {
            // Find the plot node ID corresponding to this URI
            const targetNodeId = Object.keys(plotData.meta).find(nodeId => plotData.meta[nodeId].uri === uri);
            if (targetNodeId) {
                // If found, make it clickable to trigger plot interaction + details view
                // We need a global function or event listener to handle these clicks
                // For simplicity, let's just call the detail function directly for now
                 return `<a href="#" title="${title}" onclick="event.preventDefault(); handleInternalLinkClick('${uri}', '${info.type}');">${label}</a>`;
            } else {
                 // If not found in the *current* plot data (might not be loaded yet)
                 // provide a link that just tries to load details directly.
                 return `<a href="#" title="${title}" onclick="event.preventDefault(); handleInternalLinkClick('${uri}', '${info.type}');">${label} (details only)</a>`;
            }
        } else if (info && info.type === 'property') {
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        } else {
            return `<a href="${uri}" target="_blank" title="${title}">${label}</a>`;
        }
    }

    // Make detail loading functions globally accessible for internal links
    window.handleInternalLinkClick = (uri, type) => {
        console.log("Internal link clicked:", uri, type);
        if (type === 'class') {
            showClassDetails(uri);
            // Try to find and potentially expand plot - more complex
        } else if (type === 'individual') {
            showIndividualDetails(uri);
        }
    };


    async function showClassDetails(classId) {
        highlightSelectedItem(classId);
        detailsContainer.innerHTML = '<div class="loader"></div>'; // Show loader
        detailsChartContainer.innerHTML = ''; // Clear previous charts

        try {
            const encodedUri = encodeURIComponent(classId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'class' || !result.details) {
                 throw new Error("Invalid details data received.");
            }
            const classObj = result.details;

            // --- Render Text Details ---
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

            // --- Render Bar Chart ---
            renderDetailsBarChart(classObj.subClasses.length, classObj.instances.length);

        } catch (error) {
             console.error(`Error fetching details for ${classId}:`, error);
             detailsContainer.innerHTML = `<div class="error-box">Could not load details for ${getLocalName(classId)}. ${error.message}</div>`;
             detailsChartContainer.innerHTML = ''; // Clear chart on error
        }
    }

    async function showIndividualDetails(individualId) {
        highlightSelectedItem(individualId);
        detailsContainer.innerHTML = '<div class="loader"></div>'; // Show loader
        detailsChartContainer.innerHTML = ''; // Clear charts for individuals

         try {
            const encodedUri = encodeURIComponent(individualId);
            const response = await fetch(`/api/details/${encodedUri}`);
            if (!response.ok) throw new Error(`Details fetch failed: ${response.statusText}`);
            const result = await response.json();

            if (result.type !== 'individual' || !result.details) {
                 throw new Error("Invalid details data received.");
            }
            const indObj = result.details;

            // --- Render Text Details ---
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
             detailsChartContainer.innerHTML = ''; // Clear chart on error
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

    // --- New Function: Render Details Bar Chart ---
    function renderDetailsBarChart(subClassCount, instanceCount) {
        detailsChartContainer.innerHTML = ''; // Clear previous

        if (subClassCount === 0 && instanceCount === 0) {
            detailsChartContainer.innerHTML = '<div class="info-box-small">No direct subclasses or instances.</div>';
            return;
        }

        const trace = {
            x: ['Direct Subclasses', 'Direct Instances'],
            y: [subClassCount, instanceCount],
            type: 'bar',
            marker: {
                color: [var(--secondary-color), '#FFA15A'] // Colors for bars
            },
            text: [subClassCount, instanceCount], // Show values on bars
            textposition: 'auto',
            hoverinfo: 'x+y'
        };

        const layout = {
            title: 'Class Contents',
            font: { size: 12 },
            yaxis: { title: 'Count', automargin: true, zeroline: true, showgrid: true },
            xaxis: { automargin: true },
            margin: { l: 40, r: 20, b: 30, t: 40 }, // Adjust margins
            height: 200 // Fixed height for the bar chart
        };

        Plotly.newPlot(detailsChartContainer, [trace], layout, { responsive: true, displayModeBar: false }); // Hide Plotly mode bar
    }


    // --- Search Functionality (Basic) ---
    function setupSearch() {
        // NOTE: Client-side search on the plot is difficult.
        // Highlighting segments based on search requires complex Plotly updates.
        // A server-side search endpoint would be better for large ontologies.
        searchInput.addEventListener('input', debounce(function() {
            const searchTerm = this.value.toLowerCase().trim();

            // Basic idea: Filter plotData and re-render (can be slow)
            // Or: Use Plotly transforms (more advanced)
            // Or: Just log a message for now.
            if (searchTerm.length < 2) {
                // Maybe reset plot view or remove highlights if implemented
                console.log("Search cleared or too short.");
                // Potentially re-render the original plot if it was filtered
                // renderSunburstPlot(); // Re-render might reset loaded children state
                return;
            }

            console.warn("Client-side plot search not fully implemented. Searching labels only for logging.");

            // Find matching nodes (just logging)
            const matchingNodes = plotData.ids.filter(id => {
                const label = plotData.labels[plotData.ids.indexOf(id)]?.toLowerCase();
                const meta = plotData.meta[id];
                const name = meta?.uri ? getLocalName(meta.uri).toLowerCase() : '';
                return (label && label.includes(searchTerm)) || (name && name.includes(searchTerm));
            });

            console.log("Potential matches (not highlighted):", matchingNodes.map(id => plotData.meta[id]));

            // TODO: Implement actual plot highlighting or filtering if needed.

        }, 500)); // Increased debounce time
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
    fetchInitialPlotData(); // Start by fetching data for the plot

    // Add access to variables/functions for Plotly `onclick` if needed
    // window.plotData = plotData; // Expose if necessary (use with caution)
    // window.showClassDetails = showClassDetails;
    // window.showIndividualDetails = showIndividualDetails;

}); // End DOMContentLoaded 