#!/bin/bash

set -euo pipefail

# Change to platform cluster
if [[ "$(kubectl config current-context)" != "kind-platform" ]]; then
    kubectl config use-context kind-platform || {
        echo "Failed to switch context to kind-platform"
        exit 1
    }
fi

NS=kargo-system
PROJECT_NS=microservice-delivery
BASE_DIR="$(dirname "$0")"
KARGO_VERSION=1.11.2
ROLLOUTS_CHART_VERSION=2.41.1
CERT_MANAGER_VERSION=v1.21.1
PORT=3002
REPO_URL="https://github.com/koorikla/platform-engineering-backstack.git"
GIT_USERNAME=koorikla

# Kargo delegates verification to Argo Rollouts' analysis engine: the
# AnalysisTemplate/AnalysisRun kinds live in argoproj.io, not kargo.akuity.io.
# Kargo's chart checks for those CRDs at startup and silently disables the
# integration when they are missing, so the quality gate would never run and
# nothing would say why. Only the controller and CRDs are needed -- no Rollout
# resources are used.
# Kargo's chart issues its webhook and API server certificates through
# cert-manager Issuer/Certificate resources. Every tls.selfSignedCert value
# defaults to true and each one states that cert-manager CRDs must be present;
# the alternative is supplying certs and a caBundle by hand, which the chart
# itself advises against. Without it the install fails outright with
# `no matches for kind "Certificate" in version "cert-manager.io/v1"`.
echo "Installing cert-manager (Kargo issues its webhook certificates through it)..."
helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo update jetstack >/dev/null
helm upgrade --install cert-manager jetstack/cert-manager \
    --version "$CERT_MANAGER_VERSION" \
    --namespace cert-manager \
    --create-namespace \
    --set crds.enabled=true \
    --wait --timeout 5m

echo "Waiting for the cert-manager CRDs to be established..."
kubectl wait --for=condition=established --timeout=120s \
    crd/certificates.cert-manager.io crd/issuers.cert-manager.io || {
    echo "❌ cert-manager CRDs did not become established"
    exit 1
}

echo "Installing Argo Rollouts (provides the AnalysisTemplate CRD Kargo verifies with)..."
# Installed from the chart rather than `kubectl apply -f <github release url>`:
# that fetches over the network with no retry, and a transient GitHub timeout
# aborts the whole bootstrap. Helm is already a prerequisite here and retries
# repository access. installCRDs defaults to true, which is the part Kargo needs.
helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
helm repo update argo >/dev/null
helm upgrade --install argo-rollouts argo/argo-rollouts \
    --version "$ROLLOUTS_CHART_VERSION" \
    --namespace argo-rollouts \
    --create-namespace \
    --set installCRDs=true \
    --wait --timeout 5m

echo "Waiting for the AnalysisTemplate CRD to be established..."
kubectl wait --for=condition=established --timeout=120s \
    crd/analysistemplates.argoproj.io crd/analysisruns.argoproj.io || {
    echo "❌ Argo Rollouts CRDs did not become established"
    exit 1
}

echo "Installing or upgrading Kargo..."
helm upgrade --install kargo \
    oci://ghcr.io/akuity/kargo-charts/kargo \
    --version "$KARGO_VERSION" \
    --namespace "$NS" \
    --create-namespace \
    --set api.service.type=ClusterIP \
    --set api.adminAccount.passwordHash='$2a$10$Zrhhie4vLz5ygtVSaif6o.qN36jgs6vjtHbdWoYjX4uMe3Q8hnfsy' \
    --set api.adminAccount.tokenSigningKey=kargo-local-dev-signing-key \
    --set api.rollouts.integrationEnabled=true \
    --wait --timeout 5m

echo "Waiting for Kargo to be ready..."
kubectl wait --for=condition=available --timeout=180s -n "$NS" deployment --all || {
    echo "❌ Kargo deployments are not ready"
    exit 1
}

# The Project creates its own namespace, so it must exist before the Secret and
# the Warehouse/Stages that live in it.
echo "Applying the Kargo project..."
kubectl apply -f ./kargo/project.yaml
for _ in $(seq 1 30); do
    kubectl get namespace "$PROJECT_NS" >/dev/null 2>&1 && break
    sleep 2
done

# Same .env handling as the Backstage bootstrap: sourcing under `set -a` rather
# than `export $(cat .env | xargs)`, which breaks on a trailing comment.
if [[ ! -f .env ]]; then
    echo "❌ .env not found in the repo root. It must define GITHUB_TOKEN."
    exit 1
fi
set -a
# shellcheck source=/dev/null
source ./.env
set +a
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "❌ GITHUB_TOKEN is not set in .env"
    exit 1
fi

echo "Creating Kargo Git credentials..."
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
sed -e "s|<repo-url-placeholder>|$(b64 "$REPO_URL")|" \
    -e "s|<username-placeholder>|$(b64 "$GIT_USERNAME")|" \
    -e "s|<placeholder>|$(b64 "$GITHUB_TOKEN")|" \
    "$BASE_DIR/manifests/git-credentials.template.yaml" |
    kubectl apply -f -

echo "Applying the analysis template, warehouse and stages..."
kubectl apply -f ./kargo/analysis-templates
kubectl apply -f ./kargo/warehouse.yaml
kubectl apply -f ./kargo/stages

# Probe rather than trust lsof: a port-forward whose pod has gone still owns the
# socket briefly, which would make a port check report a healthy forward.
if ! curl -s --max-time 3 "http://localhost:$PORT" >/dev/null 2>&1; then
    pkill -f "port-forward svc/kargo-api" 2>/dev/null || true
    echo "Starting port-forward for the Kargo UI on port $PORT..."
    nohup kubectl --namespace "$NS" port-forward svc/kargo-api "$PORT":80 >/dev/null 2>&1 &
    for _ in $(seq 1 30); do
        curl -s --max-time 2 "http://localhost:$PORT" >/dev/null 2>&1 && break
        sleep 1
    done
else
    echo "Port-forward on $PORT is already serving."
fi

echo "✅ Kargo setup completed successfully!"
