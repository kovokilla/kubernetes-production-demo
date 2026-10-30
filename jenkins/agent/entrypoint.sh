#!/usr/bin/env bash
set -euo pipefail
cp /opt/agent-secrets/agent_host_key /etc/ssh/ssh_host_ecdsa_key
cp /opt/agent-secrets/agent_host_key_public /etc/ssh/ssh_host_ecdsa_key.pub
chmod 600 /etc/ssh/ssh_host_ecdsa_key
install -d -m 700 -o jenkins -g jenkins /home/jenkins/.ssh
cp /opt/agent-secrets/github_known_hosts /home/jenkins/.ssh/known_hosts
chown jenkins:jenkins /home/jenkins/.ssh/known_hosts
chmod 600 /home/jenkins/.ssh/known_hosts
if test -f /opt/agent-secrets/gitops-config.json; then
  install -m 600 -o jenkins -g jenkins /opt/agent-secrets/gitops-config.json /home/jenkins/gitops-config.json
fi
SOCKET_GID="$(stat -c %g /opt/docker.sock)"
if ! getent group "$SOCKET_GID" >/dev/null; then
  groupadd -g "$SOCKET_GID" docker-host
fi
usermod -aG "$SOCKET_GID" jenkins
export JENKINS_AGENT_SSH_PUBKEY="$(< /opt/agent-secrets/agent_key_public)"
exec /usr/local/bin/setup-sshd
