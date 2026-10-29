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


## Terraform / Argo CD mode

The optional isolated GitOps environment does not adopt the legacy cluster or
its Jenkins volumes. Terraform bootstraps platform components and Argo CD
owns the webapp; do not grant multiple deployment systems ownership of it.
Terraform uses local bootstrap stages, not native per-object drift detection.
Generated kubeconfigs, private keys and passwords are kept outside the checkout
and passed through stdin/volumes, not Terraform variables or outputs. State
and plan files must still be private and backed up; `sensitive` alone would not
encrypt them. Terraform artifacts are excluded from Git and Docker build contexts.

Initial provisioning registers separate GitHub deploy keys for checkout,
Argo CD read access and GitOps publication. The publication key is
**repository-write**, not branch-limited. The publisher checks its environment
marker and never force-pushes, but those checks are not a GitHub authorization
boundary: compromised CI could use the key elsewhere in the repository.
Use only trusted source branches and an appropriately scoped separate
deployment repository/GitHub App before extending this design to less-trusted
contributors. `allow_github_writes` defaults to false; turning it on authorizes
key registration and deployment-branch writes, not publication of local source.

Jenkins does not receive application deployment privileges. Its separate
observer client certificate requests a 30-day lifetime and permits only
GET of the single `demo` Application in namespace `argocd`. The actual lifetime
depends on the cluster signer; renew through the Jenkins bootstrap stage
before expiry. This is not the legacy deployment token and its expiry is not
currently alerted on. The agent still has powerful Docker-daemon access.

Argo CD has broader controller privileges as a platform component. Its `demo`
AppProject limits application destinations to `demo`, restricts resource kinds,
and does not allow Secrets or cluster-scoped objects. Repository access uses
verified SSH host keys. The UI stays ClusterIP-only and serves TLS; inspect/trust
its self-signed local certificate instead of disabling script verification.

Teardown requires an exact environment-name confirmation and matching local
ownership marker. It deletes the owned cluster and its PVC data, but deliberately
retains Docker volumes, protected credentials and GitHub deploy keys/history.
Revoke those credentials and review retained data separately when decommissioning.

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
