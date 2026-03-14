export class SplitPanel {
  private handle: HTMLElement;
  private target: HTMLElement;
  private direction: 'horizontal' | 'vertical';
  private side: 'before' | 'after';
  private minSize: number;
  private maxSize: number;
  private dragging = false;

  constructor(opts: {
    handle: HTMLElement;
    target: HTMLElement;
    direction: 'horizontal' | 'vertical';
    side: 'before' | 'after';
    minSize?: number;
    maxSize?: number;
  }) {
    this.handle = opts.handle;
    this.target = opts.target;
    this.direction = opts.direction;
    this.side = opts.side;
    this.minSize = opts.minSize ?? 150;
    this.maxSize = opts.maxSize ?? 600;

    this.handle.addEventListener('mousedown', this.onMouseDown);
  }

  private onMouseDown = (e: MouseEvent) => {
    e.preventDefault();
    this.dragging = true;
    this.handle.classList.add('dragging');
    document.body.style.cursor =
      this.direction === 'vertical' ? 'col-resize' : 'row-resize';

    const onMouseMove = (ev: MouseEvent) => {
      if (!this.dragging) return;

      if (this.direction === 'vertical') {
        const rect = this.target.parentElement!.getBoundingClientRect();
        let size: number;
        if (this.side === 'before') {
          size = ev.clientX - rect.left;
        } else {
          size = rect.right - ev.clientX;
        }
        size = Math.max(this.minSize, Math.min(this.maxSize, size));
        this.target.style.width = `${size}px`;
      } else {
        const rect = this.target.parentElement!.getBoundingClientRect();
        let size: number;
        if (this.side === 'before') {
          size = ev.clientY - rect.top;
        } else {
          size = rect.bottom - ev.clientY;
        }
        size = Math.max(this.minSize, Math.min(this.maxSize, size));
        this.target.style.height = `${size}px`;
      }
    };

    const onMouseUp = () => {
      this.dragging = false;
      this.handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };

  destroy(): void {
    this.handle.removeEventListener('mousedown', this.onMouseDown);
  }
}
