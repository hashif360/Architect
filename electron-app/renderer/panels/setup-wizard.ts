declare const architect: any;

const WIZARD_DONE_KEY = 'architect-setup-complete';

export class SetupWizard {
  private onComplete: () => void;

  constructor(onComplete: () => void) {
    this.onComplete = onComplete;
  }

  isNeeded(): boolean {
    return !localStorage.getItem(WIZARD_DONE_KEY);
  }

  async show(splashStatus: HTMLElement): Promise<void> {
    splashStatus.innerHTML = '';
    const container = document.createElement('div');
    container.style.marginTop = '24px';
    container.style.textAlign = 'left';
    container.style.maxWidth = '340px';
    container.style.margin = '24px auto 0';

    const title = document.createElement('div');
    title.style.fontWeight = '600';
    title.style.marginBottom = '16px';
    title.style.textAlign = 'center';
    title.textContent = 'First-time Setup';
    container.appendChild(title);

    // Cursor check
    const cursorRow = this.createCheckRow('Checking for Cursor AI...');
    container.appendChild(cursorRow.element);

    splashStatus.appendChild(container);

    let cursorOk = false;
    try {
      const status = await architect.cursorStatus();
      cursorOk = status.installed || status.agentAvailable;
    } catch { /* ignore */ }

    if (cursorOk) {
      cursorRow.update('Cursor AI detected', true);
    } else {
      cursorRow.update('Cursor not found (AI features will be limited)', false);
      const link = document.createElement('a');
      link.href = '#';
      link.textContent = 'Download Cursor from cursor.com';
      link.style.color = '#6c8cff';
      link.style.fontSize = '12px';
      link.style.display = 'block';
      link.style.marginTop = '4px';
      link.style.marginLeft = '24px';
      link.addEventListener('click', (e) => {
        e.preventDefault();
        require('electron').shell.openExternal('https://cursor.com');
      });
      container.appendChild(link);
    }

    // Continue button
    const btnContainer = document.createElement('div');
    btnContainer.style.textAlign = 'center';
    btnContainer.style.marginTop = '20px';

    const btn = document.createElement('button');
    btn.className = 'splash-btn primary';
    btn.textContent = 'Continue';
    btn.style.display = 'inline-flex';
    btn.addEventListener('click', () => {
      localStorage.setItem(WIZARD_DONE_KEY, 'true');
      this.onComplete();
    });
    btnContainer.appendChild(btn);
    container.appendChild(btnContainer);
  }

  private createCheckRow(text: string): {
    element: HTMLElement;
    update: (text: string, ok: boolean) => void;
  } {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '8px';
    row.style.marginBottom = '10px';
    row.style.fontSize = '13px';

    const indicator = document.createElement('span');
    indicator.textContent = '...';
    indicator.style.width = '16px';
    indicator.style.textAlign = 'center';
    row.appendChild(indicator);

    const label = document.createElement('span');
    label.textContent = text;
    label.style.color = '#a6adc8';
    row.appendChild(label);

    return {
      element: row,
      update(newText: string, ok: boolean) {
        indicator.textContent = ok ? '✓' : '!';
        indicator.style.color = ok ? '#a6e3a1' : '#f9e2af';
        label.textContent = newText;
        label.style.color = ok ? '#a6e3a1' : '#f9e2af';
      },
    };
  }
}
