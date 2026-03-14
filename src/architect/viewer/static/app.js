/* Architect Graph Viewer */

if (typeof cytoscapeCoseBilkent !== 'undefined') {
  cytoscape.use(cytoscapeCoseBilkent);
}

const TYPE_COLORS = {
  page: '#6c8cff',
  component: '#22d3ee',
  api_endpoint: '#4ade80',
  service: '#a78bfa',
  db_table: '#fb923c',
  db_column: '#facc15',
  middleware: '#f87171',
  utility: '#8b90a0',
  config: '#facc15',
  custom: '#8b90a0',
};

const STATUS_STYLES = {
  planned:      { borderStyle: 'dashed', borderWidth: 2.5, borderColor: '#f59e0b', opacity: 1 },
  in_progress:  { borderStyle: 'solid',  borderWidth: 3,   borderColor: '#3b82f6', opacity: 1 },
  implemented:  { borderStyle: 'solid',  borderWidth: 2,   borderColor: '#22c55e', opacity: 1 },
  needs_review: { borderStyle: 'dotted', borderWidth: 3,   borderColor: '#ef4444', opacity: 1 },
  deprecated:   { borderStyle: 'dotted', borderWidth: 2,   borderColor: '#6b7280', opacity: 0.35 },
};

const EDGE_TYPE_STYLES = {
  dependency:  { lineStyle: 'solid',  lineColor: '#3a4570' },
  data_flow:   { lineStyle: 'dashed', lineColor: '#3b82f6' },
  foreign_key: { lineStyle: 'dotted', lineColor: '#fb923c' },
  renders:     { lineStyle: 'solid',  lineColor: '#22d3ee' },
  calls:       { lineStyle: 'dashed', lineColor: '#a78bfa' },
  inherits:    { lineStyle: 'dotted', lineColor: '#22c55e' },
};

const LAYER_COLORS = {
  frontend: '#6c8cff22',
  backend: '#4ade8022',
  database: '#fb923c22',
  shared: '#a78bfa22',
};

function precomputeStyleData(nodes, edges) {
  for (const node of nodes) {
    const d = node.data;
    if (!d.type) continue;

    d._color = TYPE_COLORS[d.type] || '#8b90a0';

    const ss = STATUS_STYLES[d.status] || {};
    d._borderWidth = ss.borderWidth || 1.5;
    d._borderStyle = ss.borderStyle || 'solid';
    d._borderColor = ss.borderColor || '#8b90a0';
    d._borderOpacity = d.status === 'deprecated' ? 0.5 : 0.7;
    d._opacity = ss.opacity || 1;

    const lh = d.metadata && d.metadata.lighthouse;
    if (lh && typeof lh.performance === 'number') {
      const perf = lh.performance;
      d._borderColor = perf >= 90 ? '#22c55e' : perf >= 50 ? '#f59e0b' : '#ef4444';
      d._borderWidth = 3;
      d._borderOpacity = 1;
    }

    const t = d.type;
    d._shape = (t === 'db_table' || t === 'db_column') ? 'barrel'
      : t === 'api_endpoint' ? 'diamond'
      : t === 'service' ? 'hexagon'
      : t === 'middleware' ? 'triangle' : 'ellipse';

    if (d.id && LAYER_COLORS[d.id]) {
      d._layerColor = LAYER_COLORS[d.id];
    }
  }

  for (const edge of edges) {
    const d = edge.data;
    const es = EDGE_TYPE_STYLES[d.type] || {};
    if (d.deprecated) {
      d._lineColor = '#4b5563';
      d._lineStyle = 'dotted';
      d._arrowColor = '#4b5563';
      d._opacity = 0.25;
    } else {
      d._lineColor = es.lineColor || '#2e3755';
      d._lineStyle = es.lineStyle || 'solid';
      d._arrowColor = es.lineColor || '#3a4570';
      d._opacity = 0.7;
    }
  }
}

let cy;
let minimapCy;
let selectedNodeId = null;
let activeLayers = new Set(['frontend', 'backend', 'database', 'shared']);
let allElements = null;
let currentLayout = 'cose-bilkent';
let gitStatusActive = false;
let gitDirtyData = null;
let gitChangedOnly = false;
let gitChangedData = null;
let linkMode = false;
let linkSourceId = null;
let contextMenuNodeId = null;
let contextMenuEdgeId = null;
let rerouteEdgeId = null;
let rerouteMode = null;
let loadingCount = 0;
let ws = null;
let liveTrackingActive = false;
let touchedData = null;
let wsReconnectTimer = null;
let minimapTimer = null;
let minimapDirty = true;
let pendingNodePosition = null;
let canvasContextLayer = null;
let canvasContextPosition = null;

// Folder view state
let folderViewActive = false;
let _savedLayerParents = [];
let hiddenFolders = new Set();

// Focus / multi-select state
let focusSelectedIds = new Set();
let focusActive = false;

// Chat state (Vibe Code)
let _chatHistory = [];
let _chatStreaming = false;
let _chatAbortController = null;
let _chatSessionId = null;
let _chatContextFile = null;
let _chatContextNodeId = null;
let _chatReferencedFiles = [];
let _chatFileCache = null;
let _chatDropdownIdx = -1;
let _chatFilesWritten = [];

// ── Toast Notification System ────────────────────

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    if (toast.parentNode) toast.remove();
  }, 3200);
}

// ── Loading Bar ──────────────────────────────────

function showLoading() {
  loadingCount++;
  document.getElementById('loading-bar').classList.add('active');
}

function hideLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) {
    document.getElementById('loading-bar').classList.remove('active');
  }
}

// ── API Helper ───────────────────────────────────

