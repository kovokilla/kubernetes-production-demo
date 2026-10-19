# Kubernetes application demo

Scope: frontend/backend Deployments, ClusterIP Services and Ingress, ConfigMap and Secret configuration, RBAC, NetworkPolicy, and Jenkins container builds plus versioned Helm packages and deployment.

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
- `environments/local/values.yaml`: local images and HTTP ingress.
- `environments/production/values.yaml`: optional HTTPS configuration for a real deployment.
- `environments/jenkins/values.yaml`: local-kind delivery settings used by Jenkins.
- `infrastructure`: kind, namespaces, pinned dependency versions, Traefik and optional deployment RBAC.
- `Jenkinsfile`: checkout → validate → build → load images → package → deploy → verify.
- `jenkins`: Docker Compose controller/agent, pinned plugins and configuration as code.
- `scripts`: bootstrap, local deployment and validation.

## Run locally

Prerequisites: a running Docker-compatible daemon, kind, kubectl, Helm 3, Python 3.9+ and Node.js 24+. On macOS, install and start Docker Desktop or another kind-compatible Docker provider, then install the CLI tools:

```bash
brew install kind kubectl helm node python
cd ~/kubernetes-production-demo
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
bash scripts/validate.sh
bash scripts/bootstrap.sh
bash scripts/deploy-local.sh
bash scripts/smoke-test.sh
```

Allocate approximately 4 CPUs and 6 GB RAM for the application, or 8 CPUs and 8 GB RAM with Jenkins. Ports 8080 and 8443 must be free; Jenkins additionally uses 8090. Bootstrap downloads the pinned kind node, Calico manifest and Traefik chart. Review dependency versions in `infrastructure/versions.env` before upgrading.

Open **http://demo.localhost:8080**. If your resolver does not support `.localhost`, test using the explicit Host header:

```bash
curl --fail -H 'Host: demo.localhost' http://127.0.0.1:8080/api/info
```

All cluster scripts explicitly target `kind-production-demo`; they do not use your current Kubernetes context. Images are built locally and loaded directly into kind, with `imagePullPolicy: Never`. Re-running deployment rebuilds the images and restarts the Deployments.

Scripts also detect Docker Desktop's standard macOS CLI directory when it is missing from PATH. Use kubectl 1.34 for the pinned Kubernetes 1.34 cluster (supported clients are within one minor version); a matching binary in `.tools/kubectl` takes precedence over the globally installed client.

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

## Jenkins CI/CD

The private source repository remains on GitHub. Jenkins polls only `main` every two minutes; no public webhook, tunnel or GitHub Actions runner is needed. The local setup targets Apple Silicon/Linux ARM64 containers and requires Docker Compose plus an authenticated `gh` CLI with permission to register a read-only repository deploy key.

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

Renew before expiry, and after recreating the kind cluster:

```bash
bash scripts/jenkins-refresh-credentials.sh
```

This refreshes the protected kubeconfig and reloads Jenkins configuration without printing tokens. Expiry metadata is stored in `~/.local/share/kubernetes-production-demo/jenkins/credential-expiry`. Use the same `HELM_DRIVER=configmap` when inspecting or rolling back the Jenkins-managed release.

### Trust boundary and lifecycle

**The agent's Docker socket grants powerful Docker-daemon access and can bypass Kubernetes RBAC.** The agent is not a safe place to run untrusted code. Permit only trusted maintainers to change `main`, Jenkins configuration or the Jenkinsfile. Do not enable arbitrary PR builds. Neither container mounts your home directory or administrator kubeconfig. The agent's bootstrap volume contains only its host key, authorized public key and public GitHub host keys—not the controller admin password or checkout private key.

Keep the Mac awake and Docker Desktop running for polling and deployment. Jenkins uses persistent named volumes, with `restart: unless-stopped`. Stop or start it without removing volumes:

```bash
docker compose -f jenkins/compose.yaml stop
docker compose -f jenkins/compose.yaml start
```

If the Docker CLI is not on PATH, use `/Applications/Docker.app/Contents/Resources/bin/docker`. Back up Jenkins home and protected local state securely; they contain sensitive material. Do not archive them in builds. Decommission by stopping Jenkins, removing its read-only GitHub deploy key, and deleting the `demo-deployer` ServiceAccount/Role/RoleBinding to revoke cluster access. Remove volumes/state only after a deliberate backup/data-retention decision.

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

Rendered manifests and the local chart package are written to ignored `.artifacts/`. These checks do not prove image builds, actual NetworkPolicy enforcement or live rollout behavior; run bootstrap, deployment and smoke tests with Docker and Kubernetes available.

To remove only the exercise cluster and its in-cluster data:

```bash
kind delete cluster --name production-demo
```
