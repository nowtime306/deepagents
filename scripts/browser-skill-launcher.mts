// Run with Node 24+. No user URLs are opened by this supervisor.
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync, renameSync, mkdirSync, unlinkSync, openSync, closeSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';

interface Options { root: string; executable: string; extension: string; chromium: string; playwright: string }
interface Peer { instance_id: string; extension_version: string; extension_protocol_version: string; session_count: number }
interface State extends Options { helper_pid: number; daemon_pid: number; browser_pid: number; port: number; debug_port: number; status: string; error?: string }
interface Worker { url(): string; evaluate(fn: (port: number) => Promise<void>, value: number): Promise<void> }
interface Browser { newBrowserCDPSession(): Promise<{ send(method: string): Promise<unknown> }>; contexts(): { serviceWorkers(): Worker[]; waitForEvent(event: string, options: { timeout: number; predicate: (worker: Worker) => boolean }): Promise<Worker> }[] }
const options: Options = JSON.parse(readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, ''));
const root = options.root;
const state: State = { ...options, helper_pid: process.pid, daemon_pid: 0, browser_pid: 0, port: 52983, debug_port: 52982, status: 'starting' };
const env = { ...process.env, BSK_HOME: join(root, 'home'), BSK_AUTO_START: '0', BSK_AUTO_UPDATE: 'off', BSK_BROWSER_WAIT_MS: '0' };
let daemon: ChildProcess | undefined;
let chrome: ChildProcess | undefined;
let browser: Browser | undefined;
let startupError: Error | undefined;
function save(name: string, value: unknown): void {
  const path = join(root, name);
  writeFileSync(path + '.tmp', JSON.stringify(value, null, 2));
  renameSync(path + '.tmp', path);
}
function command(...args: string[]): string {
  return execFileSync(options.executable, args, { env, windowsHide: true, timeout: 15000, encoding: 'utf8', maxBuffer: 1024 * 1024 }).trim();
}
function peers(): Peer[] { return JSON.parse(command('browsers', '--json')); }
function exited(child: ChildProcess): boolean { return child.exitCode !== null || child.signalCode !== null; }
async function until<T>(probe: () => Promise<T | false> | T | false, seconds = 30): Promise<T> {
  const deadline = Date.now() + seconds * 1000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    if (state.status === 'starting') {
      if (startupError) throw startupError;
      if (daemon && exited(daemon)) throw Error(`DAEMON_EXITED_${daemon.exitCode}: see daemon.log`);
      if (chrome && exited(chrome)) throw Error(`BROWSER_EXITED_${chrome.exitCode}: see chromium.log`);
    }
    try { const result = await probe(); if (result) return result; } catch (error) { lastError = error; }
    await delay(300);
  }
  throw Error(`STARTUP_TIMEOUT${lastError instanceof Error ? ': ' + lastError.message : ''}`);
}
async function shutdown(): Promise<void> {
  // Closing this dedicated browser closes all of its contexts/sessions normally.
  if (chrome?.pid && !exited(chrome)) {
    try {
      if (!browser) {
        const { chromium } = createRequire(import.meta.url)(options.playwright);
        browser = await chromium.connectOverCDP(`http://127.0.0.1:${state.debug_port}`, { timeout: 3000 });
      }
      const cdp = await browser!.newBrowserCDPSession();
      await Promise.race([cdp.send('Browser.close').catch(() => {}), delay(3000)]);
    } catch { /* CDP may never have become ready during a failed startup. */ }
    try { await until(() => exited(chrome!), 5); }
    catch {
      // Only the live child created by this supervisor; never enumerate/kill by name.
      chrome.kill();
      await until(() => exited(chrome!), 5);
    }
  }
  if (daemon && !exited(daemon)) {
    const info = JSON.parse(readFileSync(join(root, 'home', 'daemon.json'), 'utf8'));
    if (info.pid !== daemon.pid || info.ws_port !== state.port) throw Error('DAEMON_OWNERSHIP_CHANGED');
    command('daemon', 'stop');
    await until(() => exited(daemon!), 15);
  }
}
try {
  mkdirSync(join(root, 'home'), { recursive: true });
  if (command('--version') !== 'bsk 0.4.1') throw Error('CLI_VERSION_MISMATCH');
  const manifest = JSON.parse(readFileSync(join(options.extension, 'manifest.json'), 'utf8'));
  if (manifest.version !== '0.4.1') throw Error('EXTENSION_VERSION_MISMATCH');
  save('state.json', state);
  const daemonLog = openSync(join(root, 'daemon.log'), 'a');
  daemon = spawn(options.executable, ['daemon', 'start', '--foreground', '--port', String(state.port), '--daemon-idle', '24h'], { env, windowsHide: true, stdio: ['ignore', daemonLog, daemonLog] });
  closeSync(daemonLog);
  daemon.on('error', error => { startupError = error; });
  state.daemon_pid = daemon.pid!;
  save('state.json', state);
  await until(() => {
    const info = JSON.parse(readFileSync(join(root, 'home', 'daemon.json'), 'utf8'));
    return info.pid === daemon!.pid && info.ws_port === state.port && info.version === '0.4.1';
  });
  const chromiumLog = openSync(join(root, 'chromium.log'), 'a');
  chrome = spawn(options.chromium, [
    `--user-data-dir=${join(root, 'profile')}`, '--no-first-run', '--no-default-browser-check',
    '--no-startup-window', '--remote-debugging-address=127.0.0.1', `--remote-debugging-port=${state.debug_port}`,
    `--disable-extensions-except=${options.extension}`, `--load-extension=${options.extension}`,
  ], { windowsHide: true, stdio: ['ignore', chromiumLog, chromiumLog] });
  closeSync(chromiumLog);
  chrome.on('error', error => { startupError = error; });
  state.browser_pid = chrome.pid!;
  save('state.json', state);
  const endpoint = await until(async () => {
    const response = await fetch(`http://127.0.0.1:${state.debug_port}/json/version`);
    return (await response.json() as { webSocketDebuggerUrl: string }).webSocketDebuggerUrl;
  });
  const { chromium } = createRequire(import.meta.url)(options.playwright);
  browser = await chromium.connectOverCDP(endpoint);
  const context = browser!.contexts()[0];
  const isBrowserSkill = (worker: Worker) => worker.url().startsWith('chrome-extension://') && worker.url().endsWith('/' + manifest.background.service_worker);
  const worker = context.serviceWorkers().find(isBrowserSkill) ?? await context.waitForEvent('serviceworker', { timeout: 15000, predicate: isBrowserSkill });
  console.log('Dedicated extension worker:', worker.url());
  await until(async () => {
    await worker.evaluate(async (port: number) => {
      // A newly attached worker may precede initialization of extension bindings.
      const extension = globalThis as unknown as { chrome: { runtime: { getManifest(): { name: string; version: string } }; storage: { local: { set(value: object): Promise<void> } } } };
      const installed = extension.chrome.runtime.getManifest();
      if (installed.name !== 'BrowserSkill' || installed.version !== '0.4.1') throw Error('EXTENSION_WORKER_MISMATCH');
      await extension.chrome.storage.local.set({ bsk_daemon_port: port, bh_connection_enabled: true });
    }, state.port);
    return true;
  }, 15);
  const peer = await until(() => {
    const connected = peers();
    if (connected.length !== 1) return false;
    const peer = connected[0];
    return /^[0-9a-f]{8}$/.test(peer.instance_id) && peer.extension_version === '0.4.1' && peer.extension_protocol_version === '1.8' ? peer : false;
  });
  save('connection.json', { schema_version: 1, executable: options.executable, instance_id: peer.instance_id, cli_version: '0.4.1', extension_version: '0.4.1', protocol_version: '1.8' });
  state.status = 'ready';
  save('state.json', state);
  while (!existsSync(join(root, 'stop.request'))) {
    if (exited(daemon) || exited(chrome)) throw Error('OWNED_PROCESS_EXITED');
    await delay(500);
  }
  state.status = 'stopping'; save('state.json', state);
  unlinkSync(join(root, 'connection.json'));
  await shutdown();
  state.status = 'stopped'; save('state.json', state);
} catch (error) {
  if (existsSync(join(root, 'connection.json'))) unlinkSync(join(root, 'connection.json'));
  state.status = 'failed';
  state.error = error instanceof Error ? error.message : 'START_FAILED';
  try { await shutdown(); } catch { state.error += '; CLEANUP_REQUIRED'; }
  save('state.json', state);
  process.exitCode = 1;
}
