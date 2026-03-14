import { app, BrowserWindow, Menu, MenuItemConstructorOptions, dialog } from 'electron';

export function buildMenu(mainWindow: BrowserWindow): void {
  const isMac = process.platform === 'darwin';

  const template: MenuItemConstructorOptions[] = [
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              { role: 'about' as const },
              { type: 'separator' as const },
              { role: 'services' as const },
              { type: 'separator' as const },
              { role: 'hide' as const },
              { role: 'hideOthers' as const },
              { role: 'unhide' as const },
              { type: 'separator' as const },
              { role: 'quit' as const },
            ],
          } as MenuItemConstructorOptions,
        ]
      : []),
    {
      label: 'File',
      submenu: [
        {
          label: 'Open Folder...',
          accelerator: 'CmdOrCtrl+O',
          click: () => {
            mainWindow.webContents.send('menu:open-folder');
          },
        },
        { type: 'separator' },
        {
          label: 'Save',
          accelerator: 'CmdOrCtrl+S',
          click: () => {
            mainWindow.webContents.send('menu:save');
          },
        },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        {
          label: 'Toggle File Explorer',
          accelerator: 'CmdOrCtrl+B',
          click: () => mainWindow.webContents.send('menu:toggle-panel', 'explorer'),
        },
        {
          label: 'Toggle Terminal',
          accelerator: 'CmdOrCtrl+`',
          click: () => mainWindow.webContents.send('menu:toggle-panel', 'terminal'),
        },
        {
          label: 'Toggle Graph',
          accelerator: 'CmdOrCtrl+G',
          click: () => mainWindow.webContents.send('menu:toggle-panel', 'graph'),
        },
        {
          label: 'Toggle AI Chat',
          accelerator: 'CmdOrCtrl+Shift+C',
          click: () => mainWindow.webContents.send('menu:toggle-panel', 'chat'),
        },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { role: 'resetZoom' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About Architect',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: 'About Architect',
              message: 'Architect IDE',
              detail: `Version ${app.getVersion()}\nArchitecture visualization IDE powered by Cursor AI.`,
            });
          },
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}