async function api(path, opts = {}) {
  showLoading();
  try {
    const res = await fetch('/api' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    if (!res.ok) {
      let detail = `API error: ${res.status}`;
      try {
        const body = await res.json();
        if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  } finally {
    hideLoading();
  }
}

// ── Cytoscape Styles ─────────────────────────────

function buildCytoscapeStyle() {
  return [
    {
      selector: 'node[_color]',
      style: {
        label: 'data(label)',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 7,
        'font-size': '10px',
        'font-weight': '500',
        'font-family': 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
        color: '#c8ccd8',
        'text-outline-color': '#07090f',
        'text-outline-width': 2,
        'text-wrap': 'wrap',
        'text-max-width': '80px',
        'background-color': 'data(_color)',
        'background-opacity': 0.85,
        width: 28,
        height: 28,
        'border-width': 'data(_borderWidth)',
        'border-style': 'data(_borderStyle)',
        'border-color': 'data(_borderColor)',
        'border-opacity': 'data(_borderOpacity)',
        opacity: 'data(_opacity)',
        'overlay-opacity': 0,
        shape: 'data(_shape)',
        'transition-property': 'width, height, border-width, border-opacity, background-opacity',
        'transition-duration': '200ms',
      },
    },
    {
      selector: ':parent',
      style: {
        'background-color': (el) => el.data('_folderColor') || LAYER_COLORS[el.data('id')] || '#ffffff06',
        'background-opacity': (el) => el.data('_isFolder') ? 0.8 : 0.6,
        'border-color': (el) => el.data('_isFolder') ? '#5570a8' : '#2a3050',
        'border-width': (el) => el.data('_isFolder') ? 2 : 1,
        'border-style': 'dashed',
        label: 'data(label)',
        'text-valign': 'top',
        'text-halign': 'right',
        'font-size': (el) => el.data('_isFolder') ? '12px' : '10px',
        'font-weight': '600',
        color: (el) => el.data('_isFolder') ? '#8899cc' : '#4a5275',
        'text-margin-y': -6,
        'text-margin-x': -10,
        padding: '36px',
      },
    },
    {
      selector: '.focus-selected',
      style: {
        'border-color': '#f59e0b',
        'border-width': 3.5,
        'border-style': 'solid',
        'border-opacity': 1,
        width: 34,
        height: 34,
        'overlay-color': '#f59e0b',
        'overlay-opacity': 0.12,
      },
    },
    {
      selector: '.focus-dimmed',
      style: {
        opacity: 0.3,
      },
    },
    {
      selector: 'edge',
      style: {
        width: 1.2,
        'line-color': 'data(_lineColor)',
        'line-style': 'data(_lineStyle)',
        'target-arrow-color': 'data(_arrowColor)',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'arrow-scale': 0.7,
        label: 'data(label)',
        'font-size': '9px',
        color: '#4a5275',
        'text-background-color': '#07090f',
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'text-background-shape': 'roundrectangle',
        opacity: 'data(_opacity)',
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-color': '#ffffff',
        'border-width': 3,
        'border-opacity': 1,
        width: 36,
        height: 36,
        'background-opacity': 1,
        color: '#ffffff',
      },
    },
    {
      selector: '.hovered',
      style: {
        width: 34,
        height: 34,
        'border-opacity': 1,
        'background-opacity': 1,
        color: '#e1e4ed',
      },
    },
    { selector: '.faded', style: { opacity: 0.12 } },
    { selector: '.search-faded', style: { opacity: 0.12 } },
    { selector: '.search-match', style: { opacity: 1, 'border-color': '#8b5cf6', 'border-width': 3, 'border-opacity': 1 } },
    { selector: '.highlighted', style: { opacity: 1 } },
    {
      selector: '.impact-source',
      style: {
        'border-color': '#fb923c',
        'border-width': 3,
        width: 34,
        height: 34,
      },
    },
    {
      selector: '.impact-hit',
      style: {
        'border-color': '#f87171',
        'border-width': 2.5,
        opacity: 1,
      },
    },
    {
      selector: '.dirty',
      style: {
        'border-color': '#fb923c',
        'border-width': 3.5,
        'border-opacity': 1,
        width: 34,
        height: 34,
        'background-opacity': 1,
        'overlay-color': '#fb923c',
        'overlay-opacity': 0.12,
      },
    },
    {
      selector: '.impacted-d1',
      style: {
        'border-color': '#f59e0b',
        'border-width': 2.5,
        'border-opacity': 0.9,
        'background-opacity': 0.95,
        'overlay-color': '#f59e0b',
        'overlay-opacity': 0.08,
      },
    },
    {
      selector: '.impacted-d2',
      style: {
        'border-color': '#fbbf24',
        'border-width': 2,
        'border-opacity': 0.7,
        'background-opacity': 0.9,
        'overlay-color': '#fbbf24',
        'overlay-opacity': 0.05,
      },
    },
    {
      selector: '.impacted-d3',
      style: {
        'border-color': '#fcd34d',
        'border-width': 1.5,
        'border-opacity': 0.5,
        'background-opacity': 0.85,
        'overlay-color': '#fcd34d',
        'overlay-opacity': 0.03,
      },
    },
    {
      selector: '.dirty-edge',
      style: {
        'line-color': '#fb923c',
        'target-arrow-color': '#fb923c',
        width: 2,
        opacity: 1,
      },
    },
    {
      selector: '.git-neighbor',
      style: {
        'border-color': '#a78bfa',
        'border-width': 2.5,
        'border-style': 'dashed',
        'border-opacity': 0.8,
        'background-opacity': 0.85,
        'overlay-color': '#a78bfa',
        'overlay-opacity': 0.06,
      },
    },
    {
      selector: '.touched',
      style: {
        'border-color': '#00e5ff',
        'border-width': 3.5,
        'border-opacity': 1,
        width: 34,
        height: 34,
        'background-opacity': 1,
        'overlay-color': '#00e5ff',
        'overlay-opacity': 0.15,
      },
    },
    {
      selector: '.touch-affected-d1',
      style: {
        'border-color': '#26c6da',
        'border-width': 2.5,
        'border-opacity': 0.9,
        'background-opacity': 0.95,
        'overlay-color': '#26c6da',
        'overlay-opacity': 0.08,
      },
    },
    {
      selector: '.touch-affected-d2',
      style: {
        'border-color': '#4dd0e1',
        'border-width': 2,
        'border-opacity': 0.7,
        'background-opacity': 0.9,
        'overlay-color': '#4dd0e1',
        'overlay-opacity': 0.05,
      },
    },
    {
      selector: '.touch-affected-d3',
      style: {
        'border-color': '#80deea',
        'border-width': 1.5,
        'border-opacity': 0.5,
        'background-opacity': 0.85,
        'overlay-color': '#80deea',
        'overlay-opacity': 0.03,
      },
    },
    {
      selector: '.touched-edge',
      style: {
        'line-color': '#00e5ff',
        'target-arrow-color': '#00e5ff',
        width: 2,
        opacity: 1,
      },
    },
  ];
}

// ── Cytoscape Init ───────────────────────────────

function initCytoscape(elements) {
  const nodeCount = elements.filter(e => e.data && !e.data.source).length;
  const isLargeGraph = nodeCount > 300;

  cy = cytoscape({
    container: document.getElementById('cy'),
    elements: elements,
    style: buildCytoscapeStyle(),
    layout: { name: 'preset' },
    minZoom: 0.08,
    maxZoom: 6,
    wheelSensitivity: 0.25,
    textureOnViewport: isLargeGraph,
    hideEdgesOnViewport: isLargeGraph,
    hideLabelsOnViewport: isLargeGraph,
  });

  cy.on('tap', 'node', (evt) => {
    const node = evt.target;
    if (node.isParent()) return;
    if (linkMode) {
      handleLinkClick(node.id());
      return;
    }
    const oe = evt.originalEvent;
    if (oe && (oe.ctrlKey || oe.metaKey)) {
      toggleFocusSelect(node.id());
      return;
    }
    selectNode(node.id());
  });

  cy.on('tap', (evt) => {
    if (evt.target === cy) {
      closeDetail();
      hideContextMenu();
      hideCanvasContextMenu();
    }
  });

  cy.on('dragfree', 'node', (evt) => {
    const node = evt.target;
    if (node.isParent()) return;
    const pos = node.position();
    fetch(`/api/node/${node.id()}/position?x=${pos.x}&y=${pos.y}`, { method: 'PUT' });
  });

  cy.on('viewport', () => {
    updateViewportRect();
    if (minimapTimer) clearTimeout(minimapTimer);
    minimapTimer = setTimeout(updateMinimap, 250);
  });

  // Right-click context menu
  cy.on('cxttap', 'node', (evt) => {
    const node = evt.target;
    if (node.isParent()) return;
    evt.originalEvent.preventDefault();
    contextMenuNodeId = node.id();
    const oe = evt.originalEvent;
    showContextMenu(oe.clientX, oe.clientY);
  });

  cy.on('cxttap', 'edge', (evt) => {
    evt.originalEvent.preventDefault();
    contextMenuEdgeId = evt.target.id();
    const oe = evt.originalEvent;
    showEdgeContextMenu(oe.clientX, oe.clientY);
  });

  cy.on('cxttap', (evt) => {
    if (evt.target === cy) {
      hideContextMenu();
      hideEdgeContextMenu();
      evt.originalEvent.preventDefault();
      const modelPos = evt.position;
      let detectedLayer = 'shared';
      cy.nodes().forEach(n => {
        if (!n.isParent()) return;
        const bb = n.boundingBox();
        if (modelPos.x >= bb.x1 && modelPos.x <= bb.x2 && modelPos.y >= bb.y1 && modelPos.y <= bb.y2) {
          detectedLayer = n.id();
        }
      });
      canvasContextLayer = detectedLayer;
      canvasContextPosition = { x: modelPos.x, y: modelPos.y };
      showCanvasContextMenu(evt.originalEvent.clientX, evt.originalEvent.clientY);
    }
  });

  const tooltip = document.getElementById('node-tooltip');

  cy.on('mouseover', 'node', (evt) => {
    const node = evt.target;
    if (node.isParent()) return;
    node.addClass('hovered');
    const data = node.data();
    let dirtyHtml = '';
    if (gitStatusActive && gitDirtyData) {
      if (gitDirtyData.dirty_nodes.includes(data.id)) {
        dirtyHtml = '<div class="tt-dirty">Uncommitted changes</div>';
      } else if (gitDirtyData.impacted_nodes[data.id]) {
        const imp = gitDirtyData.impacted_nodes[data.id];
        const sourceNames = imp.dirty_sources.map(sid => {
          const sn = cy.getElementById(sid);
          return sn.length ? sn.data('label') : sid;
        });
        dirtyHtml = `<div class="tt-impacted">Affected by: ${escapeHtml(sourceNames.join(', '))} (depth ${imp.depth})</div>`;
      }
    }
    let lhHtml = '';
    const lhData = data.metadata && data.metadata.lighthouse;
    if (lhData) {
      const pc = v => v >= 90 ? 'lh-good' : v >= 50 ? 'lh-ok' : 'lh-poor';
      lhHtml = `<div class="tt-lighthouse">
        <span class="${pc(lhData.performance)}">Perf: ${lhData.performance}</span>
        <span class="${pc(lhData.accessibility)}">A11y: ${lhData.accessibility}</span>
        <span class="${pc(lhData.best_practices)}">BP: ${lhData.best_practices}</span>
        <span class="${pc(lhData.seo)}">SEO: ${lhData.seo}</span>
      </div>`;
    }
    tooltip.innerHTML = `
      <div class="tt-name">${escapeHtml(data.label || data.id)}</div>
      ${data.summary ? `<div class="tt-summary">${escapeHtml(data.summary)}</div>` : ''}
      <div class="tt-type">${(data.type || '').replace('_', ' ')} · ${data.layer || ''}</div>
      ${lhHtml}
      ${dirtyHtml}
    `;
    tooltip.classList.remove('hidden');
  });

  cy.on('mousemove', (evt) => {
    if (tooltip && !tooltip.classList.contains('hidden')) {
      const oe = evt.originalEvent || evt;
      tooltip.style.left = ((oe.clientX || oe.pageX || 0) + 14) + 'px';
      tooltip.style.top = ((oe.clientY || oe.pageY || 0) - 10) + 'px';
    }
  });

  cy.on('mouseout', 'node', (evt) => {
    const node = evt.target;
    if (node.isParent()) return;
    node.removeClass('hovered');
    tooltip.classList.add('hidden');
  });

  initMinimap();
  runLayout();
}

// ── Layout ───────────────────────────────────────

function runLayout(name) {
  if (name) currentLayout = name;

  if (currentLayout === 'preset') {
    const hasPositions = cy.nodes().some(n => !n.isParent() && n.position().x !== 0 && n.position().y !== 0);
    if (hasPositions) {
      cy.fit(undefined, 40);
      rebuildMinimap();
      updateViewportRect();
      return;
    }
    currentLayout = 'cose-bilkent';
  }

  const opts = {
    animate: false,
    padding: 40,
    stop: () => {
      rebuildMinimap();
      updateViewportRect();
    },
  };

  if (currentLayout === 'cose-bilkent') {
    const nodeCount = cy.nodes().filter(n => !n.isParent()).length;

    if (nodeCount > 2000) {
      Object.assign(opts, { name: 'grid', rows: undefined, condense: false, spacingFactor: 1.3 });
      showToast(`Large graph (${nodeCount} nodes) — using Grid layout. Switch manually via Layout dropdown.`, 'info');
      cy.layout(opts).run();
      return;
    }

    const quality = nodeCount > 500 ? 'default' : 'proof';
    const nodeRepulsion = nodeCount > 1000 ? 12000 : 8000;
    const idealEdgeLength = nodeCount > 1000 ? 150 : 100;
    const numIter = nodeCount > 1000 ? 1500 : 2500;

    Object.assign(opts, {
      name: 'cose-bilkent',
      quality: quality,
      nodeDimensionsIncludeLabels: true,
      nodeRepulsion: nodeRepulsion,
      idealEdgeLength: idealEdgeLength,
      edgeElasticity: 0.08,
      nestingFactor: 0.2,
      gravity: 0.25,
      tile: true,
      tilingPaddingVertical: 30,
      tilingPaddingHorizontal: 30,
      numIter: numIter,
    });
  } else if (currentLayout === 'breadthfirst') {
    Object.assign(opts, { name: 'breadthfirst', directed: true, spacingFactor: 1.4 });
  } else if (currentLayout === 'circle') {
    Object.assign(opts, { name: 'circle', spacingFactor: 1.6 });
  } else if (currentLayout === 'grid') {
    Object.assign(opts, { name: 'grid', rows: undefined, condense: false, spacingFactor: 1.3 });
  } else {
    opts.name = currentLayout;
  }

  cy.layout(opts).run();
}

// ── Tree View (dagre) ────────────────────────────

const ROOT_ENTRY_PATTERNS = [
  /^index\.[jt]sx?$/i,
  /^App\.[jt]sx?$/i,
  /^main\.[jt]sx?$/i,
  /^server\.[jt]s$/i,
  /^app\.[jt]s$/i,
];

function runTreeView() {
  if (!cy) return;

  const visibleNodes = cy.nodes().filter(n => !n.isParent() && n.visible());
  if (visibleNodes.length === 0) {
    showToast('No visible nodes to arrange', 'info');
    return;
  }

  let rootIds = [];

  visibleNodes.forEach(n => {
    const fp = n.data('file_path') || '';
    const basename = fp.split('/').pop();
    if (basename && ROOT_ENTRY_PATTERNS.some(p => p.test(basename))) {
      rootIds.push(n.id());
    }
  });

  if (rootIds.length === 0) {
    const incomingTargets = new Set();
    cy.edges().filter(e => e.visible()).forEach(e => incomingTargets.add(e.target().id()));
    visibleNodes.forEach(n => {
      if (!n.isParent() && !incomingTargets.has(n.id())) {
        rootIds.push(n.id());
      }
    });
  }

  const rootSelector = rootIds.length > 0
    ? rootIds.map(id => `#${CSS.escape(id)}`).join(', ')
    : undefined;

  cy.layout({
    name: 'dagre',
    rankDir: 'TB',
    nodeSep: 60,
    rankSep: 100,
    edgeSep: 20,
    roots: rootSelector,
    animate: false,
    padding: 40,
    nodeDimensionsIncludeLabels: true,
    stop: () => {
      cy.fit(undefined, 40);
      rebuildMinimap();
      updateViewportRect();
    },
  }).run();

  const btn = document.getElementById('btn-tree-view');
  btn.classList.add('active');
  showToast(`Tree view: ${rootIds.length} root node${rootIds.length !== 1 ? 's' : ''} detected`, 'info');
}

// ── Folder View ─────────────────────────────────

function getFolderForNode(node) {
  let filePath = '';
  let nodeType = '';
  if (typeof node.data === 'function') {
    filePath = node.data('file_path') || '';
    nodeType = node.data('type') || '';
  } else if (node.data) {
    filePath = node.data.file_path || '';
    nodeType = node.data.type || '';
  }
  if (filePath) {
    const normalized = filePath.replace(/\\/g, '/');
    const parts = normalized.split('/').filter(Boolean);
    if (parts.length > 1) return parts[parts.length - 2];
    return 'root';
  }
  if (nodeType) return nodeType.replace('_', ' ');
  return 'other';
}

function toggleFolderView() {
  if (!cy) return;
  folderViewActive = !folderViewActive;
  const btn = document.getElementById('btn-folder-view');

  if (folderViewActive) {
    btn.classList.add('active');
    _savedLayerParents = [];
    const layerIds = new Set(['frontend', 'backend', 'database', 'shared']);

    cy.nodes().forEach(n => {
      if (n.isParent() && layerIds.has(n.id())) {
        _savedLayerParents.push(n.id());
      }
    });

    const folderMap = {};
    cy.nodes().forEach(n => {
      if (n.isParent()) return;
      const folder = getFolderForNode(n);
      if (!folderMap[folder]) folderMap[folder] = [];
      folderMap[folder].push(n.id());
    });

    if (Object.keys(folderMap).length <= 1) {
      folderViewActive = false;
      btn.classList.remove('active');
      showToast('All nodes belong to a single group — folder view needs multiple folders', 'info');
      return;
    }

    cy.batch(() => {
      // Orphan children BEFORE removing parents (remove() cascades to children)
      _savedLayerParents.forEach(id => {
        const pn = cy.getElementById(id);
        if (pn.length) {
          pn.children().forEach(child => child.move({ parent: null }));
          pn.remove();
        }
      });

      const FOLDER_COLORS = [
        '#6c8cff30', '#4ade8030', '#fb923c30', '#a78bfa30',
        '#22d3ee30', '#f8717130', '#facc1530', '#8b90a030',
      ];
      let colorIdx = 0;

      for (const [folder, nodeIds] of Object.entries(folderMap)) {
        const parentId = `_folder_${folder}`;
        cy.add({
          group: 'nodes',
          data: {
            id: parentId,
            label: folder,
            _isFolder: true,
            _folderColor: FOLDER_COLORS[colorIdx % FOLDER_COLORS.length],
          },
        });
        colorIdx++;

        nodeIds.forEach(nid => {
          const node = cy.getElementById(nid);
          if (node.length) node.move({ parent: parentId });
        });
      }
    });

    buildSidebarByFolder();
    runLayout(currentLayout);
    showToast(`Folder view: ${Object.keys(folderMap).length} folders`, 'info');
  } else {
    btn.classList.remove('active');
    exitFolderView();
  }
}

function exitFolderView() {
  if (!cy) return;
  hiddenFolders.clear();

  cy.batch(() => {
    const folderParents = cy.nodes().filter(n => n.data('_isFolder'));
    const children = [];
    folderParents.forEach(fp => {
      fp.children().forEach(c => {
        c.style('display', 'element');
        children.push(c);
      });
      fp.style('display', 'element');
    });
    cy.edges().style('display', 'element');

    children.forEach(c => c.move({ parent: null }));
    folderParents.remove();

    const layerIds = new Set(['frontend', 'backend', 'database', 'shared']);
    _savedLayerParents.forEach(layerId => {
      if (!cy.getElementById(layerId).length) {
        cy.add({
          group: 'nodes',
          data: {
            id: layerId,
            label: layerId.charAt(0).toUpperCase() + layerId.slice(1),
            _layerColor: LAYER_COLORS[layerId],
          },
        });
      }
    });

    cy.nodes().forEach(n => {
      if (n.isParent()) return;
      const layer = n.data('layer');
      if (layer && layerIds.has(layer)) {
        n.move({ parent: layer });
      }
    });
  });

  if (allElements) {
    const sidebarNodes = allElements.nodes.filter(n => n.data.type);
    buildSidebar(sidebarNodes);
  }
  runLayout(currentLayout);
}

function buildSidebarByFolder() {
  if (!cy) return;
  const folderMap = {};

  cy.nodes().forEach(n => {
    if (n.isParent()) return;
    const folder = getFolderForNode(n);
    if (!folderMap[folder]) folderMap[folder] = [];
    folderMap[folder].push({
      data: {
        id: n.data('id'),
        label: n.data('label'),
        type: n.data('type'),
        layer: n.data('layer'),
        tags: n.data('tags') || [],
        file_path: n.data('file_path'),
      }
    });
  });

  sidebarAllItems = [];
  const sortedFolders = Object.keys(folderMap).sort((a, b) => {
    if (a === 'other') return 1;
    if (b === 'other') return -1;
    return a.localeCompare(b);
  });

  for (const folder of sortedFolders) {
    const nodes = folderMap[folder];
    sidebarAllItems.push({ type: 'header', layer: folder, count: nodes.length, isFolder: true });
    for (const n of nodes) {
      sidebarAllItems.push({ type: 'node', node: n });
    }
  }

  renderSidebarFromItems();
}

function _buildFolderHeaderHtml(item) {
  if (item.isFolder) {
    const escaped = item.layer.replace(/'/g, "\\'");
    const checked = hiddenFolders.has(item.layer) ? '' : 'checked';
    return `<h2><label class="folder-vis-label"><input type="checkbox" class="folder-vis-cb" data-folder="${item.layer}" ${checked} onchange="toggleFolderVisibility('${escaped}', this.checked)"> <span class="folder-icon">&#128193;</span> ${item.layer} <span class="layer-count">${item.count}</span></label></h2>`;
  }
  return `<h2>${item.layer} <span class="layer-count">${item.count}</span></h2>`;
}

function toggleFolderVisibility(folder, visible) {
  if (!folderViewActive || !cy) return;
  if (visible) {
    hiddenFolders.delete(folder);
  } else {
    hiddenFolders.add(folder);
  }
  const folderId = '_folder_' + folder;
  const parentNode = cy.getElementById(folderId);
  if (!parentNode.length) return;

  cy.batch(() => {
    if (!visible) {
      parentNode.style('display', 'none');
      parentNode.children().forEach(ch => ch.style('display', 'none'));
      parentNode.children().connectedEdges().forEach(e => e.style('display', 'none'));
    } else {
      parentNode.style('display', 'element');
      parentNode.children().forEach(ch => ch.style('display', 'element'));
      parentNode.children().connectedEdges().forEach(e => {
        const src = e.source();
        const tgt = e.target();
        if (src.style('display') !== 'none' && tgt.style('display') !== 'none') {
          e.style('display', 'element');
        }
      });
    }
  });
  cy.fit(cy.elements().filter(el => el.style('display') !== 'none'), 40);
}

// ── Focus / Multi-select ────────────────────────

function toggleFocusSelect(nodeId) {
  if (focusActive) return;
  const node = cy.getElementById(nodeId);
  if (!node.length || node.isParent()) return;

  if (focusSelectedIds.has(nodeId)) {
    focusSelectedIds.delete(nodeId);
    node.removeClass('focus-selected');
  } else {
    focusSelectedIds.add(nodeId);
    node.addClass('focus-selected');
  }
  updateFocusButton();
  updateSidebarFocusCheckboxes();
}

function updateFocusButton() {
  const btn = document.getElementById('btn-focus');
  btn.disabled = focusSelectedIds.size === 0 && !focusActive;
  if (focusSelectedIds.size > 0 && !focusActive) {
    btn.textContent = `Focus (${focusSelectedIds.size})`;
  } else if (focusActive) {
    btn.textContent = 'Focus';
  } else {
    btn.textContent = 'Focus';
  }
}

function applyFocusFilter() {
  if (focusSelectedIds.size === 0) return;
  focusActive = true;

  cy.batch(() => {
    const neighbors = new Set();
    focusSelectedIds.forEach(id => {
      const node = cy.getElementById(id);
      if (!node.length) return;
      node.neighborhood('node').forEach(nb => {
        if (!nb.isParent()) neighbors.add(nb.id());
      });
    });

    cy.nodes().forEach(n => {
      if (n.isParent()) {
        n.style('display', 'element');
        return;
      }
      const nid = n.id();
      if (focusSelectedIds.has(nid)) {
        n.style('display', 'element');
        n.style('opacity', 1);
        n.removeClass('focus-dimmed');
      } else if (neighbors.has(nid)) {
        n.style('display', 'element');
        n.addClass('focus-dimmed');
      } else {
        n.style('display', 'none');
        n.removeClass('focus-dimmed');
      }
    });

    cy.edges().forEach(edge => {
      const srcId = edge.data('source');
      const tgtId = edge.data('target');
      const srcVisible = focusSelectedIds.has(srcId) || neighbors.has(srcId);
      const tgtVisible = focusSelectedIds.has(tgtId) || neighbors.has(tgtId);
      edge.style('display', srcVisible && tgtVisible ? 'element' : 'none');
    });

    cy.nodes(':parent').forEach(p => {
      const hasVisibleChild = p.children().some(c => c.style('display') !== 'none');
      p.style('display', hasVisibleChild ? 'element' : 'none');
    });
  });

  const badge = document.getElementById('focus-badge');
  const badgeText = document.getElementById('focus-badge-text');
  badgeText.textContent = `Focusing on ${focusSelectedIds.size} node${focusSelectedIds.size !== 1 ? 's' : ''}`;
  badge.classList.remove('hidden');

  const btn = document.getElementById('btn-focus');
  btn.classList.add('active');
  btn.disabled = false;

  cy.fit(cy.nodes().filter(n => n.style('display') !== 'none'), 40);
  rebuildMinimap();
  updateViewportRect();
  showToast(`Focused on ${focusSelectedIds.size} node${focusSelectedIds.size !== 1 ? 's' : ''}`, 'info');
}

function clearFocus() {
  focusActive = false;
  cy.batch(() => {
    cy.nodes().removeClass('focus-selected focus-dimmed');
    cy.nodes().style('opacity', '');
  });
  focusSelectedIds.clear();

  const badge = document.getElementById('focus-badge');
  badge.classList.add('hidden');

  const btn = document.getElementById('btn-focus');
  btn.classList.remove('active');
  btn.disabled = true;
  btn.textContent = 'Focus';

  applyAllFilters();
  cy.fit(undefined, 40);
  rebuildMinimap();
  updateViewportRect();
  updateSidebarFocusCheckboxes();
  showToast('Focus cleared', 'info');
}

function updateSidebarFocusCheckboxes() {
  document.querySelectorAll('.node-focus-cb').forEach(cb => {
    cb.checked = focusSelectedIds.has(cb.dataset.nodeId);
  });
  const focusSidebarBtn = document.getElementById('sidebar-focus-btn');
  if (focusSidebarBtn) {
    if (focusSelectedIds.size > 0 && !focusActive) {
      focusSidebarBtn.classList.remove('hidden');
      focusSidebarBtn.textContent = `Focus ${focusSelectedIds.size} selected`;
    } else {
      focusSidebarBtn.classList.add('hidden');
    }
  }
}

// ── Minimap ──────────────────────────────────────

function initMinimap() {
  const container = document.getElementById('minimap');
  if (!container || !cy) return;

  const nodeCount = cy.nodes().filter(n => !n.isParent()).length;
  if (nodeCount > 2000) {
    container.innerHTML = '<div style="color:#4e5568;font-size:10px;padding:8px;text-align:center;">Minimap disabled<br>for large graphs</div>';
    return;
  }

  minimapCy = cytoscape({
    container: container,
    elements: cy.elements().jsons(),
    style: [
      {
        selector: 'node',
        style: {
          width: 6, height: 6,
          'background-color': (el) => TYPE_COLORS[el.data('type')] || '#555',
          label: '',
          'border-width': 0,
        },
      },
      { selector: ':parent', style: { 'background-opacity': 0.15, 'border-width': 0, label: '' } },
      { selector: 'edge', style: { width: 0.5, 'line-color': '#3a406040', 'target-arrow-shape': 'none' } },
    ],
    layout: { name: 'preset' },
    userPanningEnabled: false,
    userZoomingEnabled: false,
    autoungrabify: true,
    autounselectify: true,
  });

  minimapDirty = false;
  minimapCy.fit(undefined, 4);
  minimapCy.on('tap', (evt) => {
    if (evt.target === minimapCy) {
      const pos = evt.position;
      cy.center(cy.nodes().closestTo({ x: pos.x, y: pos.y }));
    }
  });
}

function updateMinimap() {
  if (!minimapCy || !cy) return;
  if (minimapDirty) {
    minimapCy.elements().remove();
    minimapCy.add(cy.elements().jsons());
    minimapDirty = false;
  }
  minimapCy.fit(undefined, 4);
}

function rebuildMinimap() {
  minimapDirty = true;
  updateMinimap();
}

function updateViewportRect() {
  if (!minimapCy || !cy) return;
  const rect = document.getElementById('minimap-viewport-rect');
  if (!rect) return;

  const ext = cy.extent();
  const mmExt = minimapCy.extent();
  const mmContainer = document.getElementById('minimap');
  if (!mmContainer) return;

  const mmW = mmContainer.clientWidth;
  const mmH = mmContainer.clientHeight;
  const mmGraphW = mmExt.w || 1;
  const mmGraphH = mmExt.h || 1;

  const scaleX = mmW / mmGraphW;
  const scaleY = mmH / mmGraphH;

  const left = (ext.x1 - mmExt.x1) * scaleX;
  const top = (ext.y1 - mmExt.y1) * scaleY;
  const width = ext.w * scaleX;
  const height = ext.h * scaleY;

  rect.style.left = Math.max(0, left) + 'px';
  rect.style.top = Math.max(0, top) + 'px';
  rect.style.width = Math.min(mmW, Math.max(8, width)) + 'px';
  rect.style.height = Math.min(mmH, Math.max(6, height)) + 'px';
}

// ── Load Graph ───────────────────────────────────

async function loadGraph() {
  try {
    const data = await api('/graph');
    allElements = data;
    const nodeIds = new Set(data.nodes.map(n => n.data.id));
    const validEdges = data.edges.filter(e => {
      if (!nodeIds.has(e.data.source) || !nodeIds.has(e.data.target)) {
        return false;
      }
      return true;
    });
    data.edges = validEdges;
    precomputeStyleData(data.nodes, validEdges);
    const elements = [...data.nodes, ...validEdges];
    const sidebarNodes = data.nodes.filter(n => n.data.type);
    const realNodes = sidebarNodes.filter(n => !['frontend','backend','database','shared'].includes(n.data.id));
    const emptyState = document.getElementById('empty-state');

    if (realNodes.length === 0) {
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
      openSidebarByDefault();
    }

    buildSidebar(sidebarNodes);
    updateStats();
    loadADRs();
    initCytoscape(elements);
  } catch (err) {
    console.error('[Architect] loadGraph ERROR:', err);
    showToast('Failed to load graph: ' + err.message, 'error');
  }
}

function openSidebarByDefault() {
  const sidebar = document.getElementById('sidebar');
  const app = document.getElementById('app');
  sidebar.classList.remove('collapsed');
  app.classList.add('sidebar-open');
  if (cy) setTimeout(() => cy.resize(), 300);
}

// ── Build Sidebar ────────────────────────────────

const SIDEBAR_BATCH_SIZE = 100;
let sidebarAllItems = [];
let sidebarRenderedCount = 0;
let sidebarObserver = null;

function buildNodeItemHtml(n) {
  const tags = n.data.tags || [];
  const stale = tags.includes('stale') ? '<span class="stale-badge">stale</span>' : '';
  const checked = focusSelectedIds.has(n.data.id) ? 'checked' : '';
  return `<div class="node-item" data-id="${n.data.id}" data-name="${escapeHtml(n.data.label || '').toLowerCase()}">
      <input type="checkbox" class="node-focus-cb" data-node-id="${n.data.id}" ${checked} onclick="event.stopPropagation(); toggleFocusSelect('${n.data.id}')" title="Select for focus">
      <span class="node-dot dot-${n.data.type}" onclick="selectNode('${n.data.id}')"></span>
      <span class="name" onclick="selectNode('${n.data.id}')">${n.data.label}</span>
      ${stale}
      <span class="badge">${(n.data.type || '').replace('_', ' ')}</span>
    </div>`;
}

function buildSidebar(nodes) {
  const grouped = {};
  for (const n of nodes) {
    const layer = n.data.layer;
    if (!layer) continue;
    if (!grouped[layer]) grouped[layer] = [];
    grouped[layer].push(n);
  }

  sidebarAllItems = [];
  for (const [layer, layerNodes] of Object.entries(grouped)) {
    sidebarAllItems.push({ type: 'header', layer, count: layerNodes.length });
    for (const n of layerNodes) {
      sidebarAllItems.push({ type: 'node', node: n });
    }
  }

  renderSidebarFromItems();
}

function renderSidebarFromItems() {
  const list = document.getElementById('node-list');
  if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }

  let html = '<button id="sidebar-focus-btn" class="sidebar-focus-btn hidden" onclick="applyFocusFilter()">Focus selected</button>';

  if (sidebarAllItems.length <= SIDEBAR_BATCH_SIZE) {
    for (const item of sidebarAllItems) {
      if (item.type === 'header') {
        html += _buildFolderHeaderHtml(item);
      } else {
        html += buildNodeItemHtml(item.node);
      }
    }
    list.innerHTML = html;
    sidebarRenderedCount = sidebarAllItems.length;
    updateSidebarFocusCheckboxes();
    return;
  }

  const initialBatch = sidebarAllItems.slice(0, SIDEBAR_BATCH_SIZE);
  for (const item of initialBatch) {
    if (item.type === 'header') {
      html += _buildFolderHeaderHtml(item);
    } else {
      html += buildNodeItemHtml(item.node);
    }
  }
  html += '<div id="sidebar-sentinel" style="height:1px;"></div>';
  list.innerHTML = html;
  sidebarRenderedCount = SIDEBAR_BATCH_SIZE;

  const sentinel = document.getElementById('sidebar-sentinel');
  if (sentinel) {
    sidebarObserver = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) renderMoreSidebarItems();
    }, { root: list.closest('.sidebar-content'), threshold: 0 });
    sidebarObserver.observe(sentinel);
  }
  updateSidebarFocusCheckboxes();
}

