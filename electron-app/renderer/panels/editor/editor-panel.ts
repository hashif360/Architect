declare const architect: any;

import * as monaco from 'monaco-editor';

interface EditorTab {
  path: string;
  name: string;
  model: monaco.editor.ITextModel;
  viewState: monaco.editor.ICodeEditorViewState | null;
  modified: boolean;
  originalContent: string;
}

const EXT_LANGUAGE: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
  py: 'python', json: 'json', md: 'markdown', html: 'html', css: 'css',
  scss: 'scss', sql: 'sql', yaml: 'yaml', yml: 'yaml', toml: 'ini',
  xml: 'xml', sh: 'shell', bash: 'shell', ps1: 'powershell', bat: 'bat',
  rs: 'rust', go: 'go', java: 'java', rb: 'ruby', php: 'php',
  c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp', cs: 'csharp',
  swift: 'swift', kt: 'kotlin', r: 'r', lua: 'lua',
  dockerfile: 'dockerfile', graphql: 'graphql',
};

function detectLanguage(filePath: string): string {
  const name = filePath.replace(/\\/g, '/').split('/').pop() || '';
  if (name === 'Dockerfile') return 'dockerfile';
  if (name === 'Makefile') return 'makefile';
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return EXT_LANGUAGE[ext] || 'plaintext';
}

export class EditorPanel {
  private container: HTMLElement;
  private tabList: HTMLElement;
  private editor: monaco.editor.IStandaloneCodeEditor | null = null;
  private tabs: EditorTab[] = [];
  private activeTabIndex = -1;
  private onStatusUpdate: (info: { line: number; col: number; language: string }) => void;

  constructor(
    container: HTMLElement,
    tabList: HTMLElement,
    onStatusUpdate: (info: { line: number; col: number; language: string }) => void
  ) {
    this.container = container;
    this.tabList = tabList;
    this.onStatusUpdate = onStatusUpdate;
    this.initMonaco();
  }

  private initMonaco(): void {
    self.MonacoEnvironment = {
      getWorkerUrl: function (_moduleId: string, label: string) {
        return `data:text/javascript;charset=utf-8,${encodeURIComponent('')}`;
      },
    };

    this.editor = monaco.editor.create(this.container, {
      theme: 'vs-dark',
      fontSize: 14,
      fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace",
      fontLigatures: true,
      minimap: { enabled: true },
      scrollBeyondLastLine: false,
      automaticLayout: true,
      tabSize: 2,
      wordWrap: 'on',
      renderWhitespace: 'selection',
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true },
      smoothScrolling: true,
      cursorBlinking: 'smooth',
      cursorSmoothCaretAnimation: 'on',
      padding: { top: 8 },
    });

    this.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      this.saveCurrentFile();
    });

    this.editor.onDidChangeCursorPosition((e) => {
      const pos = e.position;
      const model = this.editor?.getModel();
      this.onStatusUpdate({
        line: pos.lineNumber,
        col: pos.column,
        language: model?.getLanguageId() || '',
      });
    });
  }

  async openFile(filePath: string): Promise<void> {
    const existingIndex = this.tabs.findIndex((t) => t.path === filePath);
    if (existingIndex >= 0) {
      this.activateTab(existingIndex);
      return;
    }

    let content: string;
    try {
      content = await architect.readFile(filePath);
    } catch (e: any) {
      console.error('Failed to read file:', e);
      return;
    }

    const language = detectLanguage(filePath);
    const uri = monaco.Uri.file(filePath);

    let model = monaco.editor.getModel(uri);
    if (model) {
      model.setValue(content);
    } else {
      model = monaco.editor.createModel(content, language, uri);
    }

    const name = filePath.replace(/\\/g, '/').split('/').pop() || filePath;
    const tab: EditorTab = {
      path: filePath,
      name,
      model,
      viewState: null,
      modified: false,
      originalContent: content,
    };

    model.onDidChangeContent(() => {
      const currentContent = model!.getValue();
      tab.modified = currentContent !== tab.originalContent;
      this.renderTabs();
    });

    this.tabs.push(tab);
    this.activateTab(this.tabs.length - 1);
  }

  private activateTab(index: number): void {
    if (index < 0 || index >= this.tabs.length) return;

    if (this.activeTabIndex >= 0 && this.activeTabIndex < this.tabs.length) {
      this.tabs[this.activeTabIndex].viewState = this.editor?.saveViewState() ?? null;
    }

    this.activeTabIndex = index;
    const tab = this.tabs[index];
    this.editor?.setModel(tab.model);
    if (tab.viewState) {
      this.editor?.restoreViewState(tab.viewState);
    }
    this.editor?.focus();
    this.renderTabs();

    this.container.classList.remove('hidden');
    document.getElementById('welcome-tab')?.classList.add('hidden');
    document.getElementById('graph-container')?.classList.add('hidden');
    this.container.classList.remove('hidden');
  }

  closeTab(index: number): void {
    if (index < 0 || index >= this.tabs.length) return;
    const tab = this.tabs[index];

    if (tab.modified) {
      if (!confirm(`Save changes to ${tab.name}?`)) {
        // discard
      } else {
        architect.writeFile(tab.path, tab.model.getValue());
      }
    }

    tab.model.dispose();
    this.tabs.splice(index, 1);

    if (this.tabs.length === 0) {
      this.activeTabIndex = -1;
      this.editor?.setModel(null);
      this.container.classList.add('hidden');
      document.getElementById('welcome-tab')?.classList.remove('hidden');
    } else if (index <= this.activeTabIndex) {
      this.activateTab(Math.max(0, this.activeTabIndex - 1));
    }

    this.renderTabs();
  }

  async saveCurrentFile(): Promise<void> {
    if (this.activeTabIndex < 0) return;
    const tab = this.tabs[this.activeTabIndex];
    const content = tab.model.getValue();
    try {
      await architect.writeFile(tab.path, content);
      tab.originalContent = content;
      tab.modified = false;
      this.renderTabs();
    } catch (e: any) {
      console.error('Failed to save:', e);
    }
  }

  private renderTabs(): void {
    this.tabList.innerHTML = '';
    this.tabs.forEach((tab, i) => {
      const el = document.createElement('div');
      el.className = `tab-item${i === this.activeTabIndex ? ' active' : ''}${tab.modified ? ' modified' : ''}`;

      const nameEl = document.createElement('span');
      nameEl.className = 'tab-name';
      nameEl.textContent = tab.name;
      el.appendChild(nameEl);

      const closeEl = document.createElement('span');
      closeEl.className = 'tab-close';
      closeEl.textContent = '×';
      closeEl.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeTab(i);
      });
      el.appendChild(closeEl);

      el.addEventListener('click', () => this.activateTab(i));
      this.tabList.appendChild(el);
    });
  }

  layout(): void {
    this.editor?.layout();
  }

  getEditor(): monaco.editor.IStandaloneCodeEditor | null {
    return this.editor;
  }

  hasOpenTabs(): boolean {
    return this.tabs.length > 0;
  }
}
