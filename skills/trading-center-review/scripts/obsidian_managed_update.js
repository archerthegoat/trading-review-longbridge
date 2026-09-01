// Fixed code executed inside Obsidian. The only substituted token is JSON data.
(async () => {
  const request = /*__REQUEST__*/;
  const fs = require('node:fs');
  const path = require('node:path');
  const crypto = require('node:crypto');
  const hash = value => crypto.createHash('sha256').update(value, 'utf8').digest('hex');
  const start = '<!-- trading-review:managed:start -->';
  const end = '<!-- trading-review:managed:end -->';
  const isOpen = () => app.workspace.getLeavesOfType('markdown').some(leaf => leaf.view?.file?.path === request.path);
  const fail = reason => { throw new Error(reason); };
  try {
    if (app.vault.getName() !== 'Mars知识库vault' || app.vault.adapter.getBasePath() !== request.vault) fail('vault_mismatch');
    if (!/^25 投资交易\/(?:10 每日复盘\/\d{4}-\d\d-\d\d 交易复盘|20 周度复盘\/\d{4}-\d\d-\d\d 至 \d{4}-\d\d-\d\d 周度复盘)\.md$/.test(request.path)) fail('target_path_invalid');
    if (isOpen()) return JSON.stringify({status: 'deferred', reason: 'note_open'});
    if (request.operation === 'probe') return JSON.stringify({status: 'ready'});
    if (request.operation !== 'update') fail('operation_invalid');
    const file = app.vault.getAbstractFileByPath(request.path);
    if (!file || file.path !== request.path || file.extension !== 'md') return JSON.stringify({status: 'deferred', reason: 'note_not_indexed'});
    const absolute = path.join(request.vault, request.path);
    await app.vault.process(file, current => {
      // No await in this callback. Check again at the actual Obsidian write seam.
      if (isOpen()) fail('note_open');
      const info = fs.lstatSync(absolute);
      if (info.isSymbolicLink() || !info.isFile() || info.nlink !== 1 || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.ino !== request.identity.inode || info.dev !== request.identity.device) fail('file_identity_changed');
      if (hash(current) !== request.before_hash) fail('content_changed');
      if (current.split(start).length !== 2 || current.split(end).length !== 2 || current.indexOf(start) >= current.indexOf(end)) fail('markers_changed');
      const first = current.indexOf(start), last = current.indexOf(end) + end.length;
      if (!current.slice(last).includes('## 我的补充')) fail('manual_section_missing');
      const next = current.slice(0, first) + request.managed_body + current.slice(last);
      if (hash(next) !== request.after_hash) fail('after_hash_mismatch');
      return next;
    });
    return JSON.stringify({status: 'written'});
  } catch (error) {
    const known = new Set(['vault_mismatch', 'target_path_invalid', 'operation_invalid', 'file_identity_changed', 'content_changed', 'markers_changed', 'manual_section_missing', 'after_hash_mismatch', 'note_open']);
    const reason = known.has(error.message) ? error.message : 'obsidian_atomic_update_failed';
    return JSON.stringify({status: reason === 'note_open' ? 'deferred' : 'conflict', reason});
  }
})()