function renderMoreSidebarItems() {
  if (sidebarRenderedCount >= sidebarAllItems.length) {
    if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }
    const sentinel = document.getElementById('sidebar-sentinel');
    if (sentinel) sentinel.remove();
    return;
  }

  const list = document.getElementById('node-list');
  const sentinel = document.getElementById('sidebar-sentinel');
  const end = Math.min(sidebarRenderedCount + SIDEBAR_BATCH_SIZE, sidebarAllItems.length);
  const fragment = document.createDocumentFragment();

  for (let i = sidebarRenderedCount; i < end; i++) {
    const item = sidebarAllItems[i];
    const temp = document.createElement('div');
    if (item.type === 'header') {
      temp.innerHTML = _buildFolderHeaderHtml(item);
    } else {
      temp.innerHTML = buildNodeItemHtml(item.node);
    }
    while (temp.firstChild) fragment.appendChild(temp.firstChild);
  }

  if (sentinel) {
    list.insertBefore(fragment, sentinel);
  } else {
    list.appendChild(fragment);
  }

  sidebarRenderedCount = end;

  if (sidebarRenderedCount >= sidebarAllItems.length) {
    if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }
    if (sentinel) sentinel.remove();
  }
}

// ── Sidebar Filter ───────────────────────────────

document.getElementById('sidebar-filter-input').addEventListener('input', (e) => {
  const query = e.target.value.toLowerCase().trim();
  document.querySelectorAll('.node-item').forEach(item => {
    const name = item.dataset.name || '';
    item.style.display = (!query || name.includes(query)) ? '' : 'none';
  });
});

// ── ADRs ─────────────────────────────────────────

