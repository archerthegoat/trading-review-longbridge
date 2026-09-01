import * as fs from 'node:fs';
import * as path from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { PublicationStore } from './publication.ts';
import { DEFAULT_ROOT, HASH, hash, jsonBytes, parseJson, canonical, noLinks, readSafe, object } from './private-store.ts';
import { PYTHON } from './data.ts';
import { PORT } from './service.ts';

export const LABEL = 'com.marstradingcenter.web-ui';
export const PLIST = path.join(homedir(), `Library/LaunchAgents/${LABEL}.plist`);
export const SOURCE_ROOT = fileURLToPath(new URL('../', import.meta.url));
export const PACKAGE_FILES = [
  'web/types.ts', 'web/data.ts', 'web/render.ts', 'web/private-store.ts', 'web/publication.ts',
  'web/service.ts', 'web/launchagent.ts', 'web/cli.ts', 'web/package.json',
  'scripts/trading_review_display.py', 'scripts/render_trade_review_dashboard_v2.py',
  'scripts/trading_review_state.py', 'assets/trade-review-dashboard-v2-standalone.html',
  'scripts/trading_review_valuation.py', 'scripts/trading_review_instruments.py', 'scripts/trading_review_portfolio.py',
] as const;
// Admit only the exact previous bundle shape for a verified in-place upgrade.
const PRE_PORTFOLIO_ENRICH_PACKAGE_FILES = PACKAGE_FILES.filter(name => name !== 'scripts/trading_review_portfolio.py');
const PRE_INSTRUMENT_PACKAGE_FILES = PRE_PORTFOLIO_ENRICH_PACKAGE_FILES.filter(name => name !== 'scripts/trading_review_instruments.py');
const LEGACY_PACKAGE_FILES = PRE_INSTRUMENT_PACKAGE_FILES.filter(name => name !== 'scripts/trading_review_valuation.py');
interface Installation { schema_version: 'trading-review-ts-installation.v1'; code_id: string; files: Record<string, string>; node: string; node_version: string; architecture: string }
const domain = () => `gui/${process.getuid!()}`;
function launchctl(args: string[], check = true) {
  const r = spawnSync('/bin/launchctl', args, { encoding: 'utf8', timeout: 15000, maxBuffer: 1024 * 1024 });
  if (r.error || (check && r.status !== 0)) throw new Error('launchagent_operation_failed');
  return r;
}
export function launchState(): { loaded: boolean; state: string; pid: number | null } {
  const r = launchctl(['print', `${domain()}/${LABEL}`], false);
  if (r.status === 113) return { loaded: false, state: 'not_loaded', pid: null };
  if (r.status !== 0) throw new Error('launchagent_state_unavailable');
  return { loaded: true, state: /^\s*state = ([a-zA-Z ]+)$/m.exec(r.stdout)?.[1]?.trim() ?? 'unknown', pid: Number(/^\s*pid = (\d+)$/m.exec(r.stdout)?.[1]) || null };
}
export function packageSource() {
  const files = Object.fromEntries(PACKAGE_FILES.map(name => [name, fs.readFileSync(path.join(SOURCE_ROOT, name))]));
  const hashes = Object.fromEntries(Object.entries(files).map(([name, content]) => [name, hash(content)]));
  return { code_id: hash(jsonBytes(hashes)), files, hashes };
}
export function verifyInstallation(store: PublicationStore): Installation {
  const info = object(parseJson(store.read('installation.json')), ['schema_version', 'code_id', 'files', 'node', 'node_version', 'architecture']) as unknown as Installation;
  const shape = Object.keys(info.files ?? {}).sort().join('|');
  if (info.schema_version !== 'trading-review-ts-installation.v1' || !HASH.test(info.code_id) || !info.files || ![PACKAGE_FILES, PRE_PORTFOLIO_ENRICH_PACKAGE_FILES, PRE_INSTRUMENT_PACKAGE_FILES, LEGACY_PACKAGE_FILES].some(files => shape === [...files].sort().join('|')) || hash(jsonBytes(info.files)) !== info.code_id || !path.isAbsolute(info.node)) throw new Error('installation_manifest_invalid');
  for (const name of Object.keys(info.files)) if (hash(store.read(`code/${info.code_id}/${name}`)) !== info.files[name]) throw new Error('installed_code_integrity_failed');
  return info;
}
function config(store: PublicationStore, info: Installation) {
  return { Label: LABEL, ProgramArguments: [info.node, '--disable-proto=throw', path.join(store.root, 'code', info.code_id, 'web/cli.ts'), 'serve'],
    WorkingDirectory: store.root, RunAtLoad: true, KeepAlive: true, ThrottleInterval: 5, Umask: 0o077,
    EnvironmentVariables: { NODE_OPTIONS: '', PATH: '/usr/bin:/bin' },
    StandardOutPath: path.join(store.root, 'logs/stdout.log'), StandardErrorPath: path.join(store.root, 'logs/stderr.log') };
}
const xmlEscape = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function xml(value: unknown): string {
  if (typeof value === 'string') return `<string>${xmlEscape(value)}</string>`;
  if (typeof value === 'number') return `<integer>${value}</integer>`;
  if (typeof value === 'boolean') return value ? '<true/>' : '<false/>';
  if (Array.isArray(value)) return `<array>${value.map(xml).join('')}</array>`;
  return `<dict>${Object.entries(value as object).map(([k, v]) => `<key>${xmlEscape(k)}</key>${xml(v)}`).join('')}</dict>`;
}
function readAgent(): unknown | null {
  noLinks(PLIST);
  if (!fs.existsSync(PLIST)) return null;
  const bytes = readSafe(PLIST);
  const r = spawnSync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', '-'], { input: bytes, encoding: 'utf8', timeout: 5000 });
  if (r.status !== 0 || r.error) throw new Error('invalid_launchagent_plist');
  const v = JSON.parse(r.stdout) as Record<string, unknown>, args = v.ProgramArguments;
  if (v.Label !== LABEL || !Array.isArray(args) || args.length !== 4 || typeof args[0] !== 'string' || args[1] !== '--disable-proto=throw' || typeof args[2] !== 'string' || !args[2].startsWith(DEFAULT_ROOT + '/code/') || !args[2].endsWith('/web/cli.ts') || args[3] !== 'serve') throw new Error('launchagent_ownership_mismatch');
  return v;
}
function writeAgent(content: Buffer) {
  noLinks(PLIST); readAgent();
  const parent = path.dirname(PLIST);
  if (!fs.existsSync(parent)) fs.mkdirSync(parent, { mode: 0o700 });
  const p = fs.lstatSync(parent);
  if (!p.isDirectory() || p.uid !== process.getuid!() || (p.mode & 0o022)) throw new Error('unsafe_launchagents_directory');
  const temp = path.join(parent, `.mars-trading-${randomUUID()}.plist`);
  const fd = fs.openSync(temp, 'wx', 0o600);
  try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  try { readAgent(); fs.renameSync(temp, PLIST); } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
}
export function install(store: PublicationStore) {
  if (process.platform !== 'darwin') throw new Error('launchagent_requires_macos');
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major! < 24 || (major === 24 && minor! < 12)) throw new Error('node_24_12_required');
  const py = spawnSync(PYTHON, ['-E', '-s', '-B', '-c', 'import sys; assert sys.version_info >= (3, 9)'], { timeout: 5000 });
  if (py.status !== 0 || py.error) throw new Error('python_3_9_required_for_publication');
  store.load();
  const bundle = packageSource();
  const info: Installation = { schema_version: 'trading-review-ts-installation.v1', code_id: bundle.code_id, files: bundle.hashes, node: process.execPath, node_version: process.versions.node, architecture: process.arch };
  return store.lock('install', () => {
    const oldAgent = readAgent();
    if (launchState().loaded) throw new Error('stop_agent_before_upgrade');
    for (const [name, content] of Object.entries(bundle.files)) store.writeOnce(`code/${info.code_id}/${name}`, content);
    if (fs.existsSync(path.join(store.root, 'installation.json'))) { verifyInstallation(store); store.write('installation.previous.json', store.read('installation.json')); }
    if (oldAgent) store.write('launch-agent.previous.plist', readSafe(PLIST));
    for (const name of ['stdout', 'stderr']) { if (!fs.existsSync(path.join(store.root, `logs/${name}.log`))) store.write(`logs/${name}.log`, Buffer.alloc(0)); else store.path(`logs/${name}.log`); }
    store.write('installation.json', jsonBytes(info)); verifyInstallation(store);
    writeAgent(Buffer.from(`<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">${xml(config(store, info))}</plist>\n`));
    return { status: 'installed_not_started', code_id: info.code_id, architecture: info.architecture, url: `http://127.0.0.1:${PORT}/` };
  });
}
export function verifyAgent(store: PublicationStore): Installation {
  const info = verifyInstallation(store);
  if (canonical(readAgent()) !== canonical(config(store, info))) throw new Error('launchagent_build_mismatch');
  return info;
}
export async function start(store: PublicationStore) {
  verifyAgent(store); store.load();
  if (launchState().loaded) return launchState();
  await new Promise<void>((resolve, reject) => {
    const probe = createServer();
    probe.once('error', () => reject(new Error('port_8765_occupied_no_process_stopped')));
    probe.listen(PORT, '127.0.0.1', () => probe.close(error => error ? reject(error) : resolve()));
  });
  launchctl(['bootstrap', domain(), PLIST]); return launchState();
}
export function stop(store: PublicationStore) { verifyAgent(store); if (launchState().loaded) launchctl(['bootout', `${domain()}/${LABEL}`]); return launchState(); }
export function status(store: PublicationStore) { const info = verifyAgent(store); const { manifest } = store.load(); return { status: 'verified', ...launchState(), code_id: info.code_id, publication_id: manifest.publication_id, url: `http://127.0.0.1:${PORT}/` }; }
export function uninstall(store: PublicationStore) {
  stop(store);
  const target = store.path(`disabled/${LABEL}-${Date.now()}-${randomUUID()}.plist`, true);
  fs.renameSync(PLIST, target); return { status: 'uninstalled', data_retained: true };
}
