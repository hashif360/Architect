import { BrowserWindow, screen } from 'electron';
import * as path from 'path';

export function createMainWindow(isDev: boolean): BrowserWindow {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  const win = new BrowserWindow({
    width: Math.min(1600, width),
    height: Math.min(1000, height),
    minWidth: 900,
    minHeight: 600,
    title: 'Architect',
    backgroundColor: '#1e1e2e',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  const indexPath = isDev
    ? path.join(__dirname, '..', '..', 'renderer', 'index.html')
    : path.join(__dirname, '..', '..', 'renderer', 'index.html');

  win.loadFile(indexPath);

  win.once('ready-to-show', () => {
    win.show();
  });

  if (isDev) {
    win.webContents.openDevTools({ mode: 'detach' });
  }

  return win;
}
