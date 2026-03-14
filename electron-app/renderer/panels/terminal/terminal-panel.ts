declare const architect: any;

import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';

interface TerminalInstance {
  id: number;
  ptyId: number;
  terminal: Terminal;
  fitAddon: FitAddon;
  element: HTMLElement;
  name: string;
}

export class TerminalPanel {
  private container: HTMLElement;
  private tabsContainer: HTMLElement;
  private instances: TerminalInstance[] = [];
  private activeIndex = -1;
  private nextId = 1;
  private projectDir: string = '';
  private cleanupFns: Array<() => void> = [];

  constructor(container: HTMLElement, tabsContainer: HTMLElement) {
    this.container = container;
    this.tabsContainer = tabsContainer;
  }

  setProjectDir(dir: string): void {
    this.projectDir = dir;
  }

  async createTerminal(name?: string): Promise<void> {
    const id = this.nextId++;
    const termName = name || `Terminal ${id}`;

    const element = document.createElement('div');
    element.style.width = '100%';
    element.style.height = '100%';
    element.style.display = 'none';
    this.container.appendChild(element);

    const terminal = new Terminal({
      theme: {
        background: '#181825',
        foreground: '#cdd6f4',
        cursor: '#f5e0dc',
        selectionBackground: '#45475a',
        black: '#45475a',
        red: '#f38ba8',
        green: '#a6e3a1',
        yellow: '#f9e2af',
        blue: '#89b4fa',
        magenta: '#cba6f7',
        cyan: '#94e2d5',
        white: '#bac2de',
        brightBlack: '#585b70',
        brightRed: '#f38ba8',
        brightGreen: '#a6e3a1',
        brightYellow: '#f9e2af',
        brightBlue: '#89b4fa',
        brightMagenta: '#cba6f7',
        brightCyan: '#94e2d5',
        brightWhite: '#a6adc8',
      },
      fontFamily: "'JetBrains Mono', 'Cascadia Code', monospace",
      fontSize: 13,
      cursorBlink: true,
      allowProposedApi: true,
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(element);

    setTimeout(() => fitAddon.fit(), 50);

    const cols = terminal.cols;
    const rows = terminal.rows;
    const ptyId = await architect.ptySpawn(this.projectDir || undefined, cols, rows);

    terminal.onData((data: string) => {
      architect.ptyWrite(ptyId, data);
    });

    terminal.onResize(({ cols, rows }: { cols: number; rows: number }) => {
      architect.ptyResize(ptyId, cols, rows);
    });

    const cleanup = architect.on('pty:data', (id: number, data: string) => {
      if (id === ptyId) terminal.write(data);
    });
    this.cleanupFns.push(cleanup);

    const cleanupExit = architect.on('pty:exit', (id: number) => {
      if (id === ptyId) {
        terminal.writeln('\r\n[Process exited]');
      }
    });
    this.cleanupFns.push(cleanupExit);

    const instance: TerminalInstance = {
      id,
      ptyId,
      terminal,
      fitAddon,
      element,
      name: termName,
    };

    this.instances.push(instance);
    this.activateTerminal(this.instances.length - 1);
  }

  private activateTerminal(index: number): void {
    if (index < 0 || index >= this.instances.length) return;
    this.activeIndex = index;

    for (let i = 0; i < this.instances.length; i++) {
      this.instances[i].element.style.display = i === index ? 'block' : 'none';
    }

    const inst = this.instances[index];
    setTimeout(() => {
      inst.fitAddon.fit();
      inst.terminal.focus();
    }, 50);

    this.renderTabs();
  }

  closeTerminal(index: number): void {
    if (index < 0 || index >= this.instances.length) return;
    const inst = this.instances[index];
    architect.ptyKill(inst.ptyId);
    inst.terminal.dispose();
    inst.element.remove();
    this.instances.splice(index, 1);

    if (this.instances.length === 0) {
      this.activeIndex = -1;
    } else {
      this.activateTerminal(Math.min(index, this.instances.length - 1));
    }
    this.renderTabs();
  }

  private renderTabs(): void {
    this.tabsContainer.innerHTML = '';
    this.instances.forEach((inst, i) => {
      const tab = document.createElement('div');
      tab.className = `terminal-tab${i === this.activeIndex ? ' active' : ''}`;

      const name = document.createElement('span');
      name.textContent = inst.name;
      tab.appendChild(name);

      const close = document.createElement('span');
      close.className = 'tab-close';
      close.textContent = '×';
      close.style.opacity = '0.6';
      close.style.marginLeft = '6px';
      close.style.cursor = 'pointer';
      close.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeTerminal(i);
      });
      tab.appendChild(close);

      tab.addEventListener('click', () => this.activateTerminal(i));
      this.tabsContainer.appendChild(tab);
    });
  }

  fitAll(): void {
    for (const inst of this.instances) {
      try {
        inst.fitAddon.fit();
      } catch { /* may not be visible */ }
    }
  }

  hasInstances(): boolean {
    return this.instances.length > 0;
  }

  destroy(): void {
    for (const fn of this.cleanupFns) fn();
    for (const inst of this.instances) {
      architect.ptyKill(inst.ptyId);
      inst.terminal.dispose();
    }
    this.instances = [];
  }
}
