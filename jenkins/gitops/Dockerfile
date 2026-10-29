FROM jenkins/jenkins:2.580.1-jdk21@sha256:2635653a1b716e0e4be2cfb11bef31e85c133c9cf564c287e4d3764ebae1a433
COPY jenkins/controller/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY jenkins/gitops/jenkins.yaml /usr/share/jenkins/ref/jenkins.yaml
COPY jenkins/gitops/init.groovy /usr/share/jenkins/ref/init.groovy.d/gitops.groovy.override
