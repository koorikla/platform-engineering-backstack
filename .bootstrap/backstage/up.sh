#!/bin/bash

set -euo pipefail

# Configuration
NS=backstage-system
BASE_DIR="$(dirname "$0")"
PORT=3000
IMAGE="backstage:latest"
CLUSTER_NAME="platform"
CONTEXT_NAME="kind-${CLUSTER_NAME}"

# Switch kubectl context if not already set
if [[ "$(kubectl config current-context)" != "$CONTEXT_NAME" ]]; then
    echo "Switching kubectl context to $CONTEXT_NAME..."
    kubectl config use-context "$CONTEXT_NAME" || {
        echo "❌ Failed to switch context to $CONTEXT_NAME"
        exit 1
    }
fi

# Create namespace if it doesn't exist
if ! kubectl get namespace "$NS" >/dev/null 2>&1; then
    echo "Creating namespace: $NS"
    kubectl create namespace "$NS"
else
    echo "✅ Namespace $NS already exists."
fi

echo "Checking if Backstage image '$IMAGE' already exists..."
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "🔨 Building Backstage image $IMAGE..."
    # Multi-stage build: yarn install and the repo build run inside Docker, so
    # the host needs no Node toolchain. The build compiles native modules
    # (isolated-vm, better-sqlite3) that only work on the Node major versions
    # listed under `engines` in backstage/package.json, which is easy to get
    # wrong on a host with a newer Node on PATH.
    DOCKER_BUILDKIT=1 docker build ./backstage \
        -f ./backstage/packages/backend/Dockerfile \
        --tag "$IMAGE"
else
    echo "✅ Docker image $IMAGE already exists. Skipping build."
fi

kind load docker-image "$IMAGE" --name "$CLUSTER_NAME"

# `export $(cat .env | xargs)` splits a trailing `# comment` -- or any value
# containing spaces -- into bare words that export rejects. Because the apply was
# chained onto it with `&&`, that failure silently skipped creating the secret
# rather than stopping the script. Sourcing under `set -a` applies normal shell
# parsing, so comments and quoting behave.
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

# printf rather than echo: echo appends a newline, which would be baked into the
# base64 and give Backstage a token with a trailing \n. tr strips the line wrap
# that GNU base64 adds for inputs over 76 chars (macOS base64 does not wrap).
GITHUB_TOKEN_B64="$(printf '%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"

# Argo CD admin password. Defaults to the value .bootstrap/argocd/up.sh sets as
# ARGO_PWD_NEW; override in .env if that is ever changed.
ARGOCD_PASSWORD="${ARGOCD_PASSWORD:-12345678}"
ARGOCD_PASSWORD_B64="$(printf '%s' "$ARGOCD_PASSWORD" | base64 | tr -d '\n')"

sed -e "s|<placeholder>|$GITHUB_TOKEN_B64|" \
    -e "s|<argocd-placeholder>|$ARGOCD_PASSWORD_B64|" \
    "$BASE_DIR/backstage-secrets.template.yaml" |
    kubectl apply -n "$NS" -f -

# Argo CD signs its API with a self-signed certificate generated per cluster.
# Its SANs cover argocd-server.argocd-system.svc.cluster.local, so only the
# issuer is untrusted -- copying the certificate here and pointing
# NODE_EXTRA_CA_CERTS at it (see manifests/deployment.yaml) trusts that one
# certificate, instead of switching off TLS verification for every outbound
# request Backstage makes.
echo "Copying Argo CD's CA certificate into $NS..."
if kubectl get secret argocd-secret -n argocd-system >/dev/null 2>&1; then
    kubectl get secret argocd-secret -n argocd-system -o jsonpath='{.data.tls\.crt}' |
        base64 --decode >/tmp/argocd-ca.crt
    kubectl create configmap argocd-ca -n "$NS" \
        --from-file=argocd-ca.crt=/tmp/argocd-ca.crt \
        --dry-run=client -o yaml | kubectl apply -f -
    rm -f /tmp/argocd-ca.crt
else
    echo "⚠️  argocd-secret not found; the Argo CD plugin will not be able to verify TLS."
fi

# Wait for postgres deployment to be ready
echo "Waiting for postgres deployment to be ready..."
kubectl rollout status deployment/postgres -n "$NS" --timeout=120s || {
    echo "❌ Postgres deployment is not ready"
    exit 1
}

# Wait for backstage deployment to be ready
echo "Waiting for backstage deployment to be ready..."
kubectl rollout status deployment/backstage -n "$NS" --timeout=120s || {
    echo "❌ Backstage deployment is not ready"
    exit 1
}

# Start port-forward in background if not already running
if ! lsof -i TCP:$PORT | grep LISTEN >/dev/null 2>&1; then
    if kubectl get svc/backstage -n "$NS" >/dev/null 2>&1; then
        echo "Starting port-forward for Backstage on port $PORT..."
        nohup kubectl --namespace "$NS" port-forward svc/backstage ${PORT}:80 >/dev/null 2>&1 &
        echo "Port-forward started in background."
    else
        echo "Backstage service not found. Skipping port-forward."
        exit 1
    fi
else
    echo "Port $PORT is already in use. Assuming port-forward is running."
fi

echo "✅ Backstage setup completed successfully!"
