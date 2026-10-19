# Kubernetes application demo

Scope: frontend/backend Deployments, ClusterIP Services and Ingress, ConfigMap and Secret configuration, RBAC, NetworkPolicy, and GitHub Actions container builds plus versioned Helm packages.

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
- `environments/production/values.yaml`: deployment settings used by CI.
- `infrastructure`: kind, namespaces, pinned dependency versions, Traefik and optional deployment RBAC.
- `.github/workflows/ci-cd.yaml`: validate → build → package/publish → optional deploy.
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

Allocate approximately 4 CPUs and 6 GB RAM to the Docker VM. Ports 8080 and 8443 must be free. Bootstrap downloads the pinned kind node, Calico manifest and Traefik chart. Review dependency versions in `infrastructure/versions.env` before upgrading.

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

## GitHub Actions

Push this directory as the **root of a GitHub repository**, using branch `main`. No local Git repository, remote, commits or cloud resources are created by the setup scripts.

The workflow:

1. Runs application and release-script tests, syntax checks, Helm lint/template, architecture/security assertions and Kubernetes schema validation.
2. On `main` pushes or manual workflow dispatch, builds **both** images for `linux/amd64` and `linux/arm64` and publishes to GHCR. Pull requests validate without publishing.
3. Uses commit tags for traceability and immutable `sha256` image digests for deployments.
4. Updates chart defaults, release values, chart version (`0.1.<workflow-run-number>`) and `appVersion` in the release workspace.
5. Packages and pushes the updated chart to `oci://ghcr.io/<owner>/charts/webapp`, and uploads a `helm-release` artifact containing the `.tgz` and `values.yaml`.
6. Optionally installs that exact package with Helm, waits for rollout and automatically rolls back a failed upgrade.

The pipeline does **not** commit generated versions to Git. The published package and workflow artifact are the release records. Build/package jobs have `contents: read` and `packages: write` only. `GITHUB_TOKEN` supplies registry authorization; no personal token is required for publishing. Ensure repository/organization policy permits package publishing. If packages with these names already exist, grant the repository access to them.

Image names are `ghcr.io/<owner>/<repository>/frontend` and `.../backend`. Image references are generated automatically; the initial placeholders in source values are never used for a published release. For simple anonymous pulls, make both container packages public. For private packages, provision a `kubernetes.io/dockerconfigjson` Secret in `demo` and set `imagePullSecrets` in `environments/production/values.yaml`.

### Optional continuous deployment

Builds and packaging work without cluster credentials. To enable actual deployment:

1. Prepare a **runner-reachable** Kubernetes cluster with an enforcing CNI and Traefik IngressClass `traefik`. GitHub-hosted runners cannot reach a kind cluster on your laptop; use a reachable cloud/test cluster or adapt `runs-on` to a dedicated trusted runner with cluster access.
2. As an administrator, create the `demo` namespace, the `demo-app` Secret with key `signing-key`, and the TLS Secret `demo-tls`. Install Traefik in namespace `ingress` or adjust `networkPolicy.ingressNamespace`. Application charts do not create cluster prerequisites.
3. Set your actual DNS host in `environments/production/values.yaml`. Supply a matching certificate through `demo-tls` and route DNS to your ingress controller. Production values accept HTTPS only; the HTTP override is local-demo-only.
4. Apply `infrastructure/deployer-rbac.yaml` on the target cluster as an administrator. Configure a dedicated kubeconfig using that service account or equivalent namespaced deployment identity. The Role cannot list all Secrets or create cluster-scoped resources; deployment privileges are still trusted because they can change application workloads.
5. Create a GitHub environment named `production`, restrict it to `main`, and configure approval rules as appropriate. Add environment secret `KUBE_CONFIG_DATA`: base64-encoded contents of the dedicated deployment kubeconfig. Upload it through your approved secret-management process; never commit or print it. Use renewable/short-lived credentials where possible, and ensure any kubeconfig authentication plugin is available on the runner.
6. Set **repository variable** `ENABLE_DEPLOYMENT` to `true`. Leave it unset to publish artifacts only.

The deployment job uses `HELM_DRIVER=configmap` so Helm release metadata does not require broad Secret read/write access. Keep that driver when inspecting, upgrading or rolling back CI-managed releases. Kubeconfig is written with mode 0600 and removed after the job. The deployment refuses stale source revisions if `main` moved during the build or approval wait; dispatch a new workflow on the latest revision.

Do not point deployment credentials at an unrelated cluster. Local bootstrap uses NodePort on loopback-mapped ports; cloud ingress exposure and certificates must be configured for the target provider.

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
