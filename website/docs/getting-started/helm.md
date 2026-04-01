---
sidebar_position: 4
title: Helm
description: Install Houndarr with Helm or manage it via Flux using the official OCI chart.
---

# Helm

The official Houndarr Helm chart is published to GHCR as an OCI artifact and works with both plain `helm` and Flux.

:::warning
Houndarr uses SQLite. Only one replica is supported; do not scale beyond 1.
:::

## Prerequisites

- Helm 3.8 or later (OCI registry support is required)
- A Kubernetes cluster
- A namespace: `kubectl create namespace houndarr`

## Installing with Helm

Install directly from the OCI registry:

```bash
helm install houndarr \
  oci://ghcr.io/av1155/charts/houndarr \
  --version 1.4.0 \
  --namespace houndarr \
  --create-namespace
```

### Common overrides

**Non-root mode** (for clusters with Pod Security Standards):

```bash
helm install houndarr oci://ghcr.io/av1155/charts/houndarr \
  --version 1.4.0 \
  --namespace houndarr \
  --set securityMode=nonroot
```

**With Ingress and TLS** (replace CIDRs with your Ingress controller's pod CIDR):

```bash
helm install houndarr oci://ghcr.io/av1155/charts/houndarr \
  --version 1.4.0 \
  --namespace houndarr \
  --set ingress.enabled=true \
  --set ingress.host=houndarr.example.com \
  --set ingress.tls.enabled=true \
  --set ingress.tls.secretName=houndarr-tls \
  --set env.secureCookies=true \
  --set "env.trustedProxies=10.244.0.0/16"
```

### Upgrading

```bash
helm upgrade houndarr oci://ghcr.io/av1155/charts/houndarr \
  --version <new-version> \
  --namespace houndarr \
  --reuse-values
```

:::danger
Back up the `/data` PVC before upgrading. It contains the Fernet encryption master key (`houndarr.masterkey`). Loss of the master key makes all stored *arr API keys unrecoverable.
:::

## Flux HelmRelease (OCI)

Use `OCIRepository` (not `HelmRepository` with `type: oci`; that API is in maintenance mode per the Flux docs) to consume the chart in Flux.

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: houndarr
  namespace: flux-system
spec:
  interval: 12h
  url: oci://ghcr.io/av1155/charts/houndarr
  ref:
    semver: ">=1.4.0 <2.0.0"
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: houndarr
  namespace: houndarr
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: houndarr
    namespace: flux-system
  values:
    timezone: America/New_York
    env:
      secureCookies: true
      trustedProxies: "10.244.0.0/16"
    ingress:
      enabled: true
      className: nginx
      host: houndarr.example.com
      tls:
        enabled: true
        secretName: houndarr-tls
```

The `chartRef` field (instead of `chart.spec.sourceRef`) is what connects a `HelmRelease` to an `OCIRepository` source in Flux.

## Values reference

| Key | Default | Description |
|-----|---------|-------------|
| `image.repository` | `ghcr.io/av1155/houndarr` | Container image repository |
| `image.tag` | `""` | Image tag. Defaults to the chart's `appVersion` when empty |
| `image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `timezone` | `UTC` | Timezone (`TZ` env var) |
| `securityMode` | `compat` | `compat` (PUID/PGID remapping) or `nonroot` (pod securityContext) |
| `puid` | `1000` | UID for compat mode |
| `pgid` | `1000` | GID for compat mode |
| `env.secureCookies` | `false` | Set `HOUNDARR_SECURE_COOKIES=true`. Required for HTTPS |
| `env.cookieSamesite` | `lax` | `HOUNDARR_COOKIE_SAMESITE`: `lax` (allows dashboard links) or `strict` |
| `env.trustedProxies` | `""` | Comma-separated CIDRs for `HOUNDARR_TRUSTED_PROXIES` |
| `env.authMode` | `builtin` | `HOUNDARR_AUTH_MODE`: `builtin` or `proxy` |
| `env.authProxyHeader` | `""` | `HOUNDARR_AUTH_PROXY_HEADER` (e.g., `Remote-User`) |
| `env.logLevel` | `info` | `HOUNDARR_LOG_LEVEL`: `debug`, `info`, `warning`, `error` |
| `persistence.size` | `1Gi` | PVC size |
| `persistence.storageClassName` | `""` | StorageClass. Empty uses the cluster default |
| `persistence.accessMode` | `ReadWriteOnce` | PVC access mode |
| `resources.requests.memory` | `64Mi` | Memory request |
| `resources.requests.cpu` | `100m` | CPU request |
| `resources.limits.memory` | `256Mi` | Memory limit |
| `resources.limits.cpu` | `500m` | CPU limit |
| `service.port` | `8877` | Service port |
| `ingress.enabled` | `false` | Enable Ingress |
| `ingress.className` | `""` | `ingressClassName` |
| `ingress.annotations` | `{}` | Ingress annotations |
| `ingress.host` | `houndarr.example.com` | Ingress hostname |
| `ingress.tls.enabled` | `false` | Enable TLS in the Ingress |
| `ingress.tls.secretName` | `houndarr-tls` | TLS secret name |
| `extraEnv` | `[]` | Additional environment variables injected verbatim |
| `nameOverride` | `""` | Override the chart name portion of resource names |
| `fullnameOverride` | `""` | Override the full resource name |

For the two security modes and their trade-offs, see the [Kubernetes guide](./kubernetes).
