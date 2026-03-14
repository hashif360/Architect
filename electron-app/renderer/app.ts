declare const architect: any;

import { PanelManager } from './layout/panel-manager';
import { SplitPanel } from './layout/split-panel';
import { FileTree } from './panels/explorer/file-tree';
import { EditorPanel } from './panels/editor/editor-panel';
import { TerminalPanel } from './panels/terminal/terminal-panel';
import { GraphPanel } from './panels/graph/graph-panel';
import { ChatPanel } from './panels/chat/chat-panel';
import { SetupWizard } from './panels/setup-wizard';

let panelManager: PanelManager;
let fileTree: FileTree;
let editorPanel: EditorPanel;
let terminalPanel: TerminalPanel;
let graphPanel: GraphPanel;
let chatPanel: ChatPanel;

let projectDir: string = '';
let backendUrl: string = '';

function $(id: string): HTMLElement {
  return document.getElementById(id)!;
}

async function init(): Promise<void> {
  panelManager = new PanelManager();

  // Editor panel
  editorPanel = new EditorPanel(
    $('monaco-container'),
    $('tab-list'),
    (info) => {
      $('status-line-col').textContent = `Ln ${info.line}, Col ${info.col}`;
      $('status-language').textContent = info.language;
    }
  );

  // File tree
  fileTree = new FileTree($('file-tree'), (filePath) => {
    editorPanel.openFile(filePath);
    const titlePath = $('titlebar-path');
    if (titlePath) {
      const relative = filePath.replace(projectDir, '').replace(/^[\\/]/, '');
      titlePath.textContent = relative;
    }
  });

  // Terminal panel
  terminalPanel = new TerminalPanel($('terminal-container'), $('terminal-tabs'));

  // Graph panel
  graphPanel = new GraphPanel($('graph-container'), (filePath) => {
    editorPanel.openFile(filePath);
  });

  // Chat panel
  chatPanel = new ChatPanel(
    $('chat-messages') as HTMLElement,
    $('chat-input') as HTMLTextAreaElement,
    $('btn-send-chat')
  );

  // Set up resize handles
  new SplitPanel({
    handle: $('resize-left'),
    target: $('sidebar-left'),
    direction: 'vertical',
    side: 'before',
    minSize: 180,
    maxSize: 500,
  });

  new SplitPanel({
    handle: $('resize-right'),
    target: $('sidebar-right'),
    direction: 'vertical',
    side: 'after',
    minSize: 250,
    maxSize: 500,
  });

  new SplitPanel({
    handle: $('resize-bottom'),
    target: $('panel-bottom'),
    direction: 'horizontal',
    side: 'after',
    minSize: 120,
    maxSize: 600,
  });

  setupPanelToggles();
  setupActivityBar();
  setupMenuHandlers();
  setupSplashScreen();
  applyPanelState();
}

function setupPanelToggles(): void {
  panelManager.onChange((panel, visible) => {
    switch (panel) {
      case 'explorer':
        toggleElement($('sidebar-left'), visible);
        toggleElement($('resize-left'), visible);
        break;
      case 'terminal':
        toggleElement($('panel-bottom'), visible);
        toggleElement($('resize-bottom'), visible);
        if (visible && !terminalPanel.hasInstances()) {
          terminalPanel.createTerminal();
        }
        if (visible) {
          setTimeout(() => terminalPanel.fitAll(), 100);
        }
        break;
      case 'chat':
        toggleElement($('sidebar-right'), visible);
        toggleElement($('resize-right'), visible);
        break;
      case 'graph':
        if (visible) {
          graphPanel.show();
        } else {
          graphPanel.hide();
          const monaco = $('monaco-container');
          if (editorPanel.hasOpenTabs()) {
            monaco.classList.remove('hidden');
          } else {
            $('welcome-tab').classList.remove('hidden');
          }
        }
        break;
    }
    updateActivityBar();
    setTimeout(() => editorPanel.layout(), 50);
  });
}

function setupActivityBar(): void {
  const buttons = document.querySelectorAll('.activity-btn[data-panel]');
  buttons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const panel = (btn as HTMLElement).dataset.panel as any;
      panelManager.toggle(panel);
    });
  });
}

function updateActivityBar(): void {
  const buttons = document.querySelectorAll('.activity-btn[data-panel]');
  buttons.forEach((btn) => {
    const panel = (btn as HTMLElement).dataset.panel as any;
    btn.classList.toggle('active', panelManager.isVisible(panel));
  });
}

function setupMenuHandlers(): void {
  architect.on('menu:toggle-panel', (panel: string) => {
    panelManager.toggle(panel as any);
  });

  architect.on('menu:save', () => {
    editorPanel.saveCurrentFile();
  });

  architect.on('menu:open-folder', async () => {
    const folder = await architect.openFolder();
    if (folder) {
      await openProject(folder);
    }
  });
}

function setupSplashScreen(): void {
  const wizard = new SetupWizard(() => {
    $('splash-status').innerHTML = '';
  });

  if (wizard.isNeeded()) {
    wizard.show($('splash-status'));
  }

  $('btn-open-folder').addEventListener('click', async () => {
    const folder = await architect.openFolder();
    if (folder) {
      await openProject(folder);
    }
  });
}

async function openProject(folder: string): Promise<void> {
  projectDir = folder;

  $('splash-screen').classList.add('hidden');
  $('ide-container').classList.remove('hidden');

  const folderName = folder.replace(/\\/g, '/').split('/').pop() || folder;
  $('project-name').textContent = folderName;
  $('status-project').textContent = folderName;

  await fileTree.setRoot(folder);
  terminalPanel.setProjectDir(folder);
  chatPanel.setProjectDir(folder);

  // Start backend
  $('splash-status').textContent = 'Starting backend...';
  try {
    backendUrl = await architect.getBackendUrl();
    if (backendUrl) {
      graphPanel.setBackendUrl(backendUrl);
      chatPanel.setBackendUrl(backendUrl);
      ($('backend-dot') as HTMLElement).classList.add('active');
      $('status-backend').title = `Backend: ${backendUrl}`;
    }
  } catch (e) {
    console.warn('Backend not available:', e);
  }

  // Watch for file system changes
  architect.watchDir(folder);
  architect.on('fs:change', () => {
    fileTree.render();
  });

  setTimeout(() => editorPanel.layout(), 100);
}

function toggleElement(el: HTMLElement, visible: boolean): void {
  el.classList.toggle('hidden', !visible);
}

function applyPanelState(): void {
  toggleElement($('sidebar-left'), panelManager.isVisible('explorer'));
  toggleElement($('resize-left'), panelManager.isVisible('explorer'));
  toggleElement($('panel-bottom'), panelManager.isVisible('terminal'));
  toggleElement($('resize-bottom'), panelManager.isVisible('terminal'));
  toggleElement($('sidebar-right'), panelManager.isVisible('chat'));
  toggleElement($('resize-right'), panelManager.isVisible('chat'));
  updateActivityBar();
}

// New terminal button
document.addEventListener('DOMContentLoaded', () => {
  $('btn-new-terminal')?.addEventListener('click', () => {
    terminalPanel.createTerminal();
  });

  $('btn-close-panel')?.addEventListener('click', () => {
    panelManager.hide('terminal');
  });

  window.addEventListener('resize', () => {
    editorPanel.layout();
    terminalPanel.fitAll();
  });

  init();
});