async function loadADRs() {
  try {
    const adrs = await api('/adrs');
    const list = document.getElementById('adr-list');
    if (!adrs.length) {
      list.innerHTML = '<div style="padding:14px;color:var(--text-dim);font-size:12px;">No ADRs yet.</div>';
      return;
    }
    list.innerHTML = adrs.map(a => `
      <div class="adr-item" onclick="showADRDetail('${a.id}')">
        <div class="adr-title">${escapeHtml(a.title)}</div>
        <div class="adr-meta">
          <span class="adr-status-badge ${a.status}">${a.status}</span>
          ${a.linked_nodes.length ? `<span>${a.linked_nodes.length} linked</span>` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {}
}

async function showADRDetail(adrId) {
  const adr = await api(`/adr/${adrId}`);
  const panel = document.getElementById('detail-panel');
  panel.classList.add('open');
  selectedNodeId = null;

  document.getElementById('detail-name').textContent = adr.title;
  document.getElementById('detail-meta').innerHTML = `
    <span class="adr-status-badge ${adr.status}">${adr.status}</span>
    <span class="meta-tag">ADR</span>
  `;

  document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));

  const body = document.getElementById('detail-body');
  let html = '';
  if (adr.context) html += `<h4 style="color:var(--accent-end);margin-bottom:4px;">Context</h4><p style="font-size:12px;margin-bottom:12px;">${escapeHtml(adr.context)}</p>`;
  if (adr.decision) html += `<h4 style="color:var(--accent-end);margin-bottom:4px;">Decision</h4><p style="font-size:12px;margin-bottom:12px;">${escapeHtml(adr.decision)}</p>`;
  if (adr.consequences) html += `<h4 style="color:var(--accent-end);margin-bottom:4px;">Consequences</h4><p style="font-size:12px;margin-bottom:12px;">${escapeHtml(adr.consequences)}</p>`;
  if (adr.linked_nodes.length) {
    html += `<h4 style="color:var(--accent-end);margin-bottom:4px;">Linked Nodes</h4>`;
    html += adr.linked_nodes.map(nid => `<div class="impact-node" onclick="selectNode('${nid}')">${nid}</div>`).join('');
  }
  body.innerHTML = html;

  document.getElementById('detail-actions').innerHTML = `
    <button class="btn btn-secondary" onclick="editADR('${adr.id}')">Edit</button>
    <button class="btn btn-danger" onclick="deleteADR('${adr.id}')">Delete</button>
  `;
}

// ── Node Selection & Detail ──────────────────────

async function selectNode(nodeId) {
  selectedNodeId = nodeId;

  document.querySelectorAll('.node-item').forEach(el => el.classList.remove('selected'));
  const item = document.querySelector(`.node-item[data-id="${nodeId}"]`);
  if (item) item.classList.add('selected');

  const cyNode = cy.getElementById(nodeId);
  if (cyNode.length) {
    cy.batch(() => {
      cy.nodes().removeClass('highlighted faded impact-source impact-hit');
      cy.edges().removeClass('highlighted faded');

      const neighborhood = cyNode.neighborhood().add(cyNode);
      cy.elements().addClass('faded');
      neighborhood.removeClass('faded').addClass('highlighted');
      neighborhood.parents().removeClass('faded');
      cyNode.select();
    });
  }

  await showDetail(nodeId);
}

async function showDetail(nodeId) {
  const body = document.getElementById('detail-body');
  body.innerHTML = '<div class="loading-placeholder"><span class="spinner"></span> Loading...</div>';
  const panel = document.getElementById('detail-panel');
  panel.classList.add('open');

  const node = await api(`/node/${nodeId}`);

  document.getElementById('detail-name').textContent = node.name;

  const tags = (node.tags || []).map(t => `<span class="meta-tag" style="color:var(--yellow)">${t}</span>`).join('');
  const metaHtml = `
    <span class="meta-tag dot-bg" style="border-left:3px solid ${TYPE_COLORS[node.type] || '#888'}">${node.type.replace('_', ' ')}</span>
    <span class="meta-tag">${node.layer}</span>
    <span class="meta-tag">${node.status.replace('_', ' ')}</span>
    ${node.file_path ? `<span class="meta-tag">${node.file_path}</span>` : ''}
    ${node.workspace ? `<span class="meta-tag" style="color:var(--cyan)">ws: ${node.workspace}</span>` : ''}
    ${tags}
  `;
  document.getElementById('detail-meta').innerHTML = metaHtml;

  document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.detail-tab[data-tab="context"]').classList.add('active');

  showContextTab(node);
}

function showContextTab(node) {
  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');

  if (node.context_html) {
    body.innerHTML = `<div class="context-html">${node.context_html}</div>`;
  } else {
    body.innerHTML = '<div style="color:var(--text-dim);font-size:12px;">No context yet. Click "Edit" to add context.</div>';
  }
  actions.innerHTML = '';
}

function showEditTab(node) {
  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');
  const raw = node.context_raw || '';

  body.innerHTML = `
    <div class="editor-toolbar">
      <button onclick="insertAtCursor('# ')" title="Heading"><b>H</b></button>
      <button onclick="insertAtCursor('**', '**')" title="Bold"><b>B</b></button>
      <button onclick="insertAtCursor('_', '_')" title="Italic"><i>I</i></button>
      <button onclick="insertAtCursor('\\n- ')" title="List">List</button>
      <button onclick="insertAtCursor('\\n\`\`\`\\n', '\\n\`\`\`')" title="Code">Code</button>
    </div>
    <div class="editor-split">
      <textarea class="context-editor" id="context-textarea">${escapeHtml(raw)}</textarea>
      <div class="editor-preview context-html" id="editor-preview">${node.context_html || '<span style="color:var(--text-dim)">Preview will appear here...</span>'}</div>
    </div>
  `;
  actions.innerHTML = '<button class="btn btn-primary" onclick="saveContext()">Save Context</button>';

  const textarea = document.getElementById('context-textarea');
  let previewTimer;
  textarea.addEventListener('input', () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const result = await fetch('/api/context/' + selectedNodeId);
        const preview = document.getElementById('editor-preview');
        if (preview) {
          const md = textarea.value;
          const tempDiv = document.createElement('div');
          tempDiv.textContent = md;
          preview.innerHTML = tempDiv.innerHTML
            .replace(/^### (.*$)/gm, '<h3>$1</h3>')
            .replace(/^## (.*$)/gm, '<h2>$1</h2>')
            .replace(/^# (.*$)/gm, '<h1>$1</h1>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/^- (.*$)/gm, '<li>$1</li>')
            .replace(/\n/g, '<br>');
        }
      } catch (e) {}
    }, 300);
  });
}

function insertAtCursor(before, after = '') {
  const ta = document.getElementById('context-textarea');
  if (!ta) return;
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const text = ta.value;
  const selected = text.substring(start, end);
  ta.value = text.substring(0, start) + before + selected + after + text.substring(end);
  ta.focus();
  ta.setSelectionRange(start + before.length, start + before.length + selected.length);
  ta.dispatchEvent(new Event('input'));
}

function showMetadataTab(node) {
  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');

  let lighthouseSection = '';
  const lh = node.metadata && node.metadata.lighthouse;
  if (lh) {
    const badge = (label, val) => {
      const cls = val >= 90 ? 'lh-good' : val >= 50 ? 'lh-ok' : 'lh-poor';
      return `<div class="lh-badge ${cls}"><span class="lh-score">${val}</span><span class="lh-label">${label}</span></div>`;
    };
    lighthouseSection = `
      <div class="lighthouse-section">
        <h4 style="color:var(--accent-end);margin:0 0 8px;">Lighthouse Scores</h4>
        <div class="lh-badges">
          ${badge('Performance', lh.performance)}
          ${badge('Accessibility', lh.accessibility)}
          ${badge('Best Practices', lh.best_practices)}
          ${badge('SEO', lh.seo)}
        </div>
        <div class="lh-meta">
          <span>URL: ${escapeHtml(lh.url || '')}</span>
          ${lh.audited_at ? `<span>Audited: ${new Date(lh.audited_at).toLocaleDateString()}</span>` : ''}
        </div>
      </div>
    `;
  }

  let metaRows = '';
  const meta = node.metadata || {};
  for (const [k, v] of Object.entries(meta)) {
    if (k === 'lighthouse') continue;
    metaRows += `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</td></tr>`;
  }
  const filteredKeys = Object.keys(meta).filter(k => k !== 'lighthouse');
  const metaTable = filteredKeys.length
    ? `<h4 style="color:var(--accent-end);margin:10px 0 4px;">Custom Metadata</h4>
       <table class="meta-table"><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody>${metaRows}</tbody></table>`
    : '';

  body.innerHTML = `
    ${lighthouseSection}
    <div class="form-group">
      <label>Name</label>
      <input id="edit-name" value="${escapeHtml(node.name)}">
    </div>
    <div class="form-group">
      <label>Summary</label>
      <input id="edit-summary" value="${escapeHtml(node.summary || '')}">
    </div>
    <div class="form-group">
      <label>Status</label>
      <select id="edit-status">
        ${['planned','in_progress','implemented','needs_review','deprecated'].map(s =>
          `<option value="${s}" ${node.status === s ? 'selected' : ''}>${s.replace('_',' ')}</option>`
        ).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>Type</label>
      <select id="edit-type">
        ${['page','component','api_endpoint','service','db_table','db_column','middleware','utility','config','custom'].map(t =>
          `<option value="${t}" ${node.type === t ? 'selected' : ''}>${t.replace('_',' ')}</option>`
        ).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>Layer</label>
      <select id="edit-layer">
        ${['frontend','backend','database','shared'].map(l =>
          `<option value="${l}" ${node.layer === l ? 'selected' : ''}>${l}</option>`
        ).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>File Path</label>
      <input id="edit-filepath" value="${escapeHtml(node.file_path || '')}">
    </div>
    <div class="form-group">
      <label>Tags (comma-separated)</label>
      <input id="edit-tags" value="${escapeHtml((node.tags || []).join(', '))}">
    </div>
    ${metaTable}
  `;
  const isDeprecated = node.status === 'deprecated';
  actions.innerHTML = `
    ${isDeprecated ? '<button class="btn btn-restore" onclick="restoreCurrentNode()">Restore</button>' : ''}
    ${isDeprecated ? '<button class="btn btn-danger" onclick="permanentDeleteNode()">Permanently Delete</button>' : '<button class="btn btn-danger" onclick="deleteCurrentNode()">Delete Node</button>'}
    <button class="btn btn-primary" onclick="saveMetadata()">Save Changes</button>
  `;
}

async function showImpactTab(node) {
  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');
  actions.innerHTML = '';

  body.innerHTML = '<div class="loading-placeholder"><span class="spinner"></span> Analyzing impact...</div>';

  try {
    const result = await api(`/impact/${node.id}`);
    if (!result.impacted.length) {
      body.innerHTML = '<div style="color:var(--green);font-size:12px;">No other nodes are impacted by changes to this node.</div>';
      return;
    }

    cy.batch(() => {
      cy.nodes().removeClass('impact-source impact-hit faded highlighted');
      cy.edges().removeClass('faded highlighted');
      cy.elements().addClass('faded');

      const sourceEl = cy.getElementById(node.id);
      sourceEl.removeClass('faded').addClass('impact-source');

      for (const item of result.impacted) {
        const el = cy.getElementById(item.node.id);
        if (el.length) el.removeClass('faded').addClass('impact-hit');
      }
    });

    const grouped = {};
    for (const item of result.impacted) {
      const d = item.depth;
      if (!grouped[d]) grouped[d] = [];
      grouped[d].push(item.node);
    }

    let html = `<div class="impact-count">${result.total} nodes impacted</div>`;
    html += '<div class="impact-tree">';
    for (const depth of Object.keys(grouped).sort((a, b) => a - b)) {
      html += `<div class="depth-label">Depth ${depth}</div>`;
      for (const n of grouped[depth]) {
        html += `<div class="impact-node" onclick="selectNode('${n.id}')">
          <span class="node-dot dot-${n.type}"></span>
          <span>${escapeHtml(n.name)}</span>
          <span style="color:var(--text-dim);font-size:10px;">(${n.type.replace('_',' ')})</span>
        </div>`;
      }
    }
    html += '</div>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div style="color:var(--red);font-size:12px;">Error: ${e.message}</div>`;
  }
}

let _lastCodeResult = null;
let _lastCodeNodeId = null;
let _codeEditMode = false;

async function showCodeTab(node) {
  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');
  actions.innerHTML = '';
  _codeEditMode = false;

  if (!node.file_path) {
    body.innerHTML = '<div style="color:var(--text-dim);font-size:12px;">No source file linked to this node. Set a file path in the Metadata tab.</div>';
    return;
  }

  body.innerHTML = '<div class="loading-placeholder"><span class="spinner"></span> Loading source code...</div>';

  let result;
  try {
    result = await api(`/file/${encodeURIComponent(node.id)}`);
  } catch (e) {
    body.innerHTML = `<div style="color:var(--red);font-size:12px;">Could not load file: ${escapeHtml(e.message)}</div>`;
    return;
  }

  _lastCodeResult = result;
  _lastCodeNodeId = node.id;

  const langClass = result.language && result.language !== 'plain'
    ? `language-${result.language}`
    : 'language-none';

  body.innerHTML = `
    <div class="code-viewer">
      <div class="code-header">
        <span class="code-filepath">${escapeHtml(result.file_path)}</span>
        <div class="code-header-actions">
          <span class="code-lang">${escapeHtml(result.language)}</span>
          <button class="btn-vibe-code" id="code-vibe-btn" title="Open AI chat for this file">Vibe Code</button>
          <button class="code-header-btn" id="code-edit-btn" title="Edit file">Edit</button>
          <button class="code-header-btn" id="code-fullscreen-btn" title="Open fullscreen">&#x26F6;</button>
        </div>
      </div>
      <pre class="line-numbers"><code class="${langClass}">${escapeHtml(result.content)}</code></pre>
    </div>
  `;

  try {
    if (typeof Prism !== 'undefined') {
      Prism.highlightAllUnder(body);
    }
  } catch (e) {}

  document.getElementById('code-fullscreen-btn').addEventListener('click', () => {
    openCodeFullscreen(result, node.id);
  });

  document.getElementById('code-edit-btn').addEventListener('click', () => {
    enterSidebarEditMode(result, node.id);
  });

  document.getElementById('code-vibe-btn').addEventListener('click', () => {
    activateChatTab();
    _chatContextFile = result.file_path;
    _chatContextNodeId = node.id;
    const input = document.getElementById('chat-input');
    input.focus();
    input.placeholder = `Ask about ${result.file_path}... Use @ to reference files`;
  });
}

function enterSidebarEditMode(result, nodeId) {
  if (_codeEditMode) return;
  _codeEditMode = true;

  const body = document.getElementById('detail-body');
  const actions = document.getElementById('detail-actions');

  body.innerHTML = `
    <div class="code-viewer">
      <div class="code-header">
        <span class="code-filepath">${escapeHtml(result.file_path)}</span>
        <div class="code-header-actions">
          <span class="code-lang">${escapeHtml(result.language)}</span>
          <button class="code-header-btn" id="code-fullscreen-btn" title="Open fullscreen">&#x26F6;</button>
        </div>
      </div>
      <textarea class="code-edit-area" id="sidebar-code-editor">${escapeHtml(result.content)}</textarea>
    </div>
  `;

  const textarea = document.getElementById('sidebar-code-editor');
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.substring(0, start) + '  ' + textarea.value.substring(end);
      textarea.selectionStart = textarea.selectionEnd = start + 2;
    }
  });

  actions.innerHTML = `
    <button class="btn btn-secondary" id="code-edit-cancel">Cancel</button>
    <button class="btn btn-primary" id="code-edit-save">Save</button>
  `;

  document.getElementById('code-edit-cancel').addEventListener('click', async () => {
    const graph = await api('/graph');
    const node = graph.nodes.find(n => n.id === nodeId);
    if (node) showCodeTab(node);
  });

  document.getElementById('code-edit-save').addEventListener('click', async () => {
    const content = document.getElementById('sidebar-code-editor').value;
    try {
      await api(`/file/${encodeURIComponent(nodeId)}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      });
      showToast('File saved successfully', 'success');
      result.content = content;
      _lastCodeResult = result;
      _codeEditMode = false;
      const graph = await api('/graph');
      const node = graph.nodes.find(n => n.id === nodeId);
      if (node) showCodeTab(node);
    } catch (e) {
      showToast('Failed to save file: ' + e.message, 'error');
    }
  });

  document.getElementById('code-fullscreen-btn').addEventListener('click', () => {
    openCodeFullscreen(result, nodeId);
  });
}

function openCodeFullscreen(result, nodeId) {
  const modal = document.getElementById('code-fullscreen-modal');
  const body = document.getElementById('code-fullscreen-body');
  const footer = document.getElementById('code-fullscreen-footer');
  const editBtn = document.getElementById('code-fullscreen-edit');

  document.getElementById('code-fullscreen-filepath').textContent = result.file_path;
  document.getElementById('code-fullscreen-lang').textContent = result.language;

  footer.classList.add('hidden');
  editBtn.textContent = 'Edit';

  const langClass = result.language && result.language !== 'plain'
    ? `language-${result.language}`
    : 'language-none';

  body.innerHTML = `<pre class="line-numbers"><code class="${langClass}">${escapeHtml(result.content)}</code></pre>`;

  try {
    if (typeof Prism !== 'undefined') {
      Prism.highlightAllUnder(body);
    }
  } catch (e) {}

  modal.classList.remove('hidden');

  editBtn.onclick = () => {
    enterFullscreenEditMode(result, nodeId);
  };
}

function enterFullscreenEditMode(result, nodeId) {
  const body = document.getElementById('code-fullscreen-body');
  const footer = document.getElementById('code-fullscreen-footer');
  const editBtn = document.getElementById('code-fullscreen-edit');

  body.innerHTML = `<textarea class="code-edit-area" id="fullscreen-code-editor">${escapeHtml(result.content)}</textarea>`;

  const textarea = document.getElementById('fullscreen-code-editor');
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.substring(0, start) + '  ' + textarea.value.substring(end);
      textarea.selectionStart = textarea.selectionEnd = start + 2;
    }
  });
  textarea.focus();

  footer.classList.remove('hidden');
  editBtn.textContent = 'View';

  editBtn.onclick = () => {
    openCodeFullscreen(result, nodeId);
  };

  document.getElementById('code-fullscreen-save').onclick = async () => {
    const content = document.getElementById('fullscreen-code-editor').value;
    try {
      await api(`/file/${encodeURIComponent(nodeId)}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      });
      showToast('File saved successfully', 'success');
      result.content = content;
      _lastCodeResult = result;
      openCodeFullscreen(result, nodeId);
    } catch (e) {
      showToast('Failed to save file: ' + e.message, 'error');
    }
  };

  document.getElementById('code-fullscreen-cancel').onclick = () => {
    openCodeFullscreen(result, nodeId);
  };
}

// ── Save / Delete Actions ────────────────────────

async function saveContext() {
  if (!selectedNodeId) return;
  try {
    const content = document.getElementById('context-textarea').value;
    await api(`/context/${selectedNodeId}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
    showToast('Context saved successfully', 'success');
    await showDetail(selectedNodeId);
  } catch (e) {
    showToast('Failed to save context: ' + e.message, 'error');
  }
}

async function saveMetadata() {
  if (!selectedNodeId) return;
  try {
    const tags = document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);
    await api(`/node/${selectedNodeId}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: document.getElementById('edit-name').value,
        summary: document.getElementById('edit-summary').value,
        status: document.getElementById('edit-status').value,
        type: document.getElementById('edit-type').value,
        layer: document.getElementById('edit-layer').value,
        file_path: document.getElementById('edit-filepath').value || null,
        tags,
      }),
    });
    showToast('Node updated successfully', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to update node: ' + e.message, 'error');
  }
}

async function deleteCurrentNode() {
  if (!selectedNodeId) return;
  const cyNode = cy.getElementById(selectedNodeId);
  const status = cyNode.length ? cyNode.data('status') : 'planned';
  const isPlanned = status === 'planned';
  const msg = isPlanned
    ? 'Remove this planned node? It will be permanently deleted.'
    : 'This node has code — it will be marked as deprecated (ghost). Continue?';
  if (!confirm(msg)) return;
  try {
    const result = await api(`/node/${selectedNodeId}`, { method: 'DELETE' });
    if (result.action === 'hard_deleted') {
      showToast('Node permanently deleted', 'success');
      closeDetail();
    } else {
      showToast('Node deprecated (ghost)', 'info');
    }
    await refreshGraph();
  } catch (e) {
    showToast('Failed to delete node: ' + e.message, 'error');
  }
}

async function permanentDeleteNode() {
  if (!selectedNodeId) return;
  if (!confirm('Permanently delete this node and all its connections? This cannot be undone.')) return;
  try {
    await api(`/node/${selectedNodeId}/permanent`, { method: 'DELETE' });
    showToast('Node permanently deleted', 'success');
    closeDetail();
    await refreshGraph();
  } catch (e) {
    showToast('Failed to delete node: ' + e.message, 'error');
  }
}

