# Security policy

## Supported use

This repository is a local DevOps demonstration, not a production platform or a
multi-tenant CI service. It demonstrates explicit controls and their limitations.
Passing tests or secret scans is not a security certification.

## Reporting a vulnerability

Do not open a public issue containing passwords, tokens, private keys, raw
kubeconfigs, credential files or sensitive logs. Use GitHub private vulnerability
reporting if it is available on the repository; otherwise contact the maintainer
privately through their GitHub profile before sharing sensitive details.

Include the affected revision/file, expected behavior and a minimal reproduction
using dummy credentials. There is no guaranteed response SLA. If a real credential
is exposed, revoke/rotate it immediately; deleting its current file or changing
repository visibility is not sufficient.

## Trust boundaries

- Only trusted changes to `main` may run on the Jenkins Docker-socket agent.
  The socket grants powerful Docker-daemon access and can bypass Kubernetes
  restrictions. Groovy sandboxing, non-root jobs and separate containers do not
  make untrusted shell/build commands safe.
- Do not enable automatic builds of forks or unreviewed pull requests on that
  agent. Source-write and Jenkins administrator access must stay restricted.
- Jenkins is a single-admin local setup. Its current authorization strategy
  grants broad permissions to authenticated users; do not add less-trusted
  accounts without replacing that strategy with appropriate authorization.
- Kubernetes deployer permissions are namespaced but trusted: an identity that
  modifies workloads can affect data accessible to those workloads.
- Jenkins is loopback-bound; Grafana/Prometheus/Alertmanager use local
  port-forwards. Do not expose these services or the Kubernetes API publicly
  without a separate authentication, TLS and authorization design.
- Application HTTP and direct kind image loading are lab conveniences, not
  recommendations for public production services.

## Argo CD ownership handoff

The Argo-only path reuses the existing cluster, application and Jenkins
containers/volumes; it does not provision another cluster or change the CNI.
Installation alone creates no application ownership. The separate migration
command requires explicitly approved Git writes and published trusted source.
It pauses legacy deployment, backs up Jenkins privately, verifies the exact
deployed chart through server-side dry-runs and preserves application resources
and Helm history. Checkpoint/ownership records prevent accidental competing
Helm deployment and permit rollback after a partial handoff.

Argo CD receives a separate read-only repository key. Jenkins GitOps publication
uses a separately authorized **repository-write** key; it is not restricted to
the deployment branch by GitHub. Environment markers and non-force pushes are
safety checks, not an authorization boundary. A compromised Docker-socket agent
can bypass both Git and Kubernetes controls; do not enable unreviewed PR builds.
The helper reuses registered keys and does not silently create broader access.

The AppProject restricts the source repository, destination namespace and
allowed resource kinds. It cannot deploy Secrets or cluster-scoped objects.
Argo's controller itself has broader platform permissions and must remain
administrator-controlled. Its UI is ClusterIP-only and uses TLS with a local
self-signed certificate; inspect/trust that certificate rather than disabling
verification in scripts.

The active GitOps job receives no direct Kubernetes deployment credential.
Its observer client certificate requests a 30-day lifetime and permits only
GET of the single `demo` Application in `argocd`. Inspect the actual issued
expiry and renew through the Argo helper while CI is idle; no observer-expiry
alert is currently configured. Old deployment-token material is retained
privately for rollback, not bound to the GitOps job.

Migration backups contain sensitive Jenkins data and must remain outside Git
with restricted permissions. A failed handoff leaves CI disabled intentionally.
Do not erase the checkpoint or force legacy deployment while Argo still owns
the application. Ownership rollback removes only a verified, non-cascading
Application and does not uninstall the workloads.

## Local HTTP clients

Credential-bearing local helpers are bound to one validated HTTP loopback
origin: scheme, hostname and effective port. Cross-origin requests/redirects,
URL userinfo and non-HTTP targets are rejected; environment proxies are disabled.
Same-origin redirects remain supported. Cookie/CSRF data is not forwarded to a
different origin by these helpers.

This prevents redirect-based credential forwarding, not a malicious local process
impersonating the original service. Prefer a helper-owned port-forward; do not
point authenticated helpers at an untrusted localhost listener. Never place
credentials in URL userinfo or command-line literals.

## Secret handling and scans

Generated Jenkins/Grafana credentials live outside the repository in protected
local state and dedicated Docker volumes. They are not encrypted merely because
file permissions are restrictive; backups must be secured separately.
Kubernetes Secret base64 is not encryption. Short-lived deployment tokens need
renewal and can be revoked by removing the relevant identity/access.

Run `bash scripts/security-check.sh` before publication and after sensitive
configuration changes. Install a reviewed Gitleaks version first; v8.30.1 was used
to validate the configuration. The script scans reachable history and a snapshot
of tracked/non-ignored source, redacts findings, and rejects publishable private
guides/generated directories.

`.gitleaks.toml` retains default detectors. Its only added exception requires
both the exact public `ssh-credentials` plugin-version line and its manifest path.
It does not allowlist all plugin files, test files, commits or credential names.
Review any exception before widening it. Scanning cannot detect every custom,
encoded or previously compromised credential.

## Public-release controls

Before changing visibility, review all reachable history and old CI logs/artifacts,
author identity/privacy, source ownership and dependency licenses.
Use protected `main`/rulesets when available: prevent force pushes/deletion,
require pull requests and resolve review conversations. Require independent
approval when trusted reviewers exist; a sole maintainer cannot approve their
own pull request, so choose that policy deliberately.

Enable available GitHub secret scanning and push protection after publication,
and keep GitHub Actions disabled unless its workflows are intentionally reviewed.
Check container/package visibility separately from repository visibility.
Review dependency/image advisories regularly; pins and checksums improve
integrity/reproducibility but do not establish freedom from vulnerabilities.

Private `_README.private.md` files are excluded locally through
`.git/info/exclude` and omitted from build packages. Local Git exclusion is not
cloned and can be bypassed with force-add; the publication check must still pass.
