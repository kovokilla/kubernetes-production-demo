import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtempSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createReceiver } from '../charts/monitoring-resources/files/receiver.mjs';

test('exports only expiry metadata and bounded notification counters', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'monitoring-test-'));
  const expiryFile = join(directory, 'expires_at');
  writeFileSync(expiryFile, '2030-01-01T00:00:00Z');
  const logs = [];
  const server = createReceiver({ expiryFile, log: value => logs.push(value) });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  t.after(() => rmSync(directory, { recursive: true }));
  const base = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  const response = await fetch(`${base}/alerts`, {
    method: 'POST',
    body: JSON.stringify({
      status: 'firing',
      alerts: [{
        labels: { alertname: 'MonitoringSmokeTest', severity: 'warning', namespace: 'monitoring', ignored: 'private' },
        annotations: { ignored: 'do-not-log-this' },
      }],
    }),
  });
  assert.equal(response.status, 202);
  assert.deepEqual(logs[0], {
    event: 'alert_notification', status: 'firing',
    labels: { alertname: 'MonitoringSmokeTest', severity: 'warning', namespace: 'monitoring' },
  });
  assert.equal(JSON.stringify(logs).includes('do-not-log-this'), false);
  const metrics = await (await fetch(`${base}/metrics`)).text();
  assert.match(metrics, /jenkins_deployer_credential_expiry_timestamp_seconds 1893456000/);
  assert.match(metrics, /monitoring_test_alert_notifications_total\{status="firing"\} 1/);
  assert.equal((await fetch(`${base}/alerts`, { method: 'POST', body: '{invalid' })).status, 400);
  assert.equal((await fetch(`${base}/other`)).status, 404);
});

test('missing public metadata emits zero instead of inventing an expiry', async t => {
  const server = createReceiver({ expiryFile: '/nonexistent/metadata', log: () => {} });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  const metrics = await (await fetch(`http://127.0.0.1:${server.address().port}/metrics`)).text();
  assert.match(metrics, /jenkins_deployer_credential_expiry_timestamp_seconds 0/);
});

test('starts when executed through a ConfigMap-style symlink', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'monitoring-symlink-'));
  const link = join(directory, 'receiver.mjs');
  symlinkSync(fileURLToPath(new URL('../charts/monitoring-resources/files/receiver.mjs', import.meta.url)), link);
  const child = spawn(process.execPath, [link], {
    env: { ...process.env, PORT: '0' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(async () => {
    child.kill('SIGTERM');
    if (child.exitCode === null) await once(child, 'exit');
    rmSync(directory, { recursive: true });
  });
  const [chunk] = await once(child.stdout, 'data', { signal: AbortSignal.timeout(5000) });
  const started = JSON.parse(chunk.toString());
  assert.equal(started.event, 'receiver_started');
  assert.equal((await fetch(`http://127.0.0.1:${started.port}/healthz`)).status, 200);
});
