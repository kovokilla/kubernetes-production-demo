FROM node:24.14.0-bookworm-slim AS node
FROM docker:29.8.2-cli AS docker
FROM jenkins/ssh-agent:jdk21@sha256:22f644521d3b26d405b67e3b9b75f1333b2c04cad22cd5f0bc001efe6070030f
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY requirements-dev.txt /opt/bootstrap/requirements-dev.txt
COPY scripts/fetch-helm.py scripts/fetch-kubeconform.py /opt/bootstrap/
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /opt/bootstrap/requirements-dev.txt \
    && python3 /opt/bootstrap/fetch-helm.py \
    && python3 /opt/bootstrap/fetch-kubeconform.py \
    && ln -s /opt/.tools/helm /usr/local/bin/helm \
    && ln -s /opt/.tools/kubeconform /usr/local/bin/kubeconform
COPY jenkins/agent/install-tools.py /opt/bootstrap/install-tools.py
RUN python3 /opt/bootstrap/install-tools.py
COPY jenkins/agent/entrypoint.sh /usr/local/bin/demo-agent-entrypoint
RUN chmod 755 /usr/local/bin/demo-agent-entrypoint
ENV PATH="/opt/venv/bin:${PATH}"
ENV DOCKER_HOST="unix:///opt/docker.sock"
ENTRYPOINT ["/usr/local/bin/demo-agent-entrypoint"]
