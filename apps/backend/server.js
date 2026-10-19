import http from 'node:http';
import { createHmac } from 'node:crypto';
import { hostname } from 'node:os';
import { pathToFileURL } from 'node:url';

export function createApp({ message = 'Hello from Kubernetes!', signingKey, instance = hostname() } = {}) {
  if (!signingKey) throw new Error('SIGNING_KEY is required');
  let ready = true;
  const started = Date.now();
  const api = http.createServer((req, res) => {
    const path = req.url.split('?', 1)[0];
    let status = 200;
    let body;
    if (req.method !== 'GET') {
      status = 405;
      body = { error: 'Method not allowed' };
    } else if (path === '/healthz') {
      body = { status: 'ok' };
    } else if (path === '/readyz') {
      status = ready ? 200 : 503;
      body = { ready };
    } else if (path === '/api/info') {
      body = {
        message,
        instance,
        uptimeSeconds: Math.floor((Date.now() - started) / 1000),
        signature: createHmac('sha256', signingKey).update(instance).digest('hex'),
      };
    } else {
      status = 404;
      body = { error: 'Not found' };
    }
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  });
  api.requestTimeout = 15_000;
  api.headersTimeout = 10_000;
  return { api, setReady: value => { ready = value; } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const app = createApp({ message: process.env.APP_MESSAGE, signingKey: process.env.SIGNING_KEY });
  app.api.listen(Number(process.env.PORT ?? 8080), '0.0.0.0');
  console.log(JSON.stringify({ event: 'started', port: Number(process.env.PORT ?? 8080) }));
  process.on('SIGTERM', () => {
    app.setReady(false);
    // Give endpoint propagation time before closing keep-alive connections.
    setTimeout(() => {
      app.api.close();
    }, 5000);
    setTimeout(() => process.exit(1), 25_000).unref();
  });
}
