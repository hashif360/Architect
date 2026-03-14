import { ipcMain } from 'electron';
import { execFile, spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

interface CursorStatus {
  installed: boolean;
  cliAvailable: boolean;
  agentAvailable: boolean;
  cursorPath: string | null;
  agentPath: string | null;
}

export class CursorBridge {
  private status: CursorStatus | null = null;

  registerIpcHandlers(): void {
    ipcMain.handle('cursor:status', async () => {
      this.status = await this.detectCursor();
      return this.status;
    });

    ipcMain.handle('cursor:chat', async (_event, message: string, projectDir: string) => {
      return this.startChat(message, projectDir);
    });
  }

  private async detectCursor(): Promise<CursorStatus> {
    const status: CursorStatus = {
      installed: false,
      cliAvailable: false,
      agentAvailable: false,
      cursorPath: null,
      agentPath: null,
    };

    const cursorPaths = this.getCursorPaths();
    for (const p of cursorPaths) {
      if (fs.existsSync(p)) {
        status.installed = true;
        status.cursorPath = p;
        break;
      }
    }

    status.agentPath = await this.findOnPath('agent');
    status.agentAvailable = status.agentPath !== null;

    const cursorCli = await this.findOnPath('cursor');
    status.cliAvailable = cursorCli !== null;

    return status;
  }

  private getCursorPaths(): string[] {
    const platform = process.platform;
    if (platform === 'win32') {
      const localAppData = process.env.LOCALAPPDATA || '';
      return [
        path.join(localAppData, 'Programs', 'cursor', 'Cursor.exe'),
        path.join(localAppData, 'cursor', 'Cursor.exe'),
      ];
    } else if (platform === 'darwin') {
      return ['/Applications/Cursor.app'];
    } else {
      return [
        '/usr/bin/cursor',
        '/usr/local/bin/cursor',
        path.join(os.homedir(), '.local', 'bin', 'cursor'),
        '/snap/bin/cursor',
      ];
    }
  }

  private findOnPath(cmd: string): Promise<string | null> {
    return new Promise((resolve) => {
      const isWin = process.platform === 'win32';
      const which = isWin ? 'where' : 'which';
      execFile(which, [cmd], (error, stdout) => {
        if (error) {
          resolve(null);
        } else {
          resolve(stdout.trim().split('\n')[0] || null);
        }
      });
    });
  }

  private async startChat(message: string, projectDir: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const agentPath = this.status?.agentPath;
      if (!agentPath) {
        reject(new Error('Cursor agent CLI not found'));
        return;
      }

      let output = '';
      const proc = spawn(agentPath, ['chat', '--message', message], {
        cwd: projectDir,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      proc.stdout?.on('data', (data: Buffer) => {
        output += data.toString();
      });

      proc.stderr?.on('data', (data: Buffer) => {
        console.error(`[cursor] ${data.toString().trim()}`);
      });

      proc.on('close', (code) => {
        if (code === 0) {
          resolve(output);
        } else {
          reject(new Error(`Agent exited with code ${code}`));
        }
      });

      proc.on('error', reject);
    });
  }
}