async function restoreCurrentNode() {
  if (!selectedNodeId) return;
  try {
    await api(`/node/${selectedNodeId}/restore`, { method: 'POST' });
    showToast('Node restored', 'success');
    await refreshGraph();
    await showDetail(selectedNodeId);
  } catch (e) {
    showToast('Failed to restore node: ' + e.message, 'error');
  }
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
  selectedNodeId = null;
  document.querySelectorAll('.node-item').forEach(el => el.classList.remove('selected'));
  if (cy) {
    cy.batch(() => {
      cy.elements().removeClass('faded highlighted impact-source impact-hit search-faded search-match');
      cy.nodes(':selected').unselect();
    });
    if (gitStatusActive && gitDirtyData) {
      applyGitOverlay(gitDirtyData);
    }
    if (liveTrackingActive && touchedData) {
      applyTouchedOverlay(touchedData);
    }
  }
}

async function refreshGraph(reLayout = false) {
  const data = await api('/graph');
  allElements = data;
  precomputeStyleData(data.nodes, data.edges);
  cy.batch(() => {
    cy.elements().remove();
    cy.add([...data.nodes, ...data.edges]);
  });
  if (reLayout) {
    runLayout();
  } else {
    cy.fit(undefined, 40);
    rebuildMinimap();
    updateViewportRect();
  }
  const sidebarNodes = data.nodes.filter(n => n.data.type);
  buildSidebar(sidebarNodes);
  updateStats();
  applyAllFilters();
  if (gitStatusActive && gitDirtyData) {
    applyGitOverlay(gitDirtyData);
  }
  if (liveTrackingActive && touchedData) {
    applyTouchedOverlay(touchedData);
  }
  const realNodes = sidebarNodes.filter(n => !['frontend','backend','database','shared'].includes(n.data.id));
  const emptyState = document.getElementById('empty-state');
  if (realNodes.length === 0) {
    emptyState.classList.remove('hidden');
  } else {
    emptyState.classList.add('hidden');
  }
}

// ── Filters ──────────────────────────────────────

function applyAllFilters() {
  applyLayerFilter();
  applyTypeFilter();
  applyStatusFilter();
  applySearch();
}

function applyLayerFilter() {
  cy.batch(() => {
    if (folderViewActive) {
      cy.nodes().forEach(node => {
        node.style('display', 'element');
      });
    } else {
      cy.nodes().forEach(node => {
        if (node.isParent()) {
          node.style('display', activeLayers.has(node.id()) ? 'element' : 'none');
        } else {
          const layer = node.data('parent') || node.data('layer');
          node.style('display', activeLayers.has(layer) ? 'element' : 'none');
        }
      });
    }
  });
  updateEdgeVisibility();
}

function applyTypeFilter() {
  const typeVal = document.getElementById('type-filter').value;
  if (!typeVal) return;
  cy.batch(() => {
    cy.nodes().forEach(n => {
      if (n.isParent()) return;
      if (n.style('display') === 'none') return;
      if (n.data('type') !== typeVal) n.style('display', 'none');
    });
  });
  updateEdgeVisibility();
}

function applyStatusFilter() {
  const statusVal = document.getElementById('status-filter').value;
  if (!statusVal) return;
  cy.batch(() => {
    cy.nodes().forEach(n => {
      if (n.isParent()) return;
      if (n.style('display') === 'none') return;
      if (n.data('status') !== statusVal) n.style('display', 'none');
    });
  });
  updateEdgeVisibility();
}

function applySearch() {
  const query = document.getElementById('search-input').value.toLowerCase().trim();
  const clearBtn = document.getElementById('search-clear');
  const countSpan = document.getElementById('search-count');

  clearBtn.classList.toggle('visible', query.length > 0);

  cy.batch(() => {
    cy.nodes().removeClass('search-faded search-match');
    cy.edges().removeClass('search-faded');

    if (!query) {
      countSpan.textContent = '';
      return;
    }

    let matchCount = 0;
    cy.nodes().forEach(n => {
      if (n.isParent()) return;
      if (n.style('display') === 'none') return;
      const label = (n.data('label') || '').toLowerCase();
      const nodeType = (n.data('type') || '').toLowerCase();
      const tags = (n.data('tags') || []).join(' ').toLowerCase();
      const workspace = (n.data('workspace') || '').toLowerCase();
      const match = label.includes(query) || nodeType.includes(query) || tags.includes(query) || workspace.includes(query);
      if (match) {
        n.addClass('search-match');
        matchCount++;
      } else {
        n.addClass('search-faded');
      }
    });

    cy.edges().forEach(edge => {
      const src = cy.getElementById(edge.data('source'));
      const tgt = cy.getElementById(edge.data('target'));
      if (src.hasClass('search-faded') && tgt.hasClass('search-faded')) {
        edge.addClass('search-faded');
      }
    });

    countSpan.textContent = matchCount + ' found';
  });
}

function updateEdgeVisibility() {
  cy.batch(() => {
    cy.edges().forEach(edge => {
      const src = cy.getElementById(edge.data('source'));
      const tgt = cy.getElementById(edge.data('target'));
      const visible = src.style('display') !== 'none' && tgt.style('display') !== 'none';
      edge.style('display', visible ? 'element' : 'none');
    });
  });
}

async function updateStats() {
  const stats = await api('/stats');
  const stale = stats.stale_count ? ` | ${stats.stale_count} stale` : '';
  document.getElementById('stats').textContent =
    `${stats.project_name} | ${stats.total_nodes} nodes, ${stats.total_edges} edges${stale}`;
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

// ── Add Node ─────────────────────────────────────

async function addNode() {
  const nameInput = document.getElementById('new-node-name');
  const name = nameInput.value.trim();
  const fg = document.getElementById('fg-new-node-name');

  if (!name) {
    fg.classList.add('invalid');
    nameInput.focus();
    return;
  }
  fg.classList.remove('invalid');

  try {
    const tagsStr = document.getElementById('new-node-tags').value;
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];
    const layer = document.getElementById('new-node-layer').value;
    const type = document.getElementById('new-node-type').value;
    const summary = document.getElementById('new-node-summary').value;

    let posX, posY;
    if (pendingNodePosition) {
      posX = pendingNodePosition.x;
      posY = pendingNodePosition.y;
      pendingNodePosition = null;
    } else {
      const ext = cy.extent();
      posX = (ext.x1 + ext.x2) / 2;
      posY = (ext.y1 + ext.y2) / 2;
    }

    const result = await api('/node', {
      method: 'POST',
      body: JSON.stringify({
        name,
        type,
        layer,
        summary,
        tags,
        position_x: posX,
        position_y: posY,
      }),
    });

    document.getElementById('add-node-modal').classList.add('hidden');
    nameInput.value = '';
    document.getElementById('new-node-summary').value = '';
    document.getElementById('new-node-tags').value = '';
    showToast(`Node "${name}" created`, 'success');

    if (!cy.getElementById(layer).length) {
      const layerEle = { data: { id: layer, label: layer.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) } };
      precomputeStyleData([layerEle], []);
      cy.add(layerEle);
    }

    const newEle = {
      data: {
        id: result.id,
        label: result.name,
        type: result.type,
        layer: result.layer,
        status: result.status,
        summary: result.summary,
        file_path: result.file_path || '',
        tags: result.tags || [],
        metadata: result.metadata || {},
        workspace: result.workspace || '',
        parent: result.layer,
      },
      position: { x: posX, y: posY },
    };
    precomputeStyleData([newEle], []);
    cy.add(newEle);

    rebuildMinimap();
    updateViewportRect();

    const data = await api('/graph');
    allElements = data;
    const sidebarNodes = data.nodes.filter(n => n.data.type);
    buildSidebar(sidebarNodes);
    updateStats();
    applyAllFilters();

    const emptyState = document.getElementById('empty-state');
    emptyState.classList.add('hidden');
  } catch (e) {
    showToast('Failed to create node: ' + e.message, 'error');
  }
}

// ── Export ────────────────────────────────────────

async function doExport() {
  const fmt = document.getElementById('export-format').value;
  document.getElementById('export-modal').classList.add('hidden');

  try {
    if (fmt === 'png' || fmt === 'svg') {
      let dataUrl;
      if (fmt === 'png') {
        dataUrl = cy.png({ full: true, scale: 2, bg: '#07090f' });
      } else {
        dataUrl = cy.svg({ full: true, scale: 1, bg: '#07090f' });
      }
      const a = document.createElement('a');
      a.href = dataUrl;
      a.download = `architect-graph.${fmt}`;
      a.click();
      showToast(`Exported as ${fmt.toUpperCase()}`, 'success');
      return;
    }

    const res = await fetch(`/api/export/${fmt}`);
    const text = await res.text();
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ext = fmt === 'mermaid' ? 'mmd' : fmt === 'plantuml' ? 'puml' : 'json';
    a.download = `architect-graph.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Exported as ${fmt}`, 'success');
  } catch (e) {
    showToast('Export failed: ' + e.message, 'error');
  }
}

// ── ADR CRUD ─────────────────────────────────────

function openADRModal(adr = null) {
  document.getElementById('adr-modal').classList.remove('hidden');
  document.getElementById('adr-modal-title').textContent = adr ? 'Edit ADR' : 'Add ADR';
  document.getElementById('adr-edit-id').value = adr ? adr.id : '';
  document.getElementById('adr-title').value = adr ? adr.title : '';
  document.getElementById('adr-status').value = adr ? adr.status : 'proposed';
  document.getElementById('adr-context').value = adr ? adr.context : '';
  document.getElementById('adr-decision').value = adr ? adr.decision : '';
  document.getElementById('adr-consequences').value = adr ? adr.consequences : '';
  document.getElementById('adr-linked').value = adr ? (adr.linked_nodes || []).join(', ') : '';
  setTimeout(() => document.getElementById('adr-title').focus(), 100);
}

async function editADR(adrId) {
  const adr = await api(`/adr/${adrId}`);
  openADRModal(adr);
}

async function deleteADR(adrId) {
  if (!confirm('Delete this ADR?')) return;
  try {
    await api(`/adr/${adrId}`, { method: 'DELETE' });
    showToast('ADR deleted', 'success');
    closeDetail();
    loadADRs();
  } catch (e) {
    showToast('Failed to delete ADR: ' + e.message, 'error');
  }
}

async function saveADR() {
  const id = document.getElementById('adr-edit-id').value;
  const linked = document.getElementById('adr-linked').value
    .split(',').map(s => s.trim()).filter(Boolean);

  const body = {
    title: document.getElementById('adr-title').value,
    status: document.getElementById('adr-status').value,
    context: document.getElementById('adr-context').value,
    decision: document.getElementById('adr-decision').value,
    consequences: document.getElementById('adr-consequences').value,
    linked_nodes: linked,
  };

  try {
    if (id) {
      await api(`/adr/${id}`, { method: 'PUT', body: JSON.stringify(body) });
    } else {
      await api('/adr', { method: 'POST', body: JSON.stringify(body) });
    }
    document.getElementById('adr-modal').classList.add('hidden');
    showToast(id ? 'ADR updated' : 'ADR created', 'success');
    loadADRs();
  } catch (e) {
    showToast('Failed to save ADR: ' + e.message, 'error');
  }
}

// ── Git Status Overlay ───────────────────────────

async function toggleGitStatus() {
  const btn = document.getElementById('btn-git-status');
  const legendGit = document.getElementById('legend-git');

  if (gitStatusActive) {
    clearGitOverlay();
    gitStatusActive = false;
    gitDirtyData = null;
    btn.classList.remove('active');
    btn.textContent = 'Git Status';
    legendGit.classList.add('hidden');
    return;
  }

  btn.textContent = 'Loading...';
  btn.disabled = true;

  try {
    const data = await api('/git/dirty');
    gitDirtyData = data;

    if (!data.is_git_repo) {
      btn.textContent = 'No Git';
      btn.disabled = false;
      showToast('Not a git repository', 'info');
      return;
    }

    applyGitOverlay(data);
    gitStatusActive = true;
    const count = data.dirty_nodes.length;
    btn.textContent = count > 0 ? `Git (${count} dirty)` : 'Git (clean)';
    btn.classList.add('active');
    legendGit.classList.remove('hidden');
  } catch (e) {
    btn.textContent = 'Git Error';
    showToast('Git status error: ' + e.message, 'error');
  }

  btn.disabled = false;
}

function applyGitOverlay(data) {
  if (!cy) return;

  const dirtySet = new Set(data.dirty_nodes);
  const impactedKeys = new Set(Object.keys(data.impacted_nodes));

  cy.batch(() => {
    for (const nid of data.dirty_nodes) {
      const el = cy.getElementById(nid);
      if (el.length) el.addClass('dirty');
    }

    for (const [nid, info] of Object.entries(data.impacted_nodes)) {
      const el = cy.getElementById(nid);
      if (!el.length) continue;
      const d = info.depth;
      if (d <= 1) el.addClass('impacted-d1');
      else if (d <= 2) el.addClass('impacted-d2');
      else el.addClass('impacted-d3');
    }

    cy.edges().forEach(edge => {
      const srcId = edge.data('source');
      const tgtId = edge.data('target');
      const srcAffected = dirtySet.has(srcId) || impactedKeys.has(srcId);
      const tgtAffected = dirtySet.has(tgtId) || impactedKeys.has(tgtId);
      if (srcAffected && tgtAffected) {
        edge.addClass('dirty-edge');
      }
    });
  });

  updateSidebarDirtyState(data);
}

function clearGitOverlay() {
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().removeClass('dirty impacted-d1 impacted-d2 impacted-d3');
    cy.edges().removeClass('dirty-edge');
  });

  document.querySelectorAll('.node-item .dirty-indicator').forEach(el => el.remove());
  document.querySelectorAll('.node-item .impacted-indicator').forEach(el => el.remove());
  document.querySelectorAll('.node-item').forEach(el => el.classList.remove('dirty-bg', 'impacted-bg'));
}

function updateSidebarDirtyState(data) {
  document.querySelectorAll('.node-item').forEach(el => {
    const nid = el.dataset.id;

    if (data.dirty_nodes.includes(nid)) {
      el.classList.add('dirty-bg');
      if (!el.querySelector('.dirty-indicator')) {
        const badge = document.createElement('span');
        badge.className = 'dirty-indicator';
        badge.textContent = 'modified';
        el.appendChild(badge);
      }
    }

    const impact = data.impacted_nodes[nid];
    if (impact) {
      el.classList.add('impacted-bg');
      if (!el.querySelector('.impacted-indicator')) {
        const badge = document.createElement('span');
        badge.className = 'impacted-indicator';
        badge.textContent = `d${impact.depth}`;
        badge.title = `Affected by: ${impact.dirty_sources.join(', ')}`;
        el.appendChild(badge);
      }
    }
  });
}

// ── Git Changed-Only View ────────────────────────

