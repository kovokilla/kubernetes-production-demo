pipeline {
    agent { label 'kind-builder' }
    options {
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '15', artifactNumToKeepStr: '10'))
    }
    triggers { pollSCM('H/2 * * * *') }
    environment {
        HELM_DRIVER = 'configmap'
        PATH = '/opt/venv/bin:/usr/local/bin:/usr/bin:/bin'
        DOCKER_HOST = 'unix:///opt/docker.sock'
    }
    stages {
        stage('Checkout trusted main') {
            steps {
                deleteDir()
                checkout scm
                script {
                    env.RELEASE_SHA = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    env.IMAGE_TAG = "sha-${env.RELEASE_SHA}-build-${env.BUILD_NUMBER}"
                }
            }
        }
        stage('Test and validate') {
            steps {
                sh 'bash scripts/validate.sh'
                sh 'bash scripts/validate-infrastructure.sh'
                sh 'kubeconform -strict -summary -kubernetes-version 1.34.0 .artifacts/local.yaml .artifacts/production.yaml'
            }
        }
        stage('Build containers') {
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
for component in backend frontend; do
  docker build --label "org.opencontainers.image.revision=$RELEASE_SHA" \
    -t "demo/$component:$IMAGE_TAG" "apps/$component"
done
'''
            }
        }
        stage('Load kind images') {
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
kind load docker-image "demo/backend:$IMAGE_TAG" "demo/frontend:$IMAGE_TAG" --name production-demo
'''
            }
        }
        stage('Update and package chart') {
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
python3 scripts/update-release.py --sha "$RELEASE_SHA" --run-number "$BUILD_NUMBER"
helm lint charts/webapp --strict -f .artifacts/release/values.yaml
helm template demo charts/webapp --namespace demo \
  -f .artifacts/release/values.yaml > .artifacts/release/manifests.yaml
python3 scripts/check-manifests.py .artifacts/release/manifests.yaml
kubeconform -strict -summary -kubernetes-version 1.34.0 .artifacts/release/manifests.yaml
helm package charts/webapp --destination .artifacts/release
'''
                archiveArtifacts artifacts: '.artifacts/release/*.tgz,.artifacts/release/values.yaml,.artifacts/release/images.json', fingerprint: true
            }
        }
        stage('Deploy exact package') {
            steps {
                withCredentials([file(credentialsId: 'kind-deployer', variable: 'KUBECONFIG')]) {
                    sh '''#!/usr/bin/env bash
set -euo pipefail
kubectl --context kind-production-demo -n demo get secret demo-app -o name
helm --kube-context kind-production-demo upgrade --install demo \
  ".artifacts/release/webapp-0.2.$BUILD_NUMBER.tgz" --namespace demo \
  -f .artifacts/release/values.yaml --atomic --wait --timeout 5m
kubectl --context kind-production-demo -n demo rollout status deployment/demo-frontend --timeout=300s
kubectl --context kind-production-demo -n demo rollout status deployment/demo-backend --timeout=300s
'''
                }
            }
        }
        stage('Verify ingress and API') {
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error --retry 5 --retry-connrefused \
  -H 'Host: demo.localhost' http://production-demo-control-plane:30080/api/info |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["message"] and d["instance"]; print("API verification passed")'
'''
            }
        }
    }
}
