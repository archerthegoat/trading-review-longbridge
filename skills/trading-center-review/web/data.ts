import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import type { DisplaySnapshot, Weekly } from './types.ts';

export const PYTHON = '/usr/bin/python3';
const ADAPTER = fileURLToPath(new URL('../scripts/trading_review_display.py', import.meta.url));
export function dataOperation(operation: 'validate' | 'project' | 'weekly' | 'weekly-db', input: unknown | Buffer): unknown {
  const result = spawnSync(PYTHON, ['-E', '-s', '-B', ADAPTER, operation], {
    input: Buffer.isBuffer(input) ? input : JSON.stringify(input), encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024, timeout: 15000,
    env: { PATH: '/usr/bin:/bin', PYTHONIOENCODING: 'utf-8', LANG: 'en_US.UTF-8' },
  });
  if (result.error || result.status !== 0) throw new Error('display_data_gate_failed');
  return JSON.parse(result.stdout) as unknown;
}
export const validate = (value: unknown): DisplaySnapshot => dataOperation('validate', value) as DisplaySnapshot;
export const project = (daily: unknown, weekly: unknown): DisplaySnapshot => dataOperation('project', { daily, weekly }) as DisplaySnapshot;
export const weeklyData = (value: unknown): Weekly => dataOperation('weekly', value) as Weekly;
export const weeklyFromDatabase = (review_key: string): Weekly => dataOperation('weekly-db', { review_key }) as Weekly;
