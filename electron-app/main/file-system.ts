import { ipcMain, shell } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import * as chokidar from 'chokidar';

interface DirEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  isFile: boolean;
  size: number;
  modified: number;
}

const IGNORE_DIRS = new Set([
  'node_modules', '.git', '__pycache__', '.architect', 'venv', '.venv',
  'env', '.env', 'dist', 'build', '.next', '.cache', '.idea', '.vscode',
  '.cursor', 'coverage', '.tox', 'egg-info', '.DS_Store',
]);

const watchers = new Map<string, chokidar.FSWatcher>();

function shouldIgnore(name: string): boolean {
  return IGNORE_DIRS.has(name) || name.startsWith('.');
}

function loadGitignorePatterns(projectDir: string): string[] {
  const gitignorePath = path.join(projectDir, '.gitignore');
  try {
    const content = fs.readFileSync(gitignorePath, 'utf-8');
    return content
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'));
  } catch {
    return [];
  }
}

export function registerFileSystemHandlers(): void {
  ipcMain.handle('fs:readdir', async (_event, dirPath: string) => {
    try {
      const entries = fs.readdirSync(dirPath, { withFileTypes: true });
      const result: DirEntry[] = [];

      for (const entry of entries) {
        if (shouldIgnore(entry.name)) continue;
        const fullPath = path.join(dirPath, entry.name);
        try {
          const stats = fs.statSync(fullPath);
          result.push({
            name: entry.name,
            path: fullPath,
            isDirectory: entry.isDirectory(),
            isFile: entry.isFile(),
            size: stats.size,
            modified: stats.mtimeMs,
          });
        } catch {
          // skip inaccessible entries
        }
      }

      result.sort((a, b) => {
        if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
        return a.name.localeCompare(b.name);
      });

      return result;
    } catch (e: any) {
      throw new Error(`Failed to read directory: ${e.message}`);
    }
  });

  ipcMain.handle('fs:readfile', async (_event, filePath: string) => {
    try {
      return fs.readFileSync(filePath, 'utf-8');
    } catch (e: any) {
      throw new Error(`Failed to read file: ${e.message}`);
    }
  });

  ipcMain.handle('fs:writefile', async (_event, filePath: string, content: string) => {
    try {
      const dir = path.dirname(filePath);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(filePath, content, 'utf-8');
      return true;
    } catch (e: any) {
      throw new Error(`Failed to write file: ${e.message}`);
    }
  });

  ipcMain.handle('fs:stat', async (_event, filePath: string) => {
    try {
      const stats = fs.statSync(filePath);
      return {
        size: stats.size,
        modified: stats.mtimeMs,
        created: stats.birthtimeMs,
        isDirectory: stats.isDirectory(),
        isFile: stats.isFile(),
      };
    } catch (e: any) {
      throw new Error(`Failed to stat: ${e.message}`);
    }
  });

  ipcMain.handle('fs:exists', async (_event, filePath: string) => {
    return fs.existsSync(filePath);
  });

  ipcMain.handle('fs:mkdir', async (_event, dirPath: string) => {
    fs.mkdirSync(dirPath, { recursive: true });
    return true;
  });

  ipcMain.handle('fs:rename', async (_event, oldPath: string, newPath: string) => {
    fs.renameSync(oldPath, newPath);
    return true;
  });

  ipcMain.handle('fs:delete', async (_event, targetPath: string) => {
    const stats = fs.statSync(targetPath);
    if (stats.isDirectory()) {
      fs.rmSync(targetPath, { recursive: true, force: true });
    } else {
      fs.unlinkSync(targetPath);
    }
    return true;
  });

  ipcMain.handle('fs:reveal', async (_event, filePath: string) => {
    shell.showItemInFolder(filePath);
  });

  ipcMain.handle('fs:watch', async (event, dirPath: string) => {
    if (watchers.has(dirPath)) return;

    const watcher = chokidar.watch(dirPath, {
      ignored: /(^|[\/\\])(node_modules|\.git|__pycache__|\.architect|\.next|\.cache|dist|build)/,
      persistent: true,
      depth: 1,
      ignoreInitial: true,
    });

    watcher.on('all', (eventType, filePath) => {
      event.sender.send('fs:change', { type: eventType, path: filePath });
    });

    watchers.set(dirPath, watcher);
  });

  ipcMain.handle('fs:unwatch', async (_event, dirPath: string) => {
    const watcher = watchers.get(dirPath);
    if (watcher) {
      await watcher.close();
      watchers.delete(dirPath);
    }
  });
}
