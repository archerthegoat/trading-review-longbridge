import { createServer } from 'node:http';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { PublicationStore, ROUTE } from './publication.ts';
import { jsonBytes } from './private-store.ts';

export const PORT = 8765;
export const CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'";
export function makeServer(store: PublicationStore) {
  const server = createServer((request: IncomingMessage, response: ServerResponse) => {
    const port = (server.address() as { port: number }).port;
    const hosts = [`127.0.0.1:${port}`, `localhost:${port}`];
    const raw = request.rawHeaders.filter((_, i) => i % 2 === 0).map(h => h.toLowerCase());
    const permitted = raw.filter(h => h === 'host').length === 1 && hosts.includes((request.headers.host ?? '').toLowerCase()) &&
      raw.filter(h => h === 'origin').length <= 1 && (!request.headers.origin || hosts.some(h => request.headers.origin === `http://${h}`)) &&
      ['none', 'same-origin', 'same-site'].includes(String(request.headers['sec-fetch-site'] ?? 'none'));
    const reply = (status: number, content: Buffer | string, type = 'text/plain; charset=utf-8') => {
      const body = typeof content === 'string' ? Buffer.from(content) : content;
      response.writeHead(status, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store',
        'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
        'Cross-Origin-Resource-Policy': 'same-origin', 'X-Frame-Options': 'DENY' });
      response.end(request.method === 'HEAD' ? undefined : body);
    };
    if (!permitted) { reply(403, 'Local access only.\n'); return; }
    if (!['GET', 'HEAD'].includes(request.method ?? '')) { reply(405, 'Read-only local service.\n'); return; }
    const url = request.url ?? '';
    if (url === '/favicon.ico') { reply(204, ''); return; }
    if (!['/', '/healthz'].includes(url) && !ROUTE.test(url)) { reply(404, 'Not found.\n'); return; }
    try {
      const index = store.index();
      const id = url === '/' || url === '/healthz' ? index.current : index.routes[url];
      if (!id) { reply(404, 'Not found.\n'); return; }
      const { html, manifest } = store.load(id);
      if (url === '/healthz') reply(200, jsonBytes({ status: 'ready', publication_id: id, html_sha256: manifest.html_sha256, source_times: manifest.source_times }), 'application/json; charset=utf-8');
      else reply(200, html, 'text/html; charset=utf-8');
    } catch { reply(503, 'Published review unavailable. Check the local service status.\n'); }
  });
  server.requestTimeout = 5000; server.headersTimeout = 5000; server.keepAliveTimeout = 1000; server.maxConnections = 32;
  server.on('clientError', (_error, socket) => { if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n'); });
  return server;
}
