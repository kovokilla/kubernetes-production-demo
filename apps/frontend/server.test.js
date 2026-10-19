import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createFrontend } from './server.js';
import { createApp } from '../backend/server.js';

test('static frontend, same-origin proxy, private routes and upstream failure', async t => {
  const backend = createApp({ signingKey: 'test-only-key' }).api;
  backend.listen(0, '127.0.0.1');
  await once(backend, 'listening');
  const frontend = await createFrontend({ backendUrl: `http://127.0.0.1:${backend.address().port}` });
  frontend.listen(0, '127.0.0.1');
  await once(frontend, 'listening');
  t.after(() => new Promise(resolve => frontend.close(resolve)));
  t.after(() => new Promise(resolve => backend.close(resolve)));
  const base = `http://127.0.0.1:${frontend.address().port}`;
  const page = await fetch(base);
  assert.match(await page.text(), /Kubernetes Production Demo/);
  assert.match(page.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  assert.equal((await fetch(`${base}/app.js`)).status, 200);
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  assert.equal((await fetch(`${base}/readyz`)).status, 200);
  assert.equal((await fetch(`${base}/api/info`)).status, 200);
  assert.equal((await fetch(`${base}/metrics`)).status, 404);
  assert.equal((await fetch(`${base}/api/other`)).status, 404);
  assert.equal((await fetch(base, { method: 'POST' })).status, 405);
  await new Promise(resolve => backend.close(resolve));
  assert.equal((await fetch(`${base}/api/info`)).status, 502);
});
