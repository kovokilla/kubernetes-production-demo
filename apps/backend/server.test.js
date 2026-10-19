import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createHmac } from 'node:crypto';
import { createApp } from './server.js';

test('API, secret-backed signing, readiness and routing', async t => {
  const app = createApp({ signingKey: 'test-only-key', instance: 'test-pod', message: 'Test message' });
  app.api.listen(0, '127.0.0.1');
  await once(app.api, 'listening');
  t.after(() => new Promise(resolve => app.api.close(resolve)));
  const base = `http://127.0.0.1:${app.api.address().port}`;
  const response = await fetch(`${base}/api/info`);
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.message, 'Test message');
  assert.equal(data.instance, 'test-pod');
  assert.equal(data.signature, createHmac('sha256', 'test-only-key').update('test-pod').digest('hex'));
  assert.equal(JSON.stringify(data).includes('test-only-key'), false);
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  app.setReady(false);
  assert.equal((await fetch(`${base}/readyz`)).status, 503);
  assert.equal((await fetch(`${base}/api/missing`)).status, 404);
  assert.equal((await fetch(`${base}/api/info`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${base}//`)).status, 404);
  assert.equal((await fetch(`${base}/api/info?query=test`)).status, 200);
});

test('missing signing key is rejected', () => {
  assert.throws(() => createApp(), /SIGNING_KEY is required/);
});
