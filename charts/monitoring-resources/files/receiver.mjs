import http from 'node:http';
import { readFileSync, realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function createReceiver({
  expiryFile = '/metadata/expires_at',
  log = value => console.log(JSON.stringify(value)),
} = {}) {
  const notifications = { firing: 0, resolved: 0 };
  const smoke = { firing: 0, resolved: 0 };
  return http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/healthz') {
      res.writeHead(200).end('ok');
      return;
    }
    if (req.method === 'GET' && req.url === '/metrics') {
      let expires = 0;
      try {
        expires = Date.parse(readFileSync(expiryFile, 'utf8')) / 1000;
        if (!Number.isFinite(expires) || expires < 0) expires = 0;
      } catch { /* Missing public metadata is exposed as zero and alerted on. */ }
      const lines = [
        '# HELP jenkins_deployer_credential_expiry_timestamp_seconds Expiration of the restricted deployment credential.',
        '# TYPE jenkins_deployer_credential_expiry_timestamp_seconds gauge',
        `jenkins_deployer_credential_expiry_timestamp_seconds ${expires}`,
        '# HELP monitoring_alert_notifications_total Received alert notifications by status.',
        '# TYPE monitoring_alert_notifications_total counter',
        ...Object.entries(notifications).map(([status, count]) =>
          `monitoring_alert_notifications_total{status="${status}"} ${count}`),
        '# HELP monitoring_test_alert_notifications_total Received synthetic delivery-test alerts.',
        '# TYPE monitoring_test_alert_notifications_total counter',
        ...Object.entries(smoke).map(([status, count]) =>
          `monitoring_test_alert_notifications_total{status="${status}"} ${count}`),
      ];
      res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4; charset=utf-8' });
      res.end(`${lines.join('\n')}\n`);
      return;
    }
    if (req.method !== 'POST' || req.url !== '/alerts') {
      res.writeHead(404).end();
      return;
    }
    const chunks = [];
    let size = 0;
    req.on('data', chunk => {
      size += chunk.length;
      if (size > 65536) {
        res.writeHead(413).end();
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on('end', () => {
      try {
        const message = JSON.parse(Buffer.concat(chunks).toString());
        if (!Array.isArray(message.alerts) || message.alerts.length > 100) {
          throw new Error('Invalid alert batch');
        }
        for (const alert of message.alerts) {
          const status = alert.status ?? message.status;
          if (!Object.hasOwn(notifications, status)) continue;
          notifications[status] += 1;
          const labels = Object.fromEntries(
            ['alertname', 'severity', 'namespace'].map(key =>
              [key, String(alert.labels?.[key] ?? '').slice(0, 200)]));
          if (labels.alertname === 'MonitoringSmokeTest') smoke[status] += 1;
          // Never log annotations, complete payloads, credential values or URLs.
          log({ event: 'alert_notification', status, labels });
        }
        res.writeHead(202).end();
      } catch {
        res.writeHead(400).end('Invalid notification');
      }
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const server = createReceiver();
  server.listen(Number(process.env.PORT ?? 8080), '0.0.0.0', () => {
    console.log(JSON.stringify({ event: 'receiver_started', port: server.address().port }));
  });
  process.on('SIGTERM', () => server.close());
}
