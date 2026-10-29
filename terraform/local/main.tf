terraform {
  required_version = ">= 1.10.0, < 2.0.0"
}

variable "cluster_name" {
  type    = string
  default = "production-demo-gitops"
  validation {
    condition     = can(regex("^production-demo-gitops[-a-z0-9]*$", var.cluster_name))
    error_message = "Use an isolated production-demo-gitops-prefixed cluster, never production-demo."
  }
}

variable "repository" {
  type    = string
  default = "kovokilla/kubernetes-production-demo"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.repository))
    error_message = "Expected GitHub owner/repository."
  }
}

variable "source_branch" {
  type    = string
  default = "main"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_/-]*$", var.source_branch))
    error_message = "Use a simple trusted source branch name."
  }
}

variable "gitops_branch" {
  type    = string
  default = "gitops/local"
  validation {
    condition     = can(regex("^gitops/[A-Za-z0-9][A-Za-z0-9_/-]*$", var.gitops_branch))
    error_message = "Use a dedicated gitops/... deployment branch."
  }
}

variable "http_port" {
  type    = number
  default = 18080
}
variable "https_port" {
  type    = number
  default = 18443
}
variable "jenkins_port" {
  type    = number
  default = 18090
}

variable "allow_github_writes" {
  description = "Explicit authorization to register three deploy keys and let CI publish gitops_branch. Does not commit/push source."
  type        = bool
  default     = false
}

locals {
  root = abspath("${path.module}/../..")
  config = {
    cluster_name        = var.cluster_name
    repository          = var.repository
    source_branch       = var.source_branch
    gitops_branch       = var.gitops_branch
    http_port           = var.http_port
    https_port          = var.https_port
    jenkins_port        = var.jenkins_port
    allow_github_writes = var.allow_github_writes
  }
  # Do not put credentials or file contents from protected local state here.
  environment = {
    DEMO_TF_CONFIG = jsonencode(local.config)
  }
  tracked_inputs = concat(
    sort(tolist(fileset(local.root, "scripts/*.py"))),
    sort(tolist(fileset(local.root, "scripts/*.sh"))),
    sort(tolist(fileset(local.root, "infrastructure/**"))),
    sort(tolist(fileset(local.root, "charts/**"))),
    sort(tolist(fileset(local.root, "jenkins/**"))),
    sort(tolist(fileset(local.root, "environments/**"))),
    sort(tolist(fileset(local.root, "apps/**"))),
    sort(tolist(fileset(local.root, "tests/**"))),
    ["Jenkinsfile.gitops", "requirements-dev.txt"]
  )
  content_hash = sha256(join("", [
    for name in local.tracked_inputs : "${name}:${filesha256("${local.root}/${name}")}"
    if !strcontains(name, "__pycache__") && !endswith(name, ".pyc")
  ]))
}

resource "terraform_data" "preflight" {
  triggers_replace = [local.config, local.content_hash]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py preflight"
  }
  lifecycle {
    precondition {
      condition = (
        var.source_branch != var.gitops_branch &&
        length(distinct([var.http_port, var.https_port, var.jenkins_port])) == 3 &&
        alltrue([for p in [var.http_port, var.https_port, var.jenkins_port] :
          p >= 1024 && p <= 65535 && floor(p) == p && !contains([8080, 8443, 8090], p)
        ])
      )
      error_message = "Source and deployment branches must differ; use distinct non-legacy unprivileged ports."
    }
  }
}

resource "terraform_data" "cluster" {
  # Only immutable cluster identity/port changes recreate the cluster. Code
  # changes rerun downstream idempotent stages, not the cluster destroy hook.
  input = {
    config = local.config
    root   = local.root
  }
  triggers_replace = [var.cluster_name, var.http_port, var.https_port]
  depends_on       = [terraform_data.preflight]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py cluster"
  }
  provisioner "local-exec" {
    when        = destroy
    working_dir = self.input.root
    environment = { DEMO_TF_CONFIG = jsonencode(self.input.config) }
    command     = "python3 scripts/gitops-bootstrap.py destroy"
  }
}

resource "terraform_data" "platform" {
  triggers_replace = [terraform_data.preflight.id, terraform_data.cluster.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py platform"
  }
}

resource "terraform_data" "argocd" {
  triggers_replace = [terraform_data.platform.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py argocd"
  }
}

resource "terraform_data" "jenkins" {
  triggers_replace = [terraform_data.argocd.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py jenkins"
  }
}

resource "terraform_data" "application" {
  triggers_replace = [terraform_data.jenkins.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py application"
  }
}

resource "terraform_data" "monitoring" {
  triggers_replace = [terraform_data.application.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py monitoring"
  }
}

resource "terraform_data" "verification" {
  triggers_replace = [terraform_data.monitoring.id]
  provisioner "local-exec" {
    working_dir = local.root
    environment = local.environment
    command     = "python3 scripts/gitops-bootstrap.py verify"
  }
}

output "application_url" {
  value = "http://demo.localhost:${var.http_port}"
}
output "jenkins_url" {
  value = "http://127.0.0.1:${var.jenkins_port}"
}
output "argocd_port_forward" {
  value = ".tools/kubectl --kubeconfig \"$HOME/.local/share/kubernetes-production-demo/${var.cluster_name}/kubeconfig\" --context kind-${var.cluster_name} -n argocd port-forward --address 127.0.0.1 service/argocd-server 18081:443"
}
