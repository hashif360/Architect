import { ipcMain, IpcMainInvokeEvent } from 'electron';
import { spawn, ChildProcess } from 'child_process';
import * as os from 'os';

interface TerminalInstance {
  process: ChildProcess;
  cwd: string;
}

const terminals = new Map<number, TerminalInstance>();
let nextId = 1;

function getDefaultShell(): { cmd: string; args: string[] } {
  if (process.platform === 'win32') {
    const shell = process.env.COMSPEC || 'cmd.exe';
    if (shell.toLowerCase().includes('powershell')) {
      return { cmd: shell, args: ['-NoLogo'] };
    }
    return { cmd: 'powershell.exe', args: ['-NoLogo', '-NoProfile'] };
  }
  const shell = process.env.SHELL || '/bin/bash';
  return { cmd: shell, args: ['-l'] };
}

export function registerPtyHandlers(): void {
  ipcMain.handle(
    'pty:spawn',
    async (event: IpcMainInvokeEvent, cwd?: string) => {
      const id = nextId++;
      const { cmd, args } = getDefaultShell();

      const proc = spawn(cmd, args, {
        cwd: cwd || os.homedir(),
        env: { ...process.env, TERM: 'xterm-256color' },
        shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      terminals.set(id, { process: proc, cwd: cwd || os.homedir() });

      proc.stdout?.on('data', (data: Buffer) => {
        try {
          event.sender.send('pty:data', id, data.toString());
        } catch { /* window may be closed */ }
      });

      proc.stderr?.on('data', (data: Buffer) => {
        try {
          event.sender.send('pty:data', id, data.toString());
        } catch { /* window may be closed */ }
      });

      proc.on('exit', (exitCode) => {
        try {
          event.sender.send('pty:exit', id, exitCode);
        } catch { /* window may be closed */ }
        terminals.delete(id);
      });

      proc.on('error', (err) => {
        try {
          event.sender.send('pty:data', id, `\r\nError: ${err.message}\r\n`);
          event.sender.send('pty:exit', id, 1);
        } catch { /* window may be closed */ }
        terminals.delete(id);
      });

      return id;
    }
  );

  ipcMain.handle('pty:write', async (_event, id: number, data: string) => {
    const term = terminals.get(id);
    if (term?.process.stdin?.writable) {
      term.process.stdin.write(data);
    }
  });

  ipcMain.handle('pty:resize', async (_event, _id: number, _cols: number, _rows: number) => {
    // child_process doesn't support resize — no-op
  });

  ipcMain.handle('pty:kill', async (_event, id: number) => {
    const term = terminals.get(id);
    if (term) {
      term.process.kill();
      terminals.delete(id);
    }
  });
}
