declare const architect: any;

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export class ChatPanel {
  private messagesContainer: HTMLElement;
  private input: HTMLTextAreaElement;
  private sendBtn: HTMLElement;
  private projectDir: string = '';
  private messages: ChatMessage[] = [];
  private backendUrl: string = '';
  private cursorAvailable: boolean = false;

  constructor(
    messagesContainer: HTMLElement,
    input: HTMLTextAreaElement,
    sendBtn: HTMLElement
  ) {
    this.messagesContainer = messagesContainer;
    this.input = input;
    this.sendBtn = sendBtn;

    this.sendBtn.addEventListener('click', () => this.sendMessage());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    this.checkCursorStatus();
  }

  setProjectDir(dir: string): void {
    this.projectDir = dir;
  }

  setBackendUrl(url: string): void {
    this.backendUrl = url;
  }

  async checkCursorStatus(): Promise<void> {
    try {
      const status = await architect.cursorStatus();
      this.cursorAvailable = status.agentAvailable;
      this.updateBadge();
      if (!this.cursorAvailable) {
        this.showCursorMissing();
      }
    } catch {
      this.cursorAvailable = false;
      this.showCursorMissing();
    }
  }

  private showCursorMissing(): void {
    const banner = document.createElement('div');
    banner.className = 'cursor-missing-banner';
    banner.innerHTML = `
      <p>Cursor AI is not detected.</p>
      <p>Install <a href="https://cursor.com" target="_blank">Cursor</a> and its CLI to enable AI chat.</p>
      <p style="margin-top:8px;font-size:11px;">Run <code>agent login</code> in your terminal after installing.</p>
    `;
    this.messagesContainer.appendChild(banner);
  }

  private updateBadge(): void {
    const badge = document.getElementById('cursor-badge');
    if (badge) {
      badge.style.background = this.cursorAvailable
        ? 'rgba(166,227,161,0.15)'
        : 'rgba(243,139,168,0.15)';
      badge.style.color = this.cursorAvailable ? '#a6e3a1' : '#f38ba8';
      badge.textContent = this.cursorAvailable ? 'Cursor Connected' : 'Cursor Missing';
    }

    const dot = document.getElementById('cursor-dot');
    if (dot) {
      dot.className = `status-dot ${this.cursorAvailable ? 'connected' : 'disconnected'}`;
    }
  }

  private async sendMessage(): Promise<void> {
    const text = this.input.value.trim();
    if (!text) return;

    this.input.value = '';
    this.addMessage('user', text);

    if (this.backendUrl) {
      await this.sendViaBackend(text);
    } else if (this.cursorAvailable) {
      await this.sendViaCursorDirect(text);
    } else {
      this.addMessage('assistant', 'Cursor AI is not available. Please install Cursor and run `agent login`.');
    }
  }

  private async sendViaBackend(text: string): Promise<void> {
    try {
      const response = await fetch(`${this.backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, project_dir: this.projectDir }),
      });

      if (!response.ok) {
        this.addMessage('assistant', `Error: ${response.statusText}`);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) return;

      let assistantText = '';
      const decoder = new TextDecoder();
      const bubble = this.addMessage('assistant', '...');

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === 'text' || data.type === 'content') {
                assistantText += data.content || data.text || '';
                bubble.innerHTML = this.renderMarkdown(assistantText);
              } else if (data.type === 'error') {
                assistantText += `\n\nError: ${data.message}`;
                bubble.innerHTML = this.renderMarkdown(assistantText);
              }
            } catch { /* skip malformed SSE */ }
          }
        }
      }

      if (!assistantText) {
        bubble.textContent = 'No response received.';
      }
    } catch (e: any) {
      this.addMessage('assistant', `Failed to connect: ${e.message}`);
    }
  }

  private async sendViaCursorDirect(text: string): Promise<void> {
    try {
      const response = await architect.cursorChat(text, this.projectDir);
      this.addMessage('assistant', response);
    } catch (e: any) {
      this.addMessage('assistant', `Error: ${e.message}`);
    }
  }

  private addMessage(role: 'user' | 'assistant', content: string): HTMLElement {
    this.messages.push({ role, content });

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;
    bubble.innerHTML = this.renderMarkdown(content);
    this.messagesContainer.appendChild(bubble);
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    return bubble;
  }

  private renderMarkdown(text: string): string {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  }
}
