declare const architect: any;

interface DirEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  isFile: boolean;
  size: number;
  modified: number;
}

const FILE_ICONS: Record<string, string> = {
  ts: '🟦', tsx: '⚛️', js: '🟨', jsx: '⚛️', py: '🐍', json: '📋',
  md: '📝', html: '🌐', css: '🎨', scss: '🎨', sql: '🗄️',
  yaml: '⚙️', yml: '⚙️', toml: '⚙️', env: '🔒', lock: '🔒',
  gitignore: '🚫', rs: '🦀', go: '🐹', java: '☕', rb: '💎',
  php: '🐘', sh: '🐚', bat: '🐚', ps1: '🐚', xml: '📄',
  svg: '🖼️', png: '🖼️', jpg: '🖼️', gif: '🖼️', ico: '🖼️',
};

function getFileIcon(name: string, isDir: boolean): string {
  if (isDir) return '📁';
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return FILE_ICONS[ext] || '📄';
}

export class FileTree {
  private container: HTMLElement;
  private rootPath: string = '';
  private selectedPath: string = '';
  private expandedDirs = new Set<string>();
  private onFileOpen: (path: string) => void;

  constructor(container: HTMLElement, onFileOpen: (path: string) => void) {
    this.container = container;
    this.onFileOpen = onFileOpen;

    this.container.addEventListener('contextmenu', (e) => this.onContextMenu(e));
  }

  async setRoot(rootPath: string): Promise<void> {
    this.rootPath = rootPath;
    this.expandedDirs.clear();
    this.expandedDirs.add(rootPath);
    await this.render();
  }

  async render(): Promise<void> {
    this.container.innerHTML = '';
    if (!this.rootPath) return;
    await this.renderDir(this.rootPath, this.container, 0);
  }

  private async renderDir(dirPath: string, parent: HTMLElement, depth: number): Promise<void> {
    let entries: DirEntry[];
    try {
      entries = await architect.readdir(dirPath);
    } catch {
      return;
    }

    for (const entry of entries) {
      const item = document.createElement('div');
      item.className = 'tree-item';
      if (entry.path === this.selectedPath) item.classList.add('selected');
      item.style.paddingLeft = `${depth * 16 + 8}px`;
      item.dataset.path = entry.path;
      item.dataset.isDir = String(entry.isDirectory);

      if (entry.isDirectory) {
        const isOpen = this.expandedDirs.has(entry.path);
        const chevron = document.createElement('span');
        chevron.className = `chevron${isOpen ? ' open' : ''}`;
        chevron.textContent = '▸';
        item.appendChild(chevron);
      } else {
        const spacer = document.createElement('span');
        spacer.className = 'chevron';
        spacer.textContent = ' ';
        item.appendChild(spacer);
      }

      const icon = document.createElement('span');
      icon.className = 'icon';
      icon.textContent = getFileIcon(entry.name, entry.isDirectory);
      item.appendChild(icon);

      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = entry.name;
      item.appendChild(name);

      item.addEventListener('click', () => this.onItemClick(entry));
      parent.appendChild(item);

      if (entry.isDirectory && this.expandedDirs.has(entry.path)) {
        await this.renderDir(entry.path, parent, depth + 1);
      }
    }
  }

  private async onItemClick(entry: DirEntry): Promise<void> {
    if (entry.isDirectory) {
      if (this.expandedDirs.has(entry.path)) {
        this.expandedDirs.delete(entry.path);
      } else {
        this.expandedDirs.add(entry.path);
      }
      await this.render();
    } else {
      this.selectedPath = entry.path;
      this.onFileOpen(entry.path);
      this.highlightSelected();
    }
  }

  private highlightSelected(): void {
    const items = this.container.querySelectorAll('.tree-item');
    items.forEach((el) => {
      const htmlEl = el as HTMLElement;
      htmlEl.classList.toggle('selected', htmlEl.dataset.path === this.selectedPath);
    });
  }

  private onContextMenu(e: Event): void {
    const mouseEvent = e as MouseEvent;
    const target = (mouseEvent.target as HTMLElement).closest('.tree-item') as HTMLElement | null;
    if (!target) return;

    mouseEvent.preventDefault();
    const filePath = target.dataset.path!;
    const isDir = target.dataset.isDir === 'true';

    this.showContextMenu(mouseEvent.clientX, mouseEvent.clientY, filePath, isDir);
  }

  private showContextMenu(x: number, y: number, filePath: string, isDir: boolean): void {
    const existing = document.querySelector('.context-menu');
    if (existing) existing.remove();

    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    const items = isDir
      ? [
          { label: 'New File...', action: () => this.promptNewFile(filePath) },
          { label: 'New Folder...', action: () => this.promptNewFolder(filePath) },
          { separator: true },
          { label: 'Reveal in File Explorer', action: () => architect.reveal(filePath) },
          { separator: true },
          { label: 'Delete', action: () => this.deleteItem(filePath) },
        ]
      : [
          { label: 'Open', action: () => this.onFileOpen(filePath) },
          { separator: true },
          { label: 'Rename...', action: () => this.promptRename(filePath) },
          { label: 'Reveal in File Explorer', action: () => architect.reveal(filePath) },
          { separator: true },
          { label: 'Delete', action: () => this.deleteItem(filePath) },
        ];

    for (const item of items) {
      if ('separator' in item && item.separator) {
        const sep = document.createElement('div');
        sep.className = 'context-menu-separator';
        menu.appendChild(sep);
      } else if ('action' in item) {
        const el = document.createElement('div');
        el.className = 'context-menu-item';
        el.textContent = item.label;
        el.addEventListener('click', () => {
          menu.remove();
          item.action();
        });
        menu.appendChild(el);
      }
    }

    document.body.appendChild(menu);

    const dismiss = (ev: MouseEvent) => {
      if (!menu.contains(ev.target as Node)) {
        menu.remove();
        document.removeEventListener('mousedown', dismiss);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', dismiss), 0);
  }

  private async promptNewFile(dirPath: string): Promise<void> {
    const name = prompt('New file name:');
    if (!name) return;
    const sep = dirPath.includes('/') ? '/' : '\\';
    const fullPath = dirPath + sep + name;
    await architect.writeFile(fullPath, '');
    await this.render();
    this.onFileOpen(fullPath);
  }

  private async promptNewFolder(dirPath: string): Promise<void> {
    const name = prompt('New folder name:');
    if (!name) return;
    const sep = dirPath.includes('/') ? '/' : '\\';
    await architect.mkdir(dirPath + sep + name);
    await this.render();
  }

  private async promptRename(filePath: string): Promise<void> {
    const parts = filePath.replace(/\\/g, '/').split('/');
    const oldName = parts.pop()!;
    const newName = prompt('Rename to:', oldName);
    if (!newName || newName === oldName) return;
    const newPath = parts.join('/') + '/' + newName;
    await architect.rename(filePath, newPath);
    await this.render();
  }

  private async deleteItem(filePath: string): Promise<void> {
    const parts = filePath.replace(/\\/g, '/').split('/');
    const name = parts.pop();
    if (!confirm(`Delete "${name}"?`)) return;
    await architect.deleteItem(filePath);
    await this.render();
  }
}
