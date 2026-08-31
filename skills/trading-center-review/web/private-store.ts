import * as fs from 'node:fs';
import * as path from 'node:path';
import { homedir } from 'node:os';
import { createHash, randomUUID } from 'node:crypto';

export const DEFAULT_ROOT = path.join(homedir(), 'Library/Application Support/MarsTradingCenter/web-ui');
export const INPUT_ROOT = '/private/tmp/trading-center-review-runtime';
export const LIMIT = 8 * 1024 * 1024;
export const HASH = /^[0-9a-f]{64}$/;
export const hash = (v: string | Buffer) => createHash('sha256').update(v).digest('hex');
export const missing = (error: unknown) => (error as NodeJS.ErrnoException)?.code === 'ENOENT';
export function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('|') !== [...keys].sort().join('|')) throw new Error('unexpected_object_fields');
  return value as Record<string, unknown>;
}
export function canonical(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical((value as Record<string, unknown>)[k])}`).join(',')}}`;
  throw new Error('invalid_json_value');
}
export const jsonBytes = (value: unknown) => Buffer.from(canonical(value) + '\n');

/** JSON.parse alone silently accepts duplicate object keys. This bounded parser does not. */
export function parseJson(bytes: Buffer): unknown {
  if (bytes.length > LIMIT) throw new Error('file_too_large');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  let i = 0;
  const ws = () => { while (/[\x20\x09\x0a\x0d]/.test(text[i] ?? 'x')) i++; };
  const string = (): string => {
    const m = /^"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/.exec(text.slice(i));
    if (!m) throw new Error('invalid_json_string');
    i += m[0].length;
    return JSON.parse(m[0]) as string;
  };
  const value = (depth: number): unknown => {
    if (depth > 80) throw new Error('json_nesting_limit');
    ws();
    if (text[i] === '"') return string();
    if (text[i] === '{') {
      i++; ws(); const result: Record<string, unknown> = Object.create(null); const seen = new Set<string>();
      if (text[i] === '}') { i++; return result; }
      while (true) {
        ws(); const key = string(); ws();
        if (seen.has(key) || ['__proto__', 'constructor', 'prototype'].includes(key) || text[i++] !== ':') throw new Error('duplicate_or_invalid_json_key');
        seen.add(key); result[key] = value(depth + 1); ws();
        const end = text[i++]; if (end === '}') return result;
        if (end !== ',') throw new Error('invalid_json_object');
      }
    }
    if (text[i] === '[') {
      i++; ws(); const result: unknown[] = [];
      if (text[i] === ']') { i++; return result; }
      while (true) { result.push(value(depth + 1)); ws(); const end = text[i++]; if (end === ']') return result; if (end !== ',') throw new Error('invalid_json_array'); }
    }
    for (const [token, v] of [['true', true], ['false', false], ['null', null]] as const) if (text.startsWith(token, i)) { i += token.length; return v; }
    const n = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(i));
    if (!n || !Number.isFinite(Number(n[0]))) throw new Error('invalid_json_number');
    i += n[0].length; return Number(n[0]);
  };
  const result = value(0); ws(); if (i !== text.length) throw new Error('trailing_json_data'); return result;
}

export function noLinks(target: string, allowVault = false): void {
  if (!path.isAbsolute(target) || target.split(path.sep).some(p => p === '..' || p === '.')) throw new Error('absolute_nontraversing_path_required');
  for (let p = target; ; p = path.dirname(p)) {
    try { if (fs.lstatSync(p).isSymbolicLink()) throw new Error('symbolic_link_rejected'); } catch (e) { if (!missing(e)) throw e; }
    if (fs.existsSync(path.join(p, '.git')) || (!allowVault && fs.existsSync(path.join(p, '.obsidian')))) throw new Error('workspace_path_rejected');
    if (p === path.dirname(p)) break;
  }
}
export function ownerMode(target: string, directory: boolean): fs.Stats {
  const s = fs.lstatSync(target);
  if (!(directory ? s.isDirectory() : s.isFile()) || s.uid !== process.getuid!() || (s.mode & 0o777) !== (directory ? 0o700 : 0o600) || (!directory && s.nlink !== 1)) throw new Error('unsafe_artifact_owner_mode_or_type');
  return s;
}
export function readSafe(target: string): Buffer {
  const before = ownerMode(target, false);
  const fd = fs.openSync(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const s = fs.fstatSync(fd);
    if (s.ino !== before.ino || s.dev !== before.dev || !s.isFile() || s.uid !== process.getuid!() || (s.mode & 0o777) !== 0o600 || s.nlink !== 1 || s.size > LIMIT) throw new Error('file_read_gate_failed');
    const content = Buffer.alloc(s.size + 1);
    let length = 0, n = 0;
    while (length < content.length && (n = fs.readSync(fd, content, length, content.length - length, null)) > 0) length += n;
    if (length > s.size || fs.fstatSync(fd).mtimeMs !== s.mtimeMs) throw new Error('file_changed_during_read');
    return content.subarray(0, length);
  } finally { fs.closeSync(fd); }
}
export function privateInput(target: string): unknown {
  noLinks(target);
  if (!target.startsWith(INPUT_ROOT + path.sep)) throw new Error('input_outside_authorized_runtime');
  let current = path.dirname(target);
  while (current.startsWith(INPUT_ROOT)) { ownerMode(current, true); if (current === INPUT_ROOT) break; current = path.dirname(current); }
  return parseJson(readSafe(target));
}

