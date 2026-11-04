#!/usr/bin/env bash
# Build the images INSIDE THE OVERNIGHT BUILD VM ONLY, where the internet is reached through an
# HTTPS proxy. On the Mac, use `docker compose build` (or ./try.sh) instead: it needs none of this.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${HTTPS_PROXY:?this script is for the proxy VM only}"
# The proxy's whole CA bundle (it re-signs TLS with more than one CA), under the name the Dockerfile expects.
ca_dir=$(mktemp -d)
trap 'rm -rf "$ca_dir"' EXIT
cp "${PROXY_CA_BUNDLE:-/root/.ccr/ca-bundle.crt}" "$ca_dir/agent-proxy-ca.crt"
what=${1:-all}
args=(--network host --build-arg NET=proxy --build-context "proxyca=$ca_dir"
      --build-arg "https_proxy=$HTTPS_PROXY" --build-arg "HTTPS_PROXY=$HTTPS_PROXY"
      --build-arg "no_proxy=${NO_PROXY:-localhost}" --build-arg "NO_PROXY=${NO_PROXY:-localhost}")
if [ "$what" = all ] || [ "$what" = bot ]; then
  docker build "${args[@]}" -t meeting-bot:latest bot
fi
if [ "$what" = all ] || [ "$what" = manager ]; then
  docker build "${args[@]}" -t meeting-bot-manager:latest -f manager/Dockerfile .
fi
if [ "$what" = all ] || [ "$what" = testkit ]; then
  docker build "${args[@]}" -t meeting-bot-testkit:latest -f tests/testkit/Dockerfile .
fi
