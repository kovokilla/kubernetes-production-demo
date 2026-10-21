# Kubernetes application demo

Scope: frontend/backend Deployments, ClusterIP Services and Ingress, ConfigMap and Secret configuration, RBAC, NetworkPolicy, Jenkins CI/CD, and Prometheus/Alertmanager/Grafana monitoring.

**Application:** http://demo.localhost:8080 · **Jenkins:** http://127.0.0.1:8090 · **Job:** `production-demo`

## Contents

- [Architecture](#architecture)
- [Run locally](#run-locally)
- [Security](#security)
- [Public-release checklist](#public-release-checklist)
- [Jenkins CI/CD](#jenkins-cicd)
- [Prometheus and Alertmanager](#prometheus-and-alertmanager)
- [Grafana dashboard](#grafana-dashboard)
- [Validation without a cluster](#validation-without-a-cluster)
- [Operations and troubleshooting](#operations-and-troubleshooting)
- [Cleanup](#cleanup)

## Architecture

Browser → Traefik (`ingress` namespace) → frontend ClusterIP Service → frontend pods → backend ClusterIP Service → backend pods (`demo` namespace).

- **Frontend:** static HTML/JavaScript served by Node.js, with a fixed same-origin `/api/info` reverse proxy.
- **Backend:** dependency-free Node.js JSON API. `/healthz` and `/readyz` support Kubernetes probes.
- **Helm:** two replicas per Deployment, rolling updates, resource requests/limits and placement across nodes when possible.
- **ConfigMap:** application message and internal backend URL.
- **Secret:** externally created `demo-app`, key `signing-key`. The backend uses it to sign its pod identity with HMAC; the API never returns the key. This demonstrates Secret consumption, not user authentication.
- **kind:** one control-plane and two workers. Calico enforces NetworkPolicy; Traefik implements the Ingress.

Local kind is a reproducible exercise environment, not a highly available production cluster.

## Files

- `apps/frontend`, `apps/backend`: applications, tests and Dockerfiles.
- `charts/webapp`: application Helm chart.
- `charts/monitoring-resources`: monitoring probes, alert rules and local receiver.
- `environments/local/values.yaml`: local images and HTTP ingress.
- `environments/production/values.yaml`: optional HTTPS configuration for a real deployment.
- `environments/jenkins/values.yaml`: local-kind delivery settings used by Jenkins.
- `infrastructure`: kind, namespaces, pinned dependency versions, Traefik and optional deployment RBAC.
- `infrastructure/monitoring-values.yaml`, `infrastructure/blackbox-values.yaml`: bounded monitoring stack settings.
- `Jenkinsfile`: checkout → validate → build → load images → package → deploy → verify.
- `jenkins`: Docker Compose controller/agent, pinned plugins and configuration as code.
- `scripts`: bootstrap, local deployment and validation.
- `SECURITY.md`: trust boundaries, vulnerability reporting and release safeguards.
- `.gitleaks.toml`: default secret detectors with one exact public dependency-pin exception.

## Run locally

Prerequisites: Docker Desktop with its Compose plugin, kind, Python 3.9+, Node.js 24+, OpenSSH tools and GitHub CLI. The application can run on other Kubernetes platforms, but the pinned Jenkins controller/agent images in this repository target ARM64. The documented setup is macOS on Apple Silicon.

For a fresh checkout, install and start Docker Desktop, then:

```bash
brew install kind gh node python
gh auth login
gh repo clone kovokilla/kubernetes-production-demo ~/kubernetes-production-demo
cd ~/kubernetes-production-demo
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 scripts/fetch-helm.py
python3 scripts/fetch-kubectl.py
bash scripts/validate.sh
bash scripts/bootstrap.sh
bash scripts/deploy-local.sh
bash scripts/smoke-test.sh
```

Skip cloning if you already have the repository. GitHub authentication must permit reading the private repository; Jenkins setup also requires permission to register a read-only deploy key. Internet access is needed for image, plugin, binary and chart downloads, and SSH access to GitHub is needed for Jenkins checkout.

Allocate approximately 4 CPUs and 6 GB RAM for the application, or 8 CPUs and 8 GB RAM with Jenkins. Ports 8080 and 8443 must be free; Jenkins additionally uses 8090. Bootstrap downloads the pinned kind node, Calico manifest and Traefik chart. Review dependency versions in `infrastructure/versions.env` before upgrading.

Open **http://demo.localhost:8080**. If your resolver does not support `.localhost`, test using the explicit Host header:

```bash
curl --fail -H 'Host: demo.localhost' http://127.0.0.1:8080/api/info
```

All cluster scripts explicitly target `kind-production-demo`; they do not use your current Kubernetes context. Images are built locally and loaded directly into kind, with `imagePullPolicy: Never`. Before Jenkins takes ownership, re-running local deployment rebuilds the images and restarts the Deployments. Afterward, use the Jenkins job instead; the local script deliberately refuses competing updates.

The fetch helpers verify official SHA256 checksums and install Helm 3.19.0 and kubectl 1.34.0 into ignored `.tools/`, without changing global tool configuration. Project scripts prioritize that directory and detect Docker Desktop's standard macOS CLI directory if needed. A current Homebrew kubectl may be too new for the pinned Kubernetes 1.34 cluster; supported clients are within one minor version.

The chart's base values and `environments/production/values.yaml` are **examples**, with placeholder published images and an HTTPS hostname. Do not deploy them unchanged. `environments/local/values.yaml` supplies runnable local images and HTTP routing; Jenkins generates its own release values from `environments/jenkins/values.yaml`. A real cloud deployment additionally needs published images, DNS, a trusted TLS certificate and an enforcing CNI.

The Secret generation script creates a random key directly through Kubernetes stdin, without writing a plaintext credentials file, printing the value or replacing an existing Secret. Secrets are not Helm-managed. To rotate a key, use your secret-management process and increment `secretRevision` in chart values to restart pods consuming it as an environment variable.

## Security

- Dedicated frontend/backend service accounts have no API roles and do not mount API tokens.
- `demo-reader` grants `get/list/watch` for operational resources only. It excludes Secrets, mutation verbs and cluster-scoped resources.
- The RoleBinding grants access to the `demo-readers` identity-provider group and the `demo-observer` demonstration service account. A group name alone does not configure Kubernetes authentication.
- Containers run as UID/GID 10001 with read-only root filesystems, no privilege escalation, all capabilities dropped, and runtime-default seccomp.
- The `demo` namespace enforces the restricted Pod Security Standard.

Application NetworkPolicies allow only:

1. Application pods → CoreDNS in `kube-system`, TCP/UDP 53.
2. Traefik-labelled pods in namespace `ingress` → frontend pods, TCP 8080.
3. Frontend pods → backend pods in the same namespace, TCP 8080.

All other application pod ingress and egress is denied. Return traffic for permitted connections is allowed by the CNI. These policies require an enforcing CNI; kind's default CNI is deliberately disabled. Label-based isolation assumes untrusted users cannot create or relabel privileged workloads. Do not install unrelated workloads in the dedicated `demo` namespace.

`smoke-test.sh` verifies the full ingress/API path, positive and negative RBAC checks, allowed frontend → backend traffic, denied backend → frontend traffic, and denied access from an unrelated namespace. It creates a temporary test namespace and removes it afterward. It requires administrator impersonation and namespace-creation rights; it is not intended to run as `demo-reader`.

## Public-release checklist

See [SECURITY.md](SECURITY.md) for the supported trust boundaries and private vulnerability-reporting guidance. Publishing source does not make this local lab production-ready or expose its services automatically.

Before an explicitly authorized visibility change:

1. Run tests and review the exact changes intended for publication:

   ```bash
   bash scripts/validate.sh
   git diff --check
   ```

2. Install a reviewed Gitleaks release (v8.30.1 was validated), then scan all reachable history and current publishable source:

   ```bash
   bash scripts/security-check.sh
   ```

   The scan uses default detectors and fully redacts findings. Its one rule-scoped exception covers only the exact known public plugin-version line in `jenkins/controller/plugins.txt`; it does not exempt the whole file. The script rejects accidentally publishable private guides/generated directories and refuses to follow source symlinks without review. It is a separate local gate, not a mandatory Jenkins stage unless Gitleaks is also installed on that agent.

3. Review commit-author privacy. Using a GitHub noreply address affects future commits only. Existing author metadata remains unless history is deliberately rewritten; this project preserves its existing history.
4. Confirm generated credentials, kubeconfigs, private `_README.private.md` guides, tool downloads and local artifacts remain excluded. Never use force-add to bypass those exclusions. Review old CI logs/artifacts and package visibility separately from source visibility.
5. Configure protected `main`/rulesets as soon as the GitHub plan/visibility supports them: require pull requests, block force pushes/deletion, resolve conversations and require independent approval when a trusted reviewer is available. A sole maintainer cannot approve their own PR; choose that approval requirement deliberately. Do not require a CI status that Jenkins does not actually publish—the current job polls and deploys trusted `main`, not unreviewed PRs.
6. Enable available GitHub secret scanning, push protection and private vulnerability reporting after publication. Keep GitHub Actions disabled unless intentionally reviewed, and never run untrusted fork/PR code on the privileged Jenkins agent.
7. Review dependency/image advisories and choose an appropriate license before inviting general reuse.

GitHub can reject branch-protection/ruleset configuration for a private repository on a plan that does not support it. Do not silently substitute weaker protections or change visibility to bypass that decision; keep the repository private until the owner explicitly authorizes the next publication step.

The Jenkins/Grafana HTTP helpers now bind credentials to the configured loopback scheme/host/port, reject unsafe redirects and disable environment proxies. Same-origin redirects continue to work. This does not authenticate an unknown process listening on localhost; prefer helper-owned port-forwards and do not send local admin credentials to an untrusted listener.

## Jenkins CI/CD

The source repository remains on GitHub. Jenkins polls only `main` every two minutes; no public webhook, tunnel or GitHub Actions runner is needed. The local setup targets Apple Silicon/Linux ARM64 containers and requires Docker Compose plus an authenticated `gh` CLI with permission to register a read-only repository deploy key.

Commit and push the Jenkinsfile before the first setup. Setup queues an initial build to establish the SCM checkout baseline; subsequent pushes are detected by polling. If setup happened before the Jenkinsfile was available, run `python3 scripts/jenkins-client.py bootstrap` after pushing it.

After the cluster bootstrap above:

```bash
bash scripts/jenkins-setup.sh
python3 scripts/jenkins-client.py status
python3 scripts/jenkins-client.py lint
```

Open **http://127.0.0.1:8090**, username **admin**. Setup generates the password into a protected local file, `~/.local/share/kubernetes-production-demo/jenkins/admin_password`; import it securely into your password manager. The directory is mode 0700 and credential files are mode 0600. No credential contents are printed.

The controller has zero executors, no Docker socket, no anonymous access and no public listener. It connects to the separate `kind-builder` SSH agent using a dedicated private key and a pinned host public key. Private checkout uses a separate read-only GitHub deploy key; GitHub SSH host keys come from the authenticated HTTPS metadata API, not a trust-all SSH setting. Controller bootstrap secrets and agent bootstrap files are separate Docker volumes.

The `production-demo` Pipeline-from-SCM job:

1. Checks out trusted `main` and records the full commit SHA.
2. Runs application/release tests, source checks, Helm lint/render, infrastructure validation and strict Kubernetes schema checks.
3. Builds native ARM64 frontend and backend images with unique `sha-<commit>-build-<number>` tags.
4. Loads those images into every node of `production-demo`.
5. Updates chart image references, chart version `0.2.<build-number>` and `appVersion` in the workspace.
6. Archives the Helm `.tgz`, non-sensitive release `values.yaml` and image-ID provenance manifest.
7. Deploys that exact package using the namespaced deployer credential, waits for rollouts and checks the API through Traefik.

The pipeline uses `imagePullPolicy: Never` and the local HTTP overlay. It needs no registry credentials and does not publish images or charts to GHCR. Its packaged release is intended only for nodes where those exact local images have been loaded. It does not commit generated release versions back to Git. Previous GitHub-published artifacts remain unchanged.

Concurrent builds are disabled, builds time out after 30 minutes, and only the latest 15 build logs/10 artifact sets are retained. To trigger a build manually:

```bash
python3 scripts/jenkins-client.py build
python3 scripts/jenkins-client.py status
```

### Deployment credentials and renewal

`jenkins-setup.sh` applies `infrastructure/deployer-rbac.yaml` and creates a **24-hour** TokenRequest credential. Its kubeconfig includes only that identity, the public cluster CA and `https://production-demo-control-plane:6443`, whose certificate hostname is verified. The administrator kubeconfig is never mounted into Jenkins. Jenkins Credentials stores this as the `kind-deployer` file credential, bound only in the deployment stage.

The deployer can manage namespaced application resources but cannot list all Secrets, read nodes or create cluster roles. Helm uses `HELM_DRIVER=configmap`, avoiding broad Secret storage permissions. Existing application resources are adopted under the same release name; old Secret-backed local Helm history is preserved. `deploy-local.sh` refuses to overwrite a ConfigMap-backed CI-managed release.

Renew before expiry on the existing cluster:

```bash
bash scripts/jenkins-refresh-credentials.sh
```

This refreshes the protected kubeconfig and reloads Jenkins configuration without printing tokens. Renewal is **manual**, not scheduled: after expiry, the deploy stage fails until the credential is renewed. Expiry metadata is stored in `~/.local/share/kubernetes-production-demo/jenkins/credential-expiry`. After cluster recreation, re-run bootstrap and `jenkins-setup.sh` so namespaces, Secrets and deployer RBAC are also restored.

Use `HELM_DRIVER=configmap` when inspecting or rolling back the Jenkins-managed release. A normal `helm list` with the default Secret driver can display preserved pre-Jenkins history instead of the active CI release.

### Trust boundary and lifecycle

**The agent's Docker socket grants powerful Docker-daemon access and can bypass Kubernetes RBAC.** The agent is not a safe place to run untrusted code. Permit only trusted maintainers to change `main`, Jenkins configuration or the Jenkinsfile. Do not enable arbitrary PR builds. Neither container mounts your home directory or administrator kubeconfig. The agent's bootstrap volume contains only its host key, authorized public key and public GitHub host keys—not the controller admin password or checkout private key.

Keep the Mac awake and Docker Desktop running for polling and deployment. Jenkins uses persistent named volumes, with `restart: unless-stopped`. Stop or start it without removing volumes:

```bash
docker compose -f jenkins/compose.yaml stop
docker compose -f jenkins/compose.yaml start
```

If the Docker CLI is not on PATH, use `/Applications/Docker.app/Contents/Resources/bin/docker`. Back up Jenkins home and protected local state securely; they contain sensitive material. Do not archive them in builds. Changes to controller/agent Dockerfiles, plugins or bootstrap configuration require a reviewed rebuild/recreation through `jenkins-setup.sh`; an application build does not automatically reconfigure Jenkins itself.

## Prometheus and Alertmanager

Monitoring is separate from Jenkins and the application Helm release. From the repository root, with the kind cluster and Jenkins already running:

```bash
bash scripts/monitoring-setup.sh
python3 scripts/monitoring-verify.py
```

The setup requires your administrator workstation context, not the restricted Jenkins credential. It installs pinned `kube-prometheus-stack` 92.10.1 (including Grafana), `prometheus-blackbox-exporter` 11.20.0 and the local `monitoring-resources` chart into namespace `monitoring`. Bundled Grafana dashboards and kind-unreachable control-plane collectors are disabled; one project-specific dashboard is provisioned instead. Application default-deny NetworkPolicies remain unchanged.

Prometheus scrapes/evaluates every 30 seconds, retains up to 24 hours of samples with a 512 MB size limit and uses a 2 Gi local-path PVC. Alertmanager has a 256 Mi PVC for its local state; Grafana has a separate 1 Gi PVC. Explicit resource requests/limits keep the stack bounded: Prometheus and Grafana are each limited to 768 Mi RAM, and each Grafana provisioning sidecar to 128 Mi. Grafana uses a 512 MiB Go soft-memory target, leaving headroom for browser asset/query workloads. Allow roughly 2–3 Gi additional Docker memory headroom for the monitoring stack, with extra headroom for image builds. PVC data survives pod restarts but not deletion of the kind cluster.

### Private UI access

All three UIs are ClusterIP-only; no public ingress or always-running host port is created. Start these foreground port-forwards in **separate terminals**, then open the corresponding URL:

```bash
.tools/kubectl --context kind-production-demo -n monitoring port-forward --address 127.0.0.1 service/monitoring-prometheus 9090:9090
```

Prometheus: **http://127.0.0.1:9090** — inspect targets, rules, alerts and metrics.

```bash
.tools/kubectl --context kind-production-demo -n monitoring port-forward --address 127.0.0.1 service/monitoring-alertmanager 9093:9093
```

Alertmanager: **http://127.0.0.1:9093** — inspect grouped alerts and silences. Stop each port-forward with Ctrl+C when finished.

### Grafana dashboard

Start Grafana's loopback-only port-forward from the repository root:

```bash
.tools/kubectl --context kind-production-demo -n monitoring port-forward --address 127.0.0.1 service/monitoring-grafana 3000:80
```

Open **http://127.0.0.1:3000**. Log in as **admin**, using the separately generated password in `~/.local/share/kubernetes-production-demo/grafana/admin_password`. The directory is mode 0700 and the file mode 0600; import the password securely into your password manager rather than printing it into shared logs or chat. `scripts/grafana-credentials.py` creates the `grafana-admin` Kubernetes Secret through stdin and preserves existing credentials on subsequent setup runs.

Open **Production demo → Application & Jenkins Overview**, or go directly to **http://127.0.0.1:3000/d/production-demo-overview** after login.

The twelve metric panels show:

1. End-to-end application API availability.
2. Jenkins controller HTTP availability.
3. Jenkins agent SSH availability, not Jenkins connection state.
4. Restricted deployment credential hours remaining; negative means expired.
5. Desired versus available frontend/backend replicas.
6. Application-container restarts over the preceding 15 minutes.
7. Blackbox probe duration, not end-user latency percentiles.
8. Currently firing application/Jenkins/monitoring alerts.
9. Kubernetes node readiness for each node.
10. Node-exporter scrape health for each node.
11. Five-minute CPU usage percentage with readable node names.
12. Memory usage percentage based on available versus total memory.

A separate **Nodes** notice explains the kind limitation: the three nodes share one Docker Desktop Linux VM. Their node-exporter CPU/memory readings can be nearly identical and must not be summed as three independent machines or mistaken for per-container resource allocations. Readiness and scrape health are separate signals; missing metrics are shown as unknown, not zero usage or UP.

The exporter-health panel derives node names from Kubernetes pod metadata, rather than requiring the exporter to return its own identity metric while down. CPU/memory legends use `node_uname_info` to replace IP addresses with node names. These panels add visualization only; no new exporters or node alerts are installed.

The existing Prometheus datasource uses UID `prometheus`; no new application instrumentation or extra datasource credentials are required. Dashboard JSON is in `charts/monitoring-resources/files/dashboard.json` and is delivered through a labelled ConfigMap. Datasource/dashboard sidecars watch only namespace `monitoring` with namespaced RBAC. Anonymous access and sign-up are disabled.

The dashboard is provisioned read-only and refreshes every 30 seconds. To change the managed dashboard, edit its JSON and re-run monitoring setup; use a separate UI copy for experiments. Grafana stores its local SQLite settings/user state on the PVC, not the Prometheus time series. A single-instance Recreate strategy avoids overlapping Grafana processes on that local database.

Verify login protection, provisioning, datasource health and all panel queries without printing the password:

```bash
python3 scripts/grafana-verify.py
```

To verify the same local connection used by your browser, keep its port-forward running and use `python3 scripts/grafana-verify.py --url http://127.0.0.1:3000`. Verification checks browser JS/CSS assets, built-in time-series metadata and the actual `POST /api/ds/query` path, in addition to datasource health and proxy queries.

Changing the Secret alone is not a supported password-rotation procedure for an existing Grafana database. Use Grafana's documented administration/reset procedure and keep the protected local credential and Secret consistent. Do not delete the PVC merely to reset a password.

### Monitored paths and alerts

The application probe calls `/api/info` through Traefik with `Host: demo.localhost`, checking frontend → backend connectivity as well as HTTP 200 and an instance field. Jenkins probes check controller `/login` availability and the agent's SSH banner. Kubernetes metrics provide Deployment replica and container restart signals.

Rules in `charts/monitoring-resources/files/alerts.yaml`:

- `DemoUnavailableReplicas`: a desired frontend/backend replica is unavailable for two minutes.
- `DemoFrequentRestarts`: more than three application-container restarts over 15 minutes, sustained for two minutes.
- `DemoAPIUnavailable`: the end-to-end API probe fails for one minute.
- `JenkinsControllerUnavailable`: Jenkins login HTTP checks fail for one minute.
- `JenkinsAgentSSHUnavailable`: the agent SSH endpoint is unavailable for two minutes.
- `MonitoringTargetDown`: a selected monitoring scrape fails for two minutes.
- `MonitoringProbeMissing`: an expected application/Jenkins probe disappears for three minutes.
- `JenkinsCredentialExpiring`: expiry is between one and four hours away, sustained for one minute.
- `JenkinsCredentialExpiringSoon`: expiry is less than one hour away or already passed, sustained for one minute.
- `JenkinsCredentialMetadataMissing`: the public expiry metric is absent/zero for five minutes.

These are availability and credential-expiry alerts, **not** Jenkins build-result metrics, Jenkins agent-connection-state checks, application request/error-rate metrics or Warp usage/billing measurements. A single local monitoring installation also cannot send an alert when the entire Mac/Prometheus/Alertmanager is down; external paging/watchdog infrastructure would be needed for that.

### Local alert delivery and verification

Alertmanager groups notifications for ten seconds and sends firing/resolved alerts to `monitoring-receiver`, a private ClusterIP webhook. The receiver logs bounded summaries containing alert name, severity, namespace and status. It does not log complete notification bodies, annotations or credentials. NetworkPolicy limits receiver ingress to Prometheus and Alertmanager pods in its namespace.

**No email, Slack or external paging is configured.** This is intentional, testable local delivery—not a production notification guarantee. To add an external destination, configure a real Alertmanager receiver and store its authentication/webhook credentials outside Git. The local receiver's counters/logs are diagnostic and reset on restart; they are not a durable billing or audit ledger.

`monitoring-verify.py` checks all active targets, the three probes, ten loaded rules and the Alertmanager connection. It temporarily creates `MonitoringSmokeTest`, verifies delivery through Prometheus → Alertmanager → receiver, then removes the rule and closes its loopback port-forwards. It does not stop the app or Jenkins. Alert resolution may take another evaluation/grouping interval after test-rule removal.

### Jenkins address and expiry synchronization

The Jenkins controller joins Docker's existing `kind` network without exposing its host port beyond loopback. Monitoring Services use EndpointSlices populated from current Docker addresses, not fixed IPs. Re-sync after manually recreating or changing containers:

```bash
python3 scripts/monitoring-sync-targets.py
```

Jenkins setup and credential renewal call this helper automatically when monitoring is installed. The helper reads only the non-secret `credential-expiry` timestamp file, publishing it through a ConfigMap and gauge. No token, kubeconfig, password or private key is exposed to Prometheus, and Jenkins receives no additional Kubernetes permissions.

Configuration and rule validation, including synthetic rule-evaluation cases:

```bash
bash scripts/validate.sh
bash scripts/validate-infrastructure.sh
bash scripts/monitoring-check-rules.sh
```

These checks do not need a running Kubernetes cluster, but chart/image downloads require internet access. The last command requires Docker and runs native `promtool` checks using streamed inputs; it also works inside the Dockerized Jenkins agent. Monitoring installation is an administrator operation, not an automatic stage in the restricted application deployment job.

## Validation without a cluster

```bash
source .venv/bin/activate
python3 scripts/fetch-helm.py
bash scripts/validate.sh
bash scripts/validate-infrastructure.sh
```

The optional fetch script downloads a checksum-verified Helm binary into ignored `.tools/`, not a global directory. To also run the same schema validator as CI:

```bash
python3 scripts/fetch-kubeconform.py
.tools/kubeconform -strict -summary -kubernetes-version 1.34.0 .artifacts/local.yaml .artifacts/production.yaml
```

Include `.artifacts/jenkins.yaml` to check the Jenkins overlay as well. With Jenkins running, its native Declarative Pipeline validator checks the Jenkinsfile without triggering a build:

```bash
python3 scripts/jenkins-client.py lint
```

Rendered manifests and the local chart package are written to ignored `.artifacts/`. These checks do not prove image builds, actual NetworkPolicy enforcement or live rollout behavior; run bootstrap, deployment and smoke tests with Docker and Kubernetes available.

## Operations and troubleshooting

From the repository root:

```bash
python3 scripts/jenkins-client.py status
.tools/kubectl --context kind-production-demo -n demo get deployments,pods,services,ingress
HELM_DRIVER=configmap .tools/helm --kube-context kind-production-demo -n demo history demo
```

- **No automatic build:** confirm the Jenkinsfile is pushed to `main`, the Mac is awake, Docker is running and `kind-builder` is online. Allow up to two minutes for polling. Use `jenkins-client.py bootstrap` if no initial successful checkout exists.
- **Unauthorized/expired deploy credential:** run `bash scripts/jenkins-refresh-credentials.sh`. Do not replace it with an administrator kubeconfig.
- **Missing local image:** use the full Jenkins build to preload every node. An archived chart alone cannot fetch these `Never`-pull images.
- **App name does not resolve:** use the explicit Host-header curl command above. Check that ports 8080, 8443 and 8090 are not claimed by another service.
- **Jenkins is still starting or an agent is offline:** inspect service status with `docker compose -f jenkins/compose.yaml ps`. Controller/plugin downloads and SSH startup can take time. Do not paste raw credential files or unreviewed logs into issue reports.
- **Grafana page opens but shows “Failed to fetch” or panel-loading errors:** check that port 3000 is still forwarded and the Grafana container has not restarted/OOM-killed. A restart can drop the tunnel while leaving the old page visible. Re-establish the forward and hard-refresh the browser; inspect current memory/restart state before raising limits further.

Use `python3 scripts/jenkins-client.py log 3` to inspect a specific build; substitute the required build number. The helper masks known local credentials, but review logs before sharing them.

For an intentional rollback, inspect Helm history first, then select an existing revision whose images are still present on every kind node. For example, to roll back to revision 1:

```bash
HELM_DRIVER=configmap .tools/helm --kube-context kind-production-demo -n demo rollback demo 1 --wait --timeout 5m
```

The next successful Jenkins build deploys its newly packaged revision. Do not use `deploy-local.sh` to roll back a Jenkins-owned release.

## Cleanup

Stop Jenkins before removing its target cluster. These commands preserve Jenkins volumes, archived builds and protected credential files, but **cluster deletion removes all in-cluster resources and application Secrets**:
```bash
docker compose -f jenkins/compose.yaml stop
kind delete cluster --name production-demo
```

To decommission permanently, also remove the read-only deploy key from the GitHub repository and revoke `demo-deployer` access if the cluster is retained. Review backups and retention requirements before removing Jenkins volumes or protected local state. Do not use broad Docker prune commands or `compose down -v` as routine repository cleanup.

To remove monitoring while retaining the application and cluster, uninstall `monitoring-resources`, then `monitoring-blackbox`, then `monitoring`, using Helm with context `kind-production-demo` and namespace `monitoring`. Helm preserves monitoring PVCs and operator CRDs; review their data and any remaining resources before deliberate deletion. Do not delete shared CRDs or persistent volumes as routine cleanup.

Git excludes `.tools/`, `.artifacts/`, `.venv/`, Python caches, environment secrets and kubeconfigs. Credentials are held outside the checkout; no password, private key, kubeconfig or Jenkins data volume belongs in the repository or a build artifact.
