import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import * as path from 'node:path';
import { PublicationStore, prepare } from './publication.ts';
import { install, start, stop, status, uninstall, verifyAgent } from './launchagent.ts';
import { makeServer, PORT } from './service.ts';

async function main() {
  const { positionals, values } = parseArgs({ allowPositionals: true, options: {
    'daily-input': { type: 'string' }, 'display-input': { type: 'string' },
    'weekly-input': { type: 'string' }, 'weekly-key': { type: 'string' }, route: { type: 'string' },
    lock: { type: 'string' }, nonce: { type: 'string' },
  } });
  const [command] = positionals;
  const allowed = ['publish', 'rebuild', 'rollback', 'install', 'start', 'stop', 'restart', 'status', 'uninstall', 'serve', 'recover-lock'];
  if (positionals.length !== 1 || !allowed.includes(command!)) throw new Error('usage_publish_rebuild_rollback_install_start_stop_restart_status_uninstall');
  if (command === 'publish' && (values.lock || values.nonce)) throw new Error('recovery_options_require_recover_lock');
  if (command === 'recover-lock' && Object.keys(values).some(k => !['lock', 'nonce'].includes(k))) throw new Error('recovery_accepts_only_lock_identity');
  if (!['publish', 'recover-lock'].includes(command!) && Object.keys(values).length) throw new Error('options_only_for_publish_or_recovery');
  const store = new PublicationStore(undefined, { create: command === 'publish' });
  let result: unknown;
  if (command === 'publish') { const p = prepare(store, { dailyInput: values['daily-input'], displayInput: values['display-input'], weeklyInput: values['weekly-input'], weeklyKey: values['weekly-key'] }); result = store.publish(p.view, { route: values.route, expectedCurrent: p.current }); }
  else if (command === 'rebuild') { const current = store.index().current; result = store.publish(store.load(current).view, { expectedCurrent: current }); }
  else if (command === 'rollback') result = store.rollback();
  else if (command === 'install') result = install(store);
  else if (command === 'start') result = await start(store);
  else if (command === 'stop') result = stop(store);
  else if (command === 'restart') { stop(store); result = await start(store); }
  else if (command === 'status') result = status(store);
  else if (command === 'uninstall') result = uninstall(store);
  else if (command === 'recover-lock') { store.recoverLock(values.lock ?? '', values.nonce ?? ''); result = { status: 'dead_writer_lock_recovered' }; }
  else {
    const info = verifyAgent(store);
    if (fileURLToPath(import.meta.url) !== path.join(store.root, 'code', info.code_id, 'web/cli.ts')) throw new Error('serve_requires_pinned_installation');
    const server = makeServer(store);
    server.on('error', () => { console.error('web_service_start_failed'); process.exitCode = 2; });
    server.listen(PORT, '127.0.0.1', () => console.log('web_service_listening_loopback'));
    const close = () => server.close(() => process.exit(0));
    process.once('SIGTERM', close); process.once('SIGINT', close);
    return;
  }
  console.log(JSON.stringify(result));
}
main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : '';
  const code = (error as NodeJS.ErrnoException)?.code ?? '';
  console.error(/^[a-z][a-z0-9_-]{3,100}$/.test(message) ? message : /^[A-Z_]{3,32}$/.test(code) ? `local_web_io_${code}` : 'local_web_command_failed');
  process.exitCode = 2;
});
