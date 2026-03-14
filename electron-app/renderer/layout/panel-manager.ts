type PanelId = 'explorer' | 'graph' | 'chat' | 'terminal';

interface PanelState {
  explorer: boolean;
  graph: boolean;
  chat: boolean;
  terminal: boolean;
}

const STORAGE_KEY = 'architect-panel-state';

export class PanelManager {
  private state: PanelState;
  private listeners: Array<(panel: PanelId, visible: boolean) => void> = [];

  constructor() {
    this.state = this.loadState();
  }

  isVisible(panel: PanelId): boolean {
    return this.state[panel];
  }

  toggle(panel: PanelId): void {
    this.state[panel] = !this.state[panel];
    this.saveState();
    this.notify(panel, this.state[panel]);
  }

  show(panel: PanelId): void {
    if (!this.state[panel]) {
      this.state[panel] = true;
      this.saveState();
      this.notify(panel, true);
    }
  }

  hide(panel: PanelId): void {
    if (this.state[panel]) {
      this.state[panel] = false;
      this.saveState();
      this.notify(panel, false);
    }
  }

  onChange(cb: (panel: PanelId, visible: boolean) => void): void {
    this.listeners.push(cb);
  }

  private notify(panel: PanelId, visible: boolean): void {
    for (const cb of this.listeners) cb(panel, visible);
  }

  private loadState(): PanelState {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    return { explorer: true, graph: false, chat: false, terminal: false };
  }

  private saveState(): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.state));
  }
}
