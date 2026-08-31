import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import { join } from 'node:path';
import { request } from 'node:http';
import { once } from 'node:events';
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { PrivateTree, parseJson, jsonBytes, hash, canonical, privateInput } from '../../skills/trading-center-review/web/private-store.ts';
import { prepare } from '../../skills/trading-center-review/web/publication.ts';
import { makeServer } from '../../skills/trading-center-review/web/service.ts';
import { packageSource, PACKAGE_FILES, verifyInstallation } from '../../skills/trading-center-review/web/launchagent.ts';
import { isolated, snapshot } from './helpers.ts';

test('atomic publications are private, idempotent, weekly-preserving and independently rebuildable', t => {
  const f = isolated(); t.after(f.cleanup); const v = snapshot();
  const one = f.store.publish(v, { route: '/first/', expectedCurrent: null });
  assert.equal(f.store.publish(v).publication_id, one.publication_id);
  const p = prepare(f.store); assert.equal(p.current, one.publication_id);
  assert.equal(canonical(p.view.weekly), canonical(v.weekly));
  p.view.daily.meta.generated_at = '2026-08-31T09:00:00+08:00';
  const two = f.store.publish(p.view, { expectedCurrent: p.current });
  assert.notEqual(one.publication_id, two.publication_id);
  assert.equal(f.store.index().routes['/first/'], one.publication_id);
  assert.equal(f.store.load().manifest.source_times.weekly_generated_at, v.weekly!.meta.generated_at);
  assert.equal(f.store.publish(f.store.load().view).publication_id, two.publication_id);
  assert.equal(f.store.rollback().publication_id, one.publication_id);
  assert.equal(f.store.rollback().publication_id, two.publication_id);
  assert.equal(fs.statSync(f.store.root).mode & 0o777, 0o700);
  assert.equal(fs.statSync(f.store.path('publications.json')).mode & 0o777, 0o600);
});
test('validation, stale preparation, history mutation and source regression cannot change the current index', t => {
  const f = isolated(); t.after(f.cleanup); const v = snapshot();
  f.store.publish(v, { route: '/first/' }); const before = f.store.read('publications.json');
  assert.throws(() => f.store.publish(v, { expectedCurrent: null }));
  const changed = structuredClone(v); changed.daily.meta.generated_at = '2026-08-31T09:00:00+08:00';
  assert.throws(() => f.store.publish(changed, { route: '/first/' }));
  changed.daily.meta.generated_at = '2026-08-20T09:00:00+08:00'; assert.throws(() => f.store.publish(changed));
  changed.daily.meta.generated_at = '2026-08-31T09:00:00+08:00'; changed.weekly = null; assert.throws(() => f.store.publish(changed));
  const unknown = { ...v, account: 'not admitted' }; assert.throws(() => f.store.publish(unknown));
  assert.ok(f.store.read('publications.json').equals(before));
});
test('failure before index commit leaves a valid old page; retry resumes the same immutable bundle', t => {
  const f = isolated(); t.after(f.cleanup); const v = snapshot(); const one = f.store.publish(v);
  v.daily.meta.generated_at = '2026-08-31T09:00:00+08:00';
  const original = f.store.write.bind(f.store);
  f.store.write = (name, bytes) => { if (name === 'publications.json') throw new Error('simulated_crash_before_pointer'); original(name, bytes); };
  assert.throws(() => f.store.publish(v)); assert.equal(f.store.load().manifest.publication_id, one.publication_id);
  f.store.write = original;
  const two = f.store.publish(v); assert.equal(f.store.load().manifest.publication_id, two.publication_id);
  assert.notEqual(two.publication_id, one.publication_id);
});
test('second writer and unsafe paths, symlinks, hardlinks, modes and tampering fail closed', t => {
  const f = isolated(); t.after(f.cleanup);
  assert.throws(() => new PrivateTree(join(f.root, 'other')));
  assert.throws(() => f.store.path('../escape'));
  assert.throws(() => f.store.path('/absolute'));
  assert.throws(() => privateInput('/etc/passwd'));
  f.store.lock('publish', () => assert.throws(() => f.store.lock('publish', () => null)));
  fs.mkdirSync(join(f.root, 'git'), { mode: 0o700 }); fs.mkdirSync(join(f.root, 'git/.git'));
  assert.throws(() => new PrivateTree(join(f.root, 'git/web'), { testRoot: f.root, create: true }));
  const manifest = f.store.publish(snapshot()); const name = `publications/${manifest.publication_id}/index.html`;
  const file = f.store.path(name); const saved = fs.readFileSync(file);
  fs.chmodSync(file, 0o644); assert.throws(() => f.store.load()); fs.chmodSync(file, 0o600);
  const link = join(f.root, 'extra-link'); fs.linkSync(file, link); assert.throws(() => f.store.load()); fs.unlinkSync(link);
  fs.writeFileSync(file, 'corrupt'); assert.throws(() => f.store.load()); fs.writeFileSync(file, saved);
  const moved = join(f.root, 'moved.html'); fs.renameSync(file, moved); fs.symlinkSync(moved, file); assert.throws(() => f.store.load());
});
test('strict bounded JSON rejects duplicates, nonfinite values, prototype keys, malformed UTF-8 and trailing data', () => {
  for (const value of ['{"a":1,"a":2}', '{"__proto__":{}}', 'NaN', '1e999', '[0,]', '{"a":1}{}', '01', '"bad\nstring"']) assert.throws(() => parseJson(Buffer.from(value)));
  assert.throws(() => parseJson(Buffer.from([0xc3, 0x28])));
  const value = { 中文: ['quote"', null, true, 0.125], abc: { z: 0, a: 'safe' } };
  assert.equal(canonical(parseJson(jsonBytes(value))), canonical(value));
});
test('installed source package contains TS UI and data adapter, no Python HTTP service or development dependencies', t => {
  const f = isolated(); t.after(f.cleanup);
  const bundle = packageSource();
  assert.equal(bundle.code_id, hash(jsonBytes(bundle.hashes)));
  assert.equal(Object.keys(bundle.files).length, PACKAGE_FILES.length);
  assert.ok('web/render.ts' in bundle.files); assert.ok('scripts/trading_review_display.py' in bundle.files);
  assert.ok(Object.keys(bundle.files).every(n => !n.includes('node_modules') && !n.endsWith('trading_review_web.py')));
  for (const [name, content] of Object.entries(bundle.files)) f.store.write(`code/${bundle.code_id}/${name}`, content);
  f.store.write('installation.json', jsonBytes({ schema_version: 'trading-review-ts-installation.v1', code_id: bundle.code_id, files: bundle.hashes, node: process.execPath, node_version: process.versions.node, architecture: process.arch }));
  assert.equal(verifyInstallation(f.store).code_id, bundle.code_id);
  f.store.write(`code/${bundle.code_id}/web/render.ts`, Buffer.from('tampered'));
  assert.throws(() => verifyInstallation(f.store));
});
test('crashed-writer lock recovery requires exact nonce and a proven dead PID, never a timeout', t => {
  const f = isolated(); t.after(f.cleanup);
  const nonce = randomUUID(), dir = join(f.store.root, 'publish.lock');
  fs.mkdirSync(dir, { mode: 0o700 });
  fs.writeFileSync(join(dir, 'owner.json'), jsonBytes({ pid: process.pid, nonce }), { mode: 0o600 });
  assert.throws(() => f.store.recoverLock('publish', nonce));
  const child = spawnSync(process.execPath, ['-e', 'process.exit(0)']); assert.equal(child.status, 0);
  fs.writeFileSync(join(dir, 'owner.json'), jsonBytes({ pid: child.pid, nonce }));
  assert.throws(() => f.store.recoverLock('publish', randomUUID()));
  f.store.recoverLock('publish', nonce); assert.equal(fs.existsSync(dir), false);
  assert.equal(f.store.lock('publish', () => 'recovered'), 'recovered');
});
function get(port: number, url: string, method = 'GET', headers: Record<string, string> = {}) {
  return new Promise<{ status: number; body: Buffer; headers: import('node:http').IncomingHttpHeaders }>((resolve, reject) => {
    const req = request({ hostname: '127.0.0.1', port, path: url, method, headers }, res => {
      const chunks: Buffer[] = []; res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode!, body: Buffer.concat(chunks), headers: res.headers }));
    }); req.on('error', reject); req.end();
  });
}
test('public HTTP seam is loopback/read-only and never maps URLs to private files', async t => {
  const f = isolated(); t.after(f.cleanup); const m = f.store.publish(snapshot(), { route: '/review/' });
  const server = makeServer(f.store); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => new Promise<void>(resolve => server.close(() => resolve())));
  const address = server.address() as { address: string; port: number }; assert.equal(address.address, '127.0.0.1');
  for (const url of ['/', '/review/']) { const r = await get(address.port, url); assert.equal(r.status, 200); assert.equal(hash(r.body), m.html_sha256); assert.match(String(r.headers['content-security-policy']), /default-src 'none'/); assert.equal(r.headers['access-control-allow-origin'], undefined); }
  const h = await get(address.port, '/healthz'); assert.equal(h.status, 200); assert.equal(JSON.parse(h.body.toString()).publication_id, m.publication_id); assert.doesNotMatch(h.body.toString(), /positions|account|plan_id/);
  for (const url of ['/view.json', '/daily-dashboard.json', '/publications.json', '/../../etc/passwd', '/%2e%2e/', '/absent/', '/?source=true']) assert.equal((await get(address.port, url)).status, 404);
  const deniedHeaders: Record<string, string>[] = [{ Host: 'evil.example' }, { Origin: 'https://evil.example' }, { 'Sec-Fetch-Site': 'cross-site' }, { Origin: 'null' }];
  for (const headers of deniedHeaders) assert.equal((await get(address.port, '/', 'GET', headers)).status, 403);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE']) assert.equal((await get(address.port, '/', method)).status, 405);
  const head = await get(address.port, '/', 'HEAD'); assert.equal(head.status, 200); assert.equal(head.body.length, 0); assert.equal(Number(head.headers['content-length']), f.store.load().html.length);
  fs.writeFileSync(f.store.path(`publications/${m.publication_id}/index.html`), 'tampered');
  assert.equal((await get(address.port, '/')).status, 503); assert.equal((await get(address.port, '/healthz')).status, 503);
});
