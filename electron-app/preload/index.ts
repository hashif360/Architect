import { contextBridge, ipcRenderer } from 'electron';

const api = {
  // App
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  openFolder: () => ipcRenderer.invoke('app:open-folder'),
  getProjectDir: () => ipcRenderer.invoke('app:get-project-dir'),

  // File system
  readdir: (dirPath: string) => ipcRenderer.invoke('fs:readdir', dirPath),
  readFile: (filePath: string) => ipcRenderer.invoke('fs:readfile', filePath),
  writeFile: (filePath: string, content: string) =>
    ipcRenderer.invoke('fs:writefile', filePath, content),
  stat: (filePath: string) => ipcRenderer.invoke('fs:stat', filePath),
  exists: (filePath: string) => ipcRenderer.invoke('fs:exists', filePath),
  mkdir: (dirPath: string) => ipcRenderer.invoke('fs:mkdir', dirPath),
  rename: (oldPath: string, newPath: string) =>
    ipcRenderer.invoke('fs:rename', oldPath, newPath),
  deleteItem: (targetPath: string) => ipcRenderer.invoke('fs:delete', targetPath),
  reveal: (filePath: string) => ipcRenderer.invoke('fs:reveal', filePath),
  watchDir: (dirPath: string) => ipcRenderer.invoke('fs:watch', dirPath),
  unwatchDir: (dirPath: string) => ipcRenderer.invoke('fs:unwatch', dirPath),

  // Terminal
  ptySpawn: (cwd?: string, cols?: number, rows?: number) =>
    ipcRenderer.invoke('pty:spawn', cwd, cols, rows),
  ptyWrite: (id: number, data: string) => ipcRenderer.invoke('pty:write', id, data),
  ptyResize: (id: number, cols: number, rows: number) =>
    ipcRenderer.invoke('pty:resize', id, cols, rows),
  ptyKill: (id: number) => ipcRenderer.invoke('pty:kill', id),

  // Cursor
  cursorStatus: () => ipcRenderer.invoke('cursor:status'),
  cursorChat: (message: string, projectDir: string) =>
    ipcRenderer.invoke('cursor:chat', message, projectDir),

  // Event listeners
  on: (channel: string, callback: (...args: any[]) => void) => {
    const validChannels = [
      'app:ready',
      'fs:change',
      'pty:data',
      'pty:exit',
      'menu:open-folder',
      'menu:save',
      'menu:toggle-panel',
    ];
    if (validChannels.includes(channel)) {
      const listener = (_event: Electron.IpcRendererEvent, ...args: any[]) => callback(...args);
      ipcRenderer.on(channel, listener);
      return () => ipcRenderer.removeListener(channel, listener);
    }
    return () => {};
  },

  removeAllListeners: (channel: string) => {
    ipcRenderer.removeAllListeners(channel);
  },
};

contextBridge.exposeInMainWorld('architect', api);

export type ArchitectAPI = typeof api;
