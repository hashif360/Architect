declare const architect: any;

export class GraphPanel {
  private container: HTMLElement;
  private backendUrl: string = '';
  private iframe: HTMLIFrameElement | null = null;
  private onOpenFile: (path: string) => void;

  constructor(container: HTMLElement, onOpenFile: (path: string) => void) {
    this.container = container;
    this.onOpenFile = onOpenFile;
  }

  setBackendUrl(url: string): void {
    this.backendUrl = url;
  }

  show(): void {
    this.container.classList.remove('hidden');
    document.getElementById('monaco-container')?.classList.add('hidden');
    document.getElementById('welcome-tab')?.classList.add('hidden');

    if (!this.iframe && this.backendUrl) {
      this.createIframe();
    }
  }

  hide(): void {
    this.container.classList.add('hidden');
  }

  private createIframe(): void {
    this.iframe = document.createElement('iframe');
    this.iframe.style.width = '100%';
    this.iframe.style.height = '100%';
    this.iframe.style.border = 'none';
    this.iframe.style.background = '#1e1e2e';
    this.iframe.src = this.backendUrl;
    this.container.appendChild(this.iframe);

    window.addEventListener('message', (event) => {
      if (event.data?.type === 'architect:open-file' && event.data.path) {
        this.onOpenFile(event.data.path);
      }
    });
  }

  reload(): void {
    if (this.iframe) {
      this.iframe.src = this.backendUrl;
    }
  }

  isVisible(): boolean {
    return !this.container.classList.contains('hidden');
  }
}
