import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export async function createFrontend({ backendUrl = 'http://demo-backend:8080' } = {}) {
  const html = await readFile(new URL('./public/index.html', import.meta.url));
  const script = await readFile(new URL('./public/app.js', import.meta.url));
  const headers = {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    'Cache-Control': 'no-store',
  };
  const server = http.createServer((req, res) => {
    if (req.method !== 'GET') {
      res.writeHead(405, headers).end();
    } else if (req.url === '/healthz' || req.url === '/readyz') {
      res.writeHead(200, { ...headers, 'Content-Type': 'text/plain' }).end('ok');
    } else if (req.url === '/api/info') {
      // Fixed destination and route: never act as an arbitrary forward proxy.
      const upstream = http.get(new URL('/api/info', backendUrl), { timeout: 5000 }, response => {
        res.writeHead(response.statusCode, { ...headers, 'Content-Type': 'application/json' });
        response.pipe(res);
        response.on('error', () => res.destroy());
      });
      upstream.on('timeout', () => upstream.destroy(new Error('Upstream timeout')));
      upstream.on('error', () => {
        if (!res.headersSent) {
          res.writeHead(502, { ...headers, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Backend unavailable' }));
        } else res.destroy();
      });
      res.on('close', () => upstream.destroy());
    } else if (req.url === '/' || req.url === '/app.js') {
      res.writeHead(200, {
        ...headers,
        'Content-Type': req.url === '/' ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8',
      }).end(req.url === '/' ? html : script);
    } else {
      res.writeHead(404, headers).end('Not found');
    }
  });
  server.requestTimeout = 15_000;
  server.headersTimeout = 10_000;
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = await createFrontend({ backendUrl: process.env.BACKEND_URL });
  server.listen(8080, '0.0.0.0');
  console.log(JSON.stringify({ event: 'started', port: 8080 }));
  process.on('SIGTERM', () => {
    setTimeout(() => server.close(), 5000);
    setTimeout(() => process.exit(1), 25_000).unref();
  });
}