export class PrivateTree {
  readonly root: string;
  constructor(root = DEFAULT_ROOT, options: { testRoot?: string; create?: boolean; approvedRoot?: string } = {}) {
    noLinks(root); this.root = root;
    if (options.testRoot) {
      noLinks(options.testRoot); ownerMode(options.testRoot, true);
      if (!root.startsWith(options.testRoot + path.sep)) throw new Error('test_root_not_isolated');
    } else if (root !== (options.approvedRoot ?? DEFAULT_ROOT)) throw new Error('fixed_output_root_required');
    const parent = path.dirname(root);
    if (!fs.existsSync(parent) && options.create) fs.mkdirSync(parent, { mode: 0o700 });
    ownerMode(parent, true);
    if (!fs.existsSync(root) && options.create) fs.mkdirSync(root, { mode: 0o700 });
    ownerMode(root, true);
  }
  path(name: string, parents = false): string {
    const parts = name.split('/');
    if (!parts.length || path.isAbsolute(name) || parts.some(p => !p || p === '.' || p === '..')) throw new Error('invalid_artifact_name');
    noLinks(this.root); ownerMode(this.root, true);
    let p = this.root;
    for (const part of parts.slice(0, -1)) { p = path.join(p, part); if (!fs.existsSync(p) && parents) fs.mkdirSync(p, { mode: 0o700 }); ownerMode(p, true); }
    const result = path.join(this.root, ...parts);
    try { ownerMode(result, false); } catch (e) { if (!missing(e)) throw e; }
    return result;
  }
  read(name: string): Buffer { return readSafe(this.path(name)); }
  write(name: string, content: Buffer): void {
    if (content.length > LIMIT) throw new Error('file_too_large');
    const target = this.path(name, true), temp = path.join(path.dirname(target), `.write-${randomUUID()}`);
    const fd = fs.openSync(temp, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o600);
    try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    try {
      this.path(name); fs.renameSync(temp, target);
      const dir = fs.openSync(path.dirname(target), fs.constants.O_RDONLY);
      try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
    } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  }
  writeOnce(name: string, content: Buffer): void {
    try { if (!this.read(name).equals(content)) throw new Error('immutable_artifact_conflict'); }
    catch (e) { if (!missing(e)) throw e; this.write(name, content); }
  }
  recoverLock(name: string, expectedNonce: string): void {
    if (!['publish', 'install'].includes(name) || !/^[0-9a-f-]{36}$/.test(expectedNonce)) throw new Error('explicit_lock_identity_required');
    noLinks(this.root); ownerMode(this.root, true);
    const target = path.join(this.root, `${name}.lock`), lease = path.join(target, 'owner.json');
    ownerMode(target, true);
    const before = readSafe(lease), owner = object(parseJson(before), ['pid', 'nonce']);
    if (owner.nonce !== expectedNonce || !Number.isSafeInteger(owner.pid) || Number(owner.pid) <= 0) throw new Error('lock_identity_mismatch');
    try { process.kill(Number(owner.pid), 0); throw new Error('lock_owner_still_running'); }
    catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ESRCH') throw e; }
    if (!readSafe(lease).equals(before) || fs.readdirSync(target).join('|') !== 'owner.json') throw new Error('lock_changed_during_recovery');
    fs.unlinkSync(lease); fs.rmdirSync(target);
  }
  lock<T>(name: string, action: () => T): T {
    if (!/^[a-z-]+$/.test(name)) throw new Error('invalid_lock_name');
    noLinks(this.root); ownerMode(this.root, true);
    const target = path.join(this.root, `${name}.lock`);
    try { fs.mkdirSync(target, { mode: 0o700 }); } catch { throw new Error('writer_lock_held_or_stale'); }
    const nonce = randomUUID(), lease = path.join(target, 'owner.json');
    try {
      fs.writeFileSync(lease, jsonBytes({ pid: process.pid, nonce }), { mode: 0o600, flag: 'wx' });
      const result = action();
      if (result instanceof Promise) throw new Error('synchronous_lock_action_required');
      return result;
    } finally {
      // Never remove an unowned or substituted lock. Crashes intentionally fail closed.
      ownerMode(target, true);
      const owner = object(parseJson(readSafe(lease)), ['pid', 'nonce']);
      if (owner.nonce !== nonce || owner.pid !== process.pid) throw new Error('lock_owner_changed');
      fs.unlinkSync(lease); fs.rmdirSync(target);
    }
  }
}
