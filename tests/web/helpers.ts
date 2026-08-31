import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PYTHON, project } from '../../skills/trading-center-review/web/data.ts';
import { PublicationStore } from '../../skills/trading-center-review/web/publication.ts';
import type { Weekly } from '../../skills/trading-center-review/web/types.ts';

export const ROOT = fileURLToPath(new URL('../../', import.meta.url));
export function python(code: string, input?: unknown): string {
  const r = spawnSync(PYTHON, ['-E', '-s', '-B', '-c', code], { cwd: ROOT, input: input === undefined ? undefined : JSON.stringify(input), encoding: 'utf8', timeout: 15000, maxBuffer: 8 * 1024 * 1024 });
  if (r.status !== 0 || r.error) throw new Error(`synthetic_python_failed: ${r.stderr}`);
  return r.stdout;
}
export const fixture = (name = 'complete') => JSON.parse(readFileSync(join(ROOT, `tests/fixtures/dashboard_v2_${name}.json`), 'utf8')) as Record<string, any>;
export function weekly(): Weekly {
  return JSON.parse(python("import sys,json; sys.path.insert(0,'tests'); from test_render_trade_review_dashboard_v2 import weekly_packet; print(json.dumps(weekly_packet()))")) as Weekly;
}
export const snapshot = (name = 'complete') => project(fixture(name), weekly());
export const normalized = (html: string) => html.replace(/>\s+</g, '><').replace(/\s+/g, ' ').trim();
export function pythonRender(view: unknown): string {
  return python("import sys,json; sys.path.insert(0,'skills/trading-center-review/scripts'); import render_trade_review_dashboard_v2 as d; print(d.render_display_snapshot(json.load(sys.stdin),d.DEFAULT_TEMPLATE.read_text()),end='')", view);
}
export function isolated() {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mars-ts-web-test-')));
  return { root, store: new PublicationStore(join(root, 'web'), { testRoot: root, create: true }), cleanup: () => rmSync(root, { recursive: true }) };
}