async function toggleGitChanged() {
  const btn = document.getElementById('btn-git-changed');
  const legendChanged = document.getElementById('legend-changed');

  if (gitChangedOnly) {
    gitChangedOnly = false;
    gitChangedData = null;
    btn.classList.remove('active');
    btn.textContent = 'Changed Only';
    legendChanged.classList.add('hidden');
    await refreshGraph();
    return;
  }

  btn.textContent = 'Loading...';
  btn.disabled = true;

  try {
    const data = await api('/git/changed-graph');
    gitChangedData = data;

    if (!data.is_git_repo) {
      btn.textContent = 'No Git';
      btn.disabled = false;
      showToast('Not a git repository', 'info');
      return;
    }

    if (data.dirty_node_ids.length === 0) {
      btn.textContent = 'Changed Only';
      btn.disabled = false;
      showToast('No changed files found', 'info');
      return;
    }

    precomputeStyleData(data.nodes, data.edges);

    cy.batch(() => {
      cy.elements().remove();
      cy.add([...data.nodes, ...data.edges]);
    });

    allElements = data;

    const dirtySet = new Set(data.dirty_node_ids);
    const neighborSet = new Set(data.neighbor_node_ids);
    cy.batch(() => {
      for (const nid of data.dirty_node_ids) {
        const el = cy.getElementById(nid);
        if (el.length) el.addClass('dirty');
      }
      for (const nid of data.neighbor_node_ids) {
        const el = cy.getElementById(nid);
        if (el.length) el.addClass('git-neighbor');
      }
      cy.edges().forEach(edge => {
        const srcId = edge.data('source');
        const tgtId = edge.data('target');
        const srcDirty = dirtySet.has(srcId);
        const tgtDirty = dirtySet.has(tgtId);
        if (srcDirty || tgtDirty) {
          edge.addClass('dirty-edge');
        }
      });
    });

    runLayout();

    const sidebarNodes = data.nodes.filter(n => n.data.type);
    buildSidebar(sidebarNodes);
    updateStats();
    applyAllFilters();

    document.querySelectorAll('.node-item').forEach(el => {
      const nid = el.dataset.id;
      if (dirtySet.has(nid)) {
        el.classList.add('dirty-bg');
        if (!el.querySelector('.dirty-indicator')) {
          const badge = document.createElement('span');
          badge.className = 'dirty-indicator';
          badge.textContent = 'modified';
          el.appendChild(badge);
        }
      } else if (neighborSet.has(nid)) {
        el.classList.add('neighbor-bg');
        if (!el.querySelector('.neighbor-indicator')) {
          const badge = document.createElement('span');
          badge.className = 'neighbor-indicator';
          badge.textContent = 'connected';
          el.appendChild(badge);
        }
      }
    });

    gitChangedOnly = true;
    const count = data.dirty_node_ids.length;
    const ncount = data.neighbor_node_ids.length;
    btn.textContent = `Changed (${count}+${ncount})`;
    btn.classList.add('active');
    legendChanged.classList.remove('hidden');
    showToast(`Showing ${count} changed file${count !== 1 ? 's' : ''} and ${ncount} connected neighbor${ncount !== 1 ? 's' : ''}`, 'info');
  } catch (e) {
    btn.textContent = 'Error';
    showToast('Changed-only error: ' + e.message, 'error');
  }

  btn.disabled = false;
}

// ── Live File Tracking (WebSocket) ───────────────

function connectWebSocket() {
  if (ws && ws.readyState <= 1) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleLiveEvent(data);
    } catch (_) {}
  };

  ws.onclose = () => {
    wsReconnectTimer = setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = () => {
    try { ws.close(); } catch (_) {}
  };
}

function disconnectWebSocket() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
}

function handleLiveEvent(event) {
  if (!event || event.type !== 'file_changed') return;

  const shortPath = (event.file_path || '').split('/').slice(-2).join('/');
  const verb = event.change_type === 'added' ? 'created' : event.change_type === 'deleted' ? 'deleted' : 'modified';

  if (event.graph_changed) {
    refreshGraph().then(() => {
      if (liveTrackingActive) fetchAndApplyTouched();
    });
    showToast(`File ${verb}: ${shortPath} (graph updated)`, 'info');
  } else {
    if (liveTrackingActive) fetchAndApplyTouched();
    showToast(`File ${verb}: ${shortPath}`, 'info');
  }
}

async function fetchAndApplyTouched() {
  try {
    const data = await api('/touched');
    touchedData = data;
    applyTouchedOverlay(data);
  } catch (_) {}
}

function applyTouchedOverlay(data) {
  if (!cy || !data) return;

  clearTouchedOverlay();

  const touchedSet = new Set(data.touched_nodes);
  const impactedKeys = new Set(Object.keys(data.impacted_nodes));

  cy.batch(() => {
    for (const nid of data.touched_nodes) {
      const el = cy.getElementById(nid);
      if (el.length) el.addClass('touched');
    }

    for (const [nid, info] of Object.entries(data.impacted_nodes)) {
      const el = cy.getElementById(nid);
      if (!el.length) continue;
      const d = info.depth;
      if (d <= 1) el.addClass('touch-affected-d1');
      else if (d <= 2) el.addClass('touch-affected-d2');
      else el.addClass('touch-affected-d3');
    }

    cy.edges().forEach(edge => {
      const srcId = edge.data('source');
      const tgtId = edge.data('target');
      const srcAffected = touchedSet.has(srcId) || impactedKeys.has(srcId);
      const tgtAffected = touchedSet.has(tgtId) || impactedKeys.has(tgtId);
      if (srcAffected && tgtAffected) {
        edge.addClass('touched-edge');
      }
    });
  });

  updateSidebarTouchedState(data);
}

function clearTouchedOverlay() {
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().removeClass('touched touch-affected-d1 touch-affected-d2 touch-affected-d3');
    cy.edges().removeClass('touched-edge');
  });

  document.querySelectorAll('.node-item .touched-indicator').forEach(el => el.remove());
  document.querySelectorAll('.node-item .touch-affected-indicator').forEach(el => el.remove());
  document.querySelectorAll('.node-item').forEach(el => el.classList.remove('touched-bg', 'touch-affected-bg'));
}

function updateSidebarTouchedState(data) {
  document.querySelectorAll('.node-item').forEach(el => {
    const nid = el.dataset.id;

    if (data.touched_nodes.includes(nid)) {
      el.classList.add('touched-bg');
      if (!el.querySelector('.touched-indicator')) {
        const badge = document.createElement('span');
        badge.className = 'touched-indicator';
        badge.textContent = 'modified';
        el.appendChild(badge);
      }
    }

    const impact = data.impacted_nodes[nid];
    if (impact) {
      el.classList.add('touch-affected-bg');
      if (!el.querySelector('.touch-affected-indicator')) {
        const badge = document.createElement('span');
        badge.className = 'touch-affected-indicator';
        badge.textContent = `d${impact.depth}`;
        badge.title = `Affected by: ${impact.touched_sources.join(', ')}`;
        el.appendChild(badge);
      }
    }
  });
}

async function toggleLiveTracking() {
  const btn = document.getElementById('btn-live-tracking');
  const legendLive = document.getElementById('legend-live');

  if (liveTrackingActive) {
    clearTouchedOverlay();
    liveTrackingActive = false;
    touchedData = null;
    disconnectWebSocket();
    btn.classList.remove('active');
    btn.textContent = 'Live';
    legendLive.classList.add('hidden');
    return;
  }

  btn.textContent = 'Connecting...';
  btn.disabled = true;

  connectWebSocket();

  try {
    const data = await api('/touched');
    touchedData = data;
    applyTouchedOverlay(data);
    liveTrackingActive = true;

    const count = data.touched_nodes.length;
    btn.textContent = count > 0 ? `Live (${count} touched)` : 'Live (watching)';
    btn.classList.add('active');
    legendLive.classList.remove('hidden');
  } catch (e) {
    btn.textContent = 'Live Error';
    showToast('Live tracking error: ' + e.message, 'error');
    disconnectWebSocket();
  }

  btn.disabled = false;
}

// ── Context Menu ─────────────────────────────────

function showContextMenu(x, y) {
  const menu = document.getElementById('context-menu');
  menu.classList.remove('hidden');
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';

  const maxX = window.innerWidth - menu.offsetWidth - 8;
  const maxY = window.innerHeight - menu.offsetHeight - 8;
  if (x > maxX) menu.style.left = maxX + 'px';
  if (y > maxY) menu.style.top = maxY + 'px';
}

function hideContextMenu() {
  document.getElementById('context-menu').classList.add('hidden');
  contextMenuNodeId = null;
}

document.querySelectorAll('.context-menu-item').forEach(item => {
  item.addEventListener('click', async () => {
    const action = item.dataset.action;
    const nid = contextMenuNodeId;
    hideContextMenu();
    if (!nid) return;

    if (action === 'view-details') {
      selectNode(nid);
    } else if (action === 'edit-metadata') {
      selectedNodeId = nid;
      const node = await api(`/node/${nid}`);
      const panel = document.getElementById('detail-panel');
      panel.classList.add('open');
      document.getElementById('detail-name').textContent = node.name;
      showMetadataTab(node);
    } else if (action === 'impact') {
      selectedNodeId = nid;
      const node = await api(`/node/${nid}`);
      const panel = document.getElementById('detail-panel');
      panel.classList.add('open');
      document.getElementById('detail-name').textContent = node.name;
      showImpactTab(node);
    } else if (action === 'link-from') {
      startLinkMode(nid, 'from');
    } else if (action === 'link-to') {
      startLinkMode(nid, 'to');
    } else if (action === 'delete') {
      selectedNodeId = nid;
      deleteCurrentNode();
    }
  });
});

// ── Edge Context Menu ────────────────────────────

function showEdgeContextMenu(x, y) {
  const menu = document.getElementById('edge-context-menu');
  if (!menu) return;
  const edge = cy.getElementById(contextMenuEdgeId);
  const isDeprecated = edge.length && edge.data('deprecated');

  menu.querySelectorAll('[data-action="restore-edge"]').forEach(el => el.style.display = isDeprecated ? '' : 'none');
  menu.querySelectorAll('[data-action="permanent-delete-edge"]').forEach(el => el.style.display = isDeprecated ? '' : 'none');
  menu.querySelectorAll('[data-action="delete-edge"]').forEach(el => el.style.display = isDeprecated ? 'none' : '');

  menu.classList.remove('hidden');
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  const maxX = window.innerWidth - menu.offsetWidth - 8;
  const maxY = window.innerHeight - menu.offsetHeight - 8;
  if (x > maxX) menu.style.left = maxX + 'px';
  if (y > maxY) menu.style.top = maxY + 'px';
}

function hideEdgeContextMenu() {
  const menu = document.getElementById('edge-context-menu');
  if (menu) menu.classList.add('hidden');
  contextMenuEdgeId = null;
}

// ── Canvas Context Menu ─────────────────────────

function showCanvasContextMenu(x, y) {
  const menu = document.getElementById('canvas-context-menu');
  if (!menu) return;
  menu.classList.remove('hidden');
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  const maxX = window.innerWidth - menu.offsetWidth - 8;
  const maxY = window.innerHeight - menu.offsetHeight - 8;
  if (x > maxX) menu.style.left = maxX + 'px';
  if (y > maxY) menu.style.top = maxY + 'px';
}

function hideCanvasContextMenu() {
  const menu = document.getElementById('canvas-context-menu');
  if (menu) menu.classList.add('hidden');
  canvasContextLayer = null;
  canvasContextPosition = null;
}

document.querySelectorAll('.canvas-context-menu-item').forEach(item => {
  item.addEventListener('click', () => {
    const action = item.dataset.action;
    hideCanvasContextMenu();
    if (action === 'add-node-here') {
      if (canvasContextPosition) {
        pendingNodePosition = { x: canvasContextPosition.x, y: canvasContextPosition.y };
      }
      const layerSelect = document.getElementById('new-node-layer');
      if (canvasContextLayer && layerSelect) {
        layerSelect.value = canvasContextLayer;
      }
      document.getElementById('add-node-modal').classList.remove('hidden');
      setTimeout(() => document.getElementById('new-node-name').focus(), 100);
    }
  });
});

