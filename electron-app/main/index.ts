import { app, BrowserWindow, ipcMain, dialog } from 'electron';
import * as path from 'path';
import { createMainWindow } from './window';
import { BackendManager } from './backend';
import { registerFileSystemHandlers } from './file-system';
import { registerPtyHandlers } from './pty';
import { CursorBridge } from './cursor-bridge';
import { buildMenu } from './menu';

let mainWindow: BrowserWindow | null = null;
let backend: BackendManager | null = null;
let cursorBridge: CursorBridge | null = null;

const isDev = !app.isPackaged;

async function createWindow(): Promise<void> {
  mainWindow = createMainWindow(isDev);

  buildMenu(mainWindow);
  registerFileSystemHandlers();
  registerPtyHandlers();

  cursorBridge = new CursorBridge();
  cursorBridge.registerIpcHandlers();

  backend = new BackendManager(isDev);

  mainWindow.webContents.on('did-finish-load', async () => {
    mainWindow?.webContents.send('app:ready', { isDev });
  });

  ipcMain.handle('app:get-backend-url', async () => {
    if (!backend) return null;
    await backend.waitUntilReady();
    return backend.getUrl();
  });

  ipcMain.handle('app:open-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      properties: ['openDirectory'],
      title: 'Open Project Folder',
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    const folderPath = result.filePaths[0];
    backend?.setProjectDir(folderPath);
    return folderPath;
  });

  ipcMain.handle('app:get-project-dir', () => {
    return backend?.getProjectDir() ?? null;
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  backend?.stop();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on('before-quit', () => {
  backend?.stop();
});
