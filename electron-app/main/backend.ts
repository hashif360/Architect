import { ChildProcess, spawn } from 'child_process';
import * as path from 'path';
import * as net from 'net';
import { app } from 'electron';

export class BackendManager {
  private process: ChildProcess | null = null;
  private port: number = 0;
  private ready: boolean = false;
  private readyPromise: Promise<void> | null = null;
  private projectDir: string = '';
  private isDev: boolean;

  constructor(isDev: boolean) {
    this.isDev = isDev;
  }

  async start(projectDir: string): Promise<void> {
    if (this.process) this.stop();
    this.projectDir = projectDir;
    this.port = await this.findFreePort();
    this.ready = false;

    const sidecarPath = this.getSidecarPath();
    let spawnCmd: string;
    let spawnArgs: string[];

    if (this.isDev) {
      spawnCmd = sidecarPath;
      spawnArgs = ['-m', 'architect', 'view', '--port', String(this.port), '--no-browser'];
    } else {
      spawnCmd = sidecarPath;
      spawnArgs = ['view', '--port', String(this.port), '--no-browser'];
    }

    this.process = spawn(spawnCmd, spawnArgs, {
      cwd: projectDir,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[backend] ${data.toString().trim()}`);
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[backend] ${data.toString().trim()}`);
    });

    this.process.on('exit', (code) => {
      console.log(`[backend] exited with code ${code}`);
      this.process = null;
      this.ready = false;
    });

    this.readyPromise = this.pollUntilReady();
    await this.readyPromise;
  }

  stop(): void {
    if (this.process) {
      this.process.kill('SIGTERM');
      setTimeout(() => {
        if (this.process && !this.process.killed) {
          this.process.kill('SIGKILL');
        }
      }, 3000);
      this.process = null;
      this.ready = false;
    }
  }

  getUrl(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  getPort(): number {
    return this.port;
  }

  getProjectDir(): string {
    return this.projectDir;
  }

  setProjectDir(dir: string): void {
    this.projectDir = dir;
    this.start(dir);
  }

  async waitUntilReady(): Promise<void> {
    if (this.ready) return;
    if (this.readyPromise) await this.readyPromise;
  }

  private getSidecarPath(): string {
    if (this.isDev) {
      return process.platform === 'win32' ? 'python' : 'python3';
    }
    const ext = process.platform === 'win32' ? '.exe' : '';
    return path.join(
      process.resourcesPath || app.getAppPath(),
      'sidecar',
      `architect${ext}`
    );
  }

  private async findFreePort(): Promise<number> {
    return new Promise((resolve, reject) => {
      const server = net.createServer();
      server.listen(0, '127.0.0.1', () => {
        const addr = server.address();
        if (addr && typeof addr !== 'string') {
          const port = addr.port;
          server.close(() => resolve(port));
        } else {
          reject(new Error('Failed to find free port'));
        }
      });
      server.on('error', reject);
    });
  }

  private async pollUntilReady(): Promise<void> {
    const maxAttempts = 60;
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const response = await fetch(`http://127.0.0.1:${this.port}/api/graph`);
        if (response.ok) {
          this.ready = true;
          console.log(`[backend] ready on port ${this.port}`);
          return;
        }
      } catch {
        // not ready yet
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    console.error('[backend] failed to start within timeout');
  }
}