async function deleteEdge(edgeId) {
  if (!edgeId) return;
  const edge = cy.getElementById(edgeId);
  const isDeprecated = edge.length && edge.data('deprecated');
  const msg = isDeprecated ? 'Permanently delete this edge?' : 'Delete this edge?';
  if (!confirm(msg)) return;
  try {
    await api(`/edge/${edgeId}`, { method: 'DELETE' });
    showToast('Edge deleted', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to delete edge: ' + e.message, 'error');
  }
}

async function permanentDeleteEdge(edgeId) {
  if (!edgeId) return;
  if (!confirm('Permanently delete this edge? This cannot be undone.')) return;
  try {
    await api(`/edge/${edgeId}/permanent`, { method: 'DELETE' });
    showToast('Edge permanently deleted', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to delete edge: ' + e.message, 'error');
  }
}

async function restoreEdge(edgeId) {
  if (!edgeId) return;
  try {
    await api(`/edge/${edgeId}/restore`, { method: 'POST' });
    showToast('Edge restored', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to restore edge: ' + e.message, 'error');
  }
}

function showEditEdgeModal(edgeId) {
  const edge = cy.getElementById(edgeId);
  if (!edge.length) return;
  const modal = document.getElementById('edit-edge-modal');
  modal.classList.remove('hidden');
  modal.dataset.edgeId = edgeId;
  document.getElementById('edit-edge-label').value = edge.data('label') || '';
  document.getElementById('edit-edge-type').value = edge.data('type') || 'dependency';
  setTimeout(() => document.getElementById('edit-edge-label').focus(), 100);
}

async function saveEdgeEdit() {
  const modal = document.getElementById('edit-edge-modal');
  const edgeId = modal.dataset.edgeId;
  try {
    await api(`/edge/${edgeId}`, {
      method: 'PUT',
      body: JSON.stringify({
        label: document.getElementById('edit-edge-label').value.trim(),
        type: document.getElementById('edit-edge-type').value,
      }),
    });
    modal.classList.add('hidden');
    showToast('Edge updated', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to update edge: ' + e.message, 'error');
  }
}

function startEdgeReroute(edgeId, which) {
  rerouteEdgeId = edgeId;
  rerouteMode = which;
  const edge = cy.getElementById(edgeId);
  const label = which === 'source'
    ? `Reroute source — click the new source node for this edge...`
    : `Reroute target — click the new target node for this edge...`;
  document.getElementById('link-mode-text').textContent = label;
  document.getElementById('link-mode-banner').classList.add('visible');
  linkMode = true;
}

async function handleRerouteClick(nodeId) {
  if (!rerouteEdgeId || !rerouteMode) return;
  const body = {};
  body[rerouteMode] = nodeId;
  try {
    await api(`/edge/${rerouteEdgeId}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
    showToast(`Edge ${rerouteMode} rerouted`, 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to reroute edge: ' + e.message, 'error');
  }
  rerouteEdgeId = null;
  rerouteMode = null;
  cancelLinkMode();
}

// ── Link / Edge Creation Mode ────────────────────

function startLinkMode(nodeId, direction) {
  linkMode = true;
  if (direction === 'from') {
    linkSourceId = nodeId;
    const label = cy.getElementById(nodeId).data('label') || nodeId;
    document.getElementById('link-mode-text').textContent = `Source: ${label} — click the target node...`;
  } else {
    linkSourceId = null;
    const label = cy.getElementById(nodeId).data('label') || nodeId;
    document.getElementById('link-mode-text').textContent = `Target: ${label} — click the source node...`;
    linkSourceId = '__target:' + nodeId;
  }
  document.getElementById('link-mode-banner').classList.add('visible');
  document.getElementById('btn-link-mode').classList.add('active');
}

function cancelLinkMode() {
  linkMode = false;
  linkSourceId = null;
  rerouteEdgeId = null;
  rerouteMode = null;
  document.getElementById('link-mode-banner').classList.remove('visible');
  document.getElementById('btn-link-mode').classList.remove('active');
}

function handleLinkClick(nodeId) {
  if (!linkMode) return;

  if (rerouteEdgeId && rerouteMode) {
    handleRerouteClick(nodeId);
    return;
  }

  if (linkSourceId && linkSourceId.startsWith('__target:')) {
    const targetId = linkSourceId.replace('__target:', '');
    showEdgeModal(nodeId, targetId);
  } else if (linkSourceId) {
    showEdgeModal(linkSourceId, nodeId);
  } else {
    linkSourceId = nodeId;
    const label = cy.getElementById(nodeId).data('label') || nodeId;
    document.getElementById('link-mode-text').textContent = `Source: ${label} — click the target node...`;
  }
}

function showEdgeModal(sourceId, targetId) {
  cancelLinkMode();
  const modal = document.getElementById('edge-modal');
  modal.classList.remove('hidden');
  modal.dataset.source = sourceId;
  modal.dataset.target = targetId;
  document.getElementById('edge-label').value = '';
  setTimeout(() => document.getElementById('edge-label').focus(), 100);
}

async function createEdge() {
  const modal = document.getElementById('edge-modal');
  const sourceId = modal.dataset.source;
  const targetId = modal.dataset.target;
  const label = document.getElementById('edge-label').value.trim();
  const edgeType = document.getElementById('edge-type').value;

  try {
    await api('/edge', {
      method: 'POST',
      body: JSON.stringify({
        source: sourceId,
        target: targetId,
        label: label,
        type: edgeType,
      }),
    });
    modal.classList.add('hidden');
    showToast('Edge created', 'success');
    await refreshGraph();
  } catch (e) {
    showToast('Failed to create edge: ' + e.message, 'error');
  }
}

// ── Sidebar Toggle ───────────────────────────────

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const app = document.getElementById('app');
  const collapsed = sidebar.classList.toggle('collapsed');
  app.classList.toggle('sidebar-open', !collapsed);
  if (cy) setTimeout(() => cy.resize(), 300);
}

document.getElementById('sidebar-toggle').addEventListener('click', toggleSidebar);

// ── Modal Helpers ────────────────────────────────

function openModal(id) {
  const modal = document.getElementById(id);
  modal.classList.remove('hidden');
  const firstInput = modal.querySelector('input:not([type=hidden]), textarea, select');
  if (firstInput) setTimeout(() => firstInput.focus(), 100);
}

function closeModalOnOverlayClick(e) {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
}

document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', closeModalOnOverlayClick);
});

// ── Event Listeners ──────────────────────────────

document.querySelectorAll('.chip[data-layer]').forEach(chip => {
  chip.addEventListener('click', () => {
    const layer = chip.dataset.layer;
    if (activeLayers.has(layer)) {
      activeLayers.delete(layer);
      chip.classList.remove('active');
    } else {
      activeLayers.add(layer);
      chip.classList.add('active');
    }
    applyAllFilters();
  });
});

let filterTimer = null;
function debouncedApplyAllFilters() {
  if (filterTimer) clearTimeout(filterTimer);
  filterTimer = setTimeout(() => applyAllFilters(), 200);
}

document.getElementById('search-input').addEventListener('input', debouncedApplyAllFilters);
document.getElementById('search-clear').addEventListener('click', () => {
  document.getElementById('search-input').value = '';
  applyAllFilters();
});

document.getElementById('type-filter').addEventListener('change', () => applyAllFilters());
document.getElementById('status-filter').addEventListener('change', () => applyAllFilters());

document.getElementById('layout-select').addEventListener('change', (e) => {
  document.getElementById('btn-tree-view').classList.remove('active');
  runLayout(e.target.value);
});

document.getElementById('detail-close').addEventListener('click', closeDetail);

document.querySelectorAll('.detail-tab').forEach(tab => {
  tab.addEventListener('click', async () => {
    if (!selectedNodeId) return;
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');

    const body = document.getElementById('detail-body');
    body.innerHTML = '<div class="loading-placeholder"><span class="spinner"></span> Loading...</div>';

    const node = await api(`/node/${selectedNodeId}`);
    const which = tab.dataset.tab;
    if (which === 'context') showContextTab(node);
    else if (which === 'code') showCodeTab(node);
    else if (which === 'edit') showEditTab(node);
    else if (which === 'metadata') showMetadataTab(node);
    else if (which === 'impact') showImpactTab(node);
  });
});

document.querySelectorAll('.sidebar-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.stab;
    document.getElementById('sidebar-nodes').style.display = target === 'nodes' ? '' : 'none';
    document.getElementById('sidebar-adrs').style.display = target === 'adrs' ? '' : 'none';
    document.getElementById('sidebar-chat').style.display = target === 'chat' ? 'flex' : 'none';
    document.getElementById('sidebar-filter-box').style.display = target === 'nodes' ? '' : 'none';

    const app = document.getElementById('app');
    if (target === 'chat') {
      app.classList.add('sidebar-chat-active');
      app.classList.add('sidebar-open');
      document.getElementById('sidebar').classList.remove('collapsed');
    } else {
      app.classList.remove('sidebar-chat-active');
    }
    if (cy) setTimeout(() => cy.resize(), 300);
  });
});

document.getElementById('btn-add-node').addEventListener('click', () => openModal('add-node-modal'));

document.getElementById('modal-cancel').addEventListener('click', () => {
  document.getElementById('add-node-modal').classList.add('hidden');
  pendingNodePosition = null;
});

document.getElementById('modal-create').addEventListener('click', addNode);

document.getElementById('btn-fit').addEventListener('click', () => {
  cy.fit(undefined, 40);
});

document.getElementById('btn-export').addEventListener('click', () => openModal('export-modal'));

document.getElementById('export-cancel').addEventListener('click', () => {
  document.getElementById('export-modal').classList.add('hidden');
});

document.getElementById('export-confirm').addEventListener('click', doExport);

document.getElementById('btn-git-status').addEventListener('click', toggleGitStatus);

document.getElementById('btn-git-changed').addEventListener('click', toggleGitChanged);

document.getElementById('btn-live-tracking').addEventListener('click', toggleLiveTracking);

document.getElementById('btn-tree-view').addEventListener('click', () => {
  document.getElementById('btn-tree-view').classList.toggle('active');
  runTreeView();
});

document.getElementById('btn-folder-view').addEventListener('click', () => toggleFolderView());

document.getElementById('btn-focus').addEventListener('click', () => {
  if (focusActive) {
    clearFocus();
  } else {
    applyFocusFilter();
  }
});

document.getElementById('btn-add-adr').addEventListener('click', () => openADRModal());

document.getElementById('adr-modal-cancel').addEventListener('click', () => {
  document.getElementById('adr-modal').classList.add('hidden');
});

document.getElementById('adr-modal-save').addEventListener('click', saveADR);

document.getElementById('help-close').addEventListener('click', () => {
  document.getElementById('keyboard-help').classList.add('hidden');
});

document.getElementById('btn-help').addEventListener('click', () => {
  document.getElementById('keyboard-help').classList.toggle('hidden');
});

document.getElementById('btn-link-mode').addEventListener('click', () => {
  if (linkMode) {
    cancelLinkMode();
  } else {
    linkMode = true;
    linkSourceId = null;
    document.getElementById('link-mode-text').textContent = 'Click the source node...';
    document.getElementById('link-mode-banner').classList.add('visible');
    document.getElementById('btn-link-mode').classList.add('active');
  }
});

document.getElementById('edge-modal-cancel').addEventListener('click', () => {
  document.getElementById('edge-modal').classList.add('hidden');
});

document.getElementById('edge-modal-create').addEventListener('click', createEdge);

document.getElementById('btn-toolbar-more').addEventListener('click', () => {
  document.querySelectorAll('.toolbar-collapsible').forEach(el => {
    el.classList.toggle('expanded');
  });
});

// Zoom controls
document.getElementById('zoom-in').addEventListener('click', () => {
  if (cy) cy.zoom({ level: cy.zoom() * 1.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
});

document.getElementById('zoom-out').addEventListener('click', () => {
  if (cy) cy.zoom({ level: cy.zoom() / 1.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
});

document.getElementById('zoom-fit').addEventListener('click', () => {
  if (cy) cy.fit(undefined, 40);
});

// Close context menus on click outside
document.addEventListener('click', (e) => {
  const menu = document.getElementById('context-menu');
  if (!menu.classList.contains('hidden') && !menu.contains(e.target)) {
    hideContextMenu();
  }
  const edgeMenu = document.getElementById('edge-context-menu');
  if (edgeMenu && !edgeMenu.classList.contains('hidden') && !edgeMenu.contains(e.target)) {
    hideEdgeContextMenu();
  }
  const canvasMenu = document.getElementById('canvas-context-menu');
  if (canvasMenu && !canvasMenu.classList.contains('hidden') && !canvasMenu.contains(e.target)) {
    hideCanvasContextMenu();
  }
});

// Edge context menu actions
document.querySelectorAll('.edge-context-menu-item').forEach(item => {
  item.addEventListener('click', async () => {
    const action = item.dataset.action;
    const eid = contextMenuEdgeId;
    hideEdgeContextMenu();
    if (!eid) return;
    if (action === 'edit-edge') showEditEdgeModal(eid);
    else if (action === 'delete-edge') deleteEdge(eid);
    else if (action === 'permanent-delete-edge') permanentDeleteEdge(eid);
    else if (action === 'restore-edge') restoreEdge(eid);
    else if (action === 'reroute-source') startEdgeReroute(eid, 'source');
    else if (action === 'reroute-target') startEdgeReroute(eid, 'target');
  });
});

document.getElementById('edit-edge-modal-cancel').addEventListener('click', () => {
  document.getElementById('edit-edge-modal').classList.add('hidden');
});

document.getElementById('code-fullscreen-close').addEventListener('click', () => {
  document.getElementById('code-fullscreen-modal').classList.add('hidden');
});

document.getElementById('code-fullscreen-modal').addEventListener('click', (e) => {
  if (e.target.id === 'code-fullscreen-modal') {
    document.getElementById('code-fullscreen-modal').classList.add('hidden');
  }
});

document.getElementById('edit-edge-modal-save').addEventListener('click', saveEdgeEdit);

// ── Keyboard shortcuts ───────────────────────────

document.addEventListener('keydown', (e) => {
  const active = document.activeElement;
  const isInput = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT');

  if (e.key === 'Escape') {
    if (focusActive) {
      clearFocus();
      return;
    }
    document.getElementById('add-node-modal').classList.add('hidden');
    document.getElementById('export-modal').classList.add('hidden');
    document.getElementById('adr-modal').classList.add('hidden');
    document.getElementById('edge-modal').classList.add('hidden');
    document.getElementById('edit-edge-modal').classList.add('hidden');
    document.getElementById('keyboard-help').classList.add('hidden');
    document.getElementById('code-fullscreen-modal').classList.add('hidden');
    hideContextMenu();
    hideEdgeContextMenu();
    hideCanvasContextMenu();
    if (typeof hideFileDropdown === 'function') hideFileDropdown();
    if (linkMode) cancelLinkMode();
    closeDetail();
    if (isInput) active.blur();
    return;
  }

  if (isInput) return;

  if (e.key === '/') {
    e.preventDefault();
    document.getElementById('search-input').focus();
  } else if (e.key === 'F' && e.shiftKey) {
    e.preventDefault();
    applyFocusFilter();
  } else if (e.key === 'd' || e.key === 'D') {
    toggleFolderView();
  } else if (e.key === 'f' || e.key === 'F') {
    cy.fit(undefined, 40);
  } else if (e.key === 'n' || e.key === 'N') {
    openModal('add-node-modal');
  } else if (e.key === 'l' || e.key === 'L') {
    if (linkMode) {
      cancelLinkMode();
    } else {
      document.getElementById('btn-link-mode').click();
    }
  } else if (e.key === 'e' || e.key === 'E') {
    openModal('export-modal');
  } else if (e.key === 'g' || e.key === 'G') {
    toggleGitStatus();
  } else if (e.key === 'c' || e.key === 'C') {
    toggleGitChanged();
  } else if (e.key === 'w' || e.key === 'W') {
    toggleLiveTracking();
  } else if (e.key === 'h' || e.key === 'H') {
    runTreeView();
  } else if (e.key === 's' || e.key === 'S') {
    toggleSidebar();
  } else if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
    const help = document.getElementById('keyboard-help');
    help.classList.toggle('hidden');
  } else if (e.key === '1') {
    document.getElementById('layout-select').value = 'cose-bilkent';
    runLayout('cose-bilkent');
  } else if (e.key === '2') {
    document.getElementById('layout-select').value = 'breadthfirst';
    runLayout('breadthfirst');
  } else if (e.key === '3') {
    document.getElementById('layout-select').value = 'circle';
    runLayout('circle');
  } else if (e.key === '4') {
    document.getElementById('layout-select').value = 'grid';
    runLayout('grid');
  }
});

// ── Vibe Code Chat ───────────────────────────────

function activateChatTab() {
  document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  const chatTab = document.querySelector('.sidebar-tab[data-stab="chat"]');
  if (chatTab) chatTab.classList.add('active');

  document.getElementById('sidebar-nodes').style.display = 'none';
  document.getElementById('sidebar-adrs').style.display = 'none';
  document.getElementById('sidebar-chat').style.display = 'flex';
  document.getElementById('sidebar-filter-box').style.display = 'none';

  const app = document.getElementById('app');
  app.classList.add('sidebar-chat-active', 'sidebar-open');
  document.getElementById('sidebar').classList.remove('collapsed');
  if (cy) setTimeout(() => cy.resize(), 300);

  if (document.getElementById('chat-messages').children.length === 0) {
    showChatWelcome();
  }
}

let _chatAuthStatus = null;

async function checkChatAuth() {
  try {
    const res = await fetch('/api/chat/status');
    if (!res.ok) return { installed: false, authenticated: false, user: null };
    return await res.json();
  } catch {
    return { installed: false, authenticated: false, user: null };
  }
}

function setChatInputEnabled(enabled) {
  const input = document.getElementById('chat-input');
  const send = document.getElementById('chat-send');
  if (input) {
    input.disabled = !enabled;
    input.placeholder = enabled
      ? 'Ask anything... Use @ to reference files'
      : 'Login required to use Vibe Code';
  }
  if (send) send.disabled = !enabled;
}

async function showChatWelcome() {
  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML = `
    <div class="chat-welcome">
      <div class="chat-welcome-icon">&#x2728;</div>
      <h3>Vibe Code</h3>
      <p>Checking connection...</p>
      <div class="chat-auth-spinner"></div>
    </div>
  `;
  setChatInputEnabled(false);

  _chatAuthStatus = await checkChatAuth();

  if (!_chatAuthStatus.installed) {
    msgs.innerHTML = `
      <div class="chat-welcome">
        <div class="chat-welcome-icon chat-icon-warn">&#x26A0;</div>
        <h3>Cursor CLI Required</h3>
        <p>Vibe Code uses the Cursor CLI to power AI coding. It doesn't seem to be installed yet.</p>
        <div class="chat-setup-steps">
          <div class="chat-step"><span class="chat-step-num">1</span> Visit <a href="https://cursor.com/cli" target="_blank">cursor.com/cli</a> to install the CLI</div>
          <div class="chat-step"><span class="chat-step-num">2</span> Make sure <code>agent</code> is available on your PATH</div>
          <div class="chat-step"><span class="chat-step-num">3</span> Restart the Architect viewer</div>
        </div>
        <button class="chat-btn-retry" onclick="showChatWelcome()">Retry Connection</button>
      </div>
    `;
    setChatInputEnabled(false);
  } else if (!_chatAuthStatus.authenticated) {
    msgs.innerHTML = `
      <div class="chat-welcome">
        <div class="chat-welcome-icon">&#x1F511;</div>
        <h3>Sign In to Cursor</h3>
        <p>Vibe Code needs your Cursor account to power AI coding. Sign in to get started.</p>
        <button class="chat-btn-login" onclick="startChatLogin()">
          <span class="chat-login-icon">&#x1F680;</span> Sign in with Cursor
        </button>
        <p class="chat-login-hint">This will open your browser to authenticate</p>
      </div>
    `;
    setChatInputEnabled(false);
  } else {
    msgs.innerHTML = `
      <div class="chat-welcome">
        <div class="chat-welcome-icon">&#x2728;</div>
        <h3>Vibe Code</h3>
        <p>AI-powered coding assistant with full architecture context.</p>
        <div class="chat-auth-badge">
          <span class="chat-auth-dot"></span> Signed in${_chatAuthStatus.user ? ' as <strong>' + escapeHtml(_chatAuthStatus.user) + '</strong>' : ''}
        </div>
        <div class="chat-welcome-tips">
          <div class="chat-tip">Use <strong>@</strong> to reference files</div>
          <div class="chat-tip">Ask questions about the architecture</div>
          <div class="chat-tip">Request code changes in context</div>
        </div>
      </div>
    `;
    setChatInputEnabled(true);
  }
}

async function startChatLogin() {
  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML = `
    <div class="chat-welcome">
      <div class="chat-welcome-icon">&#x1F310;</div>
      <h3>Signing In...</h3>
      <p>A browser window should open for you to authenticate with Cursor.</p>
      <div class="chat-auth-spinner"></div>
      <p class="chat-login-status">Waiting for authentication...</p>
      <button class="chat-btn-cancel" onclick="showChatWelcome()">Cancel</button>
    </div>
  `;
  setChatInputEnabled(false);

  try {
    const res = await fetch('/api/chat/login', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Login failed');
    }
  } catch (e) {
    msgs.innerHTML = `
      <div class="chat-welcome">
        <div class="chat-welcome-icon chat-icon-warn">&#x26A0;</div>
        <h3>Login Failed</h3>
        <p>${escapeHtml(e.message)}</p>
        <button class="chat-btn-retry" onclick="startChatLogin()">Try Again</button>
      </div>
    `;
    return;
  }

  pollLoginStatus();
}

async function pollLoginStatus() {
  const statusEl = document.querySelector('.chat-login-status');
  let attempts = 0;
  const maxAttempts = 60;

  const poll = async () => {
    attempts++;
    if (attempts > maxAttempts) {
      const msgs = document.getElementById('chat-messages');
      msgs.innerHTML = `
        <div class="chat-welcome">
          <div class="chat-welcome-icon chat-icon-warn">&#x23F0;</div>
          <h3>Login Timed Out</h3>
          <p>The authentication didn't complete in time. Please try again.</p>
          <button class="chat-btn-retry" onclick="startChatLogin()">Try Again</button>
        </div>
      `;
      return;
    }

    const status = await checkChatAuth();
    if (status.authenticated) {
      _chatAuthStatus = status;
      const msgs = document.getElementById('chat-messages');
      msgs.innerHTML = `
        <div class="chat-welcome">
          <div class="chat-welcome-icon chat-icon-success">&#x2705;</div>
          <h3>You're In!</h3>
          <p>Successfully signed in${status.user ? ' as <strong>' + escapeHtml(status.user) + '</strong>' : ''}.</p>
          <div class="chat-welcome-tips">
            <div class="chat-tip">Use <strong>@</strong> to reference files</div>
            <div class="chat-tip">Ask questions about the architecture</div>
            <div class="chat-tip">Request code changes in context</div>
          </div>
        </div>
      `;
      setChatInputEnabled(true);
      return;
    }

    if (statusEl) {
      const dots = '.'.repeat((attempts % 3) + 1);
      statusEl.textContent = `Waiting for authentication${dots}`;
    }
    setTimeout(poll, 2000);
  };

  setTimeout(poll, 2000);
}

function renderChatMessage(msg) {
  const msgs = document.getElementById('chat-messages');
  const welcome = msgs.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `chat-msg chat-msg-${msg.role}`;

  const avatar = document.createElement('div');
  avatar.className = 'chat-avatar';
  avatar.textContent = msg.role === 'user' ? 'U' : 'A';
  div.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';

  if (msg.role === 'user') {
    bubble.textContent = msg.content;
  } else {
    bubble.innerHTML = renderMarkdown(msg.content || '');
    highlightCodeBlocks(bubble);
  }

  div.appendChild(bubble);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

function renderMarkdown(text) {
  if (!text) return '';
  try {
    if (typeof marked !== 'undefined') {
      return marked.parse(text, { breaks: true });
    }
  } catch (e) {}
  return escapeHtml(text).replace(/\n/g, '<br>');
}

function highlightCodeBlocks(container) {
  if (typeof Prism === 'undefined') return;
  container.querySelectorAll('pre code').forEach(block => {
    const parent = block.parentElement;
    const classes = block.className || '';
    const langMatch = classes.match(/language-(\w+)/);
    if (langMatch) {
      parent.classList.add(`language-${langMatch[1]}`);
    }
    try { Prism.highlightElement(block); } catch (e) {}
  });
}

function appendToolChip(msgEl, info) {
  const bubble = msgEl.querySelector('.chat-bubble');
  if (!bubble) return;

  const chip = document.createElement('div');
  const isWrite = info.tool === 'write';
  const isRead = info.tool === 'read';
  chip.className = `chat-tool-chip ${isWrite ? 'tool-write' : isRead ? 'tool-read' : 'tool-other'}`;

  const icon = isWrite ? '&#x270E;' : isRead ? '&#x1F4C4;' : '&#x2699;';
  const fname = info.path ? info.path.split(/[\\/]/).pop() : info.tool;
  let label = '';

  if (info.type === 'tool_start') {
    label = isWrite ? `Writing ${fname}...` : isRead ? `Reading ${fname}...` : `${info.tool}...`;
  } else {
    const lines = info.lines ? ` (${info.lines} lines)` : '';
    label = isWrite ? `Wrote ${fname}${lines}` : isRead ? `Read ${fname}${lines}` : `${info.tool} done`;
    if (isWrite && info.path) {
      _chatFilesWritten.push(info.path);
    }
  }

  chip.innerHTML = `<span class="tool-icon">${icon}</span> ${escapeHtml(label)}`;
  bubble.appendChild(chip);

  const msgs = document.getElementById('chat-messages');
  msgs.scrollTop = msgs.scrollHeight;
}

async function sendChatMessage(message) {
  if (_chatStreaming || !message.trim()) return;

  _chatHistory.push({ role: 'user', content: message });
  renderChatMessage({ role: 'user', content: message });
  _chatFilesWritten = [];

  const input = document.getElementById('chat-input');
  input.value = '';
  input.style.height = 'auto';

  _chatStreaming = true;
  document.getElementById('chat-send').style.display = 'none';
  document.getElementById('chat-stop').style.display = 'flex';

  _chatAbortController = new AbortController();

  const body = {
    message,
    referenced_files: [..._chatReferencedFiles],
    node_id: _chatContextNodeId || selectedNodeId || null,
    history: _chatHistory.slice(-10),
  };

  const assistantMsg = { role: 'assistant', content: '' };
  const msgEl = renderChatMessage(assistantMsg);
  const bubble = msgEl.querySelector('.chat-bubble');
  bubble.classList.add('chat-streaming-cursor');

  let fullText = '';

  try {
    let res;
    try {
      res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: _chatAbortController.signal,
      });
    } catch (fetchErr) {
      if (fetchErr.name === 'AbortError') throw fetchErr;
      throw new Error('Could not connect to the Architect server. Is it still running?');
    }

    if (!res.ok) {
      let errMsg = `Server error (${res.status})`;
      let errDetail = '';
      try {
        const errBody = await res.json();
        errDetail = errBody.detail || '';
      } catch {}

      if (res.status === 401 || errDetail === 'cursor_cli_not_authenticated') {
        _chatAuthStatus = { installed: true, authenticated: false, user: null };
        bubble.classList.remove('chat-streaming-cursor');
        bubble.innerHTML = `
          <div class="chat-inline-auth">
            <p>You need to sign in to Cursor to use Vibe Code.</p>
            <button class="chat-btn-login chat-btn-sm" onclick="_chatHistory.pop(); this.closest('.chat-msg').remove(); startChatLogin();">
              <span class="chat-login-icon">&#x1F680;</span> Sign In
            </button>
          </div>
        `;
        _chatStreaming = false;
        document.getElementById('chat-send').style.display = 'flex';
        document.getElementById('chat-stop').style.display = 'none';
        setChatInputEnabled(false);
        return;
      } else if (res.status === 503 || errDetail === 'cursor_cli_not_installed') {
        _chatAuthStatus = { installed: false, authenticated: false, user: null };
        bubble.classList.remove('chat-streaming-cursor');
        bubble.innerHTML = `
          <div class="chat-inline-auth">
            <p>Cursor CLI is not installed. Install it from <a href="https://cursor.com/cli" target="_blank">cursor.com/cli</a> and make sure <code>agent</code> is on your PATH.</p>
            <button class="chat-btn-retry chat-btn-sm" onclick="_chatHistory.pop(); this.closest('.chat-msg').remove(); showChatWelcome();">Check Again</button>
          </div>
        `;
        _chatStreaming = false;
        document.getElementById('chat-send').style.display = 'flex';
        document.getElementById('chat-stop').style.display = 'none';
        setChatInputEnabled(false);
        return;
      }
      throw new Error(errMsg);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);
          let data;
          try { data = JSON.parse(dataStr); } catch { continue; }

          if (eventType === 'session') {
            _chatSessionId = data.session_id;
          } else if (eventType === 'assistant') {
            fullText += data.text || '';
            bubble.innerHTML = renderMarkdown(fullText);
            highlightCodeBlocks(bubble);
            if (_chatStreaming) bubble.classList.add('chat-streaming-cursor');
            const msgs = document.getElementById('chat-messages');
            msgs.scrollTop = msgs.scrollHeight;
          } else if (eventType === 'tool_start') {
            appendToolChip(msgEl, { ...data, type: 'tool_start' });
          } else if (eventType === 'tool_done') {
            appendToolChip(msgEl, { ...data, type: 'tool_done' });
          } else if (eventType === 'done') {
            fullText = data.result || fullText;
          } else if (eventType === 'error') {
            const errContent = data.message || 'Unknown error from agent';
            if (!fullText) {
              fullText = `**Error:** ${errContent}`;
            } else {
              fullText += `\n\n**Error:** ${errContent}`;
            }
            bubble.innerHTML = renderMarkdown(fullText);
            highlightCodeBlocks(bubble);
          }
          eventType = '';
        }
      }
    }

    if (!fullText) {
      fullText = '*No response received from the AI agent. Make sure Cursor CLI is authenticated (`agent login`).*';
      bubble.innerHTML = renderMarkdown(fullText);
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      fullText += '\n\n*[Generation stopped]*';
    } else {
      const msg = (err.message || '').toLowerCase().includes('network')
        ? 'Connection to the AI agent was lost. The Cursor CLI may have crashed or timed out. Please try again.'
        : err.message;
      fullText = fullText
        ? fullText + `\n\n**Error:** ${msg}`
        : `**Error:** ${msg}`;
    }
    bubble.innerHTML = renderMarkdown(fullText);
    highlightCodeBlocks(bubble);
  } finally {
    bubble.classList.remove('chat-streaming-cursor');
    _chatStreaming = false;
    _chatSessionId = null;
    _chatAbortController = null;
    document.getElementById('chat-send').style.display = 'flex';
    document.getElementById('chat-stop').style.display = 'none';

    assistantMsg.content = fullText;
    _chatHistory.push(assistantMsg);

    if (_chatFilesWritten.length > 0 && selectedNodeId) {
      const graph = await api('/graph');
      const node = graph.nodes.find(n => n.data && n.data.id === selectedNodeId);
      if (node && node.data.file_path) {
        const nodeFileNorm = node.data.file_path.replace(/\\/g, '/');
        const wasModified = _chatFilesWritten.some(f =>
          f.replace(/\\/g, '/').endsWith(nodeFileNorm) ||
          nodeFileNorm.endsWith(f.replace(/\\/g, '/'))
        );
        if (wasModified) {
          const activeTab = document.querySelector('.detail-tab.active');
          if (activeTab && activeTab.dataset.tab === 'code') {
            const detail = await api(`/node/${selectedNodeId}`);
            showCodeTab(detail);
            showToast('File updated by AI — code view refreshed', 'success');
          }
        }
      }
    }
  }
}

function stopChatGeneration() {
  if (_chatAbortController) {
    _chatAbortController.abort();
  }
  if (_chatSessionId) {
    fetch('/api/chat/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: _chatSessionId }),
    }).catch(() => {});
  }
}

// ── File Reference System (@-mentions) ───────────

async function fetchChatFiles() {
  if (_chatFileCache) return _chatFileCache;
  try {
    const res = await fetch('/api/chat/files');
    _chatFileCache = await res.json();
  } catch {
    _chatFileCache = [];
  }
  return _chatFileCache;
}

function addFileReference(fpath) {
  if (_chatReferencedFiles.includes(fpath)) return;
  _chatReferencedFiles.push(fpath);
  renderFileChips();
}

function removeFileReference(fpath) {
  _chatReferencedFiles = _chatReferencedFiles.filter(f => f !== fpath);
  renderFileChips();
}

function renderFileChips() {
  const container = document.getElementById('chat-file-chips');
  container.innerHTML = '';
  for (const fpath of _chatReferencedFiles) {
    const chip = document.createElement('span');
    chip.className = 'chat-file-chip';
    const fname = fpath.split('/').pop();
    chip.innerHTML = `<span class="chip-name" title="${escapeHtml(fpath)}">@${escapeHtml(fname)}</span><span class="chip-remove">&times;</span>`;
    chip.querySelector('.chip-remove').addEventListener('click', () => removeFileReference(fpath));
    container.appendChild(chip);
  }
}

function showFileDropdown(filter) {
  const dd = document.getElementById('chat-file-dropdown');
  fetchChatFiles().then(files => {
    const q = filter.toLowerCase();
    const matches = files.filter(f => f.toLowerCase().includes(q)).slice(0, 30);
    _chatDropdownIdx = -1;

    if (matches.length === 0) {
      dd.innerHTML = '<div class="chat-file-no-results">No files found</div>';
    } else {
      dd.innerHTML = matches.map((f, i) => {
        const fname = f.split('/').pop();
        const dir = f.substring(0, f.length - fname.length);
        return `<div class="chat-file-option" data-path="${escapeHtml(f)}" data-idx="${i}"><span style="color:var(--text-dim)">${escapeHtml(dir)}</span>${escapeHtml(fname)}</div>`;
      }).join('');
    }

    dd.classList.add('visible');

    dd.querySelectorAll('.chat-file-option').forEach(opt => {
      opt.addEventListener('click', () => {
        const path = opt.dataset.path;
        addFileReference(path);
        hideFileDropdown();
        removeAtQuery();
      });
    });
  });
}

function hideFileDropdown() {
  const dd = document.getElementById('chat-file-dropdown');
  dd.classList.remove('visible');
  dd.innerHTML = '';
  _chatDropdownIdx = -1;
}

function removeAtQuery() {
  const input = document.getElementById('chat-input');
  const val = input.value;
  const atIdx = val.lastIndexOf('@');
  if (atIdx !== -1) {
    input.value = val.substring(0, atIdx);
  }
  input.focus();
}

function navigateFileDropdown(direction) {
  const dd = document.getElementById('chat-file-dropdown');
  const opts = dd.querySelectorAll('.chat-file-option');
  if (!opts.length) return;

  opts.forEach(o => o.classList.remove('selected'));
  _chatDropdownIdx += direction;
  if (_chatDropdownIdx < 0) _chatDropdownIdx = opts.length - 1;
  if (_chatDropdownIdx >= opts.length) _chatDropdownIdx = 0;

  opts[_chatDropdownIdx].classList.add('selected');
  opts[_chatDropdownIdx].scrollIntoView({ block: 'nearest' });
}

function selectFileDropdownItem() {
  const dd = document.getElementById('chat-file-dropdown');
  const selected = dd.querySelector('.chat-file-option.selected');
  if (selected) {
    addFileReference(selected.dataset.path);
    hideFileDropdown();
    removeAtQuery();
  }
}

// ── Chat Event Listeners ─────────────────────────

document.getElementById('chat-send').addEventListener('click', () => {
  const input = document.getElementById('chat-input');
  sendChatMessage(input.value);
});

document.getElementById('chat-stop').addEventListener('click', () => {
  stopChatGeneration();
});

document.getElementById('chat-input').addEventListener('keydown', (e) => {
  const dd = document.getElementById('chat-file-dropdown');
  const ddVisible = dd.classList.contains('visible');

  if (ddVisible) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      navigateFileDropdown(1);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      navigateFileDropdown(-1);
      return;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (_chatDropdownIdx >= 0) {
        e.preventDefault();
        selectFileDropdownItem();
        return;
      }
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      hideFileDropdown();
      return;
    }
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage(e.target.value);
  }
});

document.getElementById('chat-input').addEventListener('input', (e) => {
  const input = e.target;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';

  const val = input.value;
  const cursorPos = input.selectionStart;
  const textBeforeCursor = val.substring(0, cursorPos);
  const atIdx = textBeforeCursor.lastIndexOf('@');

  if (atIdx !== -1 && (atIdx === 0 || /\s/.test(textBeforeCursor[atIdx - 1]))) {
    const query = textBeforeCursor.substring(atIdx + 1);
    if (!/\s/.test(query)) {
      showFileDropdown(query);
      return;
    }
  }
  hideFileDropdown();
});

// ── Init ─────────────────────────────────────────

loadGraph();
