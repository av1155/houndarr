---
sidebar_position: 3
title: Kubernetes
description: How to deploy Houndarr on Kubernetes using a StatefulSet.
---

# Kubernetes

Houndarr can run on Kubernetes using a StatefulSet with persistent storage.

:::warning
Houndarr uses SQLite. Only one replica is supported — do not scale beyond 1.
:::

Prefer Helm or Flux? See the [Helm guide](./helm) for chart-based installation.

## Manifests

Apply all resources with `kubectl apply -f houndarr.yaml`. The sections below
can be combined into a single file separated by `---`.

### Namespace

Optional but keeps things tidy:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: houndarr
```

### StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: houndarr
  namespace: houndarr
spec:
  serviceName: "houndarr"
  replicas: 1 # SQLite — do not increase
  selector:
    matchLabels:
      app: houndarr
  template:
    metadata:
      labels:
        app: houndarr
    spec:
      containers:
        - name: houndarr
          image: ghcr.io/av1155/houndarr:latest
          ports:
            - containerPort: 8877
              name: http
          env:
            - name: TZ
              value: "America/New_York"
            - name: PUID
              value: "1000"
            - name: PGID
              value: "1000"
            # Uncomment when using an Ingress with TLS:
            # - name: HOUNDARR_SECURE_COOKIES
            #   value: "true"
            # - name: HOUNDARR_TRUSTED_PROXIES
            #   value: "10.244.0.0/16" # your ingress controller pod CIDR
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "64Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
```

The `volumeClaimTemplates` block creates a PVC automatically. The StatefulSet
manages its lifecycle — the PVC persists even if the pod is deleted or
rescheduled.

:::danger
The `/data` volume contains the encryption master key and database. Back it up.
If the master key is lost, all stored API keys become unrecoverable.
:::

### Non-root alternative with `securityContext`

If your cluster enforces Pod Security Standards or you need `runAsNonRoot:
true`, replace the PUID/PGID env vars with a `securityContext` block. Do not
combine both approaches — use one or the other.

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: houndarr
  namespace: houndarr
spec:
  serviceName: "houndarr"
  replicas: 1 # SQLite — do not increase
  selector:
    matchLabels:
      app: houndarr
  template:
    metadata:
      labels:
        app: houndarr
    spec:
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        runAsNonRoot: true
      containers:
        - name: houndarr
          image: ghcr.io/av1155/houndarr:latest
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          ports:
            - containerPort: 8877
              name: http
          env:
            - name: TZ
              value: "America/New_York"
            # No PUID/PGID — securityContext handles user identity
            # Uncomment when using an Ingress with TLS:
            # - name: HOUNDARR_SECURE_COOKIES
            #   value: "true"
            # - name: HOUNDARR_TRUSTED_PROXIES
            #   value: "10.244.0.0/16"
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "64Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
```

`fsGroup: 1000` tells the kubelet to set group ownership on the PVC contents,
so the volume is writable on first run without manual `chown`.

:::tip Migrating from PUID/PGID
If you have an existing deployment using PUID/PGID and want to switch, the
PVC contents may be owned by the old UID/GID. Run a one-time pod to fix
ownership before switching:

```bash
kubectl run fix-perms -n houndarr --rm -it --image=busybox \
  --overrides='{"spec":{"containers":[{"name":"fix","image":"busybox","command":["chown","-R","1000:1000","/data"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-houndarr-0"}}]}}'
```
:::

### Services

A StatefulSet requires a headless Service for pod DNS. A second ClusterIP
Service provides a stable endpoint for Ingress or direct access.

```yaml
# Headless Service (required by StatefulSet)
apiVersion: v1
kind: Service
metadata:
  name: houndarr
  namespace: houndarr
spec:
  clusterIP: None
  selector:
    app: houndarr
  ports:
    - port: 8877
      targetPort: http
---
# ClusterIP Service (for Ingress or port-forwarding)
apiVersion: v1
kind: Service
metadata:
  name: houndarr-web
  namespace: houndarr
spec:
  selector:
    app: houndarr
  ports:
    - port: 8877
      targetPort: http
```

## Exposing Houndarr

### Port-forwarding (quick access)

```bash
kubectl port-forward -n houndarr svc/houndarr-web 8877:8877
```

Then open `http://localhost:8877`.

### Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: houndarr
  namespace: houndarr
spec:
  rules:
    - host: houndarr.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: houndarr-web
                port:
                  number: 8877
  tls:
    - hosts:
        - houndarr.example.com
      secretName: houndarr-tls
```

When using TLS, uncomment the security env vars in the StatefulSet:

- `HOUNDARR_SECURE_COOKIES=true` — marks session cookies as HTTPS-only
- `HOUNDARR_TRUSTED_PROXIES` — set to your ingress controller's pod CIDR so
  the rate limiter sees real client IPs

See [Environment Variables](/docs/configuration/environment-variables) and
[Trust & Security](/docs/security/trust-and-security) for details.

## Verifying the deployment

```bash
# Check the pod is running
kubectl get pods -n houndarr

# View logs
kubectl logs -n houndarr houndarr-0

# Test the health endpoint
kubectl exec -n houndarr houndarr-0 -- wget -qO- http://localhost:8877/api/health
# Should return: {"status":"ok"}
```

## Helm

An official Helm chart is available. See the [Helm guide](./helm) for chart
installation and Flux HelmRelease configuration.
