import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import * as crypto from 'node:crypto';
import * as path from 'node:path';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../../skills/trading-center-review/scripts/obsidian_managed_update.js', import.meta.url), 'utf8');
const hash = (value: string) => createHash('sha256').update(value).digest('hex');
const start = '<!-- trading-review:managed:start -->', end = '<!-- trading-review:managed:end -->';
const relative = '25 投资交易/10 每日复盘/2026-08-28 交易复盘.md';

async function exercise(mutate?: (state: any) => void, body = '已核对计划。') {
  const before = `---\ntitle: human\n---\n${start}\n旧正文\n${end}\n\n## 我的补充\n\n用户的字节。\n`;
  const block = `${start}\n${body}\n${end}`;
  const after = before.replace(`${start}\n旧正文\n${end}`, block);
  const state = { text: before, open: false, inode: 42, writes: 0 };
  const request = { operation: 'update', vault: '/isolated/vault', path: relative, before_hash: hash(before), after_hash: hash(after), managed_body: block, identity: { device: 1, inode: 42 } };
  const app = {
    workspace: { getLeavesOfType: () => state.open ? [{ view: { file: { path: relative } } }] : [] },
    vault: {
      getName: () => 'Mars知识库vault', adapter: { getBasePath: () => '/isolated/vault' },
      getAbstractFileByPath: () => ({ path: relative, extension: 'md' }),
      process: async (_file: unknown, callback: (text: string) => string) => {
        mutate?.(state);
        state.text = callback(state.text); state.writes++;
      },
    },
  };
  const mockFs = { lstatSync: () => ({ isSymbolicLink: () => false, isFile: () => true, nlink: 1, uid: 501, mode: 0o600, ino: state.inode, dev: 1 }) };
  const code = source.replace('/*__REQUEST__*/', JSON.stringify(request));
  const response = await runInNewContext(code, { app, process: { getuid: () => 501 }, require: (name: string) => ({ 'node:fs': mockFs, 'node:path': path, 'node:crypto': crypto }[name]) });
  return { state, result: JSON.parse(response), before, after };
}

test('native process callback preserves frontmatter and manual suffix exactly', async () => {
  const r = await exercise();
  assert.equal(r.result.status, 'written'); assert.equal(r.state.text, r.after); assert.equal(r.state.writes, 1);
});
test('last-moment editor opening, content editing and inode replacement refuse writes', async () => {
  for (const mutation of [(s: any) => { s.open = true; }, (s: any) => { s.text += '手工并发保存'; }, (s: any) => { s.inode++; }]) {
    const r = await exercise(mutation);
    assert.notEqual(r.result.status, 'written'); assert.equal(r.state.writes, 0);
    assert.ok(r.state.text.startsWith(r.before));
  }
});
test('request text is data, never interpolated into executable source', async () => {
  const r = await exercise(undefined, '"; throw new Error("injected"); //');
  assert.equal(r.result.status, 'written'); assert.equal(r.state.text, r.after);
});
